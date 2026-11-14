"""Файлы, папки и создание документов."""
import difflib
import os
import re

from persona import line

from .utils import Reply, norm, translit


# ---------------- папки ----------------

SHELL_FOLDERS = {  # имена значений в реестре User Shell Folders (учитывают перенос в OneDrive)
    "desktop": "Desktop", "documents": "Personal", "pictures": "My Pictures", "music": "My Music",
    "videos": "My Video", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}
KNOWN_FOLDERS = [  # (регулярка по названию, ключ папки или shell:-путь, как назвать вслух)
    (r"фото\w*|фотк\w*|изображени\w*|картинк\w*|снимк\w*", "pictures", "Изображения"),
    (r"документ\w*", "documents", "Документы"),
    (r"загрузк\w*|скачанн\w*|закачк\w*", "downloads", "Загрузки"),
    (r"рабоч\w+ стол\w*", "desktop", "Рабочий стол"),
    (r"музык\w*", "music", "Музыка"),
    (r"видео\w*", "videos", "Видео"),
    (r"скриншот\w*", "screenshots", "Скриншоты"),
    (r"(этот|мой) компьютер|компьютер", "shell:MyComputerFolder", "Этот компьютер"),
    (r"корзин\w*", "shell:RecycleBinFolder", "Корзина"),
]
DRIVE_LETTERS = {"ц": "C", "си": "C", "с": "C", "д": "D", "ди": "D", "де": "D", "е": "E", "и": "E",
                 "ф": "F", "эф": "F", "джи": "G", "г": "G", "же": "G"}


def known_dir(key):
    if key == "screenshots":
        return os.path.join(known_dir("pictures"), "Screenshots")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, SHELL_FOLDERS[key])[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), {"documents": "Documents", "pictures": "Pictures",
                                                      "music": "Music", "videos": "Videos",
                                                      "downloads": "Downloads"}.get(key, "Desktop"))


def find_folder(name, search=False):
    """(путь, название) по сказанному имени папки или None. search=True — искать и среди своих папок."""
    name = norm(name)
    drive = re.fullmatch(r"(диск|раздел) (?P<l>\S+)", name)
    if drive:
        letter = DRIVE_LETTERS.get(drive.group("l"), drive.group("l").upper())
        if len(letter) == 1 and os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\", f"диск {letter}"
    for pattern, key, label in KNOWN_FOLDERS:
        if re.fullmatch(pattern, name):
            path = key if key.startswith("shell:") else known_dir(key)
            if key.startswith("shell:") or os.path.isdir(path):
                return path, label
    if not search:
        return None
    return search_folder(name)


def search_folder(name, limit=40000):
    """Ищем папку по имени (с транслитом и нечётким сравнением) в домашних папках, до 4 уровней вглубь."""
    targets = {name, translit(name)}
    roots = [known_dir(k) for k in ("desktop", "documents", "downloads", "pictures", "videos", "music")]
    roots.append(os.path.expanduser("~"))
    best, best_score, seen = None, 0.0, 0
    for root in dict.fromkeys(roots):
        base_depth = root.rstrip("\\").count("\\")
        for cur, dirs, _ in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.lower() not in
                       ("appdata", "node_modules", "__pycache__", "site-packages", "windows")]
            if cur.count("\\") - base_depth >= 4:
                dirs[:] = []
            for d in dirs:
                seen += 1
                dn = norm(d)
                for t in targets:
                    score = 1.0 if dn == t else (0.9 if t in dn.split() or dn.startswith(t)
                                                 else difflib.SequenceMatcher(None, t, dn).ratio())
                    if score > best_score:
                        best, best_score = (os.path.join(cur, d), d), score
            if best_score == 1.0 or seen > limit:
                break
        if best_score == 1.0:
            break
    return best if best_score >= 0.8 else None


class FilesMixin:
    """Создание папок и файлов."""

    def create(self, c, m):
        what = m.group("what")
        place_m = re.search(r"\s(на|в|во)\s+(рабоч\w+ стол\w*|документ\w*|загрузк\w*|фото\w*|изображени\w*|"
                            r"картинк\w*|музык\w*|видео|папк\w* \S+)$", what)
        place, place_name = known_dir("desktop"), "на рабочем столе"
        if place_m:
            spot = find_folder(re.sub(r"^папк\w* ", "", place_m.group(2)), search=True)
            if not spot:
                return Reply(line("folder_missing", t=place_m.group(2)))
            place, place_name = spot[0], f"в папке «{spot[1]}»"
            what = what[:place_m.start()]
        kind = re.match(r"(нов\w+ )?(?P<kind>папк\w*|текстов\w+ файл\w*|текстов\w+ документ\w*|"
                        r"документ\w*|файл\w*|таблиц\w*)( (с )?(именем|названием))?\s*(?P<name>.*)$", what)
        if not kind:
            return None
        k, name = kind.group("kind"), kind.group("name").strip()
        ext = ""
        dot = re.search(r"\s*(точка|dot|\.)\s*(?P<e>[a-zа-я0-9]+)$", name)
        if dot:  # «файл заметки точка txt»
            ext, name = "." + translit(dot.group("e")), name[:dot.start()].strip()
        if k.startswith("папк"):
            default = "Новая папка"
        elif k.startswith("таблиц"):
            default, ext = "Новая таблица", ext or ".xlsx"
        elif k.startswith("документ") and not k.startswith("текст"):
            default, ext = "Новый документ", ext or ".docx"
        else:
            default, ext = "Новый файл", ext or ".txt"
        name = re.sub(r'[<>:"/\\|?*]', "", name).strip() or default
        name = name[:1].upper() + name[1:]
        path = os.path.join(place, name + ext)
        n = 2
        while os.path.exists(path):  # ничего не перезаписываем
            path = os.path.join(place, f"{name} ({n}){ext}")
            n += 1
        if k.startswith("папк"):
            os.makedirs(path)
        else:
            open(path, "x", encoding="utf-8").close()  # пустые .docx/.xlsx Office открывает как новые
        return Reply(line("created", t=os.path.basename(path), where=place_name))
