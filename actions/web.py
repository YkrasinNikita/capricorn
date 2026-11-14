"""Браузер, сайты, YouTube, поиск и Википедия."""
import ctypes
import json
import logging
import os
import random
import re
import time
import urllib.parse
import urllib.request
import webbrowser

import automation
import media
from persona import line

from .keys import send_keys
from .utils import Reply, norm

log = logging.getLogger("capri")


# как браузеры называют вслух → по чему узнать в списке браузеров Windows (название ключа или exe)
BROWSER_WORDS = [
    (r"(гугл )?хром\w*|chrome", "chrome"),
    (r"яндекс\w*|yandex", "yandex"),
    (r"опер\w*|opera", "opera"),
    (r"(фаер|файр|файер)фокс\w*|мозил+\w*|firefox", "firefox"),
    (r"(майкрософт )?эдж\w*|edge", "edge"),
    (r"брейв\w*|brave", "brave"),
    (r"вивальди|vivaldi", "vivaldi"),
]
BROWSER_NAMES = {"chrome": "Google Chrome", "yandex": "Яндекс Браузер", "opera": "Opera", "firefox": "Firefox",
                 "edge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi"}
# «… в хроме», «… через яндекс браузер», «… в браузере опера» — только в конце фразы
BROWSER_SUFFIX = re.compile(r"\s+(в|через)\s+(браузер\w*\s+)?(?P<b>" + "|".join(p for p, _ in BROWSER_WORDS)
                            + r")(\s+браузер\w*)?$")


def installed_browsers():
    """{"chrome": путь к exe, ...} — браузеры из списка Windows («Приложения по умолчанию»)."""
    import winreg
    found = {}
    for hive, path in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet")):
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            try:
                cmd = winreg.QueryValue(key, name + r"\shell\open\command")
            except OSError:
                continue
            m = re.match(r'^\s*"?([^"]+?\.exe)', cmd)
            if not m or not os.path.isfile(m.group(1)):
                continue
            label = (name + " " + os.path.basename(m.group(1))).lower()
            for _, k in BROWSER_WORDS:
                if k in label or (k == "edge" and "msedge" in label):
                    found.setdefault(k, m.group(1))
    return found


def find_browser(name):
    """«хром» / «яндекс браузер» / путь к exe → путь к exe браузера или None, если такого нет."""
    if not name:
        return None
    if os.path.isfile(name):
        return name
    n = norm(name)
    for pattern, k in BROWSER_WORDS:
        if re.fullmatch(rf"({pattern})( браузер\w*)?|браузер\w* ({pattern})", n):
            return installed_browsers().get(k)
    return None


# ---------------- ютуб ----------------

BROWSERS = ("firefox", "chrome", "msedge", "opera", "browser", "brave", "vivaldi", "yandex")


def browser_playing():
    """True, если из браузера сейчас реально идёт звук (по индикатору громкости в микшере Windows)."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    for s in AudioUtilities.GetAllSessions():
        try:
            name = (s.Process.name() if s.Process else "").lower()
            if any(b in name for b in BROWSERS):
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                if meter.GetPeakValue() > 0.001:
                    return True
        except Exception:
            continue
    return False


def youtube_window():
    """Окно браузера с открытым YouTube (по заголовку активной вкладки)."""
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, buf, 512)
            if "youtube" in buf.value.lower():
                found.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    return found[0] if found else None


_ensure_token = [0]  # номер последней проверки «заиграло ли видео»; новая команда отменяет старую


def cancel_ensure():
    _ensure_token[0] += 1


def ensure_playing(wait_load=7.0):
    """Браузеры блокируют автозапуск со звуком у вкладок, открытых программой. Ждём звук;
    если тишина — переключаемся на окно YouTube и жмём K (пуск/пауза в плеере), затем проверяем.
    Если за это время пришла новая команда — тихо выходим, чтобы не кликнуть посреди её работы."""
    _ensure_token[0] += 1
    token = _ensure_token[0]
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    deadline = time.time() + wait_load
    while time.time() < deadline:  # может, запустилось само (или идёт реклама)
        if _ensure_token[0] != token:
            return
        if browser_playing():
            log.info("Видео играет само")
            return
        time.sleep(0.5)
    if _ensure_token[0] != token:
        return
    hwnd = None
    for _ in range(10):  # заголовок вкладки может ещё не смениться на «… - YouTube»
        hwnd = youtube_window()
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        log.info("Окно YouTube не найдено, не нажимаю пуск")
        return
    u = ctypes.windll.user32
    u.keybd_event(0x12, 0, 0, 0)       # «пустой» Alt — без него Windows не даёт сменить активное окно
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    time.sleep(0.4)

    # Клик мышью по плееру — настоящее действие пользователя: и окно активирует, и фокус ставит в плеер,
    # и браузер считает это разрешением на звук. Точка (30% ширины, 35% высоты) попадает в плеер
    # и в обычном, и в широком режиме YouTube.
    from ctypes import wintypes
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    x, y = r.left + int((r.right - r.left) * 0.30), r.top + int((r.bottom - r.top) * 0.35)
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    u.SetCursorPos(x, y)
    u.mouse_event(0x0002, 0, 0, 0, 0)  # левая кнопка вниз
    u.mouse_event(0x0004, 0, 0, 0, 0)  # вверх
    time.sleep(0.05)
    u.SetCursorPos(old.x, old.y)
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено кликом по плееру")
            return
    send_keys(["k"])  # запасной вариант: фокус уже в плеере
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено клавишей K")
            return
    log.info("Не удалось запустить видео: звука нет")


def youtube_first(query):
    """(id, название) первого обычного видео (не шортс) в поиске YouTube или None."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as e:
        log.info("Поиск на YouTube не удался: %s", e)
        return None
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page)
    if not m:
        return None
    title = ""
    t = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', page[m.end():m.end() + 6000])
    if t:
        try:
            title = json.loads(f'"{t.group(1)}"')
        except ValueError:
            title = t.group(1)
    return m.group(1), title


