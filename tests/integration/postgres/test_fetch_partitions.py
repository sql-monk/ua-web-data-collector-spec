"""Партиціонування `fetches` (картка PR2, «Тести»: partitioning).

Рішення (задокументоване в `partitions.py` і `models/outbox.py`): insert у місяць без
місячної партиції **не падає і не створює партицію сам** — рядок приймає DEFAULT-партиція
`fetches_default`, а `default_partition_row_count` сигналить «maintenance відстає» (метрика
WP-12). Helper `ensure_month_partitions` створює партиції на N місяців наперед; поки в DEFAULT
лежать рядки того самого періоду, створення відповідної місячної партиції падає зрозуміло.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from collector.contracts.enums import FetchOutcome
from collector.persistence.postgres.partitions import (
    default_partition_row_count,
    ensure_month_partitions,
)
from collector.persistence.postgres.repositories import artifacts

from .conftest import FIXED_NOW

pytestmark = pytest.mark.integration


def _fetch(at: datetime) -> artifacts.FetchRecord:
    return artifacts.FetchRecord(
        requested_url="https://example.test/item/1",
        outcome=FetchOutcome.SUCCESS,
        fetched_at=at,
        http_status=200,
        raw_sha256="ab" * 32,
    )


async def _partition_of(engine: AsyncEngine, fetch_id: object) -> str:
    async with engine.connect() as conn:
        return str(
            await conn.scalar(
                text("SELECT tableoid::regclass::text FROM fetches WHERE fetch_id = :id"),
                {"id": fetch_id},
            )
        )


async def test_fetch_in_covered_month_lands_in_monthly_partition(
    pg_engine: AsyncEngine, pg_session: AsyncSession
) -> None:
    async with pg_session.begin():
        fetch = await artifacts.record_fetch(pg_session, _fetch(FIXED_NOW), now=FIXED_NOW)
    assert await _partition_of(pg_engine, fetch.fetch_id) == "fetches_y2026m09"


async def test_fetch_without_monthly_partition_goes_to_default_and_is_signalled(
    pg_engine: AsyncEngine, pg_session: AsyncSession
) -> None:
    """Template має партиції на 3 місяці вперед від 2026-09; 2027-06 не покрито."""
    uncovered = datetime(2027, 6, 15, tzinfo=UTC)
    async with pg_session.begin():
        fetch = await artifacts.record_fetch(pg_session, _fetch(uncovered), now=FIXED_NOW)
    assert await _partition_of(pg_engine, fetch.fetch_id) == "fetches_default"
    async with pg_engine.begin() as conn:
        assert await default_partition_row_count(conn, "fetches") == 1
    # Партицію того самого місяця створити не можна, поки рядок у DEFAULT: помилка явна.
    with pytest.raises(DBAPIError, match="default partition"):
        async with pg_engine.begin() as conn:
            await ensure_month_partitions(
                conn, tables=("fetches",), months_ahead=0, start=date(2027, 6, 1)
            )


async def test_helper_creates_partitions_n_months_ahead_idempotently(
    pg_engine: AsyncEngine,
) -> None:
    async with pg_engine.begin() as conn:
        created = await ensure_month_partitions(conn, months_ahead=2, start=date(2028, 1, 1))
        again = await ensure_month_partitions(conn, months_ahead=2, start=date(2028, 1, 1))
        names = set(
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_inherits i "
                        "JOIN pg_class c ON c.oid = i.inhrelid "
                        "WHERE i.inhparent = 'fetches'::regclass"
                    )
                )
            ).scalars()
        )
    assert [n for n in created if n.startswith("fetches_")] == [
        "fetches_y2028m01",
        "fetches_y2028m02",
        "fetches_y2028m03",
    ]
    assert again == []
    assert {"fetches_default", "fetches_y2028m01", "fetches_y2028m03"} <= names
