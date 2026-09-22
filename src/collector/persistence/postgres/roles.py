"""Ролі БД §13: застосування `sql/roles.sql` (CLI `collector db roles`).

SQL-скрипт ідемпотентний і виконується цілком через simple query protocol asyncpg (DO-блоки з
крапками з комою не діляться на statements). Transaction boundary: викликач
(`async with engine.begin()` — увесь скрипт атомарно).
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from sqlalchemy.exc import DBAPIError
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
    """Виконує скрипт ролей/GRANT; повторний виклик безпечний.

    Скрипт іде через сирий `asyncpg.Connection.execute` (simple query protocol — інакше DO-блоки
    з `;` довелося б ділити на statements). Помилки asyncpg при цьому не є `SQLAlchemyError`,
    тому транслюються у `DBAPIError` — щоб викликачі (CLI `_run_async`) ловили їх так само, як
    помилки будь-якого іншого запиту, а не показували traceback (L-3 код-рев'ю).
    """
    script = load_roles_sql(sql_path)
    raw = await conn.get_raw_connection()
    driver: Any = raw.driver_connection  # asyncpg.Connection без типізації
    try:
        await driver.execute(script)
    except Exception as exc:
        # asyncpg не має py.typed, тому клас помилки визначаємо за модулем, а не імпортом;
        # усе, що не з asyncpg, пробрасуємо як є.
        if type(exc).__module__.split(".")[0] != "asyncpg":
            raise
        raise DBAPIError(statement=None, params=None, orig=exc) from exc
