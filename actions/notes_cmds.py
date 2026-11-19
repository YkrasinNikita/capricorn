"""Команды заметок: «запомни, что …», «что ты знаешь о …», «покажи заметки», «удали заметку …»."""
from persona import line

from .utils import Reply


class NotesMixin:
    """Обработчики заметок. Хранилище подключает main (Actions.notes)."""

    def note_add(self, c, m):
        notes = getattr(self, "notes", None)
        if not notes:
            return Reply(line("note_off"))
        text = (m.groupdict().get("t") or "").strip(" ,.;:-")
        if not text:
            return Reply(line("note_need_text"))
        note, duplicate = notes.add(text)
        return Reply(line("note_exists" if duplicate else "note_saved", t=note["text"]), None,
                     "neutral" if duplicate else "joy")

    def note_search(self, c, m):
        notes = getattr(self, "notes", None)
        if not notes:
            return Reply(line("note_off"))
        query = (m.groupdict().get("q") or "").strip(" ,.;:-")
        found, total = notes.search(query, limit=2)
        if not found:
            return Reply(line("note_not_found", q=query))
        text = "; ".join(note["text"] for note in found)
        more = f" Ещё {total - len(found)}." if total > len(found) else ""
        return Reply(line("note_found", t=text, more=more), None, "joy")

    def note_list(self, c, m):
        notes = getattr(self, "notes", None)
        if not notes:
            return Reply(line("note_off"))
        items = notes.all()
        if not items:
            return Reply(line("note_empty"))
        parts = [note["text"] for note in items[:3]]
        more = f" И ещё {len(items) - 3}." if len(items) > 3 else ""
        return Reply(line("note_list", n=len(items), t="; ".join(parts) + more))

    def note_delete(self, c, m):
        notes = getattr(self, "notes", None)
        if not notes:
            return Reply(line("note_off"))
        query = (m.groupdict().get("q") or "").strip(" ,.;:-")
        gone = notes.delete(query)
        if not gone:
            return Reply(line("note_missing"))
        return Reply(line("note_deleted", t="; ".join(note["text"] for note in gone)))
