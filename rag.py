"""Поиск по своим документам и заметкам (RAG) — локально, через эмбеддинги Ollama.

Документы — обычные .md и .txt из папки `documents/`. Индекс лежит в `index/` и
перестраивается командой «проиндексируй документы» или при первом запуске.

Всё считается на этом компьютере: облако не используется даже при включённом ИИ.
Если модель эмбеддингов не скачана (`ollama pull nomic-embed-text`), поиск
автоматически переходит на обычное совпадение слов — индекс и ответы продолжают
работать, просто менее точно.

Публичный API:
    index = RagIndex(base_dir, notes=notes)
    index.build()                      # перестроить индекс
    index.search("вопрос")             # список фрагментов с источниками
    index.answer("вопрос", llm=local)  # (ответ, [источники]) или None
"""
import datetime as dt
import json
import logging
import math
import os
import re
import threading
import urllib.request

log = logging.getLogger("capri")

URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
INDEX_FILE = "rag_index.json"
DOC_EXTS = (".md", ".txt")
CHUNK_SIZE = 700
CHUNK_OVERLAP = 120
TOP_K = 3
TIMEOUT = 25

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def normalize(text):
    """Нижний регистр, ё→е, только слова: удобно сравнивать фразы и словоформы."""
    text = (text or "").lower().replace("ё", "е")
    return " ".join(_WORD_RE.findall(text))


def stem(word):
    """Грубый стеммер как в notes.py: «встречу» и «встреча» дают общий корень."""
    word = normalize(word)
    return word[:5] if len(word) > 5 else word


def cosine(a, b):
    """Косинус между двумя векторами. Нулевые/битые векторы дают 0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Разбить документ на куски по абзацам, не длиннее size символов.

    Абзацы склеиваются, пока влезают; длинный абзац режется по предложениям,
    а совсем длинное предложение — с перекрытием, чтобы не терять стыки.
    """
    text = re.sub(r"\r\n?", "\n", str(text or ""))
    text = re.sub(r"[ \t]+", " ", text)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks, current = [], ""
    for block in blocks:
        block = re.sub(r"\s*\n\s*", " ", block).strip()
        if len(block) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long(block, size, overlap))
            continue
        if current and len(current) + 1 + len(block) > size:
            chunks.append(current)
            room = max(0, size - len(block) - 1)
            tail = current[-min(overlap, room):].lstrip() if overlap and room else ""
            current = (tail + " " + block).strip() if tail else block
        else:
            current = f"{current} {block}".strip()
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _split_long(block, size, overlap):
    """Длинный абзац: сначала по предложениям, крайний случай — по символам."""
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?…])\s+", block):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > size:
            if current:
                chunks.append(current)
                current = ""
            step = max(1, size - overlap)
            chunks.extend(sentence[i:i + size] for i in range(0, len(sentence), step))
            continue
        if current and len(current) + 1 + len(sentence) > size:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


