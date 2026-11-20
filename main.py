"""Капри — голосовой ассистент для Windows.
Запуск: run.bat (или pythonw main.py)."""
import json
import os
import queue
import random
import re
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QSlider,
                               QSystemTrayIcon, QWidget, QWidgetAction)

from actions import Actions, Reply
from ai import AI
from ai_router import AIRouter
from config_schema import CONFIG_VERSION, config_blank, load_config_file, save_config_file
from logging_setup import setup_logging
from local_ai import LocalAI
from notes import Notes
from persona import line
from rag import RagIndex
from scheduler import Scheduler
from avatar import Avatar
import model_setup
import version
import zapret
from voice import (Listener, Speaker, backend_kind, default_input_name, google_stt, input_devices,
                   make_glitch_sound, play_sound_async)

# В собранной версии (PyInstaller) программа и её файлы лежат в папке exe, а ресурсы — во временной.
FROZEN = bool(getattr(sys, "frozen", False))
BASE = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
CONFIG_PATH = os.path.join(BASE, "config.json")


def resource_path(*parts):
    """Путь к упакованным файлам (assets): рядом с исходниками или внутри сборки."""
    return os.path.join(getattr(sys, "_MEIPASS", BASE), *parts)


log = setup_logging(BASE)
log.info("%s, Python %s", version.version_line(), sys.version.split()[0])

DEFAULTS = {
    "enable_ai": False,
    "privacy_mode": False,   # только офлайн: Vosk, локальная модель, локальный TTS; облако запрещено
    "local_ai": True,
    "local_model": "qwen3:8b",
    "barge_in": True,
    "mic_device": None,
    "chatter": True,
    "chatter_min_minutes": 15,
    "chatter_max_minutes": 40,
    "roam_chance": 0.5,
    "teleport_on_answer": True,
    "search_when_unsure": True,
    "voice_volume": 100,     # громкость голоса Капри 0..100 (общая громкость Windows не меняется)
    "mic_gain": 1.0,         # громкость микрофона для Капри: 1 — как есть, 2 — вдвое громче
    "mic_sensitivity": 100,  # 100 — слышит всё; меньше — тихий шум и фон отбрасываются
    "browser": "",
    "replies": {},           # свои ответы: {"фраза": "что ответит Капри"} — проверяются раньше команд и нейросети  # в каком браузере открывать сайты: "хром", "яндекс", "опера"… или путь к exe; "" — как в Windows
    "plugins": {},           # навыки из actions/plugins: {"time": false} — отключить конкретный плагин
    "rag_enabled": True,     # поиск по папке documents и заметкам (локальные эмбеддинги Ollama)
    "rag_model": "nomic-embed-text",  # модель эмбеддингов: ollama pull nomic-embed-text
    "rag_top_k": 3,          # сколько фрагментов подмешивать в ответ
    "rag_documents_dir": "documents",  # .md/.txt, которые индексируются
    "rag_index_dir": "index",          # сюда пишется rag_index.json
    "anthropic_api_key": "",
    "model": "claude-opus-5-5",
    "wake_words": ["капри", "каприз", "капре", "кепри", "капли"],
    "use_google_stt": True,
    "stt_backend": "vosk",       # распознавание речи: vosk (офлайн, по умолчанию) или whisper (faster-whisper)
    "stt_grammar": [],           # подсказки Vosk: список фраз; пусто — без ограничения словаря
    "stt_min_speech_ms": 300,    # фраза короче этого — шум и не попадает в команды
    "stt_whisper_model": "small",
    "stt_whisper_compute": "int8",
    "voice": "ru-RU-SvetlanaNeural",
    "tts_engine": "edge",
    "tts_cache": True,       # кэш озвучки в cache/tts: повторные фразы звучат без синтеза и сети
    "voice_rate": "+10%",
    "voice_pitch": "+15Hz",
    "voice_shift": 0,
    "voice_lively": False,
    "voice_fx": 1.8,
    "listen_window_seconds": 8,
    "followup_seconds": 6,
    "hide_after_seconds": 12,
    "greet_on_start": True,
    "vosk_model": "models/vosk-model-small-ru-0.22",
    "sites": {
        "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com",
        "гугл": "https://www.google.com", "google": "https://www.google.com",
        "яндекс": "https://ya.ru", "вк": "https://vk.com", "вконтакте": "https://vk.com",
        "почту": "https://mail.google.com", "почта": "https://mail.google.com",
        "гитхаб": "https://github.com", "твич": "https://www.twitch.tv",
        "википедию": "https://ru.wikipedia.org", "википедия": "https://ru.wikipedia.org",
        "переводчик": "https://translate.google.com", "карты": "https://yandex.ru/maps",
        "погоду": "https://yandex.ru/pogoda", "chatgpt": "https://chatgpt.com",
        "клод": "https://claude.ai", "кинопоиск": "https://www.kinopoisk.ru",
        "авито": "https://www.avito.ru", "озон": "https://www.ozon.ru",
        "вайлдберриз": "https://www.wildberries.ru"
    },
    "apps": {
        "блокнот": "notepad", "калькулятор": "calc", "проводник": "explorer",
        "диспетчер задач": "taskmgr", "параметры": "ms-settings:", "настройки": "ms-settings:",
        "панель управления": "control", "командную строку": "cmd", "командная строка": "cmd",
        "терминал": "wt", "пейнт": "mspaint", "paint": "mspaint", "ворд": "winword",
        "эксель": "excel", "браузер": "https://www.google.com",
        "настройки звука": "ms-settings:sound", "настройки экрана": "ms-settings:display",
        "корзину": "shell:RecycleBinFolder", "загрузки": "shell:Downloads"
    },
    "app_aliases": {
        "хром": "chrome", "гугл хром": "google chrome", "стим": "steam", "дискорд": "discord",
        "телеграм": "telegram", "телеграмм": "telegram", "спотифай": "spotify",
        "опера": "opera", "яндекс браузер": "yandex", "вс код": "visual studio code",
        "визуал студио код": "visual studio code", "обс": "obs", "эпик геймс": "epic games",
        "ватсап": "whatsapp", "зум": "zoom", "фотошоп": "photoshop", "майнкрафт": "minecraft"
    }
}


