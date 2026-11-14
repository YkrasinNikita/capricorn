"""Клавиатура: разбор произнесённых сочетаний клавиш, прокрутка и печать в окно."""
import re
import threading
import time

import automation
from persona import line

from .utils import NUM, Reply, norm, parse_number


# ---------------- клавиши ----------------

VK = {"win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
      "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
      "printscreen": 0x2C, "capslock": 0x14, "menu": 0x5D}
EXTENDED = {"win", "delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down", "menu"}
KEY_WORDS = {  # как клавиши произносят (Vosk пишет кириллицей, Google — часто латиницей)
    "win": "вин винд винда виндовс виндоус виндоуз виндос виндус винду windows window win wind винды "
           "виндоуса виндовса",
    "ctrl": "контрол контроль контрл кнтрл ктрл контр ctrl control ctl",
    "alt": "альт алт alt", "shift": "шифт shift", "tab": "таб табуляция tab",
    "enter": "энтер ентер enter ввод return", "esc": "эскейп искейп эскейф эск esc escape",
    "space": "пробел space спейс", "backspace": "бэкспейс бекспейс backspace",
    "delete": "делит дилит делейт delete del дел", "insert": "инсерт insert", "home": "хоум home",
    "end": "энд end", "printscreen": "принтскрин printscreen prtsc", "capslock": "капслок capslock",
    "left": "влево лево налево left", "right": "вправо право направо right",
    "up": "вверх наверх up", "down": "вниз down", "menu": "меню",
}
WORD_TO_KEY = {w: k for k, ws in KEY_WORDS.items() for w in ws.split()}
LETTER_NAMES = {"эй": "a", "би": "b", "си": "c", "ди": "d", "и": "e", "эф": "f", "джи": "g", "эйч": "h",
                "ай": "i", "джей": "j", "кей": "k", "эл": "l", "эм": "m", "эн": "n", "оу": "o", "пи": "p",
                "кью": "q", "ар": "r", "эс": "s", "ти": "t", "ю": "u", "ви": "v", "дабл ю": "w",
                "дабл-ю": "w", "экс": "x", "икс": "x", "уай": "y", "игрек": "y", "зед": "z", "зет": "z",
                # по-русски: «контрол цэ», «вин дэ», и одиночные буквы «вин д»
                "а": "a", "бэ": "b", "б": "b", "цэ": "c", "ц": "c", "дэ": "d", "д": "d", "е": "e",
                "гэ": "g", "г": "g", "ха": "h", "ка": "k", "к": "k", "эль": "l", "л": "l", "м": "m",
                "н": "n", "о": "o", "пэ": "p", "п": "p", "эр": "r", "р": "r", "с": "s", "тэ": "t", "т": "t",
                "у": "u", "вэ": "v", "в": "v", "зэ": "z", "з": "z"}
SKIP_WORDS_KEYS = {"и", "плюс", "кнопку", "кнопка", "кнопки", "клавишу", "клавиши", "клавиша", "клавиш",
                   "сочетание", "сочетания", "комбинацию", "комбинация", "стрелку", "стрелка", "стрелочку",
                   "на", "клавиатуре", "одновременно", "мне", "пожалуйста", "plus", "and"}


def split_glued(word):
    """Google иногда склеивает сочетание в одно слово: «wintab», «альттаб», «ctrlshiftesc».
    Режем на известные названия клавиш (одиночная буква/цифра допустима только последней)."""
    names = sorted((w for w in WORD_TO_KEY if len(w) > 1), key=len, reverse=True)

    def go(rest):
        if not rest:
            return []
        for n in names:
            if rest.startswith(n):
                tail = go(rest[len(n):])
                if tail is not None:
                    return [WORD_TO_KEY[n]] + tail
        f = re.match(r"[fф](1[0-2]|[1-9])", rest)
        if f:
            tail = go(rest[f.end():])
            if tail is not None:
                return ["f" + f.group(1)] + tail
        if len(rest) == 1 and re.fullmatch(r"[a-z0-9]", rest):
            return [rest]
        return None
    return go(word)


