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
    if now.tzinfo is None or now.utcoffset() is None:
        msg = "now має бути aware datetime (UTC)"
        raise ValueError(msg)
    return now.astimezone(UTC)
