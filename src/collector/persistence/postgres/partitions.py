"""Місячні партиції великих таблиць (§9.1: `fetched_at`/`created_at` RANGE partitioning).

PR1 партиціонує `audit_log`; PR2 додає `fetches`, `raw_objects`, `change_events`,
`outbox_events` до `PARTITIONED_TABLES`. Партиції не є частиною міграцій (їх кількість
залежить від дати), тому:

- `ensure_month_partitions(conn, months_ahead=N)` створює відсутні `<table>_yYYYYmMM` від
  поточного місяця на N місяців уперед — викликається CLI `collector db migrate` після
  `upgrade head` і maintenance-worker WP-12;
- INSERT у місяць без партиції падає з `no partition of relation ... found for row` — обрано
  «зрозуміла помилка», а не auto-create у hot path (документовано в картці PR2), бо створення
  партиції потребує DDL-привілеїв, яких runtime-ролі не мають;
- Alembic autogenerate ігнорує child-таблиці за `is_partition_child_name`.

Transaction boundary: викликач (`AsyncConnection` у власній транзакції; DDL транзакційний).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PARTITIONED_TABLES: tuple[str, ...] = ("audit_log",)
_PARTITION_SUFFIX = re.compile(r"_y(\d{4})m(\d{2})$")
_PARTITION_CHILD = re.compile(
    r"^(?P<parent>" + "|".join(re.escape(t) for t in PARTITIONED_TABLES) + r")_y\d{4}m\d{2}$"
)
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class MonthPartition:
    parent: str
    year: int
    month: int

    @property
    def name(self) -> str:
        return f"{self.parent}_y{self.year:04d}m{self.month:02d}"

    @property
    def lower(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def upper(self) -> date:
        if self.month == 12:
            return date(self.year + 1, 1, 1)
        return date(self.year, self.month + 1, 1)

    @property
    def create_sql(self) -> str:
        if not _IDENT.match(self.parent):
            msg = f"недопустима назва таблиці {self.parent!r}"
            raise ValueError(msg)
        return (
            f"CREATE TABLE IF NOT EXISTS {self.name} PARTITION OF {self.parent} "
            f"FOR VALUES FROM ('{self.lower.isoformat()}') TO ('{self.upper.isoformat()}')"
        )


def is_partition_child_name(table_name: str) -> bool:
    """`audit_log_y2026m09` → True; використовується Alembic `include_name`."""
    return _PARTITION_CHILD.match(table_name) is not None


def month_partitions(parent: str, start: date, months_ahead: int) -> list[MonthPartition]:
    """Партиції від місяця `start` включно на `months_ahead` місяців уперед (усього N+1)."""
    if months_ahead < 0:
        msg = "months_ahead має бути >= 0"
        raise ValueError(msg)
    result: list[MonthPartition] = []
    year, month = start.year, start.month
    for _ in range(months_ahead + 1):
        result.append(MonthPartition(parent, year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return result


async def ensure_month_partitions(
    conn: AsyncConnection,
    *,
    tables: tuple[str, ...] = PARTITIONED_TABLES,
    months_ahead: int = 3,
    start: date | None = None,
) -> list[str]:
    """Створює відсутні місячні партиції; повертає назви створених. Ідемпотентно."""
    first = start or datetime.now(UTC).date().replace(day=1)
    created: list[str] = []
    for table in tables:
        if table not in PARTITIONED_TABLES:
            msg = f"{table!r} не є партиційованою таблицею ({PARTITIONED_TABLES})"
            raise ValueError(msg)
        for partition in month_partitions(table, first, months_ahead):
            exists = await conn.scalar(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": partition.name}
            )
            if exists:
                continue
            await conn.execute(text(partition.create_sql))
            created.append(partition.name)
    return created
