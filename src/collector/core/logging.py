"""Структуроване JSON-логування (structlog) для CLI, workers і API.

Один формат для всіх компонентів: JSON-рядок на подію, ISO-8601 UTC timestamp,
рівень, logger, подія і контекст. Записи сторонніх бібліотек через stdlib `logging`
(httpx, pymongo, uvicorn, alembic, ...) проходять той самий ланцюг processors
(`ProcessorFormatter` + `foreign_pre_chain`), тому теж виходять JSON-рядками.

Секрети (§13): значення ключів з `REDACTED_KEYS` (Authorization, Cookie, API keys,
tokens, passwords — case-insensitive, рекурсивно у вкладених dict/list) підміняються на
`[redacted]` до рендерингу. URL із credentials/токенами у query у поля логів не
потрапляють — за це відповідають викликачі.

`configure_logging` замінює handlers root logger (зокрема handler pytest `caplog`):
у тестах передавати `stream=` і перевіряти вивід, а не `caplog.records`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, TextIO

import structlog

DEFAULT_LEVEL = "INFO"
REDACTED_VALUE = "[redacted]"
REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "proxy_authorization",
        "cookie",
        "set-cookie",
        "set_cookie",
        "api_key",
        "apikey",
        "api-key",
        "token",
        "password",
        "secret",
    }
)


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (REDACTED_VALUE if str(key).lower() in REDACTED_KEYS else _redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_redact_value(item) for item in value)
    return value


def redact_secrets(
    logger: logging.Logger | None, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: приховати значення секретних ключів у всьому event_dict."""
    for key in list(event_dict):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = REDACTED_VALUE
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def _shared_processors() -> list[Any]:
    """Processors, спільні для structlog-подій і stdlib-записів сторонніх бібліотек."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        # stdlib `extra={...}` сторонніх бібліотек → поля події (і під redaction).
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,
    ]


def configure_logging(level: str = DEFAULT_LEVEL, *, stream: TextIO | None = None) -> None:
    """Налаштувати structlog + stdlib logging на JSON-вивід у stderr.

    Ідемпотентно: повторний виклик перенастроює обробники без дублювання виводу.
    Невідомий `level` трактується як INFO.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    shared = _shared_processors()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(numeric_level)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Іменований logger; ім'я — модуль або worker role."""
    return structlog.stdlib.get_logger(name)
