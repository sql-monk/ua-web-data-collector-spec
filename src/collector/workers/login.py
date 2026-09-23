"""Перевірка LOGIN-ролі БД при старті runtime (§13; картка WP-01D PR1b п.3, ризик I-1).

Кожен runtime-процес (`collector worker <role>`, `collector scheduler`) монтує лише свій
`postgres_dsn_<component>`. Перед першим claim/lease процес переконується, що підключився:

1. runtime-роллю без зайвих прав — `verify_runtime_login` WP-01A (не superuser, не член
   міграційної ролі чи привілейованих вбудованих ролей);
2. **саме своєю** роллю за мапінгом `collector.workers.roles` — DSN чужого компонента
   (fetch-worker під `collector_parser`) теж помилка конфігурації.

Інакше — `RoleLoginError`, і процес завершується ненульовим кодом (CLI). Повернення до
спільного міграційного `postgres_dsn` падає при старті, а не тихо працює з правами власника
схеми. Повідомлення містять лише імена ролей, без DSN/пароля.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from collector.persistence.postgres.roles import RoleLoginError, verify_runtime_login
from collector.workers.session import bounded_transaction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def verify_component_login(
    sessions: async_sessionmaker[AsyncSession], expected_role: str, *, statement_timeout_ms: int
) -> str:
    """Повертає `current_user`, якщо це `expected_role` без зайвих прав; інакше `RoleLoginError`."""
    async with bounded_transaction(sessions, statement_timeout_ms) as session:
        current = await verify_runtime_login(await session.connection())
    if current != expected_role:
        msg = (
            f"runtime-підключення під {current!r}, а цей процес має працювати під "
            f"{expected_role!r}: змонтуйте DSN свого компонента (postgres_dsn_<component>), §13"
        )
        raise RoleLoginError(msg)
    return current


__all__ = ["RoleLoginError", "verify_component_login"]