def _config_blank():
    """config.json нет или он пустой (всё стёрли) — такой можно спокойно создать заново."""
    return config_blank(CONFIG_PATH)


def _edit_config(change):
    """Прочитать config.json, применить change(data) и записать. Файл с ошибкой не перезаписываем —
    иначе пропадут правки пользователя. Возвращает текст ошибки или None."""
    try:
        if _config_blank():
            data = {}
        else:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:  # Блокнот иногда сохраняет с BOM
                data = json.load(f)
    except Exception:
        log.exception("config.json с ошибкой, не записываю")
        return "в config.json ошибка. Исправь её или удали файл, он создастся заново."
    change(data)
    data["config_version"] = CONFIG_VERSION  # файл всегда соответствует текущей схеме
    save_config_file(CONFIG_PATH, data)
    return None


def save_setting(key, value):
    """Записать одну настройку в config.json, не трогая остальные."""
    _edit_config(lambda data: data.__setitem__(key, value))


def save_entry(section, name, value):
    """Свой сайт или программа: section "sites"/"apps". value=None — убрать; встроенные записи
    убираются пометкой null, иначе вернулись бы из настроек по умолчанию."""
    def change(data):
        entries = data.setdefault(section, {})
        if value is None and name not in DEFAULTS.get(section, {}):
            entries.pop(name, None)
        else:
            entries[name] = value
    return _edit_config(change)


CONFIG_ERROR = ""  # что сказать вслух, если config.json не прочитался


def load_config():
    global CONFIG_ERROR
    cfg, CONFIG_ERROR = load_config_file(CONFIG_PATH, DEFAULTS)
    return cfg


STOP_RE = re.compile(r"\b(стоп|хватит|молчи|замолчи|помолчи|достаточно|подожди|остановись|перестань|"
                     r"заткнись|умолкни)\b")


def google_stt_allowed(cfg):
    """Google STT — сетевой сервис: в приватном режиме не вызываем даже при use_google_stt=true."""
    return bool(cfg.get("use_google_stt")) and not bool(cfg.get("privacy_mode"))


