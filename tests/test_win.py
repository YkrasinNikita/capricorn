"""Тесты, которым нужны реальные вызовы Windows."""
import ctypes

import pytest


@pytest.mark.win
def test_windows_api_available():
    """Проверяем, что Win32-вызовы вообще работают (самый безобидный из них)."""
    assert ctypes.windll.user32.GetSystemMetrics(0) > 0


@pytest.mark.win
def test_send_keys_known_shape():
    """send_keys принимает известные имена клавиш (реальное нажатие не делаем)."""
    from actions.keys import VK

    assert {"win", "ctrl", "alt", "shift", "enter", "esc"} <= set(VK)
