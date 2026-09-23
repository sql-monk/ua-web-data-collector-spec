"""Outbox publisher API (§10 п.13, R-30) і entity index з keyset pagination (§9.1, §15)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts import new_entity_id
from collector.contracts.enums import DataDomain, EntityKind
from collector.persistence.postgres.errors import NotFoundError
from collector.persistence.postgres.repositories import entities, outbox, projection

from .conftest import FIXED_NOW, make_entity, receipt, record

pytestmark = pytest.mark.integration

T0 = FIXED_NOW


async def _one_domain_event(session: AsyncSession) -> None:
    entity = await make_entity(session)
    task = (await record(session, entity.entity_uuid, 1)).task
    async with session.begin():
        await projection.acknowledge_projection(
            session,
            task.task_id,
            receipt(task.task_id, entity.entity_uuid, 1, applied=True, changed=True),
            now=T0,
        )


async def test_fetch_unpublished_respects_topic_availability_and_publish_marks(
    pg_session: AsyncSession,
) -> None:
    await _one_domain_event(pg_session)  # 1 internal projection.command + 1 domain.changed
    async with pg_session.begin():
        domain = await outbox.fetch_unpublished(pg_session, topics=[outbox.DOMAIN_TOPIC], now=T0)
        everything = await outbox.fetch_unpublished(pg_session, now=T0)
        assert await outbox.count_backlog(pg_session, now=T0) == 2
    assert [e.event_type for e in domain] == ["catalog.item.changed"]
    assert {e.topic for e in everything} == {"internal", "domain"}
    async with pg_session.begin():
        assert await outbox.mark_published(pg_session, [domain[0].outbox_id], now=T0) == 1
        # Повторне підтвердження (publisher перезапустився після доставки) — нічого не змінює.
        assert await outbox.mark_published(pg_session, [domain[0].outbox_id], now=T0) == 0
        assert await outbox.fetch_unpublished(pg_session, topics=["domain"], now=T0) == []
        assert await outbox.count_backlog(pg_session, topic="domain", now=T0) == 0
        stored = await outbox.get_event(pg_session, domain[0].event_id)
    assert stored is not None and stored.published_at == T0


async def test_mark_failed_backs_off_and_keeps_row(pg_session: AsyncSession) -> None:
    await _one_domain_event(pg_session)
    async with pg_session.begin():
        [event] = await outbox.fetch_unpublished(pg_session, topics=["domain"], now=T0)
        failed = await outbox.mark_failed(
            pg_session, event.outbox_id, error_code="broker_down", error_message="x", now=T0
        )
        assert (failed.attempts, failed.last_error_code) == (1, "broker_down")
        assert failed.available_at > T0
        # Недоступна до backoff, але не загублена.
        assert await outbox.fetch_unpublished(pg_session, topics=["domain"], now=T0) == []
        later = await outbox.fetch_unpublished(
            pg_session, topics=["domain"], now=T0 + timedelta(hours=1)
        )
        assert [e.outbox_id for e in later] == [event.outbox_id]
        age = await outbox.oldest_unpublished_age(pg_session, now=T0 + timedelta(minutes=1))
    assert age == timedelta(minutes=1)
    async with pg_session.begin():
        with pytest.raises(NotFoundError):
            await outbox.mark_failed(pg_session, new_entity_id(), error_code="x", now=T0)


async def test_parallel_publishers_do_not_receive_the_same_row(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_sessions() as session:
        entity = await make_entity(session)
        for n in range(1, 7):
            await record(session, entity.entity_uuid, n)  # 6 internal команд
    barrier = asyncio.Barrier(2)

    async def publisher() -> list[UUID]:
        async with pg_sessions() as session, session.begin():
            batch = await outbox.fetch_unpublished(session, limit=4, now=T0)
            await barrier.wait()  # обидві транзакції тримають locks одночасно
            return [row.outbox_id for row in batch]

    first, second = await asyncio.gather(publisher(), publisher())
    assert not set(first) & set(second)
    assert len(first) + len(second) == 6


def _identity(item: str) -> entities.EntityIdentity:
    return entities.EntityIdentity(
        source_id="catalog_ua_example",
        source_item_id=item,
        domain=DataDomain.CATALOG,
        entity_kind=EntityKind.CATALOG_ITEM,
    )


async def test_upsert_entity_is_idempotent_by_source_identity(pg_session: AsyncSession) -> None:
    async with pg_session.begin():
        first = await entities.upsert_entity(pg_session, _identity("sku-1"), now=T0)
        again = await entities.upsert_entity(pg_session, _identity("sku-1"), now=T0)
        found = await entities.find_entity(pg_session, "catalog_ua_example", "sku-1")
        assert await entities.get_confirmed_version(pg_session, first.entity_uuid) == 0
        with pytest.raises(NotFoundError):
            await entities.get_confirmed_version(pg_session, new_entity_id())
    assert again.entity_uuid == first.entity_uuid
    assert found is not None and found.entity_uuid == first.entity_uuid


async def test_list_entities_keyset_pagination_is_complete_and_stable(
    pg_session: AsyncSession,
) -> None:
    """25 сутностей із різними confirmed versions: сторінки по 7 покривають усі рівно раз, у
    порядку index `(domain, confirmed_projection_version, entity_uuid)`."""
    async with pg_session.begin():
        created = [
            await entities.upsert_entity(pg_session, _identity(f"sku-{i}"), now=T0)
            for i in range(25)
        ]
        for i, row in enumerate(created):
            row.projection_version = i % 4
            row.confirmed_projection_version = i % 4
        other = await entities.upsert_entity(
            pg_session,
            entities.EntityIdentity(
                source_id="auto_ua_example",
                source_item_id="car-1",
                domain=DataDomain.VEHICLE,
                entity_kind=EntityKind.VEHICLE_LISTING,
            ),
            now=T0,
        )
    seen: list[tuple[int, UUID]] = []
    after: tuple[int, UUID] | None = None
    pages = 0
    while True:
        async with pg_session.begin():
            page = await entities.list_entities(
                pg_session,
                DataDomain.CATALOG,
                after=after,
                limit=7,
            )
        if not page:
            break
        pages += 1
        seen.extend((row.confirmed_projection_version, row.entity_uuid) for row in page)
        last = page[-1]
        after = (last.confirmed_projection_version, last.entity_uuid)
    assert pages == 4
    assert len(seen) == 25 == len(set(seen))
    assert seen == sorted(seen)
    assert other.entity_uuid not in {uuid for _, uuid in seen}
