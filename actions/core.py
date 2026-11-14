"""Ядро команд: состояние Actions, цепочка команд и разбор реестра интентов."""
import ctypes
import difflib
import os
import re
import threading
import time

import automation
import media
from persona import line

from .apps import PROTECTED_PROCS, AppIndex, AppsMixin
from .fallback import FallbackMixin
from .files import FilesMixin
from .keys import KeysMixin, send_keys
from .media_cmds import MediaMixin
from .routes import FORGET_RE, FORGET_REPLY_RE, REMEMBER_RE, REPLY_RE, ROUTES
from .system import SystemMixin
from .utils import Reply, log, norm, translit
from .web import (BROWSER_NAMES, BROWSER_SUFFIX, BROWSER_WORDS, WebMixin, cancel_ensure, find_browser,
                  youtube_search_in_tab)


class Actions(MediaMixin, SystemMixin, AppsMixin, WebMixin, FilesMixin, KeysMixin, FallbackMixin):
    def __init__(self, cfg, base_dir, ai, save=None, build_index=True):
        self.cfg = cfg
        self.base_dir = base_dir
        self.ai = ai
        self.save = save  # save(раздел, название, значение или None) → текст ошибки или None
        self.browser_once = None  # браузер, названный в текущей команде («открой ютуб в хроме»)
        self.set_lists(cfg)
        self.index = AppIndex()
        if build_index:
            threading.Thread(target=self.index.build, daemon=True).start()
        self.pending = None
        self.choice = None  # варианты приложений, из которых пользователь сейчас выбирает
        self.youtube_opened_at = 0.0  # когда Капри последний раз открывала YouTube
        self.media_paused = None      # какой плеер Капри поставила на паузу
        self.local = None
        self.last_intent = None       # имя сработавшего интента — для логов и тестов
        self.routes = ROUTES

    def handle(self, cmd):
        c = norm(cmd)
        self.last_intent = None
        cancel_ensure()  # новая команда — старая проверка «заиграло ли видео» больше не нужна
        if self.pending:
            action, self.pending = self.pending, None
            if re.match(r"^(да|давай|подтверждаю|конечно|ага)\b", c):
                self.last_intent = "confirm"
                return action()
            if re.match(r"^(нет|не надо|отмена|стоп)\b", c):
                self.last_intent = "cancel"
                return Reply(line("cancel"))
        if self.stop_background():  # любая новая команда останавливает автопрокрутку
            if re.match(r"^(стоп|хватит|стой|остановись|достаточно|перестань|харэ|все|всё)\b", c):
                self.last_intent = "scroll_stop"
                return Reply(line("scroll_stop"))
        if self.choice:  # ждём ответ на «какое открыть / закрыть?»
            options, self.choice = self.choice, None
            self.last_intent = "choice"
            pick = self.resolve_choice(c, options)
            if pick is not None:
                name, act = options[pick]
                if callable(act):
                    return act()
                started = self.index.launch_best(name, [(1.0, name, act)])
                return Reply(line("launch", t=started) if started else line("app_missing", t=name))
            if re.match(r"^(никак\w*|ни одно|ни то|отмена|не надо|нет|ничего)\b", c):
                return Reply(line("cancel"))
            # ответ не про выбор — значит, это новая команда
        custom = self.custom_reply(c)  # своя фраза целиком — не делим её на цепочку команд
        if custom:
            self.last_intent = "custom_reply"
            return custom
        # «запомни … как X и Y» — название целиком, не цепочка команд
        whole = any(re.match(p, c) for p in (REMEMBER_RE, FORGET_RE, REPLY_RE, FORGET_REPLY_RE))
        steps = None if whole else automation.split_chain(c)
        if steps:
            self.last_intent = "chain"
            try:
                return self.run_chain(steps)
            except Exception as e:
                log.exception("Цепочка %s", steps)
                return Reply(line("error", e=e))
        return self.handle_one(c)

    @staticmethod
    def resolve_choice(c, options):
        """«второе», «два», «клод», «первую» → номер варианта или None."""
        ords = [(r"перв\w*|один|одну|1", 0), (r"втор\w*|два|две|2", 1), (r"трет\w*|три|3", 2),
                (r"последн\w*", len(options) - 1)]
        for pattern, idx in ords:
            if re.search(rf"(^|\s)({pattern})(\s|$)", c) and idx < len(options):
                return idx
        words = re.sub(r"^(открой|запусти|давай|вот|это|ну)\s+", "", c)
        best, best_i = 0.0, None
        for i, (name, _) in enumerate(options):
            for v in {words, translit(words)}:
                s = 1.0 if v and v in name else difflib.SequenceMatcher(None, v, name).ratio()
                if AppIndex.sounds_like(v, name.split()[0] if name.split() else name):
                    s = max(s, 0.86)
                if s > best:
                    best, best_i = s, i
        return best_i if best >= 0.6 else None

    def handle_one(self, c):
        m = BROWSER_SUFFIX.search(c)
        self.browser_once, missing = None, None
        if m:  # «открой ютуб в хроме» — браузер только для этой команды
            if find_browser(m.group("b")):
                self.browser_once = m.group("b")
            else:
                missing = next((BROWSER_NAMES[k] for p, k in BROWSER_WORDS if re.fullmatch(p, m.group("b"))),
                               m.group("b"))
            c = c[:m.start()].strip()
        try:
            r = self._handle_one(c)
        finally:
            self.browser_once = None
        if r and missing:
            r.text = line("browser_missing", b=missing) + " " + r.text
        return r

    def _handle_one(self, c):
        r = self.custom_reply(c)  # свои ответы — раньше команд и нейросети
        if r:
            return r
        self.last_intent = None
        for name, pattern, handler in self.routes:
            m = re.search(pattern, c)
            if m:
                self.last_intent = name
                fn = handler if callable(handler) else getattr(self, handler)
                try:
                    r = fn(c, m)
                except Exception as e:
                    log.exception("Команда %s (интент %s)", c, name)
                    return Reply(line("error", e=e))
                if r:
                    return r
        return None

    # --- свои сайты и программы ---
    def set_lists(self, cfg):
        """Сайты, программы и названия из настроек — при запуске и когда config.json поменяли."""
        self.sites = {norm(k): v for k, v in cfg.get("sites", {}).items()}
        self.apps = {norm(k): v for k, v in cfg.get("apps", {}).items()}
        self.aliases = {norm(k): v for k, v in cfg.get("app_aliases", {}).items()}
        self.replies = {norm(k): v for k, v in cfg.get("replies", {}).items() if v}

    def custom_reply(self, c):
        """Свой ответ на фразу из config.json → "replies" (точно или почти дословно) или None."""
        if not self.replies:
            return None
        if c in self.replies:
            return Reply(self.replies[c])
        close = difflib.get_close_matches(c, list(self.replies), n=1, cutoff=0.85)
        if close:
            return Reply(self.replies[close[0]])
        # фраза целиком внутри команды и лишнего немного: «ну как дела» → «как дела»
        for phrase in sorted(self.replies, key=len, reverse=True):
            if len(c) - len(phrase) <= 12 and re.search(rf"(^|\s){re.escape(phrase)}(\s|$)", c):
                return Reply(self.replies[phrase])
        return None

    def remember(self, section, name, target):
        """Записать сайт / программу / свой ответ (section "sites" / "apps" / "replies") в config.json
        и сразу начать её узнавать. target=None — забыть. Возвращает текст ошибки или None."""
        name = norm(name)
        err = self.save(section, name, target) if self.save else None
        if err:
            return err
        book = {"sites": self.sites, "apps": self.apps, "replies": self.replies}[section]
        stored = self.cfg.setdefault(section, {})
        if target is None:
            book.pop(name, None)
            stored.pop(name, None)
        else:
            book[name] = target
            stored[name] = target
        return None

    def remember_cmd(self, c, m):
        """«запомни этот сайт как музыка» — адрес открытой вкладки; «запомни эту программу как запрет» —
        программа, окно которой сейчас на экране."""
        name = re.sub(r"^(как|под названием|под именем|с названием|назови)\s+", "", (m.group("name") or "").strip())
        name = name.strip(" «»\"")
        site = re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what"))
        if not name:
            return Reply(line("remember_noname", what="этот сайт" if site else "эту программу"))
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if site:
            url = media.page_url(hwnd)
            if not url:
                return Reply(line("remember_nosite"))
            err = self.remember("sites", name, url)
            short = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            return Reply(line("remember_fail", e=err) if err else line("remember_site", t=name, url=short))
        exe = automation.window_exe(hwnd)
        base = os.path.splitext(os.path.basename(exe or ""))[0].lower()
        if not exe or base in PROTECTED_PROCS or base == "applicationframehost":
            return Reply(line("remember_noapp"))
        err = self.remember("apps", name, exe)
        return Reply(line("remember_fail", e=err) if err else line("remember_app", t=name, app=base))

    def forget_cmd(self, c, m):
        name = norm(m.group("name"))
        section = "sites" if re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what")) else "apps"
        book = self.sites if section == "sites" else self.apps
        if name not in book:
            return Reply(line("forget_missing", t=name))
        err = self.remember(section, name, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=name))

    def reply_cmd(self, c, m):
        """«Когда я скажу „как дела“, отвечай „лучше всех“» — свой ответ на фразу."""
        phrase = m.group("p").strip(" ,")
        text = m.group("r").strip(" ,")
        text = text[:1].upper() + text[1:]
        if not re.search(r"[.!?…]$", text):
            text += "."
        err = self.remember("replies", phrase, text)
        return Reply(line("remember_fail", e=err) if err else line("reply_saved", p=phrase, r=text))

    def forget_reply_cmd(self, c, m):
        phrase = norm(m.group("p"))
        close = phrase if phrase in self.replies else next(
            iter(difflib.get_close_matches(phrase, list(self.replies), n=1, cutoff=0.85)), None)
        if not close:
            return Reply(line("forget_missing", t=phrase))
        err = self.remember("replies", close, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=close))

    # --- поведение ассистента ---
    def mic_info(self, c, m):
        return Reply("", "mic_info")

    def mic_next(self, c, m):
        return Reply("", "mic_next")

    def quit(self, c, m):
        return Reply(line("quit"), "quit")

    def hide(self, c, m):
        return Reply(line("hide"), "hide")

    def wave(self, c, m):
        return Reply(line("wave"), "wave")

    def roam(self, c, m):
        return Reply(line("roam"), "roam")

    def help(self, c, m):
        return Reply(line("help"))

    def move(self, c, m):
        """Куда переместиться: «в левый нижний угол», «вправо», «в центр», «в другую сторону»."""
        if re.search(r"другую сторону|в другую", c):
            where = "other"
        elif re.search(r"центр|середин", c):
            where = "center"
        else:
            h = "left" if re.search(r"лев|влево", c) else "right" if re.search(r"прав|вправо", c) else ""
            v = "top" if re.search(r"верх|наверх", c) else "bottom" if re.search(r"низ|вниз", c) else ""
            where = "-".join(x for x in (v, h) if x) or "other"
        return Reply(line("move"), "move:" + where)

    def run_chain(self, steps):
        """Выполняет шаги по очереди, держа в уме, в каком окне мы сейчас (Telegram, YouTube, браузер…)."""
        log.info("Цепочка: %s", steps)
        context = None   # ключевые слова заголовка окна, с которым работаем
        kind = None      # telegram / youtube / browser / None
        said = []
        in_search = False  # курсор уже стоит в поле поиска
        for step in steps:
            s = norm(step)
            typed = automation.parse_type(s)
            if typed:
                text, send = typed
                if context and not automation.wait_and_focus(context, timeout=6):
                    # не смогли переключиться — ничего не печатаем, чтобы не написать в чужое окно
                    return Reply(" ".join(said + [line("focus_fail")]))
                if kind == "youtube":
                    youtube_search_in_tab(text)
                    said.append(line("yt", q=text))
                    continue
                if kind in ("youtube", "browser"):
                    # на сайте «напиши X» = поиск X: сначала курсор в строку поиска, потом Enter
                    if not in_search:
                        automation.focus_search(automation.foreground_title())
                        time.sleep(0.4)
                    automation.input_text(text)
                    if send is not False:
                        time.sleep(0.1)
                        send_keys(["enter"])
                    said.append(line("web", q=text))
                    continue
                if send is None:
                    send = self.cfg.get("type_press_enter", True)
                automation.input_text(text)
                if send:
                    time.sleep(0.1)
                    send_keys(["enter"])
                said.append(line("sent" if send else "typed", t=text))
                continue
            q = re.match(r"^(найди|поищи)\s+(?P<q>.+)$", s)
            if q and kind in ("youtube", "telegram"):
                if kind == "telegram":  # поиск в Telegram = открыть чат по имени
                    if not automation.telegram_open_chat(name=q.group("q")):
                        return Reply(" ".join(said + [line("focus_fail")]))
                else:
                    if not automation.wait_and_focus(context, timeout=6):
                        return Reply(" ".join(said + [line("focus_fail")]))
                    youtube_search_in_tab(q.group("q"))
                    said.append(line("yt", q=q.group("q")))
                continue
            if re.match(r"^(нажми на поиск|нажми поиск|поиск|в поиск|перейди в поиск)$", s):
                if context and not automation.wait_and_focus(context, timeout=6):
                    return Reply(" ".join(said + [line("focus_fail")]))
                automation.focus_search(automation.foreground_title())
                time.sleep(0.3)
                in_search = True
                continue
            r = self.handle_one(s)
            if r is None:
                said.append(line("chain_skip", t=step))
                continue
            if r.action in ("fail", "ask"):  # шаг не удался или нужно уточнить — дальше не идём
                return Reply(" ".join(said + [r.text]))
            words = s.split()
            for w in words:
                if w in automation.APP_WINDOWS:
                    context = automation.APP_WINDOWS[w]
                    kind = {"telegram": "telegram", "телеграм": "telegram", "телега": "telegram",
                            "ютуб": "youtube", "youtube": "youtube"}.get(w, "browser" if w == "браузер" else None)
            if re.search(r"\b(чат|диалог|переписк)", s):
                context, kind = ("telegram",), "telegram"
            if context and re.match(r"^(открой|запусти|зайди|перейди)\b", s):
                automation.wait_and_focus(context, timeout=10)
                time.sleep(2.5 if kind in ("youtube", "browser") else 0.4)  # сайту нужно загрузиться
                in_search = False
        return Reply(" ".join(said) if said else line("done"))
