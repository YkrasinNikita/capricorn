"""Заметки и память «Капри»: notes.json с поиском, дедупликацией и лимитом хранения.

Заметка: {"id", "text", "tags", "created", "modified"}. Время — ISO.
Битый файл не роняет приложение: он сохраняется как notes.json.bak, работаем дальше
с пустым списком.
"""
import datetime as dt
import difflib
import json
import logging
import os
import re
import threading
import uuid

log = logging.getLogger("capri")

DEFAULT_MAX = 500
ISO = "%Y-%m-%dT%H:%M:%S"
_SIMILAR = 0.9  # насколько похожие записи считаем дублем


def _now():
    return dt.datetime.now().replace(microsecond=0)


def _norm_text(text):
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _stem(word):
    """Грубая основа слова: «встреча» и «встречу» должны находить друг друга."""
    word = _norm_text(word)
    return word[:5] if len(word) > 5 else word


def _extract_tags(text):
    tags = [t.lower() for t in re.findall(r"#(\w+)", text or "")]
    clean = re.sub(r"#\w+", " ", text or "")
    clean = re.sub(r"\s+", " ", clean).strip(" ,.;:-")
    return clean or (text or "").strip(), tags


class Notes:
    def __init__(self, path, max_notes=DEFAULT_MAX):
        self.path = os.fspath(path)
        self.max_notes = max(1, int(max_notes))
        self.items = []
        self._lock = threading.RLock()
        self._load()

    # ----- хранение -----
    def _load(self):
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception:
            log.exception("notes.json не прочитался, начинаю с пустого списка")
            try:
                os.replace(self.path, self.path + ".bak")
            except OSError:
                log.exception("Не удалось сохранить копию notes.json")
            return
        items = data.get("notes") if isinstance(data, dict) else data
        if not isinstance(items, list):
            log.warning("notes.json: ожидался список заметок")
            return
        clean = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            created = str(item.get("created") or _now().strftime(ISO))
            clean.append({
                "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                "text": text,
                "tags": [str(t).lower() for t in (item.get("tags") or []) if str(t).strip()],
                "created": created,
                "modified": str(item.get("modified") or created),
            })
        self.items = clean

    def _save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"notes": list(self.items)}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ----- публичный API -----
    def add(self, text, tags=None):
        """Добавить заметку. Возвращает (note, duplicate). Похожая запись обновляется, а не дублируется."""
        text, embedded = _extract_tags(str(text or "").strip())
        if not text:
            raise ValueError("пустой текст заметки")
        all_tags = sorted({t.lower() for t in (tags or [])} | set(embedded))
        key = _norm_text(text)
        now = _now().strftime(ISO)
        with self._lock:
            for note in self.items:
                if _norm_text(note["text"]) == key or difflib.SequenceMatcher(
                        None, key, _norm_text(note["text"])).ratio() >= _SIMILAR:
                    note["text"] = text
                    note["tags"] = sorted(set(note.get("tags", [])) | set(all_tags))
                    note["modified"] = now
                    self._save()
                    return dict(note), True
            note = {
                "id": uuid.uuid4().hex[:8],
                "text": text,
                "tags": all_tags,
                "created": now,
                "modified": now,
            }
            self.items.append(note)
            while len(self.items) > self.max_notes:  # лимит: убираем самую старую запись
                oldest = min(self.items, key=lambda x: (x.get("created", ""), x["id"]))
                self.items.remove(oldest)
                log.info("Лимит заметок: удалена самая старая «%s»", oldest["text"][:40])
            self._save()
        log.info("Заметка добавлена: %s", text[:60])
        return dict(note), False

    def all(self):
        with self._lock:
            return sorted((dict(x) for x in self.items),
                          key=lambda x: (x.get("modified", ""), x.get("created", "")), reverse=True)

    def search(self, query, limit=None):
        """Поиск без регистра. Возвращает (список заметок, всего найдено)."""
        q = _norm_text(query)
        with self._lock:
            items = [dict(x) for x in self.items]
        if not q:
            found = sorted(items, key=lambda x: (x.get("modified", ""), x.get("created", "")), reverse=True)
            return (found[:limit] if limit else found), len(found)
        words = [w for w in q.split() if len(w) >= 3]
        scored = []
        for note in items:
            hay = _norm_text(note["text"] + " " + " ".join(note.get("tags", [])))
            score = 10 if q in hay else 0
            for word in words:
                stem = _stem(word)
                if word in hay:
                    score += 4
                elif stem and stem in hay:
                    score += 2
            if score:
                scored.append((score, note))
        scored.sort(key=lambda x: x[1].get("modified", ""), reverse=True)  # сначала свежие
        scored.sort(key=lambda x: x[0], reverse=True)                      # потом по релевантности
        found = [note for _, note in scored]
        return (found[:limit] if limit else found), len(found)

    def delete(self, query):
        """Удалить заметку по id или подстроке текста. Возвращает удалённые заметки."""
        raw = (query or "").strip()
        q = re.sub(r"^(?:про|о|об|насчет|насчёт)\s+", "", _norm_text(raw))
        if not q and not raw:
            return []
        words = [w for w in q.split() if len(w) >= 3]
        gone = []
        with self._lock:
            for note in list(self.items):
                text = _norm_text(note["text"])
                by_text = q and (q in text or text in q)
                by_words = bool(words) and all(_stem(w) in text for w in words)
                if note["id"] == raw or by_text or by_words:
                    self.items.remove(note)
                    gone.append(dict(note))
            if gone:
                self._save()
        return gone

    def count(self):
        with self._lock:
            return len(self.items)
