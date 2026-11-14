"""Приложения: поиск установленных программ, запуск, закрытие процессов, чаты Telegram."""
import ctypes
import difflib
import os
import re
import subprocess
import threading
import time
import urllib.parse

import automation
import psutil
from persona import line

from .files import find_folder
from .utils import NO_WINDOW, Reply, log, norm, ps_json, translit
from .web import stem_name

PROTECTED_PROCS = {"explorer", "python", "pythonw", "svchost", "csrss", "winlogon", "dwm", "lsass", "services",
                   "system", "smss", "wininit", "fontdrvhost", "sihost", "taskhostw", "ctfmon", "runtimebroker",
                   "searchhost", "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "ollama",
                   "ollama app", "conhost", "audiodg"}
SKIP_WORDS = ("uninstall", "удален", "удалить", "деинсталл", "readme", "help", "справка", "documentation",
              "license", "support", "поддержк", "feedback", "release notes", "website")


def installed_programs():
    """[(название, путь к exe)] из «Программ и компонентов» Windows: значок программы обычно и есть её exe."""
    import winreg
    out = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
            except OSError:
                continue
            values = {}
            for field in ("DisplayIcon", "InstallLocation", "UninstallString"):
                try:
                    values[field] = str(winreg.QueryValueEx(sub, field)[0])
                except OSError:
                    values[field] = ""
            exe = re.sub(r",\s*-?\d+$", "", values["DisplayIcon"]).strip().strip('"')
            base = os.path.basename(exe).lower()
            if exe.lower().endswith(".exe") and "unins" not in base and "setup" not in base and os.path.isfile(exe):
                out.append((norm(name), exe))
                continue
            # значок — деинсталлятор (так у Steam): ищем рядом exe с названием программы, steam → steam.exe
            folder = values["InstallLocation"].strip().strip('"')
            if not os.path.isdir(folder):
                m = re.match(r'^"?([^"]+?\.exe)', values["UninstallString"] or exe)
                folder = os.path.dirname(m.group(1)) if m else ""
            guess = os.path.join(folder, re.sub(r"[^\w\-]", "", name) + ".exe") if folder else ""
            if guess and os.path.isfile(guess):
                out.append((norm(name), guess))
    return out


