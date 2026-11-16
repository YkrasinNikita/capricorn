"""Установка офлайн-модели распознавания речи Vosk.

Один и тот же код ставщика модели в обычном запуске (setup.bat) и в собранной версии
без Python: сначала берём архив из папки bundled, если его нет — скачиваем с сайта Vosk.
Скачивание идёт только по явному согласию пользователя (окно первого запуска / setup.bat).
"""
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile

NAME = "vosk-model-small-ru-0.22"
URL = f"https://alphacephei.com/vosk/models/{NAME}.zip"


def model_dir(base, name=NAME):
    return os.path.join(base, "models", name)


def installed(base, name=NAME):
    """Модель на месте (в папке есть am/final.mdl)."""
    return os.path.isfile(os.path.join(model_dir(base, name), "am", "final.mdl"))


def bundle_roots(base):
    """Где искать архив модели: рядом с программой и внутри собранного приложения."""
    roots = [os.path.join(base, "bundled")]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        roots.append(os.path.join(meipass, "bundled"))
    return [path for path in roots if os.path.isdir(path)]


def bundled_archive(base):
    """Путь к zip с моделью в bundled или None."""
    for root in bundle_roots(base):
        exact = os.path.join(root, NAME + ".zip")
        if os.path.isfile(exact):
            return exact
        try:
            for name in sorted(os.listdir(root)):
                if name.lower().endswith(".zip"):
                    return os.path.join(root, name)
        except OSError:
            continue
    return None


def download(archive, url=URL, timeout=60, log=print):
    """Скачать архив модели. Прерванная загрузка — ошибка, а не половина файла."""
    with urllib.request.urlopen(url, timeout=timeout) as response, open(archive, "wb") as f:
        total = int(response.headers.get("Content-Length") or 0)
        step = max(total // 10, 1) if total else 4 * 2**20  # прогресс примерно раз в 10%
        next_report = step
        done = 0
        while chunk := response.read(1 << 16):
            f.write(chunk)
            done += len(chunk)
            if total and done >= next_report:
                log(f"  {done / 2**20:.0f} / {total / 2**20:.0f} МБ")
                while done >= next_report:
                    next_report += step
    if total and done < total:
        raise IOError("загрузка оборвалась")


def _find_model(root):
    """Папка модели внутри распакованного архива."""
    if os.path.isfile(os.path.join(root, "am", "final.mdl")):
        return root
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "am", "final.mdl")):
            return path
    return None


def unpack(archive, models, name=NAME, log=print):
    """Распаковать архив во временную папку и перенести модель в models/<name>."""
    target = os.path.join(models, name)
    tmp = tempfile.mkdtemp(prefix="capri_model_")
    try:
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
        found = _find_model(tmp)
        if not found:
            raise IOError("в архиве нет модели распознавания")
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(found, target)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    log(f"[Capri] модель распакована: {target}")


def install_model(base, log=print, retries=3):
    """Поставить модель и вернуть путь к ней. Ошибки — с понятным текстом для человека."""
    if installed(base):
        return model_dir(base)
    models = os.path.join(base, "models")
    os.makedirs(models, exist_ok=True)
    archive = bundled_archive(base)
    if archive:
        log("[Capri] распаковываю модель из bundled…")
        try:
            unpack(archive, models, log=log)
            if installed(base):
                return model_dir(base)
        except (OSError, zipfile.BadZipFile) as e:
            log(f"[Capri] архив из bundled не подошёл ({e}), попробую скачать")
    tmp = os.path.join(models, "model.zip")
    last = None
    for attempt in range(1, retries + 1):
        try:
            log(f"[Capri] скачиваю модель распознавания речи (~45 МБ), попытка {attempt} из {retries}…")
            download(tmp, log=log)
            unpack(tmp, models, log=log)
            break
        except Exception as e:  # сеть, архив, диск — всё это не должно ронять приложение
            last = e
            log(f"[Capri] не получилось: {e}")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    if not installed(base):
        raise RuntimeError(f"не удалось скачать модель: {last}. Проверь интернет и запусти setup.bat ещё раз")
    return model_dir(base)
