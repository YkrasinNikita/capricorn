"""Замер качества распознавания речи: один и тот же набор фраз до и после изменений.

    python tools/stt_eval.py --record              # записать 25 фраз с микрофона
    python tools/stt_eval.py --backend vosk        # замер Vosk
    python tools/stt_eval.py --backend whisper     # замер faster-whisper (если установлен)

Записи и расшифровки лежат в samples/stt (папка в .gitignore — там твой голос).
Сравнение — по словам: доля ошибок (WER) и точность 1 − WER. Фразы короткие, поэтому
удобно сравнивать «до/после» на одном и том же наборе.
"""
import argparse
import json
import os
import sys
import wave

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

SAMPLES = os.path.join(BASE, "samples", "stt")
MANIFEST = os.path.join(SAMPLES, "manifest.json")
MODEL = os.path.join(BASE, "models", "vosk-model-small-ru-0.22")
RATE = 16000

# набор фраз — реальные команды Капри разной длины, без редких слов
PHRASES = [
    "капри открой ютуб",
    "капри который час",
    "капри какая сегодня дата",
    "капри сделай громче",
    "капри выключи звук",
    "капри поставь на паузу",
    "капри следующий трек",
    "капри что сейчас играет",
    "капри найди в интернете погода",
    "капри открой телеграм мама",
    "капри напиши привет",
    "капри закрой хром",
    "капри сверни все окна",
    "капри сделай скриншот",
    "капри проверь компьютер",
    "капри напомни через десять минут позвонить маме",
    "капри какие напоминания",
    "капри запомни что кот спит на клавиатуре",
    "капри что ты знаешь о встрече",
    "капри покажи заметки",
    "капри помаши ручкой",
    "капри погуляй",
    "капри переместись в левый нижний угол",
    "капри заблокируй компьютер",
    "капри спрячься",
]


def normalize(text):
    """Сравнивать без регистра и знаков препинания."""
    text = (text or "").lower().replace("ё", "е")
    for ch in ".,!?…;:—–-\"'«»()[]":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def words(text):
    return normalize(text).split()


def word_error_rate(reference, hypothesis):
    """Доля ошибок по словам: 0 — всё верно, 1 — ни одного слова не совпало."""
    ref, hyp = words(reference), words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def accuracy(reference, hypothesis):
    return max(0.0, 1.0 - word_error_rate(reference, hypothesis))


def exact(reference, hypothesis):
    return normalize(reference) == normalize(hypothesis)


# ----- запись -----
def _record_phrase(seconds=8.0, silence=1.2, threshold=0.02):
    """Ждём речь, пишем до паузы. Возвращает bytes int16 или None."""
    import numpy as np
    import sounddevice as sd
    block = 4000
    chunks, voiced, quiet = [], 0.0, 0.0
    with sd.RawInputStream(samplerate=RATE, blocksize=block, dtype="int16", channels=1) as stream:
        for _ in range(int(seconds * RATE / block)):
            data, _ = stream.read(block)
            data = bytes(data)
            x = np.frombuffer(data, np.int16).astype(np.float32)
            loud = len(x) and float(np.sqrt(np.mean(x * x))) / 32768 >= threshold
            if loud:
                voiced += block / RATE
                quiet = 0.0
            elif voiced:
                quiet += block / RATE
            if voiced:
                chunks.append(data)
            if voiced >= 0.3 and quiet >= silence:
                break
    return b"".join(chunks) if voiced >= 0.3 else None


def record_all():
    os.makedirs(SAMPLES, exist_ok=True)
    entries = []
    print("Буду показывать фразу — прочитай её вслух после Enter. Для выхода: пустой Enter.\n")
    for i, phrase in enumerate(PHRASES, 1):
        print(f"[{i}/{len(PHRASES)}] {phrase}")
        if input("  Enter — говорить, пусто — закончить: ").strip().lower() in ("", "q", "exit", "выход"):
            break
        try:
            audio = _record_phrase()
        except Exception as e:
            print(f"  микрофон не сработал: {e}")
            return 1
        if not audio:
            print("  речи не услышала, пропускаю")
            continue
        name = f"{i:02d}.wav"
        with wave.open(os.path.join(SAMPLES, name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(audio)
        entries.append({"file": name, "text": phrase})
        print(f"  записано: samples/stt/{name}")
    manifest = {"phrases": entries}
    if os.path.exists(MANIFEST):  # дописываем к прошлым записям
        try:
            with open(MANIFEST, encoding="utf-8") as f:
                old = json.load(f).get("phrases", [])
            known = {e["file"] for e in entries}
            manifest["phrases"] = [e for e in old if e["file"] not in known] + entries
        except (OSError, ValueError):
            pass
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\nГотово: {len(entries)} записей в samples/stt")
    return 0


# ----- замер -----
def read_wav(path):
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


def make_backend(kind):
    from voice import backend_kind, make_backend as factory
    kind = backend_kind(kind)
    if kind == "whisper":
        return factory("whisper", MODEL, whisper={"model": "small", "compute_type": "int8"})
    if not os.path.isdir(MODEL):
        raise SystemExit(f"Нет модели Vosk: {MODEL}. Запусти setup.bat.")
    return factory("vosk", MODEL)


def run(kind):
    if not os.path.isfile(MANIFEST):
        print("Записей пока нет. Сначала: python tools/stt_eval.py --record")
        return 0
    with open(MANIFEST, encoding="utf-8") as f:
        entries = json.load(f).get("phrases", [])
    if not entries:
        print("В samples/stt/manifest.json нет фраз.")
        return 0
    backend = make_backend(kind)
    exact_count, acc_sum = 0, 0.0
    print(f"Движок: {kind}. Фраз: {len(entries)}\n")
    for entry in entries:
        path = os.path.join(SAMPLES, entry["file"])
        if not os.path.isfile(path):
            print(f"  пропуск {entry['file']} — файла нет")
            continue
        heard = backend.transcribe(read_wav(path))
        acc = accuracy(entry["text"], heard)
        exact_count += int(exact(entry["text"], heard))
        acc_sum += acc
        mark = "ok  " if exact(entry["text"], heard) else "mism"
        print(f"{mark} [{acc * 100:3.0f}%] {heard or '—'}")
    n = len(entries)
    print(f"\nТочно распознано: {exact_count}/{n} ({exact_count / n * 100:.0f}%), "
          f"средняя точность по словам: {acc_sum / n * 100:.0f}%")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Замер качества распознавания речи Капри")
    parser.add_argument("--record", action="store_true", help="записать фразы с микрофона")
    parser.add_argument("--backend", default="vosk", choices=["vosk", "whisper"], help="какой движок проверять")
    args = parser.parse_args(argv)
    return record_all() if args.record else run(args.backend)


if __name__ == "__main__":
    sys.exit(main())
