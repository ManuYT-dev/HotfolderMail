import os
import logging
from pathlib import Path


def get_logger(name: str, log_file: str) -> logging.Logger:
    """Erstellt einen Logger mit eigenem File-Handler."""
    base_log_dir = os.getenv("LOG_DIR", "logs")
    log_path = Path(base_log_dir) / log_file

    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False

    return logger