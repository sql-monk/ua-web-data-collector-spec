"""Джерело часу для репозиторіїв.

Усі порівняння lease/`not_before`/refill робляться за часом застосунку, переданим у функцію
(`now: datetime | None`), а не за `now()` бази: це дає детерміновані integration-тести (fake
clock) і єдиний момент часу для кількох statement-ів однієї операції. Наслідок: хости workers
мають синхронізований годинник (NTP) — те саме припущення, що й для lease timeout у §7.2.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Aware UTC now з мікросекундами (PostgreSQL `timestamptz` зберігає ту саму точність)."""
    return datetime.now(UTC)


def resolve_now(now: datetime | None) -> datetime:
    """`now` викликача або системний час; naive datetime відхиляється (§9.6 — UTC only)."""
    if now is None:
        return utcnow()
    return require_aware_utc(now, parameter="now")


def require_aware_utc(value: datetime, *, parameter: str) -> datetime:
    """Відхиляє naive datetime і нормалізує aware значення до UTC (§9.6).

    PostgreSQL/asyncpg може мовчки трактувати naive значення як timezone сесії. Репозиторії
    викликають цей helper до SQL, щоб одна й та сама точка часу не залежала від конфігурації
    з'єднання, а помилка називала саме публічний параметр API.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{parameter} має бути aware datetime (UTC)"
        raise ValueError(msg)
    return value.astimezone(UTC)
