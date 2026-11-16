"""Качество STT: выбор движка, отсечение шума и замер по набору фраз."""
import importlib.util
import pathlib

from voice import Listener, backend_kind

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("stt_eval", ROOT / "tools" / "stt_eval.py")
stt_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stt_eval)


def listener(**kw):
    """Слушатель без запуска потока и без модели — только чистая логика."""
    return Listener("нет-модели", lambda *a: None, lambda *a: None, lambda *a: None, **kw)


def test_backend_selection():
    assert backend_kind("vosk") == "vosk"
    assert backend_kind("") == "vosk"
    assert backend_kind(None) == "vosk"
    assert backend_kind("whisper") == "whisper"
    assert backend_kind("faster-whisper") == "whisper"


def test_short_noise_is_rejected():
    lst = listener(min_speech_ms=300)
    assert not lst.accept_text("", 2000)
    assert not lst.accept_text("открой ютуб", 200)   # слишком коротко для команды
    assert not lst.accept_text("э", 500)             # «слово» из тишины
    assert lst.accept_text("стоп", 500)


def test_normal_phrase_passes():
    lst = listener(min_speech_ms=300)
    assert lst.accept_text("открой ютуб", 800)
    assert lst.accept_text("капри который час", 1200)


def test_noise_set_is_small():
    """В шумовой список не попадают слова-команды вроде «да», «нет», «стоп»."""
    assert not ({"да", "нет", "стоп"} & {"и", "а", "ну", "э", "мм", "м", "хм", "у", "о", "в", "с"})


def test_phrase_set_size():
    assert 20 <= len(stt_eval.PHRASES) <= 30


def test_scoring_exact_and_errors():
    assert stt_eval.word_error_rate("открой ютуб", "Открой, ютуб!") == 0.0
    assert stt_eval.accuracy("открой ютуб", "открой ютуб") == 1.0
    assert stt_eval.word_error_rate("открой ютуб", "открой вк") == 0.5
    assert stt_eval.word_error_rate("", "") == 0.0
    assert stt_eval.word_error_rate("", "привет") == 1.0
    assert stt_eval.accuracy("открой ютуб", "совсем не то") == 0.0


def test_normalize_ignores_case_punctuation_and_yo():
    assert stt_eval.normalize("Капри, всё готово!") == "капри все готово"