def stem_name(name):
    """«с мамой» → «мам», «шахтеров» → «шахтер»: поиск Telegram ищет по началу слова, а падежные
    окончания из речи мешают найти «Мама» или «Шахтёры»."""
    out = []
    for w in name.split():
        if len(w) >= 5:
            s = re.sub(r"(ами|ями|ого|его|ому|ему|ов|ев|ей|ой|ом|ем|ым|им|ою|ею|ах|ях|ам|ям|ую|юю|ая|яя|ые|ие|"
                       r"ы|и|а|я|у|ю|е)$", "", w)
            w = s if len(s) >= 3 else w
        out.append(w)
    return " ".join(out)


def youtube_search_in_tab(text):
    """Поиск на YouTube в текущей вкладке через адресную строку (Ctrl+L): срабатывает, даже если страница
    ещё грузится и поле поиска не в фокусе."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    before = automation.window_title(hwnd)
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(text)
    for attempt in range(2):
        # адрес вводится, только если адресная строка действительно получила фокус (иначе буквы ушли бы
        # в страницу YouTube, где это горячие клавиши)
        if not media.navigate(hwnd, url):
            time.sleep(0.6)
            continue
        for _ in range(10):  # ждём, что вкладка ушла на страницу результатов
            time.sleep(0.4)
            title = automation.window_title(hwnd)
            if title != before and "youtube" in title.lower() and title.lower().startswith(text.lower()[:4]):
                return True
        log.info("Поиск на YouTube не сработал с %d-й попытки, заголовок: %s", attempt + 1, title)
        if not automation.is_foreground(hwnd):
            break  # окно потеряло фокус — второй раз вслепую не печатаем
    return False


def wiki_summary(query, sentences=2):
    """Первые предложения статьи русской Википедии по запросу, подготовленные для озвучки. None — не нашлось."""
    headers = {"User-Agent": "CapriAssistant/1.0 (personal voice assistant)"}

    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        found = get("https://ru.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=1&srsearch="
                    + urllib.parse.quote(query))["query"]["search"]
        if not found:
            return None
        page = get("https://ru.wikipedia.org/api/rest_v1/page/summary/"
                   + urllib.parse.quote(found[0]["title"].replace(" ", "_")))
    except Exception as e:
        log.info("Википедия недоступна: %s", e)
        return None
    if page.get("type") == "disambiguation":
        return None
    text = page.get("extract", "").replace("́", "")  # знаки ударения
    text = re.sub(r"\s*\([^()]*\)", "", text)       # транскрипции, даты и пр. в скобках — не для озвучки
    text = re.sub(r"\s*\[[^\]]*\]", "", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", text.strip())
    out = " ".join(parts[:sentences]).strip()
    if len(out) > 300:
        out = out[:300].rsplit(" ", 1)[0] + "…"
    return out or None


class WebMixin:
    """Сайты, поиск, YouTube, картинки и Википедия."""

    def web(self, url):
        """Открыть адрес: в браузере из команды («… в хроме»), из настройки "browser" или в браузере
        по умолчанию Windows."""
        exe = find_browser(self.browser_once) or find_browser(self.cfg.get("browser"))
        if exe:
            import subprocess
            subprocess.Popen([exe, url])
        else:
            webbrowser.open(url)

    def search(self, c, m):
        q = m.group("q")
        yt = re.search(r"(на|в) (ютубе|ютуб|youtube)", q)
        if yt:
            q = q.replace(yt.group(0), "").strip()
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
            return Reply(line("yt", q=q))
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        return Reply(line("web", q=q))

    def images(self, c, m):
        q = m.group("q").strip()
        if re.search(r"\bобои\b", c):
            q = "обои " + q
        self.web("https://www.google.com/search?tbm=isch&q=" + urllib.parse.quote(q))
        return Reply(line("images", q=q))

    def info(self, c, m):
        """Открывает поиск Google и вслух пересказывает начало статьи из Википедии, если она нашлась."""
        q = m.group("q").strip()
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        summary = wiki_summary(q)
        if summary:
            return Reply(line("info", t=summary))
        return Reply(line("web", q=q))

    def video(self, c, m):
        q = m.group("q").strip()
        q = re.sub(r"\s+(на|в) (ютуб\w*|youtube)$", "", q)  # «включи песню X на ютубе»
        q = re.sub(r"^(на|в) (ютуб\w*|youtube)\s*", "", q).strip()
        if not q or q in ("на", "в", "с", "про", "о"):  # «открой какое-нибудь видео на ютубе»
            q = random.choice(["интересное видео", "смешные моменты", "залипательное видео", "лучшие клипы"])
        kind = m.groupdict().get("kind") or ""
        search = q + (" песня" if re.match(r"песн|трек", kind) else "")
        found = youtube_first(search)
        if not found:  # не вышло достать первое видео — хотя бы откроем поиск
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(search))
            return Reply(line("yt", q=q))
        vid, title = found
        self.web(f"https://www.youtube.com/watch?v={vid}")
        import threading
        threading.Thread(target=ensure_playing, daemon=True).start()
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) > 70:
            title = title[:67].rsplit(" ", 1)[0] + "…"
        return Reply(line("video", t=title) if title else line("video_untitled"))
