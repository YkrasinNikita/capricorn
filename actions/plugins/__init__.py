"""Плагины-навыки: новый навык — один файл в этой папке, ядро править не нужно.

Модуль плагина объявляет три вещи:

    NAME = "time"                    # имя навыка (пишется в лог как интент)
    PATTERNS = [r"который час"]      # регулярки, по которым навык вызывается
    handle(cmd, match) -> Reply | None

Автоподключение: все *.py из папки, кроме файлов, начинающихся с «_».
Включение и отключение — раздел "plugins" в config.json: {"clock": false}.
Ошибка плагина логируется и не мешает остальным навыкам.
"""
import importlib
import logging
import pkgutil
import re

from ..utils import Reply

log = logging.getLogger("capri")

REQUIRED = ("NAME", "PATTERNS", "handle")
PACKAGE = __name__


class Plugin:
    """Загруженный навык: имя, регулярки и обработчик."""

    def __init__(self, module):
        self.name = module.NAME
        self.patterns = tuple(module.PATTERNS)
        self.handle = module.handle

    def match(self, cmd):
        """Первый подошедший match или None."""
        for pattern in self.patterns:
            m = re.search(pattern, cmd)
            if m:
                return m
        return None

    def __repr__(self):
        return f"<Plugin {self.name}>"


def plugin_from_module(module):
    """Проверить модуль и превратить его в Plugin. None — если это не плагин."""
    label = getattr(module, "__name__", None) or getattr(module, "NAME", None) or "?"
    missing = [name for name in REQUIRED if not hasattr(module, name)]
    if missing:
        log.warning("Плагин %s пропущен: нет %s", label, ", ".join(missing))
        return None
    if not isinstance(module.NAME, str) or not module.NAME.strip():
        log.warning("Плагин %s пропущен: NAME должен быть непустой строкой", label)
        return None
    patterns = module.PATTERNS
    if isinstance(patterns, str):
        patterns = [patterns]
    if not patterns or not all(isinstance(p, str) and p.strip() for p in patterns):
        log.warning("Плагин %s пропущен: PATTERNS должен быть списком регулярок", label)
        return None
    if not callable(module.handle):
        log.warning("Плагин %s пропущен: handle не вызывается", label)
        return None
    for pattern in patterns:  # кривую регулярку ловим при загрузке, а не в момент команды
        try:
            re.compile(pattern)
        except re.error as e:
            log.warning("Плагин %s пропущен: ошибка в регулярке %r (%s)", label, pattern, e)
            return None
    module.PATTERNS = list(patterns)
    return Plugin(module)


def load_plugins(enabled=None):
    """Загрузить все плагины из папки. enabled — словарь "plugins" из config.json.
    Ошибка одного файла не мешает остальным."""
    enabled = enabled if isinstance(enabled, dict) else {}
    found = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        if enabled.get(info.name) is False:
            log.info("Плагин %s отключён в настройках", info.name)
            continue
        try:
            module = importlib.import_module(f"{PACKAGE}.{info.name}")
        except Exception:
            log.exception("Плагин %s не загрузился", info.name)
            continue
        plugin = plugin_from_module(module)
        if plugin is None:
            continue
        if enabled.get(plugin.name) is False:
            log.info("Плагин %s отключён в настройках", plugin.name)
            continue
        found.append(plugin)
    if found:
        log.info("Плагины подключены: %s", ", ".join(p.name for p in found))
    return found


def match_plugin(cmd, plugins=None):
    """Имя плагина, чья регулярка подошла (без выполнения). None — ни один не подошёл."""
    for plugin in plugins if plugins is not None else load_plugins():
        if plugin.match(cmd):
            return plugin.name
    return None


def apply_plugins(plugins, cmd):
    """Прогнать команду по плагинам. Возвращает (имя, Reply) или None.
    Ошибка плагина логируется, остальные навыки продолжают работать."""
    for plugin in plugins:
        m = plugin.match(cmd)
        if not m:
            continue
        try:
            reply = plugin.handle(cmd, m)
        except Exception:
            log.exception("Плагин %s упал на команде «%s»", plugin.name, cmd)
            continue
        if reply is not None:
            return plugin.name, reply
    return None
