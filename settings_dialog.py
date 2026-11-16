"""Единое окно настроек Капри (меню в трее → «Настройки…»).

Вкладки: Голос · ИИ · Поведение · Напоминания и заметки · О программе.
Всё, что можно применить без перезапуска, применяется сразу; остальное честно подписано.
Сохранение идёт через те же set_* методы, что и раньше, поэтому config.json остаётся
прежним и после записи проходит миграцию (config_schema).
"""
import datetime
import os
import re
import sys

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from actions.plugins import load_plugins
from entries_dialog import STYLE
from scheduler import format_when
from voice import GATE_MAX, input_devices

try:
    from version import __version__ as VERSION
except Exception:  # до появления version.py
    VERSION = "dev"

BASE = os.path.dirname(os.path.abspath(__file__))
PINK, GREEN, DIM = QColor(255, 34, 96), QColor(40, 255, 150), QColor(90, 60, 90)
RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
PITCH_RE = re.compile(r"^[+-]\d{1,3}Hz$")
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")


def check_values(values):
    """Проверка полей перед сохранением. Возвращает список понятных ошибок (пусто — всё хорошо)."""
    errors = []
    if values.get("local_ai") and not (values.get("local_model") or "").strip():
        errors.append("Укажи модель Ollama или выключи локальную нейросеть.")
    if values.get("enable_ai"):
        if not (values.get("anthropic_api_key") or "").strip():
            errors.append("Для Claude нужен ключ Anthropic API.")
        if not (values.get("model") or "").strip():
            errors.append("Укажи модель Claude.")
    if not RATE_RE.match(values.get("voice_rate") or ""):
        errors.append("Скорость речи — в формате +10% или -5%.")
    if not PITCH_RE.match(values.get("voice_pitch") or ""):
        errors.append("Высота голоса — в формате +15Hz или -10Hz.")
    if int(values.get("chatter_min_minutes", 0)) > int(values.get("chatter_max_minutes", 0)):
        errors.append("«Появляться самой»: минимум больше максимума.")
    if values.get("stt_backend") not in ("vosk", "whisper"):
        errors.append("Распознавание речи: выбери vosk или whisper.")
    if values.get("stt_whisper_model") not in WHISPER_MODELS:
        errors.append("Модель Whisper: " + ", ".join(WHISPER_MODELS) + ".")
    return errors


def parse_grammar(text):
    """Строка из окна настроек → список фраз-подсказок Vosk (разделитель — точка с запятой)."""
    return [part.strip() for part in (text or "").split(";") if part.strip()]


class LevelMeter(QWidget):
    """Полоска громкости с микрофона: всё, что правее розовой отметки, Капри слышит."""

    def __init__(self):
        super().__init__()
        self.level = 0.0
        self.gate = 0.0
        self.setMinimumHeight(20)

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.fillRect(r, QColor(18, 8, 24))
        w = r.width() * min(1.0, self.level)
        p.fillRect(QRectF(r.left(), r.top(), w, r.height()), GREEN if self.level >= self.gate else DIM)
        x = r.left() + r.width() * self.gate
        p.fillRect(QRectF(x - 1, r.top(), 3, r.height()), PINK)
        p.setPen(PINK)
        p.drawRect(r)
        p.end()


def _slider(lo, hi, value):
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(int(value))
    return s


def _combo(items, current=None, editable=False):
    box = QComboBox()
    box.setEditable(editable)
    for label, data in items:
        box.addItem(label, data)
    if current is not None:
        idx = box.findData(current)
        if idx < 0 and editable and isinstance(current, str):
            box.addItem(current, current)
            idx = box.count() - 1
        if idx >= 0:
            box.setCurrentIndex(idx)
    return box


def _check(text, value):
    box = QCheckBox(text)
    box.setChecked(bool(value))
    return box


def _spin(lo, hi, value, suffix="", step=1):
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setValue(int(value))
    if suffix:
        box.setSuffix(" " + suffix)
    return box


