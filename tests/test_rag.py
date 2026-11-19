"""RAG по документам и заметкам: нарезка, поиск по словам/смыслу, источники и битый индекс."""
import json

from notes import Notes
from rag import RagIndex, chunk_text, cosine


def write_docs(tmp_path):
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "budget.md").write_text(
        "Бюджет проекта — сто тысяч рублей. Деньги выделены на оборудование и аренду.", encoding="utf-8")
    (docs / "vacation.txt").write_text(
        "Отпуск планируется в июле. Поездка в Карелию, три дня на озере.", encoding="utf-8")
    return docs


def fake_embed(text):
    low = text.lower()
    if "бюджет" in low or "деньг" in low or "рубл" in low:
        return [1.0, 0.0, 0.0]
    if "отпуск" in low or "карел" in low:
        return [0.0, 1.0, 0.0]
    return [0.2, 0.2, 0.95]


def test_cosine_handles_bad_vectors():
    assert cosine([1, 0], [1, 0]) == 1
    assert cosine([1, 0], [0, 1]) == 0
    assert cosine([], [1]) == 0
    assert cosine([1, 0], [1]) == 0


def test_chunk_text_respects_size_and_keeps_text():
    text = "Первое предложение. " * 120
    chunks = chunk_text(text, size=500, overlap=100)
    assert len(chunks) > 2
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "Первое предложение" in chunks[0]


def test_chunk_text_short_text_is_one_chunk():
    assert chunk_text("Короткий текст.") == ["Короткий текст."]


def test_lexical_search_without_embeddings(tmp_path):
    docs = write_docs(tmp_path)
    rag = RagIndex(tmp_path, docs_dir=docs, index_dir=tmp_path / "index", embed=lambda text: None)
    assert rag.build() >= 2
    hits = rag.search("сколько денег в бюджете")
    assert hits
    assert hits[0]["source"].endswith("budget.md")


def test_vector_search_finds_document_by_meaning(tmp_path):
    docs = write_docs(tmp_path)
    rag = RagIndex(tmp_path, docs_dir=docs, index_dir=tmp_path / "index", embed=fake_embed)
    rag.build()
    hits = rag.search("отпуск")
    assert hits[0]["source"].endswith("vacation.txt")


def test_answer_with_llm_gets_context_and_sources(tmp_path):
    docs = write_docs(tmp_path)
    rag = RagIndex(tmp_path, docs_dir=docs, index_dir=tmp_path / "index", embed=fake_embed)
    rag.build()

    class FakeLLM:
        def __init__(self):
            self.context = None

        def ask(self, question, context=None, timeout=None, retries=None):
            self.context = context
            return "Бюджет — сто тысяч рублей."

    llm = FakeLLM()
    answer, sources = rag.answer("какой бюджет проекта", llm=llm)
    assert "сто тысяч" in answer
    assert "budget.md" in " ".join(sources)
    assert "сто тысяч" in llm.context


def test_answer_without_llm_is_extractive(tmp_path):
    docs = write_docs(tmp_path)
    rag = RagIndex(tmp_path, docs_dir=docs, index_dir=tmp_path / "index", embed=lambda text: None)
    rag.build()
    answer, sources = rag.answer("Карелия")
    assert "Карелию" in answer
    assert sources[0].endswith("vacation.txt")


def test_notes_are_searchable_without_reindexing(tmp_path):
    notes = Notes(tmp_path / "notes.json")
    notes.add("встреча с Аней в пятницу")
    rag = RagIndex(tmp_path, index_dir=tmp_path / "index", notes=notes, embed=lambda text: None)
    hits = rag.search("встречу с Аней")
    assert hits
    assert hits[0]["source"] == "заметки"


def test_index_survives_restart(tmp_path):
    docs = write_docs(tmp_path)
    first = RagIndex(tmp_path, docs_dir=docs, index_dir=tmp_path / "index", embed=fake_embed)
    first.build()
    second = RagIndex(tmp_path, docs_dir=docs, index_dir=tmp_path / "index", embed=fake_embed)
    hits = second.search("отпуск")
    assert hits[0]["source"].endswith("vacation.txt")


def test_broken_index_does_not_crash(tmp_path):
    docs = write_docs(tmp_path)
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    (index_dir / "rag_index.json").write_text(json.dumps({"entries": ["мусор"]}), encoding="utf-8")
    rag = RagIndex(tmp_path, docs_dir=docs, index_dir=index_dir, embed=lambda text: None)
    assert rag.search("бюджет")[0]["source"].endswith("budget.md")


def test_index_if_needed_builds_only_once(tmp_path):
    docs = write_docs(tmp_path)
    calls = []

    def embed(text):
        calls.append(text)
        return None

    rag = RagIndex(tmp_path, docs_dir=docs, index_dir=tmp_path / "index", embed=embed)
    assert rag.index_if_needed() >= 2
    first = len(calls)
    assert rag.index_if_needed() == len(rag._entries)
    assert len(calls) == first  # документы не изменились — повторно не индексируем
