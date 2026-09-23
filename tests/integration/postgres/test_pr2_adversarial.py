"""Adversarial-сценарії PR2 (незалежний тестувальник, gate 2).

Доповнює `test_projection.py`/`test_upload_claims.py` тим, чого там немає:

- ack: bytes receipt у **не**канонічній формі зберігаються як є (доказ «без reserialization»);
  повна матриця `applied_to_current × state_changed`; конкурентний дублікат ack; конкурентні
  out-of-order acks; подія як artifact (`event_artifact`, >256 KiB);
- `record_parse_result`: конкурентний дублікат того самого artifact → одна task; відкочена
  транзакція не «з'їдає» версію (без дірок);
- upload claim: кілька producers одночасно перебирають прострочений claim → generation
  зростає рівно на 1, старий owner не комітить; межа lease строга;
- `queue.release` після recover/claim іншим worker-ом → відмова, чужий lease недоторканий;
- partitioning: межа місяця і не-UTC offset потрапляють у правильну місячну партицію.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from collector.contracts import (
    AppliedProjectionReceipt,
    DomainChangedEvent,
    encode_event,
    new_entity_id,
    sha256_hex,
)
from collector.contracts.artifacts import ArtifactRef
from collector.contracts.enums import FetchOutcome
from collector.persistence.postgres.errors import LeaseNotOwnedError, StaleClaimError
from collector.persistence.postgres.models import (
    ArtifactUploadClaim,
    ChangeEvent,
    CrawlJob,
    NormalizedArtifact,
    OutboxEvent,
    ParseAttempt,
    ProjectionAcknowledgement,
    ProjectionTask,
)
from collector.persistence.postgres.repositories import artifacts, entities, projection, queue

from .conftest import (
    COLLECTION,
    FIXED_NOW,
    artifact_ref,
    attempt_record,
    make_entity,
    receipt,
    record,
    state_hash,
)

pytestmark = pytest.mark.integration

T0 = FIXED_NOW


class _Crash(Exception):
    """Падіння процесу посеред транзакції."""


async def _count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _domain_rows(session: AsyncSession) -> list[OutboxEvent]:
    return list(await session.scalars(select(OutboxEvent).where(OutboxEvent.topic == "domain")))


def _event(task_id: UUID, entity_uuid: UUID, version: int) -> DomainChangedEvent:
    return DomainChangedEvent(
        event_id=new_entity_id(),
        aggregate_id=entity_uuid,
        aggregate_version=version,
        event_type="catalog.item.changed",
        payload_schema_version="1.0",
        occurred_at=FIXED_NOW,
        projection_task_id=task_id,
        previous_state_hash=None,
        result_state_hash=state_hash(version),
        payload={"version": version, "note": "укр текст"},
    )


def _applied_receipt(
    task_id: UUID, entity_uuid: UUID, event_id: UUID, **event_fields: object
) -> AppliedProjectionReceipt:
    return AppliedProjectionReceipt(
        projection_task_id=task_id,
        entity_uuid=entity_uuid,
        projection_version=1,
        target_collection=COLLECTION,
        document_id=entity_uuid,
        applied_to_current=True,
        state_changed=True,
        result_version=1,
        result_hash=state_hash(1),
        committed_at=FIXED_NOW,
        cluster_time="1790000000:1",
        event_id=event_id,
        **event_fields,
    )


# --- ack: bytes без reserialization --------------------------------------------------------


async def test_non_canonical_receipt_bytes_are_stored_verbatim(pg_session: AsyncSession) -> None:
    """Bytes receipt навмисно НЕ канонічні (інший порядок ключів, відступи, `\\u`-escape).

    Якби репозиторій перекодовував подію (`decode_event` → `encode_event`), в outbox потрапили
    б канонічні bytes з іншим SHA-256, і consumer побачив би «іншу» подію після replay (R-42).
    """
    entity = await make_entity(pg_session)
    task = (await record(pg_session, entity.entity_uuid, 1)).task
    event = _event(task.task_id, entity.entity_uuid, 1)
    canonical = encode_event(event)
    reshaped = json.dumps(
        dict(reversed(list(json.loads(canonical.event_bytes).items()))),
        indent=2,
        ensure_ascii=True,
    ).encode("utf-8")
    assert reshaped != canonical.event_bytes
    source = _applied_receipt(
        task.task_id,
        entity.entity_uuid,
        event.event_id,
        event_bytes=reshaped,
        event_media_type=canonical.event_media_type,
        event_sha256=sha256_hex(reshaped),
    )
    async with pg_session.begin():
        result = await projection.acknowledge_projection(pg_session, task.task_id, source, now=T0)
    assert result.outbox_event is not None and result.change_event is not None
    async with pg_session.begin():
        [domain] = await _domain_rows(pg_session)
        [change] = list(await pg_session.scalars(select(ChangeEvent)))
    assert domain.payload_bytes == reshaped
    assert change.event_bytes == reshaped
    assert domain.payload_sha256 == change.event_sha256 == sha256_hex(reshaped)
    assert domain.payload_sha256 != canonical.event_sha256
    assert domain.event_id == change.event_id == event.event_id


@pytest.mark.parametrize(
    ("applied", "changed", "emits"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
async def test_domain_changed_matrix_applied_and_changed(
    pg_session: AsyncSession, applied: bool, changed: bool, emits: bool
) -> None:
    """Подія — лише для `applied_to_current AND state_changed`; у т.ч. (False, True):
    exact-version record змінився, але current — ні (§9.5)."""
    entity = await make_entity(pg_session)
    task = (await record(pg_session, entity.entity_uuid, 1)).task
    rcpt = receipt(task.task_id, entity.entity_uuid, 1, applied=applied, changed=changed)
    async with pg_session.begin():
        result = await projection.acknowledge_projection(pg_session, task.task_id, rcpt, now=T0)
    async with pg_session.begin():
        domain = await _domain_rows(pg_session)
        changes = await _count(pg_session, ChangeEvent)
        acks = await _count(pg_session, ProjectionAcknowledgement)
        stored = await projection.get_projection_task(pg_session, task.task_id)
    assert (result.outbox_event is not None, len(domain), changes) == (
        emits,
        int(emits),
        int(emits),
    )
    assert acks == 1
    assert stored is not None and stored.status == "succeeded"
    assert result.confirmed_projection_version == 1


async def test_large_event_as_artifact_is_referenced_not_inlined(pg_session: AsyncSession) -> None:
    entity = await make_entity(pg_session)
    task = (await record(pg_session, entity.entity_uuid, 1)).task
    event = _event(task.task_id, entity.entity_uuid, 1)
    pointer = ArtifactRef(
        uri="s3://events/" + "e" * 64 + ".json",
        sha256="e" * 64,
        size_bytes=300_000,
        media_type="application/json",
    )
    rcpt = _applied_receipt(
        task.task_id, entity.entity_uuid, event.event_id, event_artifact=pointer
    )
    async with pg_session.begin():
        await projection.acknowledge_projection(pg_session, task.task_id, rcpt, event=event, now=T0)
    async with pg_session.begin():
        [domain] = await _domain_rows(pg_session)
        [change] = list(await pg_session.scalars(select(ChangeEvent)))
    assert domain.payload_bytes is None and change.event_bytes is None
    assert domain.payload_artifact_uri == change.event_artifact_uri == pointer.uri
    assert domain.payload_sha256 == change.event_sha256 == pointer.sha256
    assert change.event_artifact_size_bytes == pointer.size_bytes


# --- ack: конкурентність --------------------------------------------------------------------


async def test_concurrent_duplicate_acks_produce_one_ack_and_one_event(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Чотири репліки projector-а/reconciler одночасно фіксують той самий receipt."""
    async with pg_sessions() as session:
        entity = await make_entity(session)
        task_id = (await record(session, entity.entity_uuid, 1)).task.task_id
    rcpt = receipt(task_id, entity.entity_uuid, 1, applied=True, changed=True)
    barrier = asyncio.Barrier(4)

    async def acker() -> bool:
        async with pg_sessions() as session:
            await barrier.wait()
            async with session.begin():
                result = await projection.acknowledge_projection(session, task_id, rcpt, now=T0)
            return result.created

    created = await asyncio.gather(*(acker() for _ in range(4)))
    assert sorted(created) == [False, False, False, True]
    async with pg_sessions() as session, session.begin():
        assert await _count(session, ProjectionAcknowledgement) == 1
        assert await _count(session, ChangeEvent) == 1
        assert len(await _domain_rows(session)) == 1


