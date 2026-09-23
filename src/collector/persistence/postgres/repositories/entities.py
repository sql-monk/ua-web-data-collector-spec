"""Global entity index (§9.1, §9.5, §15 keyset pagination).

`entity_index` — єдине місце, де source identity (§9.3 п.1/п.2) перетворюється на внутрішній
`entity_uuid`, і єдиний власник лічильників версій сутності:

- `projection_version` — остання **видана** версія; збільшує лише `projection.record_parse_result`
  під row lock (див. там);
- `confirmed_projection_version` — остання **підтверджена** Mongo receipt-ом; оновлює лише
  `projection.acknowledge_projection` через `GREATEST` (§9.5 — ніколи не зменшується).

Domain document тут не дублюється: лише `mongo_collection`/`mongo_document_id` для
two-step lookup Read API (§7.3).

Transaction boundary усіх функцій — викликач.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import new_entity_id
from collector.contracts.enums import DataDomain, EntityKind
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import NotFoundError
from collector.persistence.postgres.models import EntityIndex


@dataclass(frozen=True, slots=True)
class EntityIdentity:
    """Природний ключ сутності (§9.3 п.1) плюс необов'язковий `identity_hash` (п.2).

    Якщо джерело не дає стабільного ID, викликач кладе versioned `identity_hash` і в
    `source_item_id`, і в `identity_hash`: unique source identity лишається одним index-ом, а
    походження ключа видно з другої колонки.
    """

    source_id: str
    source_item_id: str
    domain: DataDomain
    entity_kind: EntityKind
    identity_hash: str | None = None
    canonical_url: str | None = None


async def upsert_entity(
    session: AsyncSession,
    identity: EntityIdentity,
    *,
    now: datetime | None = None,
) -> EntityIndex:
    """Повертає наявний рядок за unique source identity або створює новий з новим UUIDv7.

    **Лічильники версій не чіпаються** — ні при створенні (обидва 0), ні при повторному
    виклику. Це навмисно: єдине місце, де `projection_version` зростає, — видача версії під
    row lock у `record_parse_result`; інакше два шляхи писали б в один лічильник.

    INSERT передає **лише identity-колонки** (gate 3, S-1): версії, `confirmed_at` і
    `mongo_*` беруться з DB defaults. Parser має column-level INSERT саме на ці колонки, тож
    навіть прямим SQL не може створити рядок з «підтвердженою» версією без ack; прив'язку до
    Mongo робить ack (`acknowledge_projection`) або `set_mongo_document`.

    Ідемпотентність тримається на `INSERT ... ON CONFLICT DO NOTHING` + повторний SELECT і
    вимагає **READ COMMITTED** (default PostgreSQL) — так само, як `queue.enqueue`.
    """
    current = resolve_now(now)
    stmt = (
        pg_insert(EntityIndex)
        .values(
            entity_uuid=new_entity_id(),
            domain=identity.domain.value,
            entity_kind=identity.entity_kind.value,
            source_id=identity.source_id,
            source_item_id=identity.source_item_id,
            identity_hash=identity.identity_hash,
            canonical_url=identity.canonical_url,
            created_at=current,
            updated_at=current,
        )
        .on_conflict_do_nothing(index_elements=[EntityIndex.source_id, EntityIndex.source_item_id])
        .returning(EntityIndex)
    )
    inserted = (await session.execute(stmt)).scalar_one_or_none()
    if inserted is not None:
        return inserted
    existing = await session.scalar(
        select(EntityIndex)
        .where(
            EntityIndex.source_id == identity.source_id,
            EntityIndex.source_item_id == identity.source_item_id,
        )
        .execution_options(populate_existing=True)
    )
    if existing is None:
        msg = (
            f"entity {identity.source_id}/{identity.source_item_id} не видно після "
            "ON CONFLICT: транзакція має бути READ COMMITTED"
        )
        raise NotFoundError(msg)
    return existing


async def get_entity(session: AsyncSession, entity_uuid: UUID) -> EntityIndex | None:
    """Рядок index за PK. Transaction boundary: викликач; один SELECT без блокування."""
    return await session.get(EntityIndex, entity_uuid)


async def find_entity(
    session: AsyncSession, source_id: str, source_item_id: str
) -> EntityIndex | None:
    """Рядок index за unique source identity (§9.3 п.1)."""
    stmt = select(EntityIndex).where(
        EntityIndex.source_id == source_id, EntityIndex.source_item_id == source_item_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_confirmed_version(session: AsyncSession, entity_uuid: UUID) -> int:
    """`confirmed_projection_version` сутності; `0` — жоден receipt ще не підтверджено.

    Це значення, з яким Read API робить bounded two-step lookup у Mongo (§9.5): читає
    `{entity_uuid, projection_version}` саме цієї версії, а не «останню».
    """
    version = await session.scalar(
        select(EntityIndex.confirmed_projection_version).where(
            EntityIndex.entity_uuid == entity_uuid
        )
    )
    if version is None:
        msg = f"entity {entity_uuid} не знайдено в entity_index"
        raise NotFoundError(msg)
    return int(version)


async def set_mongo_document(
    session: AsyncSession,
    entity_uuid: UUID,
    *,
    collection: str,
    document_id: UUID,
    now: datetime | None = None,
) -> EntityIndex:
    """Прив'язує current document Mongo до сутності (перший успішний projection)."""
    current = resolve_now(now)
    updated = await session.scalar(
        update(EntityIndex)
        .where(EntityIndex.entity_uuid == entity_uuid)
        .values(mongo_collection=collection, mongo_document_id=document_id, updated_at=current)
        .returning(EntityIndex)
        .execution_options(populate_existing=True)
    )
    if updated is None:
        msg = f"entity {entity_uuid} не знайдено в entity_index"
        raise NotFoundError(msg)
    return updated


async def list_entities(
    session: AsyncSession,
    domain: DataDomain,
    *,
    after: tuple[int, UUID] | None = None,
    limit: int = 100,
) -> list[EntityIndex]:
    """Keyset pagination (§15) за `(confirmed_projection_version, entity_uuid)`.

    Порядок і предикат точно відповідають index `ix_entity_index_domain_confirmed_version`
    (`domain, confirmed_projection_version, entity_uuid`) — обов'язковому за §9.1. OFFSET не
    використовується свідомо: на великих доменах він змушує читати й відкидати всі пропущені
    рядки, а сторінки «їдуть» при конкурентних оновленнях `confirmed_projection_version`.
    """
    if limit < 1:
        msg = "limit має бути >= 1"
        raise ValueError(msg)
    stmt = (
        select(EntityIndex)
        .where(EntityIndex.domain == domain.value)
        .order_by(EntityIndex.confirmed_projection_version, EntityIndex.entity_uuid)
        .limit(limit)
    )
    if after is not None:
        version, entity_uuid = after
        # Row comparison — PostgreSQL використовує його як index condition, а не фільтр.
        stmt = stmt.where(
            tuple_(EntityIndex.confirmed_projection_version, EntityIndex.entity_uuid)
            > tuple_(literal(version), literal(entity_uuid))
        )
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "EntityIdentity",
    "find_entity",
    "get_confirmed_version",
    "get_entity",
    "list_entities",
    "set_mongo_document",
    "upsert_entity",
]
