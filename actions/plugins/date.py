"""Навык «какая сегодня дата» — пример плагина."""
import datetime

from persona import line

from ..utils import Reply

NAME = "date"
PATTERNS = [r"какое (сегодня )?число|какая (сегодня )?дата|какой сегодня день"]

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def handle(cmd, match):
    today = datetime.date.today()
    return Reply(line("date", t=f"{DAYS[today.weekday()]}, {today.day} {MONTHS[today.month - 1]}"))
