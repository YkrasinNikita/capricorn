"""Кэш озвучки: ключ, LRU-очистка, отключение для длинных текстов и приватного режима."""
import os

import pytest

import voice


def test_cache_key_is_stable_and_sensitive():
    key = voice.tts_cache_key("привет", "svetlana", "edge", "+10%", "+15Hz")
    assert key == voice.tts_cache_key("привет", "svetlana", "edge", "+10%", "+15Hz")
    assert len(key) == 64
    assert key != voice.tts_cache_key("пока", "svetlana", "edge", "+10%", "+15Hz")
    assert key != voice.tts_cache_key("привет", "svetlana", "edge", "+20%", "+15Hz")
    assert key != voice.tts_cache_key("привет", "svetlana", "silero", "+10%", "+15Hz")


def test_cleanup_removes_oldest_until_limit(tmp_path):
    for i, name in enumerate(("a", "b", "c")):
        path = tmp_path / name
        path.write_bytes(b"x" * 100)
        os.utime(path, (1000 + i, 1000 + i))
    left = voice.cleanup_tts_cache(tmp_path, 150)
    assert left <= 150
    assert not (tmp_path / "a").exists()
    assert not (tmp_path / "b").exists()
    assert (tmp_path / "c").exists()


def test_cleanup_missing_folder_is_safe(tmp_path):
    assert voice.cleanup_tts_cache(tmp_path / "nope", 100) == 0


def _speaker(tmp_path, **cfg):
    base = {"tts_engine": "edge", "tts_cache": True, "voice": "svetlana",
            "voice_rate": "+10%", "voice_pitch": "+15Hz"}
    base.update(cfg)
    return voice.Speaker(base, cache_dir=tmp_path, cache_limit=1024)


def test_long_texts_are_not_cached(tmp_path):
    speaker = _speaker(tmp_path)
    assert speaker._cache_allowed("короткая фраза")
    assert not speaker._cache_allowed("я" * (voice.CACHE_MAX_TEXT + 1))


def test_cache_hit_and_drop(tmp_path):
    speaker = _speaker(tmp_path)
    path = tmp_path / (speaker._cache_key("тест") + ".wav")
    path.write_bytes(b"RIFF")
    assert os.path.abspath(speaker._cache_hit("тест")) == os.path.abspath(path)
    speaker._drop_cache("тест")
    assert speaker._cache_hit("тест") is None


def test_privacy_mode_disables_cache_and_edge(tmp_path):
    speaker = _speaker(tmp_path, privacy_mode=True)
    assert not speaker._cache_allowed("фраза")
    with pytest.raises(RuntimeError):
        speaker.render("фраза", tmp_path / "x.wav")
