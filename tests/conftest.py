"""Общие фикстуры тестов: временный config.json и конфиг без фоновых задач."""
import json

import pytest


@pytest.fixture
def config_path(tmp_path):
    """Путь к временному config.json, которого ещё нет."""
    return tmp_path / "config.json"


@pytest.fixture
def config_file(config_path):
    """Временный config.json со «старыми» настройками пользователя."""
    config_path.write_text(json.dumps({
        "voice_volume": 55,
        "local_model": "qwen3:8b",
        "unknown_key": {"kept": True},
        "sites": {"мой сайт": "https://example.com"},
        "apps": {"блокнот": None},  # null — встроенная запись убрана
    }, ensure_ascii=False), encoding="utf-8")
    return config_path


@pytest.fixture
def actions():
    """Actions без фонового сканирования приложений и без Win32-команд."""
    from actions import Actions
    return Actions({"sites": {}, "apps": {}, "app_aliases": {}, "replies": {}}, ".", None, build_index=False)
