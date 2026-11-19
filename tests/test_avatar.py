"""Эмоции и синхронизация рта: цвета эмоций, длина озвучки и поле emotion у ответа."""
import wave

from actions import Reply
from avatar import EMOTION_COLORS, normalize_emotion
from voice import audio_duration, estimate_speech_seconds


def test_reply_emotion_defaults_to_neutral():
    assert Reply("привет").emotion == "neutral"


def test_reply_emotion_can_be_set():
    assert Reply("ой", None, "error").emotion == "error"


def test_normalize_emotion_aliases():
    assert normalize_emotion("радость") == "joy"
    assert normalize_emotion("ошибка") == "error"
    assert normalize_emotion("JOY") == "joy"
    assert normalize_emotion("что-то странное") == "neutral"
    assert normalize_emotion(None) == "neutral"


def test_all_emotions_have_colors():
    for name in ("neutral", "joy", "error", "thinking"):
        assert name in EMOTION_COLORS


def test_estimate_speech_seconds_grows_with_text():
    short = estimate_speech_seconds("привет")
    long = estimate_speech_seconds("одна две три четыре пять шесть семь восемь девять десять")
    assert 0.6 <= short < long


def test_audio_duration_reads_wav(tmp_path):
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 4000)  # 0.5 секунды
    assert abs(audio_duration(path) - 0.5) < 0.01


def test_audio_duration_bad_file_is_zero(tmp_path):
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"not audio")
    assert audio_duration(path) == 0.0
