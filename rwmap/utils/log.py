"""日志与探针报告落盘。"""
from __future__ import annotations

import logging
from pathlib import Path

from .paths import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "logs"
_logger = None


def get_logger(name: str = "rwmap") -> logging.Logger:
    global _logger
    if _logger is not None:
        return logging.getLogger(name)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "rwmap.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    _logger = logging.getLogger("rwmap")
    return logging.getLogger(name)


def report_path(filename: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / filename