async def test_concurrent_out_of_order_acks_end_at_max_and_emit_only_current(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Acks версій 1..5 одночасно (5 applied, решта ні) → confirmed = 5, рівно одна подія."""
    async with pg_sessions() as session:
        entity = await make_entity(session)
        tasks = {
            n: (await record(session, entity.entity_uuid, n)).task.task_id for n in range(1, 6)
        }
    receipts = {
        n: receipt(
            tasks[n],
            entity.entity_uuid,
            n,
            applied=n == 5,
            changed=n == 5,
            current_version=None if n == 5 else 5,
        )
        for n in tasks
    }
    barrier = asyncio.Barrier(len(tasks))

    async def acker(n: int) -> int:
        async with pg_sessions() as session:
            await barrier.wait()
            async with session.begin():
                result = await projection.acknowledge_projection(
                    session, tasks[n], receipts[n], now=T0
                )
            return result.confirmed_projection_version

    observed = dict(
        zip(
            (5, 3, 1, 4, 2), await asyncio.gather(*(acker(n) for n in (5, 3, 1, 4, 2))), strict=True
        )
    )
    # Кожен ack повертає confirmed >= власної версії (GREATEST), ack v5 — рівно 5.
    assert all(observed[n] >= n for n in observed)
    assert observed[5] == 5
    async with pg_sessions() as session, session.begin():
        assert await entities.get_confirmed_version(session, entity.entity_uuid) == 5
        assert await _count(session, ChangeEvent) == 1
        statuses = {t.status for t in await session.scalars(select(ProjectionTask))}
    assert statuses == {"succeeded"}


# --- record_parse_result -------------------------------------------------------------------


async def test_concurrent_record_of_same_artifact_yields_one_task(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Дублікат idempotency key (той самий object_key) одночасно з трьох parser-ів."""
    async with pg_sessions() as session:
        entity = await make_entity(session)
    barrier = asyncio.Barrier(3)

    async def parser() -> tuple[bool, int]:
        async with pg_sessions() as session:
            await barrier.wait()
            result = await record(session, entity.entity_uuid, 7)
            return result.created, result.task.projection_version

    outcomes = await asyncio.gather(parser(), parser(), parser())
    assert sorted(outcomes) == [(False, 1), (False, 1), (True, 1)]
    async with pg_sessions() as session:
        nxt = await record(session, entity.entity_uuid, 8)
        async with session.begin():
            tasks = await _count(session, ProjectionTask)
            internal = await session.scalar(
                select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "internal")
            )
    assert nxt.task.projection_version == 2
    assert (tasks, internal) == (2, 2)


