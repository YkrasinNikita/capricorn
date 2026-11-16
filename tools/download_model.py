"""Ставит офлайн-модель распознавания речи Vosk в папку models/.

    python tools/download_model.py

Вызывается из setup.bat. Модель лежит в самом архиве Капри (bundled/), поэтому интернет и VPN
не нужны. Если архива модели нет — скачивает её с сайта разработчиков. Повторный запуск
ничего не делает, если модель уже на месте.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from model_setup import installed, install_model, model_dir  # noqa: E402

MODELS = os.path.join(BASE, "models")
TARGET = model_dir(BASE)


def ready():
    return installed(BASE)


def main():
    if ready():
        print("[Capri] Модель распознавания речи уже установлена.")
        return 0
    try:
        install_model(BASE, log=print)
    except Exception as e:
        print(f"[Capri] Не удалось поставить модель: {e}")
        print("[Capri] Скачай Капри с GitHub заново (модель лежит в папке bundled) или включи VPN")
        print("        и запусти setup.bat ещё раз.")
        return 1
    print("[Capri] Модель установлена.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
