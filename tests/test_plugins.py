"""Плагины-навыки: автоподключение, отключение через конфиг, изоляция ошибок."""
import types

from actions.plugins import Plugin, apply_plugins, load_plugins, match_plugin, plugin_from_module


def test_plugins_found():
    names = {p.name for p in load_plugins()}
    assert {"time", "date", "wave"} <= names


def test_plugin_files_have_required_parts():
    for plugin in load_plugins():
        assert plugin.name
        assert plugin.patterns
        assert callable(plugin.handle)


def test_disabled_plugin_is_not_loaded():
    names = {p.name for p in load_plugins({"time": False})}
    assert "time" not in names
    assert "date" in names
    assert match_plugin("который час", load_plugins({"time": False})) is None
    assert match_plugin("который час", load_plugins()) == "time"


def test_plugin_match_without_execution():
    """Маршрутизация плагина только по регулярке — обработчик не вызывается."""
    called = []

    def handle(cmd, match):
        called.append(cmd)
        return None

    module = types.SimpleNamespace(NAME="probe", PATTERNS=[r"^проверка$"], handle=handle)
    plugin = plugin_from_module(module)
    assert match_plugin("проверка", [plugin]) == "probe"
    assert called == []


def test_plugin_returns_reply_and_name():
    module = types.SimpleNamespace(NAME="probe", PATTERNS=[r"^проверка$"],
                                   handle=lambda cmd, m: "готово")
    name, reply = apply_plugins([plugin_from_module(module)], "проверка")
    assert (name, reply) == ("probe", "готово")


def test_broken_plugin_is_isolated(caplog):
    def boom(cmd, match):
        raise RuntimeError("плагин сломался")

    module = types.SimpleNamespace(NAME="boom", PATTERNS=[r"^бум$"], handle=boom)
    good = types.SimpleNamespace(NAME="good", PATTERNS=[r"^бум$"], handle=lambda cmd, m: "ок")
    result = apply_plugins([plugin_from_module(module), plugin_from_module(good)], "бум")
    assert result == ("good", "ок")  # упавший навык не мешает следующему
    assert "boom" in caplog.text


def test_invalid_modules_are_skipped():
    assert plugin_from_module(types.SimpleNamespace()) is None
    assert plugin_from_module(types.SimpleNamespace(NAME="", PATTERNS=["x"], handle=lambda c, m: None)) is None
    assert plugin_from_module(types.SimpleNamespace(NAME="x", PATTERNS=["("], handle=lambda c, m: None)) is None
    assert plugin_from_module(types.SimpleNamespace(NAME="x", PATTERNS=[], handle=lambda c, m: None)) is None
    assert plugin_from_module(types.SimpleNamespace(NAME="x", PATTERNS=["y"], handle="нет")) is None


def test_actions_runs_plugin_and_records_intent(actions):
    reply = actions.handle("который час")
    assert reply is not None and reply.text
    assert actions.last_intent == "time"


def test_actions_skips_disabled_plugin():
    from actions import Actions
    local = Actions({"sites": {}, "apps": {}, "app_aliases": {}, "replies": {}, "plugins": {"time": False}},
                    ".", None, build_index=False)
    assert local.handle("который час") is None
    assert local.last_intent is None