class AppIndex:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()
        self.ready = threading.Event()

    def build(self):
        items = {}
        try:
            for app in ps_json("Get-StartApps | Select-Object Name,AppID"):
                name = norm(app.get("Name") or "")
                if name and not any(w in name for w in SKIP_WORDS):
                    items[name] = ("appid", app.get("AppID"))
        except Exception:
            log.exception("Get-StartApps")
        home = os.path.expanduser("~")
        dirs = [os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop"),
                os.path.join(os.environ.get("PUBLIC", ""), "Desktop")]
        for d in dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith((".lnk", ".url", ".appref-ms")):
                        name = norm(os.path.splitext(f)[0])
                        if name and not any(w in name for w in SKIP_WORDS):
                            # ярлык надёжнее идентификатора из Get-StartApps: у программ вроде Discord
                            # (установщик Squirrel) запуск по идентификатору ничего не открывает
                            if items.get(name, ("appid",))[0] != "file":
                                items[name] = ("file", os.path.join(root, f))
        # установленные программы из реестра — на случай, если ярлыка в «Пуске» нет (бывает у Steam)
        for name, exe in installed_programs():
            if name not in items and not any(w in name for w in SKIP_WORDS):
                items[name] = ("file", exe)
        with self.lock:
            self.items = items
        self.ready.set()
        log.info("Найдено приложений: %d", len(items))

    @staticmethod
    def _sound(s):
        s = re.sub(r"\s", "", translit(s.lower()))
        for a, b in (("ph", "f"), ("ck", "k"), ("c", "k"), ("q", "k"), ("w", "v"), ("x", "ks"), ("z", "s")):
            s = s.replace(a, b)
        return s

    @staticmethod
    def _skeleton(s):
        """Согласные без гласных: «cloud», «claude», «клод» → «kld». Ловит созвучные названия."""
        return re.sub(r"[aeiouy]", "", AppIndex._sound(s))

    @staticmethod
    def sounds_like(a, b):
        """Созвучны, если совпадают согласные и первый звук: «роблокс» = «roblox», но «запрет» ≠ «aseprite»."""
        sk = AppIndex._skeleton(a)
        return len(sk) >= 3 and sk == AppIndex._skeleton(b) and AppIndex._sound(a)[:1] == AppIndex._sound(b)[:1]

    def candidates(self, query, aliases, limit=4):
        """Подходящие приложения, лучшие первыми: [(оценка, название, цель)]."""
        q = norm(query)
        variants = {q, translit(q)}
        if q in aliases:
            variants.add(norm(aliases[q]))
        with self.lock:
            items = list(self.items.items())
        scored = {}
        for name, target in items:
            best = 0.0
            for v in variants:
                if v == name:
                    score = 1.0
                elif len(v) >= 3 and re.search(r"(^|\s)" + re.escape(v), name):
                    score = 0.93 - 0.003 * len(name)
                else:
                    score = difflib.SequenceMatcher(None, v, name).ratio()
                    first = name.split()[0] if name.split() else name
                    if self.sounds_like(v, name):
                        score = max(score, 0.97)  # звучит как всё название: «роблокс» = «roblox»
                    elif self.sounds_like(v, first):
                        score = max(score, 0.86)  # звучит так же: «клод/cloud» ~ «claude»
                best = max(best, score)
            if best >= 0.72 and best > scored.get(name, (0,))[0]:
                scored[name] = (best, target)
        ranked = sorted(((s, n, t) for n, (s, t) in scored.items()), key=lambda x: -x[0])
        return ranked[:limit]

    def find(self, query, aliases):
        found = self.candidates(query, aliases, 1)
        return (found[0][1], found[0][2]) if found else None

    @staticmethod
    def launch(target):
        kind, value = target
        if kind == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{value}"], creationflags=NO_WINDOW)
            return
        os.startfile(value)

    def launch_best(self, query, options):
        """Запустить лучший вариант; если Windows его не открыла (например, Roblox.lnk — «операция
        отменена»), пробуем похожие ярлыки с тем же началом (Roblox Player), а в конце — через проводник,
        как при двойном клике. options: [(оценка, имя, цель)]. Возвращает имя запущенного или None."""
        first = options[0]
        q = first[1].split()[0] if first[1].split() else ""  # «roblox» из найденного «roblox», а не «роблокс»
        with self.lock:
            similar = [(1.0, n, t) for n, t in self.items.items() if n != first[1] and q and n.startswith(q)
                       and not re.search(r"studio|uninstall|update", n)]
        for _, name, target in [first] + sorted(similar, key=lambda x: len(x[1])):
            try:
                self.launch(target)
                return name
            except OSError as e:
                log.info("%s не запустился (%s)", name, e)
        if first[2][0] == "file":
            subprocess.Popen(["explorer.exe", first[2][1]], creationflags=NO_WINDOW)
            return first[1]
        return None


_desc_cache = {}


