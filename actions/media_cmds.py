"""Управление медиаплеерами: пауза, продолжение, треки и перемотка (в т.ч. YouTube в браузере)."""
import re
import threading
import time

import automation
import media
from persona import line

from .keys import send_keys
from .utils import Reply


class MediaMixin:
    """Мультимедиа: что играет, пауза, следующий/предыдущий трек, перемотка."""

    def _youtube_keys(self, keys_list):
        """Нажать горячие клавиши плеера YouTube в его окне (k — пауза, j/l — ±10 с, стрелки — ±5 с)."""
        hwnd = automation.find_window(("youtube",))
        if not hwnd or not automation.focus(hwnd):
            return False
        for keys in keys_list:
            send_keys(keys)
            time.sleep(0.12)
        return True

    def media_pause(self, c, m):
        from .web import browser_playing

        if browser_playing() and automation.find_window(("youtube",)):
            # YouTube в браузере: системная пауза не всегда доходит — проверяем по звуку и жмём K
            media.control("pause")
            time.sleep(0.8)
            if browser_playing():
                self._youtube_keys([["k"]])
                time.sleep(0.8)
            if not browser_playing():
                self.media_paused = "youtube"
                return Reply(line("media_paused"))
        ok, desc, src = media.control("pause")
        if not ok:
            return Reply(line("media_none"))
        self.media_paused = src  # «продолжи» вернёт именно это, а не другой плеер
        return Reply(line("media_paused"))

    def media_play(self, c, m):
        from .web import BROWSERS, browser_playing

        if self.media_paused == "youtube" or (not self.media_paused and automation.find_window(("youtube",))
                                              and not media.now_playing()):
            def started():
                for _ in range(8):  # у трансляций звук появляется не сразу
                    time.sleep(0.5)
                    if browser_playing():
                        return True
                return False
            for b in BROWSERS:  # сначала системной командой — без переключения окон
                if media.control("play", source=f"{b}.exe")[0]:
                    break
            if started() or (self._youtube_keys([["k"]]) and started()):
                return Reply(line("media_play"))
            if self.media_paused == "youtube":  # не включаем вместо YouTube какой-то другой плеер
                return Reply(line("media_play_fail"))
        ok, desc, src = media.control("play", source=self.media_paused)
        if not ok:
            return Reply(line("media_none"))
        return Reply(line("media_play"))

    def media_next(self, c, m):
        ok, desc, _ = media.control("next")
        if not ok:
            return Reply(line("media_none"))
        time.sleep(0.8)  # даём плееру переключиться, чтобы назвать новый трек
        info = media.now_playing()
        return Reply(line("media_next", t=info[0]) if info and info[0] else line("done"))

    def media_prev(self, c, m):
        ok, desc, _ = media.control("prev")
        return Reply(line("done") if ok else line("media_none"))

    def media_info(self, c, m):
        info = media.now_playing()
        if not info or not info[0]:
            return Reply(line("media_none"))
        return Reply(line("media_info", t=info[0]))

    def media_seek(self, c, m):
        from .web import BROWSERS, browser_playing, ensure_playing

        back = re.search(r"\bназад\b|\bотмотай|\bверни|\bвернись", c)
        fwd = re.search(r"\bвперед|\bпромотай|\bпропусти|\bскипни|\bмотни", c)
        text = re.sub(r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи|перемотай|перемотать|промотай|"
                      r"мотни|отмотай|прокрути|верни\w*|вернись|пропусти|скипни|перемотка)\s+", "", c)
        text = re.sub(r"\b(вперед|назад|на|с|со|в|к|видео|музык\w*|трек|песн\w*|ролик|момент\w*|отметк\w*|"
                      r"тайминг)\b", " ", text)
        secs = media.parse_time(text)
        groups = media.number_groups(text)
        to_start = re.search(r"начал|сначала|заново", c)
        if to_start:
            back = fwd = None  # «верни сначала» — это не «назад», а в самое начало
        absolute = to_start or not (back or fwd) and (
            re.match(r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи)", c)
            or re.search(r"\d[:.]\d\d|момент|отметк|тайминг|позици|\bс (\S+ )?\S+ (минут|секунд|час)", c)
            or len(groups) >= 2
            or (groups and groups[0] >= 100 and not re.search(r"секунд|минут|час", c)))  # «на 314» = 3:14
        if secs is None:
            if not (back or fwd):
                return None
            secs = 10  # «перемотай вперёд» без числа — на 10 секунд
        delta = None if absolute else (-secs if back else secs)
        yt = automation.find_window(("youtube",))
        np = media.now_playing()
        if yt and (browser_playing() or not np or any(b in (np[1] or "").lower() for b in BROWSERS)):
            # YouTube в браузере: Firefox не даёт перематывать через систему — клавиши плеера или ссылка с &t=
            if absolute:
                if media.youtube_url_seek(secs):
                    threading.Thread(target=ensure_playing, args=(5.0,), daemon=True).start()
                    return Reply(line("media_seek", t=media.fmt(secs)))
                return Reply(line("media_seek_fail"))
            n10, rest = divmod(int(abs(delta)), 10)
            keys = [["l" if delta > 0 else "j"]] * n10 + [["right" if delta > 0 else "left"]] * round(rest / 5)
            if self._youtube_keys(keys or [["right" if delta > 0 else "left"]]):
                return Reply(line("media_skip", t=media.fmt(abs(delta)), d="вперёд" if delta > 0 else "назад"))
            return Reply(line("media_seek_fail"))
        ok, target, src = media.seek(seconds=secs) if absolute else media.seek(delta=delta)
        if target is None:
            return Reply(line("media_none"))
        return Reply(line("media_seek", t=media.fmt(target)) if ok else line("media_seek_fail"))
