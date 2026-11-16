"""Единая цепочка ИИ: порядок провайдеров, повторы, таймауты и приватный режим."""
from ai_router import AIRouter, ask_ai


class FakeLocal:
    def __init__(self, answer=None, error=False):
        self.answer = answer
        self.error = error
        self.calls = []
        self.last_error = None

    def ask(self, text, timeout=None):
        self.calls.append(timeout)
        if self.error:
            self.last_error = "Ollama недоступна"
            return None
        return self.answer


class FakeCloud:
    available = True

    def __init__(self, answer=None, error=False):
        self.answer = answer
        self.error = error
        self.calls = []

    def ask(self, text, timeout=None):
        self.calls.append(timeout)
        if self.error:
            raise RuntimeError("нет связи")
        return self.answer


def test_local_answer_wins_and_cloud_is_not_called():
    local, cloud = FakeLocal("локальный ответ"), FakeCloud("облачный ответ")
    answer, source = AIRouter({"enable_ai": True}, local, cloud).ask("вопрос")
    assert (answer, source) == ("локальный ответ", "local")
    assert cloud.calls == []


def test_local_failure_falls_back_to_cloud_with_retry():
    local, cloud = FakeLocal(error=True), FakeCloud("облачный ответ")
    answer, source = AIRouter({"enable_ai": True}, local, cloud).ask("вопрос")
    assert (answer, source) == ("облачный ответ", "claude")
    assert local.calls == [20, 20]   # один повтор
    assert cloud.calls == [15]       # таймаут облака


def test_cloud_failure_falls_back_offline():
    local, cloud = FakeLocal(error=True), FakeCloud(error=True)
    offline = lambda text: "заготовка"  # noqa: E731
    answer, source = AIRouter({"enable_ai": True}, local, cloud, offline).ask("вопрос")
    assert (answer, source) == ("заготовка", "offline")
    assert len(cloud.calls) == 2


def test_privacy_mode_never_calls_cloud():
    local, cloud = FakeLocal(error=True), FakeCloud("облачный ответ")
    answer, source = AIRouter({"enable_ai": True, "privacy_mode": True}, local, cloud,
                              lambda text: "офлайн").ask("вопрос")
    assert (answer, source) == ("офлайн", "offline")
    assert cloud.calls == []


def test_cloud_disabled_by_config():
    local, cloud = FakeLocal(error=True), FakeCloud("облачный ответ")
    answer, source = AIRouter({"enable_ai": False}, local, cloud, lambda text: "офлайн").ask("вопрос")
    assert (answer, source) == ("офлайн", "offline")
    assert cloud.calls == []


def test_no_provider_returns_none():
    answer, source = AIRouter({}, FakeLocal(error=True), None, None).ask("вопрос")
    assert (answer, source) == (None, "none")


def test_ask_ai_wrapper():
    answer, source = ask_ai("вопрос", {
        "cfg": {"enable_ai": True},
        "local": FakeLocal("локально"),
        "cloud": FakeCloud("облако"),
    })
    assert (answer, source) == ("локально", "local")