class SettingsDialog(QDialog):
    def __init__(self, capri):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.capri = capri
        self.cfg = capri.cfg
        self.setWindowTitle("Капри — настройки")
        self.setStyleSheet(STYLE + "QSlider::groove:horizontal { height: 6px; background: #3a1a3a; }"
                                   "QSlider::handle:horizontal { width: 14px; margin: -5px 0; background: #ff2260; }"
                                   "QSlider::sub-page:horizontal { background: #ff2260; }"
                                   "QListWidget, QSpinBox, QDoubleSpinBox, QComboBox { background: #120818;"
                                   " color: #ffd2e1; border: 1px solid #5a2a50; }")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._voice_tab(), "Голос")
        self.tabs.addTab(self._ai_tab(), "ИИ")
        self.tabs.addTab(self._behavior_tab(), "Поведение")
        self.tabs.addTab(self._memory_tab(), "Напоминания и заметки")
        self.tabs.addTab(self._about_tab(), "О программе")

        self.status = QLabel("Изменения сохраняются в config.json. Голос, микрофон и галочки — сразу.")
        self.status.setWordWrap(True)
        apply = QPushButton("Применить")
        ok = QPushButton("ОК")
        cancel = QPushButton("Отмена")
        apply.clicked.connect(self.apply)
        ok.clicked.connect(self._ok)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.status, 1)
        row.addWidget(apply)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addLayout(row)
        self.resize(700, 620)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(80)

    # ----- вкладки -----
    def _voice_tab(self):
        cfg = self.cfg
        page = QWidget()
        lay = QVBoxLayout(page)

        mic = QGroupBox("Микрофон")
        form = QFormLayout(mic)
        options = [(f"По умолчанию ({self.capri.current_mic()})", None)]
        try:
            options += [(name, name) for name, _ in input_devices()]
        except Exception:
            pass
        self.mic = _combo(options, cfg.get("mic_device"))
        self.gain = _slider(50, 400, float(cfg.get("mic_gain", 1.0)) * 100)
        self.sens = _slider(0, 100, cfg.get("mic_sensitivity", 100))
        self.meter = LevelMeter()
        self.mic_hint = QLabel("Скажи что-нибудь — полоска должна заходить за розовую отметку.")
        self.mic_hint.setWordWrap(True)
        check_mic = QPushButton("Проверить микрофон")
        check_mic.clicked.connect(self._check_mic)
        form.addRow("Устройство:", self.mic)
        form.addRow("Громкость:", self.gain)
        form.addRow("Чувствительность:", self.sens)
        form.addRow(self.meter)
        form.addRow(self.mic_hint)
        form.addRow(check_mic)
        lay.addWidget(mic)

        voice = QGroupBox("Голос")
        form = QFormLayout(voice)
        self.volume = _slider(0, 100, cfg.get("voice_volume", 100))
        self.fx = _slider(0, 250, float(cfg.get("voice_fx", 1.8)) * 100)
        self.engine = _combo([("Microsoft (интернет)", "edge"), ("Silero (офлайн)", "silero")],
                             cfg.get("tts_engine", "edge"))
        voices = [(name, name) for name in ("ru-RU-SvetlanaNeural", "ru-RU-DariyaNeural",
                                            "ru-RU-DmitryNeural")]
        self.voice = _combo(voices, cfg.get("voice", "ru-RU-SvetlanaNeural"), editable=True)
        self.rate = _combo([("+0%", "+0%"), ("+10%", "+10%"), ("+20%", "+20%"), ("-10%", "-10%")],
                           cfg.get("voice_rate", "+10%"), editable=True)
        self.pitch = _combo([("+0Hz", "+0Hz"), ("+15Hz", "+15Hz"), ("+30Hz", "+30Hz"), ("-10Hz", "-10Hz")],
                            cfg.get("voice_pitch", "+15Hz"), editable=True)
        silero_voices = [(name, name) for name in ("xenia", "kseniya", "baya", "aidar")]
        self.silero_voice = _combo(silero_voices, cfg.get("silero_voice", "xenia"), editable=True)
        self.silero_pitch = _combo([("высокий", "high"), ("обычный", "medium"), ("низкий", "low")],
                                   cfg.get("silero_pitch", "high"))
        self.lively = _check("Живая интонация (высота у каждой фразы своя)", cfg.get("voice_lively", False))
        self.cache = _check("Кэшировать озвучку (повторные фразы звучат без синтеза)", cfg.get("tts_cache", True))
        test = QPushButton("Проверить голос")
        test.clicked.connect(lambda: self.capri.jobs.put(
            ("say", "Проверка голоса. Так меня слышно? Если нет — перезапусти Капри.")))
        form.addRow("Громкость Капри:", self.volume)
        form.addRow("Эффекты:", self.fx)
        form.addRow("Синтезатор:", self.engine)
        form.addRow("Голос:", self.voice)
        form.addRow("Скорость:", self.rate)
        form.addRow("Высота:", self.pitch)
        form.addRow("Silero: голос", self.silero_voice)
        form.addRow("Silero: высота", self.silero_pitch)
        form.addRow(self.lively)
        form.addRow(self.cache)
        form.addRow(test)
        lay.addWidget(voice)

        stt = QGroupBox("Распознавание речи")
        form = QFormLayout(stt)
        self.stt_backend = _combo([("Vosk (офлайн, по умолчанию)", "vosk"),
                                   ("Whisper (faster-whisper, точнее)", "whisper")],
                                  cfg.get("stt_backend", "vosk"))
        self.stt_whisper = _combo([(name, name) for name in WHISPER_MODELS],
                                  cfg.get("stt_whisper_model", "small"))
        self.stt_min = _spin(0, 5000, cfg.get("stt_min_speech_ms", 300), "мс", 50)
        self.stt_grammar = QLineEdit("; ".join(cfg.get("stt_grammar") or []))
        self.stt_grammar.setPlaceholderText("например: открой ютуб; громкость; который час")
        form.addRow("Движок:", self.stt_backend)
        form.addRow("Модель Whisper:", self.stt_whisper)
        form.addRow("Минимум речи:", self.stt_min)
        form.addRow("Подсказки Vosk:", self.stt_grammar)
        hint = QLabel("Подсказки — через точку с запятой. Пустое поле — распознавать без ограничений.\n"
                      "Смена движка и подсказок начнёт работать после перезапуска.")
        hint.setWordWrap(True)
        form.addRow(hint)
        lay.addWidget(stt)
        lay.addStretch()
        return page

    def _ai_tab(self):
        cfg = self.cfg
        page = QWidget()
        lay = QVBoxLayout(page)
        local = QGroupBox("Локальная нейросеть (Ollama)")
        form = QFormLayout(local)
        self.local_ai = _check("Отвечать через локальную нейросеть — без ключей и интернета", cfg.get("local_ai"))
        self.local_model = QLineEdit(cfg.get("local_model", "qwen3:8b"))
        self.local_model.setPlaceholderText("например: qwen3:8b или qwen3:4b-instruct")
        form.addRow(self.local_ai)
        form.addRow("Модель:", self.local_model)
        hint = QLabel("Если модели нет на компьютере, Капри при запуске возьмёт скачанную сама.")
        hint.setWordWrap(True)
        form.addRow(hint)
        lay.addWidget(local)

        cloud = QGroupBox("Claude (Anthropic)")
        form = QFormLayout(cloud)
        self.enable_ai = _check("Отвечать через Claude — платно и через интернет", cfg.get("enable_ai"))
        self.api_key = QLineEdit(cfg.get("anthropic_api_key", ""))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("ключ Anthropic API")
        self.model = QLineEdit(cfg.get("model", "claude-opus-5-5"))
        form.addRow(self.enable_ai)
        form.addRow("Ключ:", self.api_key)
        form.addRow("Модель:", self.model)
        lay.addWidget(cloud)

        privacy = QGroupBox("Приватность")
        form = QFormLayout(privacy)
        self.privacy = _check("Режим приватности: только офлайн — Vosk, локальная модель, локальный голос",
                              cfg.get("privacy_mode"))
        self.google = _check("Уточнять фразы через Google STT (нужен интернет)", cfg.get("use_google_stt", True))
        self.search = _check("Открывать поиск, если не знаю ответа", cfg.get("search_when_unsure", True))
        form.addRow(self.privacy)
        form.addRow(self.google)
        form.addRow(self.search)
        lay.addWidget(privacy)
        lay.addStretch()
        return page

    def _behavior_tab(self):
        cfg = self.cfg
        page = QWidget()
        lay = QVBoxLayout(page)
        chat = QGroupBox("Появления")
        form = QFormLayout(chat)
        self.chatter = _check("Появляться самой время от времени", cfg.get("chatter", True))
        self.chat_min = _spin(1, 1440, cfg.get("chatter_min_minutes", 15), "мин")
        self.chat_max = _spin(1, 1440, cfg.get("chatter_max_minutes", 40), "мин")
        self.roam = QDoubleSpinBox()
        self.roam.setRange(0.0, 1.0)
        self.roam.setSingleStep(0.05)
        self.roam.setValue(float(cfg.get("roam_chance", 0.5)))
        form.addRow(self.chatter)
        form.addRow("Не чаще, чем через:", self.chat_min)
        form.addRow("И не реже, чем через:", self.chat_max)
        form.addRow("Гулять по экрану (0…1):", self.roam)
        lay.addWidget(chat)

        behavior = QGroupBox("Поведение")
        form = QFormLayout(behavior)
        self.greet = _check("Здороваться при запуске", cfg.get("greet_on_start", True))
        self.barge = _check("Давать себя перебивать голосом", cfg.get("barge_in", True))
        self.enter = _check("Отправлять сообщение после «напиши …»", cfg.get("type_press_enter", True))
        self.teleport = _check("Скакать по экрану во время ответа", cfg.get("teleport_on_answer", True))
        self.hide_after = _spin(0, 600, cfg.get("hide_after_seconds", 12), "сек")
        self.followup = _spin(0, 120, cfg.get("followup_seconds", 6), "сек")
        self.window = _spin(1, 120, cfg.get("listen_window_seconds", 8), "сек")
        form.addRow(self.greet)
        form.addRow(self.barge)
        form.addRow(self.enter)
        form.addRow(self.teleport)
        form.addRow("Исчезать через:", self.hide_after)
        form.addRow("Ждать продолжения:", self.followup)
        form.addRow("Слушать после имени:", self.window)
        lay.addWidget(behavior)

        plugins = QGroupBox("Навыки (плагины)")
        pform = QFormLayout(plugins)
        enabled = cfg.get("plugins") or {}
        self.plugin_boxes = {}
        for plugin in load_plugins():
            box = _check(plugin.name, enabled.get(plugin.name) is not False)
            self.plugin_boxes[plugin.name] = box
            pform.addRow(box)
        hint = QLabel("Новый навык — новый файл в actions/plugins. Здесь его можно отключить.")
        hint.setWordWrap(True)
        pform.addRow(hint)
        lay.addWidget(plugins)
        lay.addStretch()
        return page

    def _memory_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        rem = QGroupBox("Напоминания")
        rlay = QVBoxLayout(rem)
        self.reminders = QListWidget()
        rrow = QHBoxLayout()
        refresh = QPushButton("Обновить")
        remove = QPushButton("Удалить выбранное")
        refresh.clicked.connect(self.refresh_memory)
        remove.clicked.connect(self._remove_reminder)
        rrow.addWidget(refresh)
        rrow.addWidget(remove)
        rrow.addStretch()
        rlay.addWidget(self.reminders)
        rlay.addLayout(rrow)
        lay.addWidget(rem)

        notes = QGroupBox("Заметки")
        nlay = QVBoxLayout(notes)
        self.notes = QListWidget()
        nrow = QHBoxLayout()
        nrefresh = QPushButton("Обновить")
        nremove = QPushButton("Удалить выбранное")
        nrefresh.clicked.connect(self.refresh_memory)
        nremove.clicked.connect(self._remove_note)
        nrow.addWidget(nrefresh)
        nrow.addWidget(nremove)
        nrow.addStretch()
        nlay.addWidget(self.notes)
        nlay.addLayout(nrow)
        lay.addWidget(notes)

        self.refresh_memory()
        return page

    def _about_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        model = os.path.join(BASE, self.cfg.get("vosk_model", "models/vosk-model-small-ru-0.22"))
        rows = [
            ("Капри", f"версия {VERSION}"),
            ("Python", sys.version.split()[0]),
            ("Папка", BASE),
            ("Настройки", os.path.join(BASE, "config.json")),
            ("Журнал", os.path.join(BASE, "capri.log")),
            ("Модель Vosk", ("найдена: " if os.path.isdir(model) else "не найдена: ") + model),
            ("Распознавание", self.cfg.get("stt_backend", "vosk")),
            ("Приватный режим", "включён" if self.cfg.get("privacy_mode") else "выключен"),
        ]
        for caption, value in rows:
            row = QHBoxLayout()
            cap = QLabel(caption + ":")
            cap.setStyleSheet("color: #28ff96; font-weight: bold;")
            cap.setMinimumWidth(150)
            val = QLabel(str(value))
            val.setWordWrap(True)
            row.addWidget(cap)
            row.addWidget(val, 1)
            lay.addLayout(row)
        open_cfg = QPushButton("Открыть config.json")
        open_cfg.clicked.connect(self._open_config)
        lay.addWidget(open_cfg, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addStretch()
        about = QLabel("Некоммерческий фан-проект. Персонаж Капри и вселенная Cyberpunk: Edgerunners "
                       "принадлежат правообладателям. Код — под лицензией MIT.")
        about.setWordWrap(True)
        lay.addWidget(about)
        return page

    # ----- значения -----
    def values(self):
        return {
            "mic_device": self.mic.currentData(),
            "mic_gain": self.gain.value() / 100,
            "mic_sensitivity": self.sens.value(),
            "voice_volume": self.volume.value(),
            "voice_fx": self.fx.value() / 100,
            "voice": self.voice.currentText().strip(),
            "voice_rate": self.rate.currentText().strip(),
            "voice_pitch": self.pitch.currentText().strip(),
            "tts_engine": self.engine.currentData(),
            "silero_voice": self.silero_voice.currentText().strip(),
            "silero_pitch": self.silero_pitch.currentData(),
            "voice_lively": self.lively.isChecked(),
            "tts_cache": self.cache.isChecked(),
            "stt_backend": self.stt_backend.currentData(),
            "stt_whisper_model": self.stt_whisper.currentData(),
            "stt_min_speech_ms": self.stt_min.value(),
            "stt_grammar": parse_grammar(self.stt_grammar.text()),
            "local_ai": self.local_ai.isChecked(),
            "local_model": self.local_model.text().strip(),
            "enable_ai": self.enable_ai.isChecked(),
            "anthropic_api_key": self.api_key.text().strip(),
            "model": self.model.text().strip(),
            "privacy_mode": self.privacy.isChecked(),
            "use_google_stt": self.google.isChecked(),
            "search_when_unsure": self.search.isChecked(),
            "chatter": self.chatter.isChecked(),
            "chatter_min_minutes": self.chat_min.value(),
            "chatter_max_minutes": self.chat_max.value(),
            "roam_chance": round(self.roam.value(), 2),
            "greet_on_start": self.greet.isChecked(),
            "barge_in": self.barge.isChecked(),
            "type_press_enter": self.enter.isChecked(),
            "teleport_on_answer": self.teleport.isChecked(),
            "hide_after_seconds": self.hide_after.value(),
            "followup_seconds": self.followup.value(),
            "listen_window_seconds": self.window.value(),
            "plugins": {name: box.isChecked() for name, box in self.plugin_boxes.items()},
        }

    def apply(self, quiet=False):
        """Проверить и сохранить настройки. False — были ошибки, ничего не записано."""
        from main import save_setting

        values = self.values()
        errors = check_values(values)
        if errors:
            QMessageBox.warning(self, "Капри", "\n".join(errors))
            return False

        capri = self.capri
        capri.set_voice_volume(values["voice_volume"])
        capri.set_voice_fx(values["voice_fx"])
        if values["mic_device"] != capri.cfg.get("mic_device"):
            capri.set_mic(values["mic_device"], announce=False)
        capri.set_mic_tuning(gain=values["mic_gain"], sensitivity=values["mic_sensitivity"])

        speech = capri.speaker
        for key in ("voice", "voice_rate", "voice_pitch", "tts_engine", "silero_voice", "silero_pitch",
                    "voice_lively", "tts_cache"):
            if capri.cfg.get(key) != values[key]:
                capri.cfg[key] = values[key]
                save_setting(key, values[key])
        if speech:
            speech.voice = values["voice"]
            speech.rate = values["voice_rate"]
            speech.pitch = values["voice_pitch"]
            speech.engine = values["tts_engine"]
            speech.silero_voice = values["silero_voice"]
            speech.silero_pitch = values["silero_pitch"]
            speech.lively = values["voice_lively"]
            speech.cache_enabled = values["tts_cache"]

        if bool(capri.cfg.get("chatter")) != values["chatter"]:
            capri.set_chatter(values["chatter"])
        if bool(capri.cfg.get("search_when_unsure")) != values["search_when_unsure"]:
            capri.set_search_when_unsure(values["search_when_unsure"])
        if bool(capri.cfg.get("privacy_mode")) != values["privacy_mode"]:
            capri.set_privacy(values["privacy_mode"])
        else:
            capri.cfg["privacy_mode"] = values["privacy_mode"]

        for key in ("use_google_stt", "local_ai", "local_model", "enable_ai", "anthropic_api_key", "model",
                    "chatter_min_minutes", "chatter_max_minutes", "roam_chance", "greet_on_start", "barge_in",
                    "type_press_enter", "teleport_on_answer", "hide_after_seconds", "followup_seconds",
                    "listen_window_seconds", "stt_backend", "stt_whisper_model", "stt_min_speech_ms",
                    "stt_grammar"):
            if capri.cfg.get(key) != values[key]:
                capri.cfg[key] = values[key]
                save_setting(key, values[key])
        capri.actions.reload_plugins(values["plugins"])
        capri.cfg["plugins"] = values["plugins"]
        save_setting("plugins", values["plugins"])
        capri.reload_config_if_changed(speak=False)  # файл проходит миграцию и валидацию (T2)
        self.status.setText("Сохранено. Голос, микрофон, громкость и галочки применились сразу.\n"
                            "Нейросеть, распознавание речи и приветствие — после перезапуска.")
        if not quiet:
            self.status.setStyleSheet("color: #28ff96;")
        return True

    def _ok(self):
        if self.apply():
            self.accept()

    # ----- мелочи -----
    def _check_mic(self):
        self.mic_hint.setText("Говори — полоска должна доходить до розовой отметки и зеленеть.")
        self.status.setText("Проверка микрофона: видно по полоске выше.")

    def _tick(self):
        listener = self.capri.listener
        self.meter.level = listener.level if listener else 0.0
        self.meter.gate = max((100 - self.sens.value()) / 100 * GATE_MAX, 0.02)
        self.meter.update()

    def refresh_memory(self):
        self.reminders.clear()
        scheduler = getattr(self.capri, "scheduler", None)
        for item in (scheduler.all() if scheduler else []):
            try:
                when = format_when(datetime.datetime.fromisoformat(item["time"]))
            except Exception:
                when = item.get("time", "")
            row = QListWidgetItem(f"{when} — {item['text']}")
            row.setData(Qt.ItemDataRole.UserRole, item["id"])
            self.reminders.addItem(row)
        self.notes.clear()
        notes = getattr(self.capri, "notes", None)
        for note in (notes.all() if notes else []):
            row = QListWidgetItem(note["text"])
            row.setData(Qt.ItemDataRole.UserRole, note["id"])
            self.notes.addItem(row)

    def _remove_reminder(self):
        item = self.reminders.currentItem()
        if item and getattr(self.capri, "scheduler", None):
            self.capri.scheduler.cancel(item.data(Qt.ItemDataRole.UserRole))
            self.refresh_memory()

    def _remove_note(self):
        item = self.notes.currentItem()
        if item and getattr(self.capri, "notes", None):
            self.capri.notes.delete(item.data(Qt.ItemDataRole.UserRole))
            self.refresh_memory()

    def _open_config(self):
        try:
            os.startfile(os.path.join(BASE, "config.json"))
        except OSError as e:
            QMessageBox.warning(self, "Капри", f"Не получилось открыть config.json: {e}")

    def open_tab(self, name):
        tabs = {"voice": 0, "ai": 1, "behavior": 2, "memory": 3, "about": 4}
        self.tabs.setCurrentIndex(tabs.get(name, 0))

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
