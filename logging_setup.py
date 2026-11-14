"""Единый логгер «Капри» с ротацией: capri.log, 5 файлов по 2 МБ."""
import logging
import logging.handlers
import os

LOG_FILE = "capri.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5
FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(base_dir, level=logging.INFO):
    """Настроить логгер "capri". Повторный вызов (тесты, перезапуск) не плодит обработчики."""
    logger = logging.getLogger("capri")
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(base_dir, LOG_FILE), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    logger.addHandler(handler)
    logger.info("Логирование включено: %s", handler.baseFilename)
    return logger