class RagIndex:
    """Индекс фрагментов документов и заметок с поиском по смыслу или словам."""

    def __init__(self, base_dir, docs_dir=None, index_dir=None, model=EMBED_MODEL,
                 top_k=TOP_K, notes=None, embed=None, enabled=True):
        self.base_dir = os.fspath(base_dir)
        self.docs_dir = os.fspath(docs_dir or os.path.join(self.base_dir, "documents"))
        self.index_dir = os.fspath(index_dir or os.path.join(self.base_dir, "index"))
        self.index_path = os.path.join(self.index_dir, INDEX_FILE)
        self.model = model or EMBED_MODEL
        self.top_k = max(1, int(top_k or TOP_K))
        self.notes = notes
        self.enabled = bool(enabled)
        self._embed = embed or self.embed_text
        self._embed_ok = True
        self._entries = []
        self._lock = threading.RLock()
        self.last_error = None
        self.on_indexed = None  # main: callback(сколько_фрагментов), вызывается после build
        self.load()

    # ----- документы -----
    def documents(self):
        """Список (источник, текст) из папки documents. Битые файлы пропускаем."""
        if not os.path.isdir(self.docs_dir):
            return []
        found = []
        for root, dirs, files in os.walk(self.docs_dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if not name.lower().endswith(DOC_EXTS) or name.startswith("."):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, encoding="utf-8-sig", errors="replace") as f:
                        text = f.read()
                except OSError:
                    log.exception("Не удалось прочитать документ %s", path)
                    continue
                if not text.strip():
                    continue
                rel = os.path.relpath(path, self.docs_dir)
                source = os.path.join("documents", rel).replace("\\", "/")
                found.append((source, text))
        return found

    def stale(self):
        """Индекс старше документов (или его нет) — пора перестроить."""
        try:
            index_time = os.path.getmtime(self.index_path)
        except OSError:
            return True
        for root, _, files in os.walk(self.docs_dir):
            for name in files:
                if name.lower().endswith(DOC_EXTS):
                    try:
                        if os.path.getmtime(os.path.join(root, name)) > index_time:
                            return True
                    except OSError:
                        pass
        return False

    # ----- эмбеддинги Ollama -----
    def embed_text(self, text):
        """Вектор текста через локальную Ollama. None — модель недоступна."""
        payload = json.dumps({"model": self.model, "prompt": text[:8000]}).encode("utf-8")
        req = urllib.request.Request(URL + "/api/embeddings", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        vector = data.get("embedding")
        return [float(x) for x in vector] if isinstance(vector, list) and vector else None

    def _safe_embed(self, text):
        """Один раз выясняем, что Ollama не отвечает, и дальше не ждём таймаут на каждом куске."""
        if not self._embed_ok:
            return None
        try:
            vector = self._embed(text)
            if vector:
                return vector
        except Exception as e:
            self.last_error = str(e)
            self._embed_ok = False
            log.info("Эмбеддинги Ollama недоступны (%s), ищу по словам", e)
        return None

    # ----- индекс -----
    def build(self, notify=False):
        """Перестроить индекс документов. Возвращает число фрагментов."""
        if not self.enabled:
            return 0
        with self._lock:
            self._embed_ok = True
            entries = []
            documents = self.documents()
            for source, text in documents:
                for number, chunk in enumerate(chunk_text(text)):
                    entries.append({
                        "source": source,
                        "title": os.path.basename(source),
                        "chunk": number,
                        "text": chunk,
                        "vector": self._safe_embed(chunk),
                    })
            data = {
                "version": 1,
                "model": self.model,
                "built": dt.datetime.now().replace(microsecond=0).isoformat(),
                "entries": entries,
            }
            try:
                os.makedirs(self.index_dir, exist_ok=True)
                tmp = self.index_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, self.index_path)
            except OSError:
                log.exception("Не удалось сохранить индекс документов")
            self._entries = entries
            log.info("Индекс документов: %d фрагментов из %d файлов", len(entries), len(documents))
        if notify and self.on_indexed:
            try:
                self.on_indexed(len(entries))
            except Exception:
                log.exception("Не удалось сообщить о готовности индекса")
        return len(entries)

    def load(self):
        """Прочитать индекс. Битый файл не мешает работе — просто перестроим."""
        try:
            with open(self.index_path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception:
            log.exception("Индекс документов повреждён, перестрою при необходимости")
            return
        entries = data.get("entries") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return
        clean = []
        for item in entries:
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            vector = item.get("vector")
            clean.append({
                "source": str(item.get("source") or "documents"),
                "title": str(item.get("title") or os.path.basename(str(item.get("source") or ""))),
                "chunk": item.get("chunk", 0),
                "text": str(item["text"]),
                "vector": [float(x) for x in vector] if isinstance(vector, list) and vector else None,
            })
        self._entries = clean

    def ensure(self):
        """Построить индекс, если его ещё нет или он старше документов."""
        if not self.enabled:
            return 0
        if self._entries and not self.stale():
            return len(self._entries)
        if not self.documents():
            return len(self._entries)
        return self.build()

    def index_if_needed(self):
        """Фоновый вызов при старте приложения: не блокирует появление Капри."""
        try:
            return self.ensure()
        except Exception:
            log.exception("Не удалось построить индекс документов")
            return 0

    # ----- поиск -----
    def _all_chunks(self):
        """Фрагменты документов + заметки (заметки читаем вживую, без переиндексации)."""
        chunks = [dict(e) for e in self._entries]
        notes = self.notes
        if notes is not None:
            try:
                for note in notes.all():
                    chunks.append({
                        "source": "заметки",
                        "title": "заметки",
                        "chunk": note.get("id", ""),
                        "text": note.get("text", ""),
                        "vector": None,
                    })
            except Exception:
                log.exception("Не удалось прочитать заметки для поиска")
        return [c for c in chunks if c["text"].strip()]

    @staticmethod
    def _key(entry):
        return entry["source"], str(entry["chunk"]), entry["text"][:60]

    @staticmethod
    def lexical_score(query, text):
        q, hay = normalize(query), normalize(text)
        if not q or not hay:
            return 0.0
        score = 6.0 if q in hay else 0.0
        words = [w for w in q.split() if len(w) >= 3]
        for word in words:
            if word in hay:
                score += 2.0
            elif stem(word) in hay:
                score += 1.0
        return score / max(1, len(words))

    def search(self, query, limit=None):
        """Лучшие фрагменты по вопросу: [{"source", "title", "text", "score"}].

        Смысл (косинус эмбеддингов) и слова объединяются ранговым голосованием,
        поэтому поиск работает и без скачанной модели эмбеддингов.
        """
        limit = max(1, int(limit or self.top_k))
        query = (query or "").strip()
        if not query:
            return []
        self.ensure()
        chunks = self._all_chunks()
        if not chunks:
            return []

        lexical = [(self.lexical_score(query, c["text"]), c) for c in chunks]
        lexical = [(s, c) for s, c in lexical if s > 0]
        lexical.sort(key=lambda pair: pair[0], reverse=True)

        vectors = []
        query_vector = self._safe_embed(query) if any(c.get("vector") for c in chunks) else None
        if query_vector:
            for c in chunks:
                if c.get("vector"):
                    score = cosine(query_vector, c["vector"])
                    if score > 0.05:
                        vectors.append((score, c))
            vectors.sort(key=lambda pair: pair[0], reverse=True)

        scores, by_key = {}, {}
        for rank, (score, c) in enumerate(lexical[:20]):
            key = self._key(c)
            by_key[key] = c
            scores[key] = scores.get(key, 0.0) + 0.4 / (rank + 1)
        for rank, (score, c) in enumerate(vectors[:20]):
            key = self._key(c)
            by_key[key] = c
            scores[key] = scores.get(key, 0.0) + 0.6 / (rank + 1)
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [dict(by_key[key], score=round(scores[key], 4)) for key in ordered]

    def answer(self, question, llm=None):
        """Ответ по найденным фрагментам: (текст, [источники]) или None.

        С локальной моделью — пересказ с опорой на документы; без неё —
        самый подходящий фрагмент дословно. Облако не используется.
        """
        hits = self.search(question)
        if not hits:
            return None
        context = "\n\n".join(f"[{i + 1}] {h['source']}: {h['text']}" for i, h in enumerate(hits))
        answer = None
        if llm is not None:
            prompt = ("Ответь на вопрос только по этим фрагментам. Если ответа в них нет, "
                      "скажи, что в документах этого не сказано.\n\n" + context)
            try:
                try:
                    answer = llm.ask(question, context=prompt, timeout=60, retries=0)
                except TypeError:  # старый/тестовый LocalAI без context
                    answer = llm.ask(question)
            except Exception:
                log.exception("Локальная модель не ответила по документам")
                answer = None
        if not answer:
            answer = _extractive(hits[0]["text"])
        sources = []
        for hit in hits:
            if hit["source"] not in sources:
                sources.append(hit["source"])
        return answer, sources


def _extractive(text, limit=320):
    """Короткий ответ-цитата без модели: целые предложения, не длиннее limit."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"), cut.rfind("…"))
    return (cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0]) + "…"