def parse_keys(text):
    """«вин таб» / «ctrl shift esc» / «эф пять» → ['win', 'tab'] ... Возвращает (клавиши, нераспознанное)."""
    t = re.sub(r"[+\-_,.]", " ", norm(text))
    for a, b in (("пейдж ап", "pageup"), ("page up", "pageup"), ("пейдж даун", "pagedown"),
                 ("page down", "pagedown"), ("принт скрин", "printscreen"), ("print screen", "printscreen"),
                 ("капс лок", "capslock"), ("caps lock", "capslock"), ("дабл ю", "дабл-ю"),
                 ("бэк спейс", "backspace"), ("back space", "backspace")):
        t = t.replace(a, b)
    words = t.split()
    keys, i = [], 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in WORD_TO_KEY or w in VK:
            keys.append(WORD_TO_KEY.get(w, w))
        elif re.fullmatch(r"[fф]\d{1,2}", w):
            keys.append("f" + w[1:])
        elif w in ("эф", "ф", "f") and (nxt.isdigit() or nxt in NUM):
            keys.append(f"f{int(nxt) if nxt.isdigit() else NUM[nxt]}")
            i += 1
        elif w in LETTER_NAMES and not (w == "и" and keys and i + 1 < len(words)):
            keys.append(LETTER_NAMES[w])
        elif re.fullmatch(r"[a-z0-9]", w):
            keys.append(w)
        elif w in NUM and NUM[w] < 10:
            keys.append(str(NUM[w]))
        elif w not in SKIP_WORDS_KEYS:
            glued = split_glued(w)
            if not glued:
                return [], w
            keys += glued
        i += 1
    return keys, None


def send_keys(keys):
    """Зажимает клавиши по порядку и отпускает в обратном — так работают сочетания вроде Win+Tab."""
    import ctypes
    u = ctypes.windll.user32
    codes = []
    for k in keys:
        if k in VK:
            code = VK[k]
        elif re.fullmatch(r"f\d{1,2}", k) and 1 <= int(k[1:]) <= 12:
            code = 0x6F + int(k[1:])
        else:
            code = ord(k.upper())
        codes.append((code, 1 if k in EXTENDED else 0))
    for code, ext in codes:
        u.keybd_event(code, 0, ext, 0)
        time.sleep(0.02)
    for code, ext in reversed(codes):
        u.keybd_event(code, 0, ext | 2, 0)  # 2 = KEYEVENTF_KEYUP
        time.sleep(0.02)


