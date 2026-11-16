"""Напоминания: разбор русского времени, хранение и срабатывание."""
import datetime as dt

import pytest

from scheduler import Scheduler, format_when, parse_when

NOW = dt.datetime(2026, 11, 16, 10, 0, 0)


@pytest.mark.parametrize("phrase, when, repeat, text", [
    ("напомни через 10 минут позвонить маме", dt.datetime(2026, 11, 16, 10, 10), "none", "позвонить маме"),
    ("напомни через два часа выпить воды", dt.datetime(2026, 11, 16, 12, 0), "none", "выпить воды"),
    ("напомни через полчаса чай", dt.datetime(2026, 11, 16, 10, 30), "none", "чай"),
    ("напомни через час 30 минут отдохнуть", dt.datetime(2026, 11, 16, 11, 30), "none", "отдохнуть"),
    ("напомни в 18:30 о встрече", dt.datetime(2026, 11, 16, 18, 30), "none", "о встрече"),
    ("напомни в 18 30 позвонить", dt.datetime(2026, 11, 16, 18, 30), "none", "позвонить"),
    ("напомни завтра в 9:00 зарядка", dt.datetime(2026, 11, 17, 9, 0), "none", "зарядка"),
    ("напомни в 8:00", dt.datetime(2026, 11, 17, 8, 0), "none", ""),
    ("напомни каждый день в 8:00 зарядка", dt.datetime(2026, 11, 17, 8, 0), "daily", "зарядка"),
])
def test_parse_when(phrase, when, repeat, text):
    parsed = parse_when(phrase, NOW)
    assert parsed is not None
    got_when, got_repeat, got_text = parsed
    assert got_when == when
    assert got_repeat == repeat
    assert got_text == text


@pytest.mark.parametrize("phrase", [
    "напомни купить 2 литра",   # число, но это не время
    "напомни что-нибудь",
    "просто фраза",
])
def test_parse_when_rejects(phrase):
    assert parse_when(phrase, NOW) is None


def test_format_when():
    assert format_when(dt.datetime(2026, 11, 16, 18, 30), NOW) == "сегодня в 18:30"
    assert format_when(dt.datetime(2026, 11, 17, 9, 5), NOW) == "завтра в 09:05"
    assert format_when(dt.datetime(2026, 11, 20, 9, 5), NOW) == "20.11 в 09:05"


def test_reminders_survive_restart(tmp_path):
    path = tmp_path / "reminders.json"
    first = Scheduler(path)
    first.add("позвонить маме", NOW + dt.timedelta(minutes=10))
    second = Scheduler(path)
    items = second.all()
    assert len(items) == 1
    assert items[0]["text"] == "позвонить маме"
    assert items[0]["repeat"] == "none"


def test_cancel_by_text_and_id(tmp_path):
    path = tmp_path / "reminders.json"
    sched = Scheduler(path)
    item = sched.add("позвонить маме", NOW + dt.timedelta(minutes=10))
    sched.add("выпить воды", NOW + dt.timedelta(hours=1))
    gone = sched.cancel("позвонить")
    assert [x["id"] for x in gone] == [item["id"]]
    assert len(sched.all()) == 1
    assert sched.cancel("такого нет") == []


def test_overdue_is_announced_and_removed(tmp_path):
    sched = Scheduler(tmp_path / "reminders.json")
    sched.add("старое", NOW - dt.timedelta(minutes=5))
    assert sched._take_due(NOW) == ["старое"]
    assert sched.all() == []
    assert sched._take_due(NOW) == []  # второй раз не повторяем


def test_daily_overdue_moves_to_tomorrow(tmp_path):
    sched = Scheduler(tmp_path / "reminders.json")
    sched.add("зарядка", NOW - dt.timedelta(minutes=5), repeat="daily")
    assert sched._take_due(NOW) == ["зарядка"]
    items = sched.all()
    assert len(items) == 1
    assert dt.datetime.fromisoformat(items[0]["time"]) > NOW


def test_broken_file_does_not_crash(tmp_path):
    path = tmp_path / "reminders.json"
    path.write_text('{"reminders": [', encoding="utf-8")
    sched = Scheduler(path)
    assert sched.all() == []
    assert path.with_name(path.name + ".bak").exists()


def test_thread_stops(tmp_path):
    sched = Scheduler(tmp_path / "reminders.json")
    sched.add("далеко", dt.datetime.now() + dt.timedelta(hours=1))
    sched.start()
    assert sched._thread is not None and sched._thread.is_alive()
    sched.stop()
    assert sched._thread is None
