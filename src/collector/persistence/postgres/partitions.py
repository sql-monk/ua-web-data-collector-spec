"""Місячні партиції великих таблиць (§9.1: `fetched_at`/`created_at` RANGE partitioning).

PR1 партиціонує `audit_log`, PR2 — `fetches` (по `fetched_at`).

**Три таблиці зі списку «Спільних вимог» картки свідомо лишилися непартиційованими**, бо
місячне RANGE-партиціонування зруйнувало б їхній головний інваріант — глобальний unique
(PostgreSQL не вміє unique без partition key у ключі):

- `raw_objects` — PK `raw_object_id` (UUID) і `UNIQUE (sha256)` від `sha256(body)` (§9.3 п.4
  «однакові bytes фізично не дублюються»);
  помісячний unique зробив би дедуплікацію помісячною. Розмір обмежений кількістю *різних*
  тіл, а не спроб (спроби — у партиційованих `fetches`);
- `change_events`, `outbox_events` — `UNIQUE (event_id)` (§9.1 «unique event ID», §7.3
  «consumer дедуплікує за `event_id`»). Деталі й розглянуті альтернативи —
  `models/outbox.py`.

Партиції не є частиною міграцій (їх кількість залежить від дати), тому:

- `ensure_month_partitions(conn, months_ahead=N)` створює відсутні `<table>_yYYYYmMM` від
  поточного місяця на N місяців уперед — викликається CLI `collector db migrate` після
  `upgrade head` і maintenance-worker WP-12;
- кожна партиційована таблиця має DEFAULT-партицію (`<table>_default`, створюється міграцією):
  пропущене обслуговування не повинно зупиняти записи — для `audit_log` це зупинило б **усі**
  audited дії control plane, бо `request_scale` пише audit у тій самій транзакції (M-5
  код-рев'ю), а для `fetches` — знищувало б докази вже виконаних HTTP-запитів (bytes у
  artifact store є, а lineage-рядка немає). Це і є відповідь на питання картки «зрозуміла
  помилка чи авто-створення» для `fetches`: **ні те, ні те** — рядок приймається у DEFAULT, а
  сигналом служить `default_partition_row_count`. Рядки в DEFAULT — сигнал «партиції
  відстають» (метрика WP-12), а не нормальний режим: поки вони там, місячну партицію того
  самого періоду створити не можна, доки maintenance їх не перенесе;
- Alembic autogenerate ігнорує child-таблиці за `is_partition_child_name`.

Transaction boundary: викликач (`AsyncConnection` у власній транзакції; DDL транзакційний).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PARTITIONED_TABLES: tuple[str, ...] = ("audit_log", "fetches")
_PARTITION_SUFFIX = re.compile(r"_y(\d{4})m(\d{2})$")
_PARTITION_CHILD = re.compile(
    r"^(?P<parent>"
    + "|".join(re.escape(t) for t in PARTITIONED_TABLES)
    + r")(_y\d{4}m\d{2}|_default)$"
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
        """DDL партиції з **явними UTC-межами**.

        Date-літерал (`'2031-03-01'`) для колонки `timestamptz` інтерпретується у `TimeZone`
        сесії, яка виконує DDL, і зберігається вже зсунутим. Партиції, створені з різних сесій
        (різний `PGTZ`, змінений `postgresql.conf`, керований інстанс з іншим дефолтом), тоді
        або перекриваються (`would overlap partition …` — падає вся `collector db migrate`,
        бо upgrade і партиції в одній транзакції), або лишають діру між місяцями, у яку не
        можна вставити рядок. Тому межі задані як `timestamptz` з явним `+00` — від TimeZone
        сесії вони більше не залежать (M-1 код-рев'ю).
        """
        if not _IDENT.match(self.parent):
            msg = f"недопустима назва таблиці {self.parent!r}"
            raise ValueError(msg)
        return (
            f"CREATE TABLE IF NOT EXISTS {self.name} PARTITION OF {self.parent} "
            f"FOR VALUES FROM ('{self.lower.isoformat()} 00:00:00+00') "
            f"TO ('{self.upper.isoformat()} 00:00:00+00')"
        )


def default_partition_name(parent: str) -> str:
    """`audit_log` → `audit_log_default` (партиція-«приймач» пропущених місяців)."""
    return f"{parent}_default"


def default_partition_sql(parent: str) -> str:
    """DDL DEFAULT-партиції; створюється міграцією, не maintenance-циклом."""
    if not _IDENT.match(parent):
        msg = f"недопустима назва таблиці {parent!r}"
        raise ValueError(msg)
    return (
        f"CREATE TABLE IF NOT EXISTS {default_partition_name(parent)} PARTITION OF {parent} DEFAULT"
    )


async def default_partition_row_count(conn: AsyncConnection, parent: str) -> int:
    """Скільки рядків осіло в DEFAULT-партиції — джерело метрики/алерту «партиції відстають».

    TODO(WP-12): опублікувати як метрику §14.1 і алерт §14.2 — ненульове значення означає, що
    maintenance не створив місячну партицію вчасно. Поки рядки лежать у DEFAULT, створити
    місячну партицію для того самого періоду **не можна** (PostgreSQL сканує DEFAULT і
    відмовляє, якщо в ній є рядки нового діапазону), тож обслуговування має спершу перенести
    їх: `BEGIN; CREATE TABLE … (LIKE parent); INSERT … SELECT … FROM parent_default WHERE …;
    DELETE …; ATTACH PARTITION; COMMIT`.
    """
    if parent not in PARTITIONED_TABLES:
        msg = f"{parent!r} не є партиційованою таблицею ({PARTITIONED_TABLES})"
        raise ValueError(msg)
    name = default_partition_name(parent)
    exists = await conn.scalar(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name})
    if not exists:
        return 0
    count = await conn.scalar(text(f"SELECT count(*) FROM {name}"))  # noqa: S608 — з allowlist
    return int(count or 0)


def is_partition_child_name(table_name: str) -> bool:
    """`audit_log_y2026m09`/`audit_log_default` → True; використовується Alembic `include_name`.

    DEFAULT-партиція створюється міграцією, але autogenerate її ігнорує так само, як місячні:
    інакше `alembic check` бачив би її як «зайву таблицю» відносно моделей.
    """
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
