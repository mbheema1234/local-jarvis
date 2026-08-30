"""Logging setup shared by every module."""

from __future__ import annotations

import logging
import sys

from .config import DATA_DIR

_configured = False


def setup(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-22s %(message)s", "%H:%M:%S"
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    file_handler = logging.FileHandler(DATA_DIR / "jarvis.log", encoding="utf-8")
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [stream, file_handler]

    # These are chatty and rarely useful.
    # ddgs/primp log every upstream search backend it tries, which buries the
    # actual application log.
    for noisy in ("httpx", "httpcore", "comtypes", "faster_whisper", "urllib3",
                  "primp", "ddgs", "ddgs.ddgs", "openwakeword"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)
