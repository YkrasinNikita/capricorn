"""Навык «который час» — пример плагина: весь навык умещается в одном файле."""
import datetime

from persona import line

from ..utils import Reply

NAME = "time"
PATTERNS = [r"который час|сколько времени|сколько сейчас времени|время сейчас"]


def handle(cmd, match):
    return Reply(line("time", t=f"{datetime.datetime.now():%H:%M}"))
