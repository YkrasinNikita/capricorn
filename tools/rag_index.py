"""Перестроить индекс документов или задать вопрос по ним из консоли.

    python tools/rag_index.py                    # перестроить index/rag_index.json
    python tools/rag_index.py "о чём документ?"  # найти ответ по документам

Работает офлайн: эмбеддинги считает локальная Ollama, при её отсутствии — поиск по словам.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from rag import RagIndex  # noqa: E402


def main():
    rag = RagIndex(BASE)
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        result = rag.answer(question)
        if not result:
            print("Ничего не нашла. Добавь .md/.txt в documents/ и перестрой индекс.")
            return 1
        answer, sources = result
        print(answer)
        print("Источники:", ", ".join(sources))
        return 0
    print("Документы:", ", ".join(source for source, _ in rag.documents()) or "папка documents пуста")
    count = rag.build()
    print(f"Готово: {count} фрагментов → {rag.index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
