# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: папка Capri со всем нужным, кроме моделей и личных файлов.

    build.bat                       # обычная сборка
    python -m PyInstaller --noconfirm --clean build.spec

Модель распознавания речи в сборку не входит (это отдельный шаг): при первом запуске
Капри сама предложит её скачать (model_setup.py). config.json, заметки и журнал
создаются рядом с Capri.exe и в сборку не попадают.
"""
import os

from PyInstaller.utils.hooks import collect_submodules

BASE = os.path.abspath(SPECPATH)

datas = [(os.path.join(BASE, "assets"), "assets")]

hiddenimports = [
    # WinRT-медиа подключается через winrt, а не обычным import — PyInstaller его не видит
    "winrt.windows.media.control",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "pycaw.pycaw",
    "comtypes.gen",
    "PIL.ImageGrab",
    "imageio_ffmpeg",
] + collect_submodules("actions.plugins")  # новые плагины попадают в сборку сами

a = Analysis(
    [os.path.join(BASE, "main.py")],
    pathex=[BASE],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # torch и faster-whisper — необязательные и очень тяжёлые, в базовую сборку не входят
    excludes=["torch", "faster_whisper", "pytest", "tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Capri",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Capri",
)
