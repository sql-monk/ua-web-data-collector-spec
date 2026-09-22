"""Структуроване JSON-логування (structlog) для CLI, workers і API.

Один формат для всіх компонентів: JSON-рядок на подію, ISO-8601 UTC timestamp,
рівень, logger, подія і контекст. URL/секрети у поля логів не потрапляють —
за це відповідають викликачі (§13, §18).
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog

DEFAULT_LEVEL = "INFO"


def configure_logging(level: str = DEFAULT_LEVEL, *, stream: TextIO | None = None) -> None:
    """Налаштувати structlog + stdlib logging на JSON-вивід у stderr.

    Ідемпотентно: повторний виклик перенастроює обробники без дублювання виводу.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(numeric_level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Іменований logger; ім'я — модуль або worker role."""
    return structlog.stdlib.get_logger(name)