def file_description(path):
    """Описание программы из свойств exe-файла (то, что показывает диспетчер задач),
    например «Steam Client WebHelper». Пустая строка, если описания нет."""
    if not path:
        return ""
    if path in _desc_cache:
        return _desc_cache[path]
    desc = ""
    try:
        v = ctypes.windll.version
        size = v.GetFileVersionInfoSizeW(path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if v.GetFileVersionInfoW(path, 0, size, buf):
                ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
                if v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
                    lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
                    key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription"
                    sptr = ctypes.c_wchar_p()
                    if v.VerQueryValueW(buf, key, ctypes.byref(sptr), ctypes.byref(ln)) and ln.value:
                        desc = sptr.value or ""
    except Exception:
        pass
    _desc_cache[path] = desc
    return desc


class AppsMixin:
    """Запуск программ, закрытие процессов и чаты Telegram."""

    def open(self, c, m):
        if re.search(r"ютуб|youtube", c):
            self.youtube_opened_at = time.time()  # следующее «напиши …» будет поиском на YouTube
        t = re.sub(r"^(мне |пожалуйста |программу |приложение )", "", m.group("t"))
        t = re.sub(r" пожалуйста$", "", t).strip()
        if t in ("звук",):
            return None
        site_mode = t.startswith("сайт ")
        if site_mode:
            t = t[5:].strip()
        else:
            explicit = re.match(r"^(папк\w*|каталог\w*|директори\w*)\s+(?P<n>.+)$", t)
            found = find_folder(explicit.group("n") if explicit else t, search=bool(explicit))
            if found:
                os.startfile(found[0])
                return Reply(line("folder_open", t=found[1]))
            if explicit:
                return Reply(line("folder_missing", t=explicit.group("n")))

        if not site_mode and t in self.apps:
            self._run(self.apps[t])
            return Reply(line("open", t=t))

        if t in self.sites:
            self.web(self.sites[t])
            return Reply(line("open", t=t))
        close = difflib.get_close_matches(t, list(self.sites), n=1, cutoff=0.82)
        if close:
            self.web(self.sites[close[0]])
            return Reply(line("open", t=close[0]))

        domain = re.sub(r"\s*(точка|dot)\s*", ".", t).replace(" ", "")
        if re.fullmatch(r"[\w\-]+(\.[\w\-]+)*\.[a-zа-я]{2,}", domain):
            self.web("https://" + domain)
            return Reply(line("open", t=domain))

        if not site_mode:
            self.index.ready.wait(timeout=8)
            options = self.index.candidates(t, self.aliases)
            if not options:
                return Reply(line("app_missing", t=t))
            top = options[0]
            close = [o for o in options[1:] if top[0] - o[0] < 0.1]
            if top[0] >= 1.0 or not close:  # одно явно подходит — запускаем
                started = self.index.launch_best(t, [top])
                return Reply(line("launch", t=started) if started else line("app_missing", t=t))
            # несколько похожих — спрашиваем, какое открыть
            self.choice = [(n, tg) for _, n, tg in [top] + close[:2]]
            names = [n.title() for n, _ in self.choice]
            ords = ["первое", "второе", "третье"]
            listed = ", ".join(f"{ords[i]} — {nm}" for i, nm in enumerate(names))
            return Reply(line("choose_app", t=listed), "ask")
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(t))
        return Reply(line("site_unknown", t=t))

    def _run(self, target):
        if target.startswith(("http://", "https://")):
            self.web(target)
            return
        try:
            os.startfile(target)
        except OSError:
            subprocess.Popen(target, shell=True, creationflags=NO_WINDOW)

    def kill_process(self, c, m):
        """«сними задачу steam client helper», «заверши процесс хром принудительно», «кильни дискорд»."""
        t = re.sub(r"^(все |всех )?(процесс\w*|задач\w*)\s+", "", m.group("t").strip())
        return self.close(c, None, target=t, force=bool(re.search(r"принудительн|насильн|жестк|совсем", c)))

    def close(self, c, m, target=None, force=False):
        t = target if target is not None else m.group("t").strip()
        force = force or bool(re.search(r"принудительн|насильн|жестк", c))
        t = re.sub(r"\s+(принудительно|насильно|жестко|совсем)$", "", t).strip()
        if re.fullmatch(r"(окно|это|это окно)", t):
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x73, 0, 0, 0)
            u.keybd_event(0x73, 0, 2, 0)
            u.keybd_event(0x12, 0, 2, 0)
            return Reply(line("closed_window"))
        q = norm(t)
        variants = {q, translit(q)}
        if q in self.aliases:
            variants.add(norm(self.aliases[q]))
        if q in self.apps:
            variants.add(os.path.splitext(os.path.basename(self.apps[q]))[0].lower())
        if self.index.ready.is_set():  # «закрой клод» → приложение Claude → процесс claude
            for _, app_name, _ in self.index.candidates(q, self.aliases, 2):
                variants.add(app_name.split()[0])
        variants = {v.replace(" ", "") for v in variants if len(v.replace(" ", "")) >= 3}

        words = [w for w in translit(q).split() if len(w) >= 3]  # для сравнения с описанием из диспетчера задач
        me = psutil.Process().username()
        best = {}  # имя процесса → (оценка, [процессы])
        for p in psutil.process_iter(["name", "pid", "username", "exe"]):
            name = (p.info["name"] or "").lower().removesuffix(".exe")
            # только свои программы: системные процессы и службы не трогаем никогда
            if not name or name in PROTECTED_PROCS or p.info["username"] != me or p.pid == os.getpid():
                continue
            score = 0.0
            for v in variants:
                if name == v:
                    s = 1.0
                elif name.startswith(v) or (len(name) >= 4 and v.startswith(name) and len(name) >= len(v) * 0.6):
                    s = 0.9  # «steam» не считается совпадением для «steamclienthelper»
                else:
                    s = difflib.SequenceMatcher(None, v, name).ratio()
                    if AppIndex.sounds_like(v, name):
                        s = max(s, 0.86)
                score = max(score, s)
            if score < 0.95 and words:
                # «steam client helper» → описание «Steam Client WebHelper» у steamwebhelper.exe
                desc = norm(file_description(p.info["exe"]))
                if desc:
                    if desc == " ".join(words):
                        score = 1.0
                    elif len(words) >= 2 and all(any(w in d for d in desc.split()) for w in words):
                        score = max(score, 0.92)
            if score >= 0.85:
                prev = best.get(name, (0.0, []))
                best[name] = (max(prev[0], score), prev[1] + [p])
        if not best:
            return Reply(line("not_running", t=t))
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
        top_name, (top_score, _) = ranked[0]
        close = [kv for kv in ranked[1:] if top_score - kv[1][0] < 0.1]
        if top_score < 1.0 and close:  # несколько похожих — спрашиваем, что закрыть
            self.choice = [(n, (lambda procs=procs, n=n: self._kill(n, procs, force))) for n, (_, procs) in
                           [ranked[0]] + close[:2]]
            ords = ["первое", "второе", "третье"]
            listed = ", ".join(f"{ords[i]} — {n}" for i, (n, _) in enumerate(self.choice))
            return Reply(line("choose_close", t=listed), "ask")
        return self._kill(top_name, best[top_name][1], force)

    @staticmethod
    def _kill(name, procs, force=False):
        """Завершить процессы: сначала вежливо (как «Снять задачу»), а если не закрылись за 3 секунды
        или просили принудительно — жёстко."""
        alive = []
        for p in procs:
            try:
                p.kill() if force else p.terminate()
                alive.append(p)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                return Reply(line("kill_denied", t=name))
        _, still = psutil.wait_procs(alive, timeout=3)
        if still:
            for p in still:
                try:
                    p.kill()
                except Exception:
                    pass
            _, still = psutil.wait_procs(still, timeout=2)
        if still:
            return Reply(line("kill_failed", t=name))
        return Reply(line("closed", names=name))

    def chat(self, c, m):
        """«первый чат», «чат мама» — открывает чат в Telegram (запускает его, если надо)."""
        if not automation.find_window(("telegram",)):
            found = self.index.find("телеграм", {"телеграм": "telegram"}) if self.index.ready.wait(8) else None
            if not found:
                return Reply(line("app_missing", t="Telegram"), "fail")
            self.index.launch(found[1])
        index = automation.ORDINALS.get(m.groupdict().get("ord") or "", None)
        name = (m.groupdict().get("name") or "").strip() or None
        if name:
            name = re.sub(r"^(с|со|контакт\w*|чат\w*|chat|с контактом)\s+", "", name)
            name = stem_name(name)
        if not automation.telegram_open_chat(index=index or (None if name else 1), name=name):
            # чат не открылся — дальше по цепочке ничего не печатаем, чтобы не написать не туда
            return Reply(line("chat_not_found", t=name or f"номер {index or 1}"), "fail")
        return Reply(line("chat_open", t=name or f"чат номер {index or 1}"))