def fullscreen_app_active():
    """Открыта игра или видео на весь экран — тогда не лезем со спонтанными репликами."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return False
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False  # рабочий стол
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.right - r.left) >= u.GetSystemMetrics(0) and (r.bottom - r.top) >= u.GetSystemMetrics(1)
    except Exception:
        return False


class Capri(QObject):
    summon = Signal()
    vanish = Signal()
    mode = Signal(str, float)
    subtitle = Signal(str)
    quit_app = Signal()
    privacy_changed = Signal(bool)
    model_missing = Signal()
    anim = Signal(str)
    roam = Signal()
    hop = Signal()
    move_to = Signal(str)
    emotion = Signal(str)
    speech = Signal(float)      # длительность реальной озвучки — для синхронизации рта

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        words = sorted(cfg["wake_words"], key=len, reverse=True)
        self.wake_re = re.compile(r"(^|\s)(" + "|".join(map(re.escape, words)) + r")(?=\s|$|[,.!?])",
                                  re.IGNORECASE)
        self.ai = AI(cfg)
        self.local = LocalAI(cfg) if cfg.get("local_ai") else None
        if self.local:
            self.local.on_model_change = self._remember_model
        self.actions = Actions(cfg, BASE, self.ai, save=save_entry)
        self.ai_router = AIRouter(cfg, local=self.local, cloud=self.ai, offline=self._offline_answer)
        self.config_mtime = self._config_mtime()
        self.zapret_checked = False  # предлагали ли уже исправить zapret в этом сеансе
        self.actions.local = self.local
        self.speaker = Speaker(cfg)
        self.speaker.on_speech_start = self.speech.emit  # из потока речи — Qt сам поставит в очередь
        self.jobs = queue.Queue()
        self.scheduler = Scheduler(os.path.join(BASE, "reminders.json"), self._on_reminder)
        self.notes = Notes(os.path.join(BASE, "notes.json"))
        self.actions.scheduler = self.scheduler
        self.actions.notes = self.notes
        self.rag = None
        if cfg.get("rag_enabled", True):
            self.rag = RagIndex(
                BASE,
                docs_dir=os.path.join(BASE, cfg.get("rag_documents_dir") or "documents"),
                index_dir=os.path.join(BASE, cfg.get("rag_index_dir") or "index"),
                model=cfg.get("rag_model") or "nomic-embed-text",
                top_k=cfg.get("rag_top_k", 3),
                notes=self.notes,
            )
            self.rag.on_indexed = self._on_rag_indexed
            self.actions.rag = self.rag
        self.awake_until = 0.0
        self.last_partial_wake = 0.0
        self.speaking = False
        self.interrupted = False
        self.speech_words = set()
        self.speech_end = 0.0
        self.last_activity = time.time()
        self.recent_chatter = []
        self.listener = None
        self._chatter_started = False
        self.glitch_wav = os.path.join(tempfile.gettempdir(), "capri_glitch.wav")
        try:
            make_glitch_sound(self.glitch_wav, self.speaker.volume)
        except Exception:
            self.glitch_wav = None

    def start(self):
        log.info("Режим приватности: %s", "включён" if self.cfg.get("privacy_mode") else "выключен")
        threading.Thread(target=self._worker, daemon=True).start()
        self.scheduler.start()  # просроченные озвучатся один раз при старте
        if self.local:
            threading.Thread(target=self.local.start_server, daemon=True).start()
        if self.rag:
            threading.Thread(target=self.rag.index_if_needed, daemon=True).start()
        if not self._start_listener():
            log.error("Модель распознавания не найдена: %s", self._model_path())
            self.jobs.put(("say", line("no_model")))
            self.model_missing.emit()  # main предложит скачать модель
            return
        # вступление: анимация capri + приветствие
        self.jobs.put(("say", line("greet") if self.cfg.get("greet_on_start") else "", "capri"))
        if CONFIG_ERROR:
            self.jobs.put(("say", CONFIG_ERROR))
        self._start_chatter()

    def _model_path(self):
        return os.path.join(BASE, self.cfg["vosk_model"])

    def _start_listener(self):
        """Создать и запустить слушателя. False — нет модели Vosk (Whisper её не требует)."""
        model = self._model_path()
        backend = self.cfg.get("stt_backend") or "vosk"
        if backend_kind(backend) == "vosk":
            if not os.path.isdir(model):
                return False
            log.info("Модель распознавания: %s", model)
        log.info("Распознавание речи: %s", backend_kind(backend))
        self.listener = Listener(model, self.on_final, self.on_partial, self.on_listener_error,
                                 device_name=self.cfg.get("mic_device") or None,
                                 gain=float(self.cfg.get("mic_gain", 1.0)),
                                 sensitivity=int(self.cfg.get("mic_sensitivity", 100)),
                                 backend=backend,
                                 grammar=self.cfg.get("stt_grammar") or None,
                                 min_speech_ms=int(self.cfg.get("stt_min_speech_ms", 300)),
                                 whisper={"model": self.cfg.get("stt_whisper_model", "small"),
                                          "compute_type": self.cfg.get("stt_whisper_compute", "int8")})
        self.listener.start()
        return True

    def _start_chatter(self):
        if self._chatter_started:  # после установки модели не запускаем второй поток
            return
        self._chatter_started = True
        threading.Thread(target=self._chatter_loop, daemon=True).start()  # сам проверяет, включено ли

    def download_model(self):
        """Первое скачивание модели распознавания речи — по согласию пользователя, в фоне."""
        threading.Thread(target=self._install_model, daemon=True).start()

    def _install_model(self):
        self.jobs.put(("say", line("model_downloading")))
        try:
            path = model_setup.install_model(BASE, log=log.info)
        except Exception as e:
            log.exception("Модель распознавания не установилась")
            self.jobs.put(("say", line("model_download_failed", e=e)))
            return
        log.info("Модель распознавания установлена: %s", path)
        if not self._start_listener():
            self.jobs.put(("say", line("no_model")))
            return
        self.jobs.put(("say", line("model_ready")))
        self.jobs.put(("say", line("greet") if self.cfg.get("greet_on_start") else "", "capri"))
        self._start_chatter()


    # ----- спонтанные появления -----
    def _chatter_loop(self):
        def next_delay():
            lo, hi = self.cfg["chatter_min_minutes"], self.cfg["chatter_max_minutes"]
            return random.uniform(lo, max(lo, hi)) * 60
        due = time.time() + next_delay()
        while True:
            time.sleep(15)
            now = time.time()
            if not self.cfg.get("chatter"):  # выключено галочкой в трее — ждём, отсчёт начнётся заново
                due = now + next_delay()
                continue
            if now < due:
                continue
            quiet = now - self.last_activity > 120 and self.jobs.empty() and not self.speaking
            if quiet and not fullscreen_app_active():
                self.jobs.put(("chatter",))
                due = now + next_delay()
            else:
                due = now + 60  # занят или в игре/фильме — попробуем позже

    def chatter_line(self):
        hour = time.localtime().tm_hour
        text = None
        if 0 <= hour < 5 and random.random() < 0.5:
            text = line("chatter_night")
        elif 6 <= hour < 11 and random.random() < 0.3:
            text = line("chatter_morning")
        elif self.local and random.random() < 0.4:
            text = self.local.chatter()
        for _ in range(5):
            if text and text not in self.recent_chatter:
                break
            text = line("chatter")
        self.recent_chatter = (self.recent_chatter + [text])[-6:]
        return text

    # ----- события микрофона (поток слушателя) -----
    def has_wake(self, text):
        return bool(self.wake_re.search(text.lower()))

    def strip_wake(self, text):
        t = text.lower()
        last = None
        for last in self.wake_re.finditer(t):
            pass
        if last and t[last.end():].strip(" ,.!?"):
            t = t[last.end():]  # берём то, что сказано после имени (до него могло быть эхо)
        t = self.wake_re.sub(" ", t)
        return re.sub(r"^[\s,.!?]+|[\s,.!?]+$", "", t)

    def appear(self, seconds):
        if self.glitch_wav:
            play_sound_async(self.glitch_wav)
        self.summon.emit()
        self.mode.emit("listening", float(seconds))

    # ----- перебивание -----
    def is_echo(self, text):
        """Микрофон слышит голос самой Капри: все слова фразы есть в том, что она говорит."""
        words = text.lower().replace("ё", "е").split()
        return bool(words) and all(w in self.speech_words for w in words)

    def wants_interrupt(self, text):
        t = text.lower()
        return (self.has_wake(t) or STOP_RE.search(t)) and not self.is_echo(t)

    def interrupt(self):
        if not self.speaking or self.interrupted:
            return
        self.interrupted = True
        self.speaker.stop()
        self.awake_until = time.time() + self.cfg["listen_window_seconds"]
        self.mode.emit("listening", float(self.cfg["listen_window_seconds"]))
        self.subtitle.emit(line("listen"))
        log.info("Капри перебили")

    def on_partial(self, text):
        now = time.time()
        if self.speaking:
            if self.cfg.get("barge_in") and self.wants_interrupt(text):
                self.interrupt()
            return
        if now - self.last_partial_wake > 3 and now > self.awake_until and self.has_wake(text):
            self.last_partial_wake = now
            self.appear(self.cfg["listen_window_seconds"])

    def on_final(self, text, audio):
        now = time.time()
        if self.actions.autoscroll_active() and STOP_RE.search(text.lower()) and not self.is_echo(text):
            # во время автопрокрутки «стоп» слышим и без имени
            self.actions.stop_background()
            self.subtitle.emit(line("scroll_stop"))
            return
        if self.speaking and not self.interrupted:
            if not (self.cfg.get("barge_in") and self.wants_interrupt(text)):
                return  # эхо собственной речи
            self.interrupt()
        if now - self.speech_end < 1.0 and self.is_echo(text):
            return
        woke = self.has_wake(text)
        if not woke and now > self.awake_until:
            return
        cmd = self.strip_wake(text) if woke else text
        if STOP_RE.match(cmd.strip()) and len(cmd.split()) <= 3:
            # «Капри, стоп» — замолчать, остановить автопрокрутку и слушать дальше
            self.awake_until = now + self.cfg["listen_window_seconds"]
            if self.actions.stop_background():
                self.subtitle.emit(line("scroll_stop"))
            return
        if len(cmd) < 2:
            self.awake_until = now + self.cfg["listen_window_seconds"]
            self.subtitle.emit("")
            if now - self.last_partial_wake > 3:
                self.appear(self.cfg["listen_window_seconds"])
            return
        self.awake_until = 0
        self.jobs.put(("voice", cmd, audio, woke))

    def on_listener_error(self, err, kind="mic"):
        self.jobs.put(("say", line("model_broken" if kind == "model" else "no_mic")))

    def _on_reminder(self, text):
        """Планировщик сработал — озвучиваем в рабочем потоке, а не в его собственном."""
        self.jobs.put(("say", line("reminder_due", t=text)))

    def _on_rag_indexed(self, count):
        """Индекс документов перестроен по команде — сообщаем вслух."""
        self.jobs.put(("say", line("rag_indexed", n=count)))

    def _offline_answer(self, cmd):
        """Последняя ступень цепочки ИИ: поиск или честное «не знаю».
        В приватном режиме поиск в браузере не открываем — это сетевой запрос."""
        reply = self.actions.fallback(cmd, force_search=bool(self.local),
                                      allow_search=not self.cfg.get("privacy_mode"))
        return reply.text if reply else None

    def shutdown(self):
        """Корректно останавливаем фоновые потоки при выходе."""
        try:
            self.scheduler.stop()
        except Exception:
            log.exception("Не удалось остановить планировщик напоминаний")

    def set_chatter(self, on):
        """Галочка в трее: появляется ли Капри сама (поболтать, погулять по экрану). Сразу и запоминается."""
        self.cfg["chatter"] = bool(on)
        save_setting("chatter", bool(on))
        log.info("Самостоятельные появления: %s", "включены" if on else "выключены")

    def set_search_when_unsure(self, on):
        """Галочка в трее: открывать ли Google, когда Капри не знает ответа. Сразу и запоминается."""
        self.cfg["search_when_unsure"] = bool(on)
        save_setting("search_when_unsure", bool(on))
        log.info("Поиск в браузере, если не знаю: %s", "включён" if on else "выключен")

    def set_privacy(self, on, announce=True, persist=True):
        """Режим приватности: только офлайн. Работает без перезапуска; в лог — только факт режима."""
        on = bool(on)
        changed = on != bool(self.cfg.get("privacy_mode"))
        self.cfg["privacy_mode"] = on
        self.speaker.privacy = on
        if changed:
            log.info("Режим приватности: %s", "включён" if on else "выключен")
            self.privacy_changed.emit(on)
        if persist:
            save_setting("privacy_mode", on)
            self.config_mtime = self._config_mtime()  # своя же запись — не перечитывать зря
        if announce:
            self.jobs.put(("say", line("privacy_on" if on else "privacy_off")))

    def _remember_model(self, name):
        """Ollama нашла другую скачанную модель — записываем её, чтобы не искать при каждом запуске."""
        self.cfg["local_model"] = name
        save_setting("local_model", name)

    # ----- config.json на лету -----
    @staticmethod
    def _config_mtime():
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return None

    def reload_config_if_changed(self, speak=True):
        """Правки config.json (сайты, программы, названия) подхватываются без перезапуска.
        speak=False — из окна в трее: там говорить нельзя (речь блокирует окно)."""
        mtime = self._config_mtime()
        if mtime == self.config_mtime:
            return
        fresh = load_config()
        if CONFIG_ERROR:  # с ошибкой — работаем как было и говорим, где ошибка
            if speak:
                self.config_mtime = mtime
                self.say(CONFIG_ERROR)
            return
        self.config_mtime = mtime
        for key in ("sites", "apps", "app_aliases", "search_when_unsure", "browser", "chatter", "replies",
                    "plugins"):
            self.cfg[key] = fresh[key]
        self.actions.set_lists(self.cfg)
        self.actions.reload_plugins(self.cfg.get("plugins"))
        privacy = bool(fresh.get("privacy_mode"))
        if privacy != bool(self.cfg.get("privacy_mode")):
            self.set_privacy(privacy, announce=speak, persist=False)  # уже записано в файле
        else:
            self.speaker.privacy = privacy
        log.info("config.json перечитан")

    # ----- кнопки -----
    # ----- микрофон -----
    def current_mic(self):
        if self.listener and self.listener.active_name:
            return self.listener.active_name
        return self.cfg.get("mic_device") or default_input_name()

    def set_mic(self, name, announce=True):
        """Сменить устройство ввода на ходу и запомнить выбор (None — как в Windows по умолчанию)."""
        self.cfg["mic_device"] = name
        save_setting("mic_device", name)
        if self.listener:
            self.listener.set_device(name)
        log.info("Выбран микрофон: %s", name or "по умолчанию")
        if announce:
            self.jobs.put(("say", line("mic_set", t=name or "микрофон Windows по умолчанию")))

    def set_mic_tuning(self, gain=None, sensitivity=None):
        """Громкость ввода и чувствительность микрофона для Капри — сразу и запоминаются."""
        if gain is not None:
            self.cfg["mic_gain"] = round(float(gain), 2)
            save_setting("mic_gain", self.cfg["mic_gain"])
            if self.listener:
                self.listener.gain = self.cfg["mic_gain"]
        if sensitivity is not None:
            self.cfg["mic_sensitivity"] = int(sensitivity)
            save_setting("mic_sensitivity", self.cfg["mic_sensitivity"])
            if self.listener:
                self.listener.sensitivity = self.cfg["mic_sensitivity"]

    def set_voice_fx(self, strength):
        """Сила цифровых эффектов голоса: 0 — чистый голос, 1.8 — как задумано, больше — сильнее."""
        self.speaker.fx = round(float(strength), 2)
        self.cfg["voice_fx"] = self.speaker.fx
        save_setting("voice_fx", self.speaker.fx)

    def set_voice_volume(self, volume):
        """Громкость голоса Капри 0..100 — только её, общая громкость Windows не меняется."""
        self.speaker.volume = int(volume)
        self.cfg["voice_volume"] = int(volume)
        save_setting("voice_volume", int(volume))
        if self.glitch_wav:
            try:
                make_glitch_sound(self.glitch_wav, self.speaker.volume)
            except Exception:
                pass

    def mic_command(self, action):
        if action == "mic_info":
            return Reply(line("mic_info", t=self.current_mic()))
        devices = [n for n, _ in input_devices()]
        if len(devices) < 2:
            return Reply(line("mic_only_one", t=self.current_mic()))
        cur = self.cfg.get("mic_device") or self.current_mic()
        idx = next((i for i, n in enumerate(devices) if n == cur or cur.startswith(n) or n.startswith(cur)), -1)
        nxt = devices[(idx + 1) % len(devices)]
        self.set_mic(nxt, announce=False)
        return Reply(line("mic_set", t=nxt))

    def call_from_click(self):
        if self.speaking:
            self.interrupt()
            return
        self.awake_until = time.time() + self.cfg["listen_window_seconds"]
        self.appear(self.cfg["listen_window_seconds"])
        self.subtitle.emit(line("listen"))

    def text_command(self, text):
        self.summon.emit()
        self.jobs.put(("text", text))

    # ----- рабочий поток -----
    def _worker(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while True:
            job = self.jobs.get()
            try:
                self._process(job)
            except Exception:
                log.exception("Ошибка обработки %s", job[0])
                try:
                    self.say(line("crash"), "error")
                except Exception:
                    log.exception("Не удалось озвучить сообщение об ошибке")

    def _process(self, job):
        kind = job[0]
        if kind == "chatter":
            text = self.chatter_line()
            if random.random() < self.cfg.get("roam_chance", 0.5):
                self.roam.emit()          # гуляет по экрану, окно с текстом сверху
                time.sleep(1.5)
            else:
                self.summon.emit()
            if not self.say(text):
                self.mode.emit("idle", 0.0)
            return
        self.last_activity = time.time()
        if kind == "say":
            self.summon.emit()
            if len(job) > 2:
                self.anim.emit(job[2])
            if not job[1]:
                self.mode.emit("idle", 0.0)
                return
            if not self.say(job[1]):
                self.mode.emit("idle", 0.0)
            return
        if kind == "voice":
            _, cmd, audio, woke = job
            self.summon.emit()
            self.mode.emit("thinking", 0.0)
            self.subtitle.emit("> " + cmd)
            if google_stt_allowed(self.cfg):
                better = google_stt(audio)
                if better:
                    better = better.lower()
                    if len(better) >= 2 and better != cmd:
                        log.info("Google STT уточнил: %s", better)
                        cmd = better
        else:
            cmd = job[1]
        if self.has_wake(cmd):
            # имя могло остаться в тексте («капри нажми …»), если Капри уже слушала без него
            stripped = self.strip_wake(cmd)
            if len(stripped) < 2:
                # просто позвали по имени — не болтаем, а слушаем
                self.awake_until = time.time() + self.cfg["listen_window_seconds"]
                self.mode.emit("listening", float(self.cfg["listen_window_seconds"]))
                self.subtitle.emit(line("listen"))
                return
            cmd = stripped
        log.info("Услышал: %s", cmd)
        self.mode.emit("thinking", 0.0)
        self.subtitle.emit("> " + cmd)
        self.reload_config_if_changed()

        reply, source = None, "command"
        try:
            reply = self.actions.handle(cmd)
        except Exception as e:
            log.exception("Ошибка выполнения команды: %s", cmd)
            reply = Reply(line("error", e=e), None, "error")
        if reply is not None and reply.action in ("mic_info", "mic_next"):
            reply = self.mic_command(reply.action)
        if reply is None:
            answer, source = self.ai_router.ask(cmd)
            if answer:
                reply = Reply(answer, "answer")
            else:
                source = "fallback"
                reply = self.actions.fallback(cmd, force_search=bool(self.local),
                                              allow_search=not self.cfg.get("privacy_mode"))
        log.info("Интент: %s; источник ответа: %s", self.actions.last_intent or "none", source)
        if reply.action == "answer" and self.cfg.get("teleport_on_answer"):
            self.hop.emit()  # отвечает, скача по экрану красной молнией
        if reply.action == "wave":
            self.anim.emit("capri")
        elif reply.action and reply.action.startswith("move:"):
            self.move_to.emit(reply.action[5:])
        elif reply.action == "roam":
            self.vanish.emit()
            time.sleep(0.8)
            self.roam.emit()
            time.sleep(1.5)
        emotion = getattr(reply, "emotion", "neutral") or "neutral"
        if emotion == "neutral" and reply.action in ("answer", "wave"):
            emotion = "joy"  # удачный ответ или приветствие — аватар радуется
        if self.say(reply.text, emotion):
            return  # перебили — уже слушаем новую команду

        if reply.action == "quit":
            self.quit_app.emit()
        elif reply.action == "hide":
            self.vanish.emit()
        else:
            follow = float(self.cfg["followup_seconds"])
            self.awake_until = time.time() + follow
            self.mode.emit("listening", follow)

    def say(self, text, emotion="neutral"):
        """Озвучивает текст. Возвращает True, если Капри перебили."""
        log.info("Капри: %s", text)
        self.emotion.emit(emotion or "neutral")
        self.subtitle.emit(text)
        self.mode.emit("speaking", 0.0)
        barge_in = self.cfg.get("barge_in") and self.listener
        if self.listener and not barge_in:
            self.listener.muted.set()
        self.speech_words = set(re.findall(r"\w+", text.lower().replace("ё", "е")))
        self.interrupted = False
        self.speaking = True
        try:
            self.speaker.say(text)
        finally:
            self.speaking = False
            self.speech_end = time.time()
            if barge_in and not self.interrupted:
                self.listener.reset_req.set()  # выбрасываем эхо её голоса
            if self.listener and not barge_in:
                time.sleep(0.25)
                self.listener.muted.clear()
        self._check_voice_blocked()
        return self.interrupted

    def _check_voice_blocked(self):
        """Голос Microsoft не прошёл из-за сети, а запущен zapret — один раз за сеанс объясняем
        и предлагаем добавить сервер голоса в исключения zapret."""
        err = self.speaker.edge_error
        if self.zapret_checked or not err or not zapret.NETWORK_ERROR.search(err) or not zapret.running():
            return
        self.zapret_checked = True
        lists = zapret.find_lists()
        if not lists:
            self.jobs.put(("say", line("zapret_manual")))
        elif zapret.excluded(lists):
            self.jobs.put(("say", line("zapret_restart")))
        else:
            def fix():
                try:
                    zapret.add_exclude(lists)
                except OSError as e:
                    log.exception("Не удалось дописать исключение zapret")
                    return Reply(line("zapret_fail", e=e))
                log.info("bing.com добавлен в исключения zapret: %s", lists)
                return Reply(line("zapret_fixed"))
            self.actions.pending = fix
            self.awake_until = time.time() + 20  # ответ «да» без имени
            self.jobs.put(("say", line("zapret_offer")))


def tray_icon(privacy=False):
    """Иконка в трее. В приватном режиме — зелёная с белой меткой-щитом."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(40, 200, 130) if privacy else QColor(255, 34, 96))
    p.setPen(QPen(QColor(30, 220, 160) if privacy else QColor(150, 70, 255), 4))
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QColor(6, 24, 20))
    p.setPen(QPen(QColor(40, 255, 150), 4))
    p.drawEllipse(14, 22, 36, 20)
    p.setBrush(QColor(40, 255, 150))
    p.drawEllipse(27, 27, 10, 10)
    if privacy:
        p.setBrush(QColor(240, 255, 250))
        p.setPen(QPen(QColor(20, 120, 80), 2))
        p.drawEllipse(46, 2, 16, 16)  # метка приватного режима
    p.end()
    return QIcon(pm)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    cfg = load_config()

    avatar = Avatar(resource_path("assets"))
    avatar.hide_after = float(cfg["hide_after_seconds"])
    capri = Capri(cfg)

    def ask_model():
        """Модели нет: объясняем и предлагаем скачать (клик пользователя — согласие на сеть)."""
        box = QMessageBox(QMessageBox.Icon.Question, "Капри",
                          "Не найдена модель распознавания речи (около 45 МБ).\n"
                          "Без неё я не слышу команды. Скачать сейчас?")
        yes = box.addButton("Скачать", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            capri.download_model()

    capri.model_missing.connect(ask_model)
    capri.summon.connect(avatar.summon)
    capri.vanish.connect(avatar.vanish)
    capri.mode.connect(avatar.set_mode)
    capri.subtitle.connect(avatar.set_subtitle)
    capri.quit_app.connect(app.quit)
    capri.anim.connect(avatar.play_anim)
    capri.roam.connect(avatar.start_roam)
    capri.hop.connect(avatar.start_hopping)
    capri.move_to.connect(avatar.move_to_named)
    capri.emotion.connect(avatar.set_emotion)
    capri.speech.connect(avatar.set_speech)
    avatar.clicked.connect(capri.call_from_click)
    app.aboutToQuit.connect(capri.shutdown)

    def ask_text():
        text, ok = QInputDialog.getText(None, "Капри", "Команда или вопрос:")
        if ok and text.strip():
            capri.text_command(text.strip())

    windows = {}  # открытые окна держим, пока открыты (иначе их соберёт сборщик мусора); одно окно каждого вида

    def show_window(kind, make):
        old = windows.get(kind)
        if old is not None and old.isVisible():
            old.close()
        dlg = windows[kind] = make()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_entries():
        from entries_dialog import EntriesDialog
        capri.reload_config_if_changed(speak=False)
        show_window("entries", lambda: EntriesDialog(capri.actions))

    def open_settings(tab="voice"):
        from settings_dialog import SettingsDialog
        capri.reload_config_if_changed(speak=False)
        show_window("settings", lambda: SettingsDialog(capri))
        dlg = windows.get("settings")
        if dlg is not None:
            dlg.open_tab(tab)

    menu = QMenu()
    for title, fn in (("Позвать Капри", capri.call_from_click),
                      ("Замолчать", capri.interrupt),
                      ("Написать команду…", ask_text),
                      ("Настройки…", open_settings),
                      ("Мои сайты, программы и ответы…", open_entries),
                      ("Проверить ПК", lambda: capri.text_command("проверь компьютер")),
                      ("Спрятать", avatar.vanish),
                      ("MIC", None),
                      ("SEARCH", None),
                      ("Открыть config.json", lambda: os.startfile(CONFIG_PATH)),
                      (None, None),
                      ("Выход", app.quit)):
        if title is None:
            menu.addSeparator()
            continue
        if title == "MIC":
            mic_menu = menu.addMenu("Микрофон")

            def fill_mics(m=mic_menu):
                """Список устройств ввода строится при каждом открытии — чтобы видеть только что подключённые."""
                m.clear()
                chosen = capri.cfg.get("mic_device")
                options = [(None, f"По умолчанию (как в Windows: {default_input_name()})")]
                try:
                    options += [(n, n) for n, _ in input_devices()]
                except Exception:
                    log.exception("Не удалось получить список микрофонов")
                for value, label in options:
                    a = QAction(label, m)
                    a.setCheckable(True)
                    a.setChecked(value == chosen or (value is None and not chosen))
                    a.triggered.connect(lambda _=False, v=value: capri.set_mic(v))
                    m.addAction(a)
                m.addSeparator()
                tune = QAction("Громкость и чувствительность…", m)
                tune.triggered.connect(lambda: open_settings("voice"))
                m.addAction(tune)
            mic_menu.aboutToShow.connect(fill_mics)

            # «Громкость Капри» — ползунок прямо в меню, чтобы не лезть в микшер громкости Windows
            vol_menu = menu.addMenu("Громкость Капри")
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(10, 6, 10, 6)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setMinimumWidth(180)
            value = QLabel()
            value.setMinimumWidth(36)
            row.addWidget(slider)
            row.addWidget(value)
            slider.valueChanged.connect(lambda v, lbl=value: lbl.setText(f"{v}%"))
            slider.sliderReleased.connect(lambda s=slider: capri.set_voice_volume(s.value()))
            slider.valueChanged.connect(lambda v, s=slider: None if s.isSliderDown() else capri.set_voice_volume(v))
            wa = QWidgetAction(vol_menu)
            wa.setDefaultWidget(box)
            vol_menu.addAction(wa)

            def sync_volume(s=slider, lbl=value):
                s.blockSignals(True)
                s.setValue(int(capri.cfg.get("voice_volume", 100)))
                s.blockSignals(False)
                lbl.setText(f"{s.value()}%")
            vol_menu.aboutToShow.connect(sync_volume)
            continue
        if title == "SEARCH":
            # галочка: открывать ли Google, когда Капри не знает ответа (search_when_unsure)
            search_act = QAction("Искать в браузере, если не знаю", menu)
            search_act.setCheckable(True)
            search_act.toggled.connect(capri.set_search_when_unsure)
            menu.addAction(search_act)

            # галочка: появляется ли Капри сама время от времени (chatter)
            chatter_act = QAction("Появляться самой", menu)
            chatter_act.setCheckable(True)
            chatter_act.toggled.connect(capri.set_chatter)
            menu.addAction(chatter_act)

            # режим приватности: только офлайн, без сети
            privacy_act = QAction("Режим приватности", menu)
            privacy_act.setCheckable(True)
            privacy_act.toggled.connect(capri.set_privacy)
            menu.addAction(privacy_act)

            def sync_search(a=search_act, b=chatter_act, d=privacy_act):
                capri.reload_config_if_changed(speak=False)  # могли поменять в config.json руками
                for act, key, default in ((a, "search_when_unsure", True), (b, "chatter", True),
                                          (d, "privacy_mode", False)):
                    act.blockSignals(True)
                    act.setChecked(bool(capri.cfg.get(key, default)))
                    act.blockSignals(False)
            menu.aboutToShow.connect(sync_search)
            continue
        act = QAction(title, menu)
        act.triggered.connect(fn)
        menu.addAction(act)

    tray = QSystemTrayIcon(tray_icon(bool(cfg.get("privacy_mode"))))
    tray.setToolTip("Капри — голосовой ассистент")
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: capri.call_from_click()
                           if r == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()

    def apply_privacy_icon(on):
        tray.setIcon(tray_icon(bool(on)))
        tray.setToolTip("Капри — приватный режим (только офлайн)" if on else "Капри — голосовой ассистент")
    capri.privacy_changed.connect(apply_privacy_icon)
    apply_privacy_icon(bool(cfg.get("privacy_mode")))

    avatar.menu_requested.connect(lambda pos: menu.popup(pos))

    capri.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