class KeysMixin:
    """Команды с клавиатурой, вкладками, прокруткой и печатью текста."""

    # --- прокрутка ---
    def scroll(self, c, m):
        up = bool(re.search(r"\b(вверх|наверх|выше|верх\w*|начал\w*|поднимись|назад)\b", c))
        down = bool(re.search(r"\b(вниз|ниже|низ\w*|конец|дальше|спустись|вперед)\b", c))
        auto = re.search(r"\b(медленно|плавно|автоматически|сама|постепенно|прокручивай|листай сама)\b", c) \
            or re.fullmatch(r"(листай|полистай|прокручивай|скроль|скролль)( медленно| плавно)?", c)
        if not (up or down or auto) or re.search(r"секунд|минут|\bчас|\d[:.]\d\d|полминут", c):
            return None  # «прокрути на 30 секунд вперёд» — это перемотка видео, не страницы
        direction = -1 if up and not down else 1
        self.stop_background()
        if auto:
            fast = bool(re.search(r"быстр", c))
            self._autoscroll_stop = threading.Event()
            threading.Thread(target=self._autoscroll, args=(direction, 0.25 if fast else 0.6,
                                                            self._autoscroll_stop), daemon=True).start()
            return Reply(line("scroll_auto"))
        if re.search(r"\b(самый|самую|самого|до конца|в конец|в начало|до низа|до верха|конец страницы|"
                     r"начало страницы)\b|^(в |на |до )?(низ|верх|низа|верха)$", c):
            automation.scroll(200 * direction, delay=0.008)  # слишком быстро — щелчки колеса теряются
            return Reply(line("scroll_end" if direction > 0 else "scroll_top"))
        n = parse_number(c)
        screen = automation.screen_notches()
        if re.search(r"\b(немного|чуть|чуть-чуть|слегка|капельку)\b|^(еще )?(ниже|выше)$", c):
            notches = 3
        elif n and re.search(r"экран|страниц|раз", c):
            notches = min(n, 20) * screen
        else:
            notches = screen
        automation.scroll(notches * direction)
        return Reply(line("scroll"))

    def _autoscroll(self, direction, interval, stop):
        """Плавная прокрутка в окне, которое было активно в момент команды, — пока не скажут «стоп»."""
        hwnd = automation.target_window()
        end = time.time() + 300
        while not stop.is_set() and time.time() < end:
            if automation.target_window() != hwnd:
                break  # пользователь ушёл в другое окно — не крутим чужое
            automation.scroll(direction, hwnd=hwnd)
            stop.wait(interval)

    def autoscroll_active(self):
        ev = getattr(self, "_autoscroll_stop", None)
        return bool(ev and not ev.is_set())

    def stop_background(self):
        """Остановить фоновые действия (автопрокрутку). Вызывается и на «стоп» из main."""
        ev = getattr(self, "_autoscroll_stop", None)
        if ev and not ev.is_set():
            ev.set()
            return True
        return False

    # --- печать и клавиши ---
    def type_cmd(self, c, m):
        """«напиши привет» — печатает в активное окно и жмёт Enter (в чате это отправка)."""
        from .web import youtube_search_in_tab  # локальный импорт: web сам использует send_keys

        parsed = automation.parse_type(c)
        if not parsed:
            return None  # «напиши стих» — это просьба к нейросети, а не печать
        text, send = parsed
        title = automation.foreground_title().lower()
        recent_yt = time.time() - self.youtube_opened_at < 120
        if "youtube" in title or (recent_yt and automation.find_window(("youtube",))):
            # на YouTube «напиши X» = поиск X (печатать в саму страницу бессмысленно)
            if "youtube" not in title and not automation.wait_and_focus(("youtube",), timeout=4):
                return Reply(line("focus_fail"))
            youtube_search_in_tab(text)
            return Reply(line("yt", q=text))
        if send is None:
            send = self.cfg.get("type_press_enter", True)
        automation.input_text(text)
        if send:
            time.sleep(0.1)
            send_keys(["enter"])
        return Reply(line("sent" if send else "typed", t=text))

    def search_field(self, c, m):
        automation.focus_search(automation.foreground_title())
        return Reply(line("search_ready"))

    def press(self, c, m):
        text = m.group("k")
        times = 1
        rep = re.search(r"\s(\S+) раз\w*$", text)
        if rep and parse_number(rep.group(1)):
            times, text = min(30, parse_number(rep.group(1))), text[:rep.start()]
        keys, unknown = parse_keys(text)
        if unknown:
            return Reply(line("key_unknown", t=unknown))
        if not keys:
            return None
        for _ in range(times):
            send_keys(keys)
            time.sleep(0.08)
        return Reply(line("pressed", t=" + ".join(k.upper() if len(k) == 1 else k.capitalize() for k in keys)))

    def tab(self, c, m):
        if re.search(r"нов(ую|ая) вкладк", c):
            send_keys(["ctrl", "t"])
            return Reply(line("tab_new"))
        if re.search(r"\b(верни|восстанови)", c):
            send_keys(["ctrl", "shift", "t"])
            return Reply(line("tab_restore"))
        if re.search(r"следующ", c):
            send_keys(["ctrl", "tab"])
            return Reply(line("done"))
        if re.search(r"предыдущ", c):
            send_keys(["ctrl", "shift", "tab"])
            return Reply(line("done"))
        n = max(1, min(20, parse_number(c) or 1))
        for _ in range(n):
            send_keys(["ctrl", "w"])
            time.sleep(0.12)
        return Reply(line("tab_closed", n=n) if n > 1 else line("tab_closed_one"))

    def minimize_all(self, c, m):
        send_keys(["win", "d"])
        return Reply(line("done"))

    def minimize(self, c, m):
        """«Сверни окно / вкладку / браузер» — сворачивает активное окно."""
        import ctypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if hwnd:
            u.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return Reply(line("done"))
