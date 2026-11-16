"""Единое окно настроек: проверка полей и разбор подсказок Vosk."""
from settings_dialog import check_values, parse_grammar


def valid(**overrides):
    values = {
        "local_ai": True,
        "local_model": "qwen3:8b",
        "enable_ai": False,
        "anthropic_api_key": "",
        "model": "claude-opus-5-5",
        "voice_rate": "+10%",
        "voice_pitch": "+15Hz",
        "chatter_min_minutes": 15,
        "chatter_max_minutes": 40,
        "stt_backend": "vosk",
        "stt_whisper_model": "small",
    }
    values.update(overrides)
    return values


def test_valid_settings_pass():
    assert check_values(valid()) == []


def test_empty_local_model_is_an_error_only_when_enabled():
    assert check_values(valid(local_model="")) != []
    assert check_values(valid(local_ai=False, local_model="")) == []


def test_claude_needs_key_and_model():
    errors = check_values(valid(enable_ai=True))
    assert any("ключ" in e.lower() for e in errors)
    assert check_values(valid(enable_ai=True, anthropic_api_key="sk-test")) == []
    assert check_values(valid(enable_ai=True, anthropic_api_key="sk-test", model=" ")) != []


def test_voice_rate_and_pitch_formats():
    assert check_values(valid(voice_rate="10%")) != []
    assert check_values(valid(voice_rate="+100%")) == []
    assert check_values(valid(voice_pitch="15hz")) != []
    assert check_values(valid(voice_pitch="-10Hz")) == []


def test_chatter_range_order():
    assert check_values(valid(chatter_min_minutes=60, chatter_max_minutes=10)) != []


def test_backend_and_whisper_model_are_checked():
    assert check_values(valid(stt_backend="google")) != []
    assert check_values(valid(stt_backend="whisper", stt_whisper_model="huge")) != []
    assert check_values(valid(stt_backend="whisper", stt_whisper_model="base")) == []


def test_grammar_parsing():
    assert parse_grammar("") == []
    assert parse_grammar(" открой ютуб ; громкость ;; ") == ["открой ютуб", "громкость"]
