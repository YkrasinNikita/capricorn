"""Навык «помаши ручкой» — пример плагина с действием анимации."""
from persona import line

from ..utils import Reply

NAME = "wave"
PATTERNS = [r"помаши|помахай|махни|помаши ручкой|поздоровайся"]


def handle(cmd, match):
    return Reply(line("wave"), "wave")
