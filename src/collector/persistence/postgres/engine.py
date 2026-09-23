"""`AsyncEngine` і `async_sessionmaker` для репозиторіїв.

Транзакційна модель (єдина для всіх репозиторіїв):

- репозиторії не викликають `commit()`/`rollback()` — межа транзакції належить викликачу
  (`async with session.begin(): ...`);
- один `AsyncSession` = одне з'єднання = одна транзакція за раз; для паралельних claim/acquire
  кожен worker task тримає власну session;
- `expire_on_commit=False`, щоб повернені ORM-об'єкти можна було читати після commit без
  повторного запиту.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from collector.persistence.postgres.config import PostgresSettings


def create_engine(
    settings: PostgresSettings,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    application_name: str = "collector",
    command_timeout: float | None = None,
) -> AsyncEngine:
    """Async engine (asyncpg). `application_name` видно в `pg_stat_activity` для діагностики.

    `command_timeout` (секунди) — верхня межа для **кожного** запиту на рівні драйвера. Без неї
    запит у «чорну діру» TCP (мережевий поділ, failover) чекає до RTO ядра — десятки хвилин, —
    і жоден таймаут у застосунку не має нижньої межі. Довгоживучі runtime-процеси (worker,
    scheduler) задають її від свого вікна self-fencing; one-shot міграції лишають `None`, бо
    `alembic upgrade` легально буває довгим.
    """
    connect_args: dict[str, object] = {"server_settings": {"application_name": application_name}}
    if command_timeout is not None:
        connect_args["command_timeout"] = command_timeout
    return create_async_engine(
        settings.url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика session для репозиторіїв; autoflush вимкнено — flush лише явний або на commit."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
