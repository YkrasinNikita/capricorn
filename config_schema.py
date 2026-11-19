"""Версия, миграция и валидация config.json.

Правила:
- недостающие ключи добираются из DEFAULTS (рекурсивно по секциям);
- неизвестные ключи сохраняются как есть;
- типы и диапазоны проверяются, явно неверные значения заменяются разумными;
- битый JSON не роняет приложение: файл копируется в config.json.bak,
  приложение стартует на значениях по умолчанию.
"""
import copy
import json
import logging
import os
import shutil

log = logging.getLogger("capri")

CONFIG_VERSION = 1

# допустимые диапазоны числовых настроек: key -> (min, max)
RANGES = {
    "voice_volume": (0, 100),
    "mic_sensitivity": (0, 100),
    "mic_gain": (0.0, 5.0),
    "voice_fx": (0.0, 10.0),
    "voice_shift": (-12.0, 12.0),
    "chatter_min_minutes": (0, 1440),
    "chatter_max_minutes": (0, 1440),
    "roam_chance": (0.0, 1.0),
    "listen_window_seconds": (1, 120),
    "followup_seconds": (0, 120),
    "hide_after_seconds": (0, 600),
    "stt_min_speech_ms": (0, 5000),
    "rag_top_k": (1, 10),
}


def _bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "да", "вкл", "включено", "on"):
            return True
        if v in ("0", "false", "no", "нет", "выкл", "отключено", "off", ""):
            return False
    return default


def _number(value, default, key, kind):
    try:
        v = kind(value)
    except (TypeError, ValueError):
        return default
    lo, hi = RANGES.get(key, (None, None))
    if lo is not None:
        v = max(lo, min(hi, v))
    return v


def _coerce(key, value, default):
    """Привести значение к типу значения по умолчанию; вернуть default, если не получилось."""
    if default is None:
        return value if value is None or isinstance(value, str) else str(value)
    if isinstance(default, bool):
        return _bool(value, default)
    if isinstance(default, int):
        if isinstance(value, str) and not value.strip().lstrip("+-").isdigit():
            return default
        return _number(value, default, key, int)
    if isinstance(default, float):
        return _number(value, default, key, float)
    if isinstance(default, str):
        return value if isinstance(value, str) else str(value)
    if isinstance(default, list):
        if not isinstance(value, list):
            return copy.deepcopy(default)
        return [str(v) if not isinstance(v, str) else v for v in value]
    if isinstance(default, dict):
        return value if isinstance(value, dict) else copy.deepcopy(default)
    return value


def merge_defaults(defaults, user):
    """DEFAULTS + сохранённые настройки. null в словаре — убранная встроенная запись."""
    out = copy.deepcopy(defaults)
    for key, value in (user or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            for name, item in value.items():
                if item is None:
                    out[key].pop(name, None)
                elif isinstance(item, dict) and isinstance(out[key].get(name), dict):
                    out[key][name] = merge_defaults(out[key][name], item)
                else:
                    out[key][name] = item
        else:
            out[key] = value
    return out


def validate_config(cfg, defaults):
    """Проверить типы и диапазоны. Возвращает (исправленный cfg, список замечаний)."""
    out = dict(cfg)
    problems = []
    for key, default in defaults.items():
        if key not in out:
            out[key] = copy.deepcopy(default)
            continue
        fixed = _coerce(key, out[key], default)
        if fixed != out[key]:
            problems.append(f"{key}: {out[key]!r} → {fixed!r}")
        out[key] = fixed
    return out, problems


# пошаговые миграции: версия, которую получаем после применения шага
MIGRATIONS = {}


def migrate_config(cfg, defaults):
    """Довести старый config.json до текущей версии, не теряя пользовательских ключей."""
    merged = merge_defaults(defaults, cfg if isinstance(cfg, dict) else {})
    version = merged.get("config_version")
    if not isinstance(version, int) or version < 0:
        version = 0
    while version < CONFIG_VERSION:
        step = MIGRATIONS.get(version + 1)
        if step:
            merged = step(merged)
        version += 1
    merged["config_version"] = CONFIG_VERSION
    merged, problems = validate_config(merged, defaults)
    if problems:
        log.warning("config.json: исправлены значения — %s", "; ".join(problems))
    return merged


def config_blank(path):
    """config.json нет или он пустой (всё стёрли) — такой можно спокойно создать заново."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return not f.read().strip()
    except FileNotFoundError:
        return True


def save_config_file(path, cfg):
    """Записать конфиг целиком."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def backup_config(path):
    """Копия битого конфига рядом: config.json.bak. Возвращает путь или None."""
    path = os.fspath(path)
    dst = path + ".bak"
    try:
        shutil.copyfile(path, dst)
        return dst
    except OSError:
        log.exception("Не удалось сохранить копию config.json")
        return None


def load_config_file(path, defaults):
    """Прочитать config.json: (cfg, текст ошибки для пользователя или "")."""
    if config_blank(path):  # файла нет или его содержимое стёрли — создаём заново
        log.info("config.json пустой или удалён, создаю заново")
        cfg = migrate_config({}, defaults)
        save_config_file(path, cfg)
        return cfg, ""
    try:
        with open(path, encoding="utf-8-sig") as f:  # Блокнот иногда сохраняет с BOM
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("в корне config.json должен быть объект {…}")
    except json.JSONDecodeError as e:
        log.error("config.json с ошибкой (строка %d, символ %d: %s), использую настройки по умолчанию",
                  e.lineno, e.colno, e.msg)
        bak = backup_config(path)
        tail = f" Старый файл сохранён как {os.path.basename(bak)}." if bak else ""
        return migrate_config({}, defaults), (
            f"В config.json ошибка в строке {e.lineno}, я его не прочитала и работаю "
            "со стандартными настройками. Проверь там кавычки, запятые и двойные слеши в путях." + tail)
    except Exception as e:
        log.exception("config.json не прочитался, использую настройки по умолчанию")
        bak = backup_config(path)
        tail = f" Старый файл сохранён как {os.path.basename(bak)}." if bak else ""
        return migrate_config({}, defaults), f"Не смогла прочитать config.json ({e}), работаю со стандартными настройками." + tail

    cfg = migrate_config(user, defaults)
    if user.get("config_version") != CONFIG_VERSION:
        # дописываем новые ключи и версию, чтобы файл соответствовал текущей схеме
        try:
            save_config_file(path, cfg)
            log.info("config.json обновлён до версии %d", CONFIG_VERSION)
        except OSError:
            log.exception("Не удалось записать обновлённый config.json")
    return cfg, ""