async def test_rolled_back_record_parse_result_leaves_no_version_gap(
    pg_session: AsyncSession,
) -> None:
    """Crash після `record_parse_result`, до commit: версія не витрачена, рядків немає."""
    entity_uuid = (await make_entity(pg_session)).entity_uuid
    with pytest.raises(_Crash):
        async with pg_session.begin():
            result = await projection.record_parse_result(
                pg_session,
                attempt=attempt_record(artifact_ref(entity_uuid, 1)),
                artifact_ref=artifact_ref(entity_uuid, 1),
                object_key=f"normalized/{1:064x}.json",
                target_collection=COLLECTION,
                target_schema_version="1.0",
                now=T0,
            )
            assert result.task.projection_version == 1
            raise _Crash
    async with pg_session.begin():
        counts = [
            await _count(pg_session, model)
            for model in (ParseAttempt, NormalizedArtifact, ProjectionTask, OutboxEvent)
        ]
        row = await entities.get_entity(pg_session, entity_uuid)
    assert counts == [0, 0, 0, 0]
    assert row is not None and row.projection_version == 0
    replay = await record(pg_session, entity_uuid, 1)
    assert replay.created and replay.task.projection_version == 1


# --- upload claim --------------------------------------------------------------------------


async def test_concurrent_reacquire_of_expired_claim_bumps_generation_once(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = "raw/contended"
    async with pg_sessions() as session, session.begin():
        first = await artifacts.acquire_upload_claim(session, key, "old", lease_seconds=30, now=T0)
    first_generation = first.claim_generation
    later = T0 + timedelta(seconds=45)
    barrier = asyncio.Barrier(3)

    async def producer(owner: str) -> int | None:
        async with pg_sessions() as session:
            await barrier.wait()
            try:
                async with session.begin():
                    claim = await artifacts.acquire_upload_claim(
                        session, key, owner, lease_seconds=30, now=later
                    )
                    return claim.claim_generation
            except StaleClaimError:
                return None

    generations = await asyncio.gather(producer("a"), producer("b"), producer("c"))
    assert sorted(g for g in generations if g is not None) == [2]
    assert generations.count(None) == 2
    async with pg_sessions() as session:
        with pytest.raises(StaleClaimError):
            async with session.begin():
                await artifacts.commit_reference(
                    session,
                    key,
                    first_generation,
                    owner="old",
                    sha256="ab" * 32,
                    size_bytes=1,
                    uri="s3://raw/raw/contended",
                    now=later,
                )
        async with session.begin():
            stored = await artifacts.get_claim(session, key)
    assert stored is not None
    assert (stored.claim_generation, stored.status, stored.sha256) == (2, "leased", None)


async def test_commit_exactly_at_lease_expiry_is_rejected(pg_session: AsyncSession) -> None:
    """Предикат `lease_expires_at > now` строгий: на самій межі lease вже прострочений."""
    async with pg_session.begin():
        claim = await artifacts.acquire_upload_claim(
            pg_session, "raw/edge", "w", lease_seconds=30, now=T0
        )
    generation, expires = claim.claim_generation, claim.lease_expires_at
    with pytest.raises(StaleClaimError):
        async with pg_session.begin():
            await artifacts.commit_reference(
                pg_session,
                "raw/edge",
                generation,
                owner="w",
                sha256="ab" * 32,
                size_bytes=1,
                uri="s3://raw/raw/edge",
                now=expires,
            )
    async with pg_session.begin():
        status = await pg_session.scalar(
            select(ArtifactUploadClaim.status).where(ArtifactUploadClaim.object_key == "raw/edge")
        )
    assert status == "leased"


# --- queue.release -------------------------------------------------------------------------


async def test_release_by_previous_owner_after_recover_and_reclaim_is_rejected(
    pg_session: AsyncSession,
) -> None:
    later = T0 + timedelta(minutes=1)
    async with pg_session.begin():
        await queue.enqueue(pg_session, queue.NewJob(job_type="fetch", idempotency_key="r"), now=T0)
        [job] = await queue.claim(pg_session, ["fetch"], "w-1", 30, now=T0)
        job_id = job.job_id
        assert await queue.recover_expired_leases(pg_session, now=later) == [job_id]
        [again] = await queue.claim(pg_session, ["fetch"], "w-2", 30, now=later)
        assert again.job_id == job_id
        with pytest.raises(LeaseNotOwnedError):
            await queue.release(pg_session, job_id, "w-1", now=later)
    async with pg_session.begin():
        refreshed = await pg_session.get(CrawlJob, job_id, populate_existing=True)
    assert refreshed is not None
    assert (refreshed.status, refreshed.lease_owner, refreshed.attempt) == ("leased", "w-2", 2)


# --- partitioning --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fetched_at", "partition"),
    [
        (datetime(2026, 9, 30, 23, 59, 59, 999999, tzinfo=UTC), "fetches_y2026m09"),
        (datetime(2026, 10, 1, 0, 0, tzinfo=UTC), "fetches_y2026m10"),
        # 01:00 +02:00 = 2026-09-30T23:00Z → вересень, а не жовтень.
        (datetime.fromisoformat("2026-10-01T01:00:00+02:00"), "fetches_y2026m09"),
    ],
)
async def test_fetch_month_boundaries_are_utc(
    pg_engine: AsyncEngine, pg_session: AsyncSession, fetched_at: datetime, partition: str
) -> None:
    async with pg_session.begin():
        fetch = await artifacts.record_fetch(
            pg_session,
            artifacts.FetchRecord(
                requested_url="https://example.test/x",
                outcome=FetchOutcome.SUCCESS,
                fetched_at=fetched_at,
            ),
            now=T0,
        )
        fetch_id = fetch.fetch_id
    async with pg_engine.connect() as conn:
        # Сесія PostgreSQL навмисно не в UTC: межа партиції не має від цього залежати.
        await conn.execute(text("SET TIME ZONE 'Pacific/Kiritimati'"))
        actual = await conn.scalar(
            text("SELECT tableoid::regclass::text FROM fetches WHERE fetch_id = :id"),
            {"id": fetch_id},
        )
    assert actual == partition
