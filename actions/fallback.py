"""Ответ, когда команда не распознана: поиск в Google или «не знаю»."""
import re
import urllib.parse

from persona import line

from .utils import Reply, norm


class FallbackMixin:
    """Запасной ответ без ИИ."""

    def fallback(self, cmd, force_search=False, allow_search=True):
        """Без ИИ: вопрос ищем в Google (если search_when_unsure не выключен), остальное — «не знаю команды».
        allow_search=False — приватный режим: в сеть не ходим даже за поиском."""
        c = norm(cmd)
        if force_search or re.match(r"^(что|кто|как|какой|какая|какое|какие|почему|зачем|сколько|где|когда|куда|"
                     r"откуда|чем|чей|расскажи|объясни|скажи)\b", c):
            if not allow_search or not self.cfg.get("search_when_unsure", True):
                return Reply(line("dont_know"))
            self.web("https://www.google.com/search?q=" + urllib.parse.quote(cmd))
            return Reply(line("search_q"))
        return Reply(line("unknown"))
