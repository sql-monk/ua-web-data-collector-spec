"""Ролі БД §13: застосування `sql/roles.sql` (CLI `collector db roles`).

SQL-скрипт ідемпотентний і виконується цілком через simple query protocol asyncpg (DO-блоки з
крапками з комою не діляться на statements). Transaction boundary: викликач
(`async with engine.begin()` — увесь скрипт атомарно).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

ROLE_NAMES: tuple[str, ...] = (
    "collector_migrate",
    "collector_scheduler",
    "collector_fetcher",
    "collector_parser",
    "collector_projector",
    "collector_translation",
    "collector_api_ro",
    "collector_export_ro",
)
RUNTIME_ROLES: tuple[str, ...] = tuple(r for r in ROLE_NAMES if r != "collector_migrate")


def default_roles_sql_path() -> Path:
    """`collector/persistence/postgres/sql/roles.sql` усередині пакета."""
    return Path(str(resources.files("collector.persistence.postgres").joinpath("sql/roles.sql")))


def load_roles_sql(path: Path | None = None) -> str:
    return (path or default_roles_sql_path()).read_text(encoding="utf-8")


async def apply_roles(conn: AsyncConnection, *, sql_path: Path | None = None) -> None:
    """Виконує скрипт ролей/GRANT; повторний виклик безпечний."""
    script = load_roles_sql(sql_path)
    raw = await conn.get_raw_connection()
    driver: Any = raw.driver_connection  # asyncpg.Connection без типізації
    await driver.execute(script)
