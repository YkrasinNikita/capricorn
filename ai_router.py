"""Единая цепочка ответов ИИ: локальная Ollama → Claude (если разрешён ключ) → офлайн-заготовка.

Наружу — один интерфейс: ask(text) -> (answer, source), где source — "local", "claude",
"offline" или "none". Роутер не знает про GUI и не отправляет запросы в облако
в приватном режиме (privacy_mode).
"""
import logging

log = logging.getLogger("capri")

LOCAL_TIMEOUT = 20   # секунд на локальную модель
CLOUD_TIMEOUT = 15   # секунд на облачный ответ
RETRIES = 1          # один повтор при ошибке


class AIRouter:
    def __init__(self, cfg, local=None, cloud=None, offline=None):
        self.cfg = cfg or {}
        self.local = local
        self.cloud = cloud
        self.offline = offline   # callable(text) -> str | None, офлайн-заготовка/поиск
        self.last_source = None

    @property
    def privacy(self):
        return bool(self.cfg.get("privacy_mode"))

    def _ask_local(self, text):
        for attempt in range(RETRIES + 1):
            try:
                try:
                    # повторы делает сам роутер: внутри LocalAI они не нужны, иначе
                    # один и тот же запрос уйдёт четыре раза
                    answer = self.local.ask(text, timeout=LOCAL_TIMEOUT, retries=0)
                except TypeError:  # совместимость с LocalAI без новых параметров
                    try:
                        answer = self.local.ask(text, timeout=LOCAL_TIMEOUT)
                    except TypeError:
                        return self.local.ask(text)
            except Exception as e:
                log.info("Локальный ИИ не ответил (попытка %d): %s", attempt + 1, e)
                answer = None
            if answer:
                return answer
            if not getattr(self.local, "last_error", None) or attempt >= RETRIES:
                return None
        return None

    def _ask_cloud(self, text):
        for attempt in range(RETRIES + 1):
            try:
                try:
                    return self.cloud.ask(text, timeout=CLOUD_TIMEOUT)
                except TypeError:  # совместимость со старым AI.ask без timeout
                    return self.cloud.ask(text)
            except Exception as e:
                log.info("Claude не ответил (попытка %d): %s", attempt + 1, e)
        return None

    def ask(self, text, context=None):
        """Ответ и его источник. Никогда не бросает наружу ошибки провайдеров."""
        context = context or {}
        privacy = bool(context.get("privacy", self.privacy))
        self.last_source = None

        if self.local is not None:
            answer = self._ask_local(text)
            if answer:
                self.last_source = "local"
                log.info("Источник ответа: локальная модель")
                return answer, "local"

        cloud_allowed = (not privacy and bool(self.cfg.get("enable_ai"))
                         and self.cloud is not None and getattr(self.cloud, "available", False))
        if cloud_allowed:
            answer = self._ask_cloud(text)
            if answer:
                self.last_source = "claude"
                log.info("Источник ответа: Claude")
                return answer, "claude"
        elif privacy and self.cloud is not None:
            log.info("Приватный режим: облачный ИИ пропущен")

        if self.offline is not None:
            try:
                answer = self.offline(text)
            except Exception:
                log.exception("Офлайн-заготовка не сработала")
                answer = None
            if answer:
                self.last_source = "offline"
                log.info("Источник ответа: офлайн-заготовка")
                return answer, "offline"

        return None, "none"


def ask_ai(text, context=None):
    """Функциональная обёртка: context = {"cfg", "local", "cloud", "offline"}."""
    context = context or {}
    router = AIRouter(context.get("cfg") or {}, context.get("local"),
                      context.get("cloud"), context.get("offline"))
    return router.ask(text)
