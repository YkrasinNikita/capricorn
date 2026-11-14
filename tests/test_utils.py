"""Чистые функции: нормализация, транслит, числа, клавиши, падежи."""
import pytest

from actions import norm, parse_number, translit
from actions.keys import parse_keys, split_glued
from actions.web import stem_name


@pytest.mark.parametrize("raw, expected", [
    ("Ёлка, ПРИВЕТ!", "елка привет"),
    ("  Много   пробелов  ", "много пробелов"),
    ("Открой ютуб.", "открой ютуб."),
    ("сайт: ya.ru/почта", "сайт: ya.ru/почта"),
    ("вин+таб", "вин таб"),
])
def test_norm(raw, expected):
    assert norm(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("привет", "privet"),
    ("ёж", "ezh"),
    ("юля", "yulya"),
    ("кот", "kot"),
])
def test_translit(raw, expected):
    assert translit(raw) == expected


@pytest.mark.parametrize("text, expected", [
    ("громкость 50", 50),
    ("сто двадцать", 120),
    ("двадцать пять", 25),
    ("на максимум", 100),
    ("сделай половину", 50),
    ("минимум", 0),
    ("без числа", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("word, expected", [
    ("wintab", ["win", "tab"]),
    ("альттаб", ["alt", "tab"]),
    ("ctrlshiftesc", ["ctrl", "shift", "esc"]),
    ("abc", None),
])
def test_split_glued(word, expected):
    assert split_glued(word) == expected


@pytest.mark.parametrize("phrase, expected_keys", [
    ("вин таб", ["win", "tab"]),
    ("ctrl shift esc", ["ctrl", "shift", "esc"]),
    ("эф пять", ["f5"]),
    ("контрол цэ", ["ctrl", "c"]),
    ("пробел", ["space"]),
])
def test_parse_keys(phrase, expected_keys):
    keys, unknown = parse_keys(phrase)
    assert unknown is None
    assert keys == expected_keys


def test_parse_keys_unknown():
    keys, unknown = parse_keys("xyzzy")
    assert keys == []
    assert unknown == "xyzzy"


@pytest.mark.parametrize("name, expected", [
    ("шахтеров", "шахтер"),
    ("с мамой", "с мам"),
    ("Мама", "Мама"),
])
def test_stem_name(name, expected):
    assert stem_name(name) == expected
