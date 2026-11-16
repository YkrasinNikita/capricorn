"""Напоминания «Капри»: разбор русского времени, хранение reminders.json
и фоновый поток-планировщик без busy-loop.

Планировщик не знает про GUI: когда напоминание срабатывает, он вызывает
callback(text), а main решает, как это озвучить.
"""
import datetime as dt
import json
import logging
import os
import re
import threading
import uuid

log = logging.getLogger("capri")

DEFAULT_FILE = "reminders.json"
ISO = "%Y-%m-%dT%H:%M:%S"

# единицы «через N …» -> длительность одного шага
_UNITS = (
    (r"секунд\w*|сек\w*", "seconds"),
    (r"минут\w*|мин\b", "minutes"),
    (r"час\w*|ч\b", "hours"),
    (r"день|дня|дней|сут\w*", "days"),
    (r"недел\w*", "weeks"),
)
_UNIT_RE = "|".join(u for u, _ in _UNITS)

_DAILY_RE = re.compile(r"\b(каждый день|ежедневно|каждодневно|по дням)\b")
_PREFIX_RE = re.compile(r"^(?:капри\s+)?напомни(?:\s+мне)?\s*[,:;-]?\s*")
_CONNECTORS_RE = re.compile(r"^(?:что|чтобы)\s+")
_HALF_RE = re.compile(
    r"через\s+(полтора\s+часа|пол\s*часа|полчаса|пол\s*минуты|полминуты|полтора\s+минут\w*|час)\b")
_AFTER_RE = re.compile(rf"через\s+([^,.!?]+?)\s*({_UNIT_RE})")


def _now(now=None):
    return (now or dt.datetime.now()).replace(microsecond=0)


def _cut(text, start, end):
    return (text[:start] + " " + text[end:]).strip()


def _clock_pattern(day=None):
    day_part = rf"(?P<day>{day})\s*" if day else ""
    return (day_part + r"(?:в\s*)?(?P<h>\d{1,2})(?::(?P<m>\d{2})|\s+(?P<m2>\d{2}))?"
            r"(?:\s*(?P<period>утра|дня|вечера|ночи))?(?:\s*час\w*)?")


def _clock(m):
    """Часы/минуты из совпадения и признак «время уже прошло»."""
    hour = int(m.group("h"))
    minute = int(m.group("m") or m.group("m2") or 0)
    period = (m.group("period") or "").lower()
    if period in ("вечера", "дня") and hour < 12:
        hour += 12
    elif period == "ночи" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _add_delta(value, unit):
    return dt.timedelta(**{unit: value})


def _units_value(raw, unit):
    from actions.utils import parse_number  # общий разбор «десять», «двадцать пять»

    n = parse_number(raw)
    if n is None:
        return None
    unit = next((name for pattern, name in _UNITS if re.fullmatch(pattern, unit)), None)
    if unit is None:
        return None
    return _add_delta(n, unit)


def parse_when(text, now=None):
    """Разобрать фразу «напомни …».

    Возвращает (время: datetime, повтор: "none"/"daily", текст напоминания)
    или None, если время не распознано.
    """
    now = _now(now)
    c = re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()
    rest = c
    repeat = "none"

    daily = _DAILY_RE.search(rest)
    if daily:
        repeat = "daily"
        rest = _cut(rest, daily.start(), daily.end())

    # 1. «через N минут/часов/дней»
    half = _HALF_RE.search(rest)
    if half:
        phrase = re.sub(r"\s+", " ", half.group(1))
        if "полтор" in phrase:
            delta = dt.timedelta(hours=1, minutes=30)  # полтора часа
        elif "пол" in phrase and "час" in phrase:
            delta = dt.timedelta(minutes=30)  # полчаса
        elif "час" in phrase:
            delta = dt.timedelta(hours=1)
        elif "минут" in phrase:
            delta = dt.timedelta(minutes=30)
        else:
            delta = dt.timedelta(seconds=30)
        rest = _cut(rest, half.start(), half.end())
        when = now + delta
        head = _PREFIX_RE.sub("", rest)
        tail = re.match(r"\s*(\d+)\s*(минут\w*|сек\w*|час\w*)", head)
        if tail:  # «через час 30 минут»
            extra = _units_value(tail.group(1), tail.group(2))
            if extra:
                when += extra
                rest = head[tail.end():].strip()
        return when, repeat, _clean_text(rest)

    after = _AFTER_RE.search(rest)
    if after:
        delta = _units_value(after.group(1).strip(), after.group(2))
        if delta:
            rest = _cut(rest, after.start(), after.end())
            return now + delta, repeat, _clean_text(rest)

    # 2. «завтра/сегодня в HH:MM» и «в HH:MM»
    m = re.search(_clock_pattern("завтра|сегодня"), rest)
    if not m:
        m = re.search(_clock_pattern(), rest)
    if m:
        day = (m.groupdict().get("day") or "").lower()
        # без «завтра/сегодня» требуем явный маркер времени («в», двоеточие, «часов»),
        # иначе «напомни купить 2 литра» превратится в напоминание на 2 часа ночи
        if not day and not re.search(r"\bв\b|:|\bчас", m.group(0)):
            m = None
    if m:
        clock = _clock(m)
        if clock:
            hour, minute = clock
            when = now.replace(hour=hour, minute=minute, second=0)
            day = (m.groupdict().get("day") or "").lower()
            if day == "завтра":
                when += dt.timedelta(days=1)
            elif day == "сегодня":
                if when <= now:
                    when += dt.timedelta(days=1)
            elif when <= now:
                when += dt.timedelta(days=1)  # «в 18:30» уже прошло — значит, завтра
            rest = _cut(rest, m.start(), m.end())
            return when, repeat, _clean_text(rest)

    return None


def _clean_text(rest):
    """Убрать «напомни», союзы и лишнюю пунктуацию из текста напоминания."""
    rest = re.sub(r"\s+", " ", rest).strip(" ,.!?;:-")
    rest = _PREFIX_RE.sub("", rest)
    rest = _CONNECTORS_RE.sub("", rest)
    return rest.strip(" ,.!?;:-")


def format_when(when, now=None):
    """«сегодня в 18:30», «завтра в 9:05» или «23.11 в 18:30» — для озвучки."""
    now = _now(now)
    if when.date() == now.date():
        return f"сегодня в {when:%H:%M}"
    if when.date() == (now + dt.timedelta(days=1)).date():
        return f"завтра в {when:%H:%M}"
    return f"{when:%d.%m} в {when:%H:%M}"


class Scheduler:
    """Поток, который спит до ближайшего напоминания и вызывает on_due(text)."""

    def __init__(self, path, on_due=None):
        self.path = os.fspath(path)
        self.on_due = on_due
        self.items = []
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._load()

    # ----- хранение -----
    def _load(self):
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception:
            log.exception("reminders.json не прочитался, начинаю с пустого списка")
            try:
                os.replace(self.path, self.path + ".bak")
            except OSError:
                log.exception("Не удалось сохранить копию reminders.json")
            return
        items = data.get("reminders") if isinstance(data, dict) else data
        if not isinstance(items, list):
            log.warning("reminders.json: ожидался список напоминаний")
            return
        clean = []
        for item in items:
            try:
                when = dt.datetime.fromisoformat(item["time"])
                clean.append({
                    "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                    "text": str(item.get("text") or "").strip(),
                    "time": when.replace(microsecond=0).strftime(ISO),
                    "repeat": "daily" if item.get("repeat") == "daily" else "none",
                })
            except Exception:
                log.warning("Пропускаю битое напоминание: %r", item)
        self.items = [x for x in clean if x["text"]]

    def _save(self):
        with self._lock:
            data = {"reminders": list(self.items)}
            tmp = self.path + ".tmp"
            os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    @staticmethod
    def _when(item):
        return dt.datetime.fromisoformat(item["time"])

    # ----- публичный API -----
    def add(self, text, when, repeat="none"):
        """Добавить напоминание. when — datetime (секунды округляются)."""
        text = str(text).strip()
        if not text:
            raise ValueError("пустой текст напоминания")
        when = when.replace(microsecond=0)
        item = {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "time": when.strftime(ISO),
            "repeat": "daily" if repeat == "daily" else "none",
        }
        with self._lock:
            self.items.append(item)
            self._save()
        self._wake.set()
        log.info("Напоминание добавлено: %s (%s)", text, item["time"])
        return item

    def all(self):
        """Копия списка, отсортированная по времени."""
        with self._lock:
            return sorted((dict(x) for x in self.items), key=self._when)

    def cancel(self, query):
        """Удалить по id или по подстроке текста. Возвращает удалённые элементы."""
        q = re.sub(r"\s+", " ", (query or "").lower().replace("ё", "е")).strip()
        if not q:
            return []
        with self._lock:
            gone = []
            for item in list(self.items):
                key = item["text"].lower().replace("ё", "е")
                if item["id"] == q or q in key or key in q:
                    self.items.remove(item)
                    gone.append(dict(item))
            if gone:
                self._save()
        return gone

    def _take_due(self, now=None):
        """Забрать сработавшие напоминания. Ежедневные переносятся на следующий день.
        Возвращает список текстов, которые надо озвучить."""
        now = _now(now)
        due = []
        with self._lock:
            changed = False
            for item in list(self.items):
                if self._when(item) > now:
                    continue
                due.append(item["text"])
                if item["repeat"] == "daily":
                    when = self._when(item)
                    while when <= now:
                        when += dt.timedelta(days=1)
                    item["time"] = when.strftime(ISO)
                    log.info("Ежедневное напоминание перенесено на %s", item["time"])
                else:
                    self.items.remove(item)
                changed = True
            if changed:
                self._save()
        return due

    def _notify(self, texts):
        if not self.on_due:
            return
        for text in texts:
            try:
                self.on_due(text)
            except Exception:
                log.exception("Не удалось озвучить напоминание: %s", text)

    # ----- поток -----
    def start(self):
        """Запустить поток и один раз озвучить всё, что просрочено (например, после перезапуска)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._notify(self._take_due())
        self._thread = threading.Thread(target=self._run, name="capri-reminders", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self):
        while not self._stop.is_set():
            self._notify(self._take_due())
            with self._lock:
                upcoming = [self._when(x) for x in self.items]
            if not upcoming:
                self._wake.wait()
                self._wake.clear()
                continue
            delay = min((w - dt.datetime.now()).total_seconds() for w in upcoming)
            # точность до секунды, но не busy-loop: ждём ровно до ближайшего события
            self._wake.wait(timeout=max(0.2, delay))
            self._wake.clear()
