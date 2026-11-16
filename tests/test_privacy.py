"""Режим приватности: офлайн-флаги и запрет сетевых STT/поиска."""
from actions.fallback import FallbackMixin
from main import DEFAULTS, google_stt_allowed


class DummyFallback(FallbackMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.opened = []

    def web(self, url):
        self.opened.append(url)


def test_privacy_default_is_off():
    assert DEFAULTS["privacy_mode"] is False


def test_google_stt_is_blocked_in_privacy_mode():
    assert google_stt_allowed({"use_google_stt": True}) is True
    assert google_stt_allowed({"use_google_stt": True, "privacy_mode": True}) is False
    assert google_stt_allowed({"use_google_stt": False}) is False
    assert google_stt_allowed({"privacy_mode": True}) is False


def test_fallback_does_not_search_without_permission():
    dummy = DummyFallback({"search_when_unsure": True})
    reply = dummy.fallback("что такое кот", allow_search=False)
    assert reply.text
    assert dummy.opened == []


def test_fallback_searches_when_allowed():
    dummy = DummyFallback({"search_when_unsure": True})
    dummy.fallback("что такое кот")
    assert len(dummy.opened) == 1
