"""Команды напоминаний: «напомни через 10 минут …», «какие напоминания», «отмени …»."""
import datetime as dt

from persona import line
from scheduler import format_when, parse_when

from .utils import Reply


class RemindersMixin:
    """Обработчики напоминаний. Планировщик подключает main (Actions.scheduler)."""

    def reminder_add(self, c, m):
        if not getattr(self, "scheduler", None):
            return Reply(line("reminder_off"))
        parsed = parse_when(c)
        if not parsed:
            return Reply(line("reminder_bad_time"))
        when, repeat, text = parsed
        try:
            item = self.scheduler.add(text or "напоминание", when, repeat)
        except Exception as e:
            return Reply(line("reminder_fail", e=e))
        return Reply(line("reminder_add", t=item["text"], when=format_when(when)), None, "joy")

    def reminder_list(self, c, m):
        scheduler = getattr(self, "scheduler", None)
        if not scheduler:
            return Reply(line("reminder_off"))
        items = scheduler.all()
        if not items:
            return Reply(line("reminder_none"))
        parts = [f"{format_when(dt.datetime.fromisoformat(item['time']))} — {item['text']}"
                 for item in items[:3]]
        more = f" И ещё {len(items) - 3}." if len(items) > 3 else ""
        return Reply(line("reminder_list", n=len(items), t="; ".join(parts) + more))

    def reminder_cancel(self, c, m):
        scheduler = getattr(self, "scheduler", None)
        if not scheduler:
            return Reply(line("reminder_off"))
        query = (m.groupdict().get("q") or "").strip()
        gone = scheduler.cancel(query)
        if not gone:
            return Reply(line("reminder_missing"))
        return Reply(line("reminder_cancel", t="; ".join(item["text"] for item in gone)))
