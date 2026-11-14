"""Общие утилиты команд: Reply, нормализация текста, числа, PowerShell."""
import json
import logging
import re
import subprocess
from dataclasses import dataclass

log = logging.getLogger("capri")
NO_WINDOW = 0x08000000


@dataclass
class Reply:
    text: str
    action: str | None = None    # "hide" / "quit"


def norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s\.\-:/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


TR = dict(zip("абвгдезийклмнопрстуфхцы", "abvgdeziyklmnoprstufhcy"))
TR.update({"ё": "e", "ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ь": "", "э": "e",
           "ю": "yu", "я": "ya"})


def translit(s):
    return "".join(TR.get(c, c) for c in s)


NUM = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
       "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
       "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
       "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
       "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
       "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}


def parse_number(text):
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total = None
    for w in text.split():
        if w in NUM:
            total = (total or 0) + NUM[w]
        elif total is not None:
            break
    if total is not None:
        return total
    if re.search(r"максимум|на полную|на всю", text):
        return 100
    if re.search(r"половин", text):
        return 50
    if re.search(r"минимум", text):
        return 0
    return None


def run_ps(cmd, timeout=60):
    full = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "$ErrorActionPreference='SilentlyContinue'; " + cmd)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.decode("utf-8", "replace").strip()


def ps_json(cmd):
    out = run_ps(cmd + " | ConvertTo-Json -Depth 3 -Compress")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]
