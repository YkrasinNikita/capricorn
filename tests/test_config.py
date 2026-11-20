"""Версия, миграция и валидация config.json."""
import json

import config_schema
from config_schema import CONFIG_VERSION, load_config_file, merge_defaults, migrate_config, validate_config

DEFAULTS = {
    "enable_ai": False,
    "privacy_mode": False,
    "voice_volume": 100,
    "mic_sensitivity": 100,
    "local_model": "qwen3:8b",
    "wake_words": ["капри", "каприз"],
    "sites": {"ютуб": "https://www.youtube.com"},
    "apps": {"блокнот": "notepad"},
}


def test_migrate_fills_defaults_and_keeps_unknown():
    cfg = migrate_config({"voice_volume": 55, "мой_ключ": {"x": 1}}, DEFAULTS)
    assert cfg["voice_volume"] == 55
    assert cfg["local_model"] == DEFAULTS["local_model"]
    assert cfg["мой_ключ"] == {"x": 1}
    assert cfg["config_version"] == CONFIG_VERSION
    assert cfg["wake_words"] == DEFAULTS["wake_words"]


def test_migrate_removes_null_builtin_entries():
    cfg = migrate_config({"apps": {"блокнот": None, "моё": "calc"}}, DEFAULTS)
    assert "блокнот" not in cfg["apps"]
    assert cfg["apps"]["моё"] == "calc"


def test_merge_is_recursive_but_keeps_unknown_nested_keys():
    merged = merge_defaults({"sites": {"a": "1", "b": "2"}}, {"sites": {"b": "3", "c": "4"}})
    assert merged == {"sites": {"a": "1", "b": "3", "c": "4"}}


def test_validate_clamps_ranges_and_types():
    cfg = {
        "voice_volume": 555,
        "mic_sensitivity": -20,
        "enable_ai": "false",
        "local_model": 5,
        "wake_words": ("капри",),
    }
    fixed, problems = validate_config(cfg, DEFAULTS)
    assert fixed["voice_volume"] == 100
    assert fixed["mic_sensitivity"] == 0
    assert fixed["enable_ai"] is False
    assert fixed["local_model"] == "5"
    assert fixed["wake_words"] == DEFAULTS["wake_words"]
    assert problems  # замечания для лога


def test_validate_accepts_good_values():
    fixed, problems = validate_config({"voice_volume": 42, "enable_ai": True}, DEFAULTS)
    assert fixed["voice_volume"] == 42
    assert fixed["enable_ai"] is True
    assert problems == []


def test_load_missing_file_creates_defaults(config_path):
    cfg, error = load_config_file(config_path, DEFAULTS)
    assert error == ""
    assert cfg["config_version"] == CONFIG_VERSION
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["config_version"] == CONFIG_VERSION
    assert saved["voice_volume"] == 100


def test_load_old_config_without_losses(config_file):
    old_text = config_file.read_text(encoding="utf-8")
    cfg, error = load_config_file(config_file, DEFAULTS)
    assert error == ""
    assert cfg["voice_volume"] == 55
    assert cfg["unknown_key"] == {"kept": True}
    assert cfg["sites"]["мой сайт"] == "https://example.com"
    assert cfg["sites"]["ютуб"] == "https://www.youtube.com"  # недостающее добрано
    assert "блокнот" not in cfg["apps"]  # null убрал встроенную запись
    # миграция сохранила старые данные и дописала версию
    new_text = config_file.read_text(encoding="utf-8")
    assert json.loads(new_text)["config_version"] == CONFIG_VERSION
    assert "мой сайт" in new_text and "unknown_key" in new_text
    assert old_text.replace(" ", "") != new_text  # файл действительно обновлён


def test_load_valid_config_is_not_rewritten(config_path):
    data = {"config_version": CONFIG_VERSION, "voice_volume": 70}
    original = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    config_path.write_text(original, encoding="utf-8")
    cfg, error = load_config_file(config_path, DEFAULTS)
    assert error == ""
    assert cfg["voice_volume"] == 70
    assert config_path.read_text(encoding="utf-8") == original


def test_upgrade_from_v1_adds_new_keys_and_saves(config_path):
    """Новые настройки (T5–T9, T14) появляются в старом config.json после миграции."""
    old = {"config_version": 1, "voice_volume": 55}
    config_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    cfg, error = load_config_file(config_path, DEFAULTS)
    assert error == ""
    assert cfg["voice_volume"] == 55
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["config_version"] == CONFIG_VERSION
    assert saved["privacy_mode"] is False
    assert saved["local_model"] == DEFAULTS["local_model"]


def test_broken_json_does_not_crash_and_makes_backup(config_path):
    broken = '{"voice_volume": 70, "sites": {'
    config_path.write_text(broken, encoding="utf-8")
    cfg, error = load_config_file(config_path, DEFAULTS)
    assert "config.json" in error and "строке" in error
    assert cfg["voice_volume"] == 100  # работаем на значениях по умолчанию
    backup = config_path.with_name(config_path.name + ".bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == broken
    assert config_schema.CONFIG_VERSION == cfg["config_version"]


def test_bool_coercion_variants():
    for raw, expected in (("true", True), ("да", True), ("1", True), ("false", False), ("нет", False), (0, False)):
        fixed, _ = validate_config({"enable_ai": raw}, DEFAULTS)
        assert fixed["enable_ai"] is expected


def test_privacy_mode_is_boolean_and_kept():
    fixed, _ = validate_config({"privacy_mode": "да"}, DEFAULTS)
    assert fixed["privacy_mode"] is True
    fixed, _ = validate_config({"privacy_mode": 0}, DEFAULTS)
    assert fixed["privacy_mode"] is False
