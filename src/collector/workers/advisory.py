"""Singleton lease через PostgreSQL session advisory lock (§7.5: «scheduler/controller мають
singleton advisory lease»).

Чому advisory lock, а не таблиця з рядком-lease: блокування прив'язане до **сесії**, тому при
падінні процесу, розриві TCP або `pg_terminate_backend` воно зникає миттєво і без sweeper-а —
другий scheduler стає активним одразу, і немає стану, який треба чистити руками. Нової таблиці
чи колонки для цього не потрібно (див. `docs/plan/deps/WP-01D-to-WP-01A.md`).

Контракт:

- `try_acquire()` — неблокуючий `pg_try_advisory_lock`: `True` = ми активний singleton;
- `is_held()` — перевірка **на боці сервера** (`pg_locks` для нашого backend pid), а не
  локального прапорця: якщо з'єднання вмерло, метод повертає `False`, і викликач зобов'язаний
  припинити роботу, яку захищав lease;
- `release()` — явне звільнення (graceful shutdown); ідемпотентне.

З'єднання утримується поза pool-ом на весь час володіння lease і працює в `AUTOCOMMIT`, щоб не
висіти в стані `idle in transaction`.
"""

from __future__ import annotations

import hashlib
from contextlib import suppress
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from collector.core.logging import get_logger

ADVISORY_CLASSID: Final[int] = 0x434F4C4C
"""Простір імен advisory locks застосунку (ASCII `COLL`) — щоб ключі `collector` не
перетиналися з локами інших застосунків у тому самому кластері."""

_IS_HELD_SQL = text(
    "SELECT EXISTS ("
    " SELECT 1 FROM pg_locks"
    " WHERE locktype = 'advisory' AND classid = :classid AND objid = :objid"
    "   AND pid = pg_backend_pid() AND granted"
    ")"
)


def advisory_key(name: str) -> int:
    """Стабільний невід'ємний 31-бітний ключ з імені lease.

    Невід'ємний навмисно: `pg_locks.objid` має тип `oid`, і від'ємний `int4` довелося б
    порівнювати в доповняльному коді. Хеш (blake2s) стабільний між версіями Python — на відміну
    від `hash()` і від `hashtext()` PostgreSQL, який не гарантований між major-версіями.
    """
    digest = hashlib.blake2s(name.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") & 0x7FFFFFFF


class AdvisoryLease:
    """Session-scoped advisory lock на іменований singleton (scheduler, controller)."""

    def __init__(self, engine: AsyncEngine, name: str, *, classid: int = ADVISORY_CLASSID) -> None:
        self.name = name
        self.classid = classid
        self.objid = advisory_key(name)
        self._engine = engine
        self._connection: AsyncConnection | None = None
        self._backend_pid: int | None = None
        self._log = get_logger("collector.advisory_lease")

    @property
    def backend_pid(self) -> int | None:
        """PID backend-а, який тримає lock (діагностика, тести); `None` — lease не наш."""
        return self._backend_pid

    @property
    def parameters(self) -> dict[str, int]:
        return {"classid": self.classid, "objid": self.objid}

    async def try_acquire(self) -> bool:
        """Спробувати взяти lease без очікування; `True` — ми активний singleton."""
        if self._connection is not None:
            return True
        connection = await self._engine.connect()
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:classid, :objid)"), self.parameters
                )
            )
            if not acquired:
                await connection.close()
                return False
            self._backend_pid = int(await connection.scalar(text("SELECT pg_backend_pid()")) or 0)
        except BaseException:
            with suppress(SQLAlchemyError, OSError):
                await connection.close()
            raise
        self._connection = connection
        return True

    async def is_held(self) -> bool:
        """Чи тримає lease саме наша жива сесія (перевірка в `pg_locks`, не локальний прапорець).

        Мертве з'єднання (kill процесу, `pg_terminate_backend`, розрив мережі) → `False` і
        скидання стану: викликач має припинити захищену роботу до нового `try_acquire`.
        """
        connection = self._connection
        if connection is None:
            return False
        try:
            held = bool(await connection.scalar(_IS_HELD_SQL, self.parameters))
        except (SQLAlchemyError, OSError):
            await self._discard()
            return False
        if not held:
            await self._discard()
        return held

    async def release(self) -> None:
        """Звільнити lease і повернути з'єднання (ідемпотентно).

        Результат `pg_advisory_unlock` перевіряється: `false` (або помилка) означає, що lock
        міг лишитись на цій сесії. Session-scoped advisory lock переживає `ROLLBACK`, який
        SQLAlchemy робить при поверненні з'єднання в pool, а `pg_try_advisory_lock`
        реентрантний у межах сесії — тож витік не побачив би ні `is_held`, ні наступний
        `try_acquire`, і singleton лишився б зайнятим простоюючим pooled-з'єднанням (L-6
        код-рев'ю). Тому за будь-якого сумніву з'єднання не повертається в pool, а
        інвалідовується.
        """
        connection = self._connection
        if connection is None:
            return
        unlocked = False
        try:
            unlocked = bool(
                await connection.scalar(
                    text("SELECT pg_advisory_unlock(:classid, :objid)"), self.parameters
                )
            )
        except (SQLAlchemyError, OSError) as exc:
            self._log.warning(
                "advisory_lease.unlock_failed", lease=self.name, error=type(exc).__name__
            )
        if not unlocked:
            self._log.warning("advisory_lease.unlock_unconfirmed", lease=self.name)
        await self._discard(invalidate=not unlocked)

    async def _discard(self, *, invalidate: bool = True) -> None:
        connection = self._connection
        self._connection = None
        self._backend_pid = None
        if connection is None:
            return
        with suppress(SQLAlchemyError, OSError):
            if invalidate:
                # Мертве або сумнівне з'єднання не повертаємо в pool разом із можливим lock-ом.
                await connection.invalidate()
            await connection.close()


__all__ = ["ADVISORY_CLASSID", "AdvisoryLease", "advisory_key"]
