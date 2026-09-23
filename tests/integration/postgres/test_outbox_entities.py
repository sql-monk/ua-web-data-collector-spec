"""Outbox publisher API (§10 п.13, R-30) і entity index з keyset pagination (§9.1, §15)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts import new_entity_id
from collector.contracts.enums import DataDomain, EntityKind
from collector.persistence.postgres.errors import (
    InvalidTransitionError,
    InvalidValueError,
    NotFoundError,
)
from collector.persistence.postgres.models import AuditLog
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


BOTH_TOPICS = (outbox.INTERNAL_TOPIC, outbox.DOMAIN_TOPIC)


async def test_fetch_unpublished_is_domain_only_by_default_and_publish_marks(
    pg_session: AsyncSession,
) -> None:
    await _one_domain_event(pg_session)  # 1 internal projection.command + 1 domain.changed
    async with pg_session.begin():
        assert await outbox.count_backlog(pg_session) == 2
        # S-6: без явних topics — лише `domain` (внутрішня команда назовні не йде, R-30).
        domain = await outbox.fetch_unpublished(pg_session, now=T0)
        internal = await outbox.fetch_unpublished(
            pg_session, topics=[outbox.INTERNAL_TOPIC], now=T0
        )
    assert [e.event_type for e in domain] == ["catalog.item.changed"]
    assert [e.topic for e in internal] == ["internal"]
    async with pg_session.begin():
        assert await outbox.mark_published(pg_session, [domain[0].outbox_id], now=T0) == 1
        # Повторне підтвердження (publisher перезапустився після доставки) — нічого не змінює.
        assert await outbox.mark_published(pg_session, [domain[0].outbox_id], now=T0) == 0
        assert await outbox.count_backlog(pg_session, topic="domain") == 0
        stored = await outbox.get_event(pg_session, domain[0].event_id)
        with pytest.raises(ValueError, match="topics"):
            await outbox.fetch_unpublished(pg_session, topics=(), now=T0)
    assert stored is not None and stored.published_at == T0


async def test_fetched_rows_stay_invisible_after_commit_until_visibility_lease_expires(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CR-2: publisher A бере рядки й комітить; publisher B після commit їх не бачить до спливу
    lease; якщо A помер (не позначив), рядки повертаються — «щонайменше один раз»."""
    async with pg_sessions() as session:
        await _one_domain_event(session)
    async with pg_sessions() as a, a.begin():
        taken = await outbox.fetch_unpublished(a, visibility_seconds=30, now=T0)
    assert len(taken) == 1
    async with pg_sessions() as b, b.begin():
        assert await outbox.fetch_unpublished(b, now=T0 + timedelta(seconds=29)) == []
        again = await outbox.fetch_unpublished(b, now=T0 + timedelta(seconds=30))
        # Лічильник backlog рахує і рядки в польоті.
        assert await outbox.count_backlog(b, topic="domain") == 1
    assert [e.outbox_id for e in again] == [taken[0].outbox_id]


async def test_mark_failed_backs_off_and_keeps_row(pg_session: AsyncSession) -> None:
    await _one_domain_event(pg_session)
    async with pg_session.begin():
        [event] = await outbox.fetch_unpublished(pg_session, now=T0)
        failed = await outbox.mark_failed(
            pg_session, event.outbox_id, error_code="broker_down", error_message="x", now=T0
        )
        assert (failed.attempts, failed.last_error_code, failed.parked_at) == (
            1,
            "broker_down",
            None,
        )
        assert failed.available_at > T0
        # Недоступна до backoff, але не загублена.
        assert await outbox.fetch_unpublished(pg_session, now=T0) == []
        later = await outbox.fetch_unpublished(pg_session, now=T0 + timedelta(hours=1))
        assert [e.outbox_id for e in later] == [event.outbox_id]
        age = await outbox.oldest_unpublished_age(pg_session, now=T0 + timedelta(minutes=1))
    assert age == timedelta(minutes=1)
    async with pg_session.begin():
        with pytest.raises(NotFoundError):
            await outbox.mark_failed(pg_session, new_entity_id(), error_code="x", now=T0)


async def test_mark_failed_parks_after_max_attempts_and_operator_unparks_with_audit(
    pg_session: AsyncSession,
) -> None:
    """CR-3: після `max_attempts` рядок паркується, publisher його не бере; `unpark` —
    операторська дія з audit, після неї доставка відновлюється з `attempts = 0`."""
    await _one_domain_event(pg_session)
    moment = T0
    async with pg_session.begin():
        [event] = await outbox.fetch_unpublished(pg_session, now=moment)
        for _ in range(3):
            event = await outbox.mark_failed(
                pg_session, event.outbox_id, error_code="broker_down", max_attempts=3, now=moment
            )
            moment += timedelta(days=1)
    assert event.attempts == 3 and event.parked_at is not None
    async with pg_session.begin():
        assert await outbox.fetch_unpublished(pg_session, now=moment + timedelta(days=30)) == []
        assert [e.outbox_id for e in await outbox.list_parked(pg_session)] == [event.outbox_id]
        assert await outbox.count_backlog(pg_session, topic="domain") == 0
        with pytest.raises(InvalidValueError):
            await outbox.unpark(pg_session, event.outbox_id, actor=" ", reason="x", now=moment)
        unparked = await outbox.unpark(
            pg_session, event.outbox_id, actor="op", reason="broker fixed", now=moment
        )
        assert (unparked.parked_at, unparked.attempts) == (None, 0)
        with pytest.raises(InvalidTransitionError):
            await outbox.unpark(pg_session, event.outbox_id, actor="op", reason="x", now=moment)
        audits = list(
            await pg_session.scalars(select(AuditLog).where(AuditLog.action == "outbox.unpark"))
        )
        redelivered = await outbox.fetch_unpublished(pg_session, now=moment)
    assert len(audits) == 1 and audits[0].actor == "op"
    assert [e.outbox_id for e in redelivered] == [event.outbox_id]


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
            batch = await outbox.fetch_unpublished(session, topics=BOTH_TOPICS, limit=4, now=T0)
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


async def test_oldest_unpublished_age_matches_backlog_scope(pg_session: AsyncSession) -> None:
    """Gate 4, N-3: вік — лише для не опублікованих і не припаркованих рядків заданих топіків
    (типово `domain`), так само як `count_backlog`; internal `projection.command` не публікується
    стандартним шляхом і не має тримати алерт §14.2 вічно."""
    await _one_domain_event(pg_session)
    later = T0 + timedelta(hours=2)
    async with pg_session.begin():
        assert await outbox.oldest_unpublished_age(pg_session, now=later) == timedelta(hours=2)
        [event] = await outbox.fetch_unpublished(pg_session, now=T0)
        await outbox.mark_failed(
            pg_session, event.outbox_id, error_code="poison", max_attempts=1, now=T0
        )
        # Домен: єдина подія припаркована → backlog і вік порожні; internal рахується лише явно.
        assert await outbox.count_backlog(pg_session, topic="domain") == 0
        assert await outbox.oldest_unpublished_age(pg_session, now=later) is None
        internal_age = await outbox.oldest_unpublished_age(
            pg_session, topics=[outbox.INTERNAL_TOPIC], now=later
        )
    assert internal_age == timedelta(hours=2)
