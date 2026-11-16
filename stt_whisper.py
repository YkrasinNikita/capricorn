"""Альтернативное распознавание речи: faster-whisper (офлайн, точнее на свободной речи).

Основной движок остаётся Vosk — он легче и работает сразу из коробки. Whisper включается
в config.json:

    "stt_backend": "whisper",
    "stt_whisper_model": "small",       # tiny / base / small / medium / large-v3
    "stt_whisper_compute": "int8"       # int8 — для процессора, float16 — для видеокарты

Нужен пакет faster-whisper:  pip install faster-whisper
Модель скачивается один раз при первом запуске и дальше работает без интернета.
"""
import logging

log = logging.getLogger("capri")
RATE = 16000


class WhisperBackend:
    """Тот же интерфейс, что у VoskBackend: reset(), accept(data) -> текст|None, partial()."""

    def __init__(self, model="small", language="ru", compute_type="int8", silence_ms=700,
                 min_ms=250, max_ms=15000, level=0.02):
        from faster_whisper import WhisperModel  # необязательная зависимость — только для этого движка
        log.info("Whisper: загружаю модель %s (%s)", model, compute_type)
        self.model = WhisperModel(model, device="cpu", compute_type=compute_type)
        self.language = language
        self.silence_ms = int(silence_ms)   # столько тишины после речи — и фраза считается законченной
        self.min_ms = int(min_ms)           # короче — шум, не распознаём
        self.max_ms = int(max_ms)           # очень длинную фразу режем принудительно
        self.level = float(level)           # порог громкости для определения речи
        self.buf = bytearray()
        self.voiced = 0.0                   # мс речи в текущей фразе
        self.silence = 0.0                  # мс тишины после речи

    def reset(self):
        self.buf.clear()
        self.voiced = 0.0
        self.silence = 0.0

    def accept(self, data):
        """Накопить звук и вернуть текст, когда фраза закончилась. Иначе None."""
        import numpy as np
        self.buf += data
        block_ms = len(data) / 2 / RATE * 1000
        x = np.frombuffer(data, np.int16).astype(np.float32)
        loud = len(x) and float(np.sqrt(np.mean(x * x))) / 32768 >= self.level
        if loud:
            self.voiced += block_ms
            self.silence = 0.0
        else:
            self.silence += block_ms
        done = (self.voiced >= self.min_ms and self.silence >= self.silence_ms) or self.voiced >= self.max_ms
        if not done:
            return None
        audio = bytes(self.buf)
        voiced = self.voiced
        self.reset()
        if voiced < self.min_ms:
            return ""
        return self._transcribe(audio)

    def partial(self):
        """Промежуточных результатов нет: Whisper распознаёт только законченную фразу."""
        return None

    def _transcribe(self, audio):
        import numpy as np
        x = np.frombuffer(audio, np.int16).astype(np.float32) / 32768
        segments, _ = self.model.transcribe(x, language=self.language, beam_size=1, vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()

    def transcribe(self, audio):
        """Распознать готовую запись целиком (для tools/stt_eval.py)."""
        return self._transcribe(audio)
