"""Команды поиска по документам и заметкам: «найди в документах …», «проиндексируй документы»."""
import threading

from persona import line

from .utils import Reply, log


class RagMixin:
    """RAG по папке documents и заметкам. Хранилище подключает main (Actions.rag)."""

    def rag_index(self, c, m):
        rag = getattr(self, "rag", None)
        if rag is None:
            return Reply(line("rag_off"))
        if getattr(self, "rag_indexing", False):
            return Reply(line("rag_busy"))

        def run():
            try:
                rag.build(notify=True)
            except Exception:
                log.exception("Не удалось обновить индекс документов")
            finally:
                self.rag_indexing = False

        self.rag_indexing = True
        threading.Thread(target=run, daemon=True).start()
        return Reply(line("rag_indexing"), None, "thinking")

    def rag_ask(self, c, m):
        rag = getattr(self, "rag", None)
        if rag is None:
            return Reply(line("rag_off"))
        groups = m.groupdict()
        query = (groups.get("q") or groups.get("q2") or "").strip(" ,.;:-")
        if not query:
            return Reply(line("rag_need_query"))
        try:
            result = rag.answer(query, llm=getattr(self, "local", None))
        except Exception:
            log.exception("Ошибка поиска по документам: %s", query)
            return Reply(line("error", e="поиск по документам не сработал"), None, "error")
        if not result:
            return Reply(line("rag_not_found", q=query))
        answer, sources = result
        src = ", ".join(sources[:2])
        return Reply(line("rag_answer", answer=answer, src=src), "answer", "joy")
