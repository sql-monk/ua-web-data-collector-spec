"""Єдиний спосіб відкрити транзакцію runtime — з обмеженим часом запиту (§7.5, S-4).

`command_timeout` asyncpg ставиться на engine (його створює `collector.cli`) і діє на **всі**
запити процесу; `statement_timeout` ставиться тут і діє на кожну транзакцію runtime окремо.
Обидва потрібні: перший рятує від зависання клієнта, коли сервер не відповідає взагалі; другий
— від запиту, який сервер прийняв, але виконує довше, ніж worker готовий чекати (блокування,
розпухла черга). Без нижньої межі на обидва self-fencing не має сенсу: lease спливе раніше,
ніж runtime дізнається, що щось не так.

Вбудований запуск (тести, кілька runtime в одному процесі) створює engine сам і може не мати
`command_timeout` — тоді `statement_timeout` лишається єдиною межею, і це задокументована
різниця, а не випадковість.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_STATEMENT_TIMEOUT_SQL = text("SELECT set_config('statement_timeout', :ms, true)")
"""`set_config(..., is_local => true)` замість `SET LOCAL`: SET не приймає bind-параметрів,
а склеювати SQL рядками не варто навіть із int."""


@asynccontextmanager
async def bounded_transaction(
    sessions: async_sessionmaker[AsyncSession], statement_timeout_ms: int
) -> AsyncIterator[AsyncSession]:
    """Транзакція runtime із `statement_timeout` на час її життя (commit — на виході)."""
    async with sessions() as session, session.begin():
        await session.execute(_STATEMENT_TIMEOUT_SQL, {"ms": str(statement_timeout_ms)})
        yield session


__all__ = ["bounded_transaction"]
