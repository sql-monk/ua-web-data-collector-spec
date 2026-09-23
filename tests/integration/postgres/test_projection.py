"""§7.3 кроки 2 і 4 (R-36, R-37, R-42): видача `projection_version`, projection queue, ack.

Картка PR2:

- 3 паралельні `record_parse_result` для однієї сутності → версії 1,2,3 без дірок/дублів;
  повторний виклик для того самого artifact → той самий task;
- ack у порядку 3,1,2 → `confirmed_projection_version = 3` і ніколи не зменшується; повторний
  ack ідемпотентний; `domain.changed` лише для `applied_to_current AND state_changed`; bytes в
  outbox побайтово дорівнюють bytes receipt;
- crash-вікно: транзакція ack відкочена після insert ack → жодних часткових записів.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collector.contracts import ProjectionCommand, decode_event, new_entity_id, sha256_hex
from collector.persistence.postgres.errors import (
    ConflictError,
    InvalidValueError,
    LeaseNotOwnedError,
    NotFoundError,
)
from collector.persistence.postgres.models import (
    ChangeEvent,
    NormalizedArtifact,
    OutboxEvent,
    ParseAttempt,
    ProjectionAcknowledgement,
    ProjectionTask,
)
from collector.persistence.postgres.repositories import entities, projection

from .conftest import FIXED_NOW, artifact_ref, attempt_record, make_entity, receipt, record

pytestmark = pytest.mark.integration

T0 = FIXED_NOW


class _Crash(Exception):
    """Симуляція падіння процесу між кроками однієї транзакції."""


async def _count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_parallel_record_parse_result_issues_versions_without_gaps_or_duplicates(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_sessions() as session:
        entity = await make_entity(session)
    barrier = asyncio.Barrier(3)

    async def parser(n: int) -> int:
        async with pg_sessions() as session:
            await barrier.wait()
            result = await record(session, entity.entity_uuid, n)
            assert result.created
            return result.task.projection_version

    versions = await asyncio.gather(parser(1), parser(2), parser(3))
    assert sorted(versions) == [1, 2, 3]
    async with pg_sessions() as session, session.begin():
        tasks = list(await session.scalars(select(ProjectionTask)))
        refreshed = await entities.get_entity(session, entity.entity_uuid)
        commands = list(
            await session.scalars(select(OutboxEvent).where(OutboxEvent.topic == "internal"))
        )
    assert sorted(t.projection_version for t in tasks) == [1, 2, 3]
    assert refreshed is not None
    assert (refreshed.projection_version, refreshed.confirmed_projection_version) == (3, 0)
    # Одна task — рівно одна внутрішня команда, event_id = task_id, bytes = canonical команда.
    assert {c.event_id for c in commands} == {t.task_id for t in tasks}
    for command in commands:
        assert command.event_type == "projection.command"
        assert command.payload_bytes is not None
        assert sha256_hex(command.payload_bytes) == command.payload_sha256
        decoded = ProjectionCommand.model_validate_json(command.payload_bytes)
        assert decoded.task_id == command.event_id
        assert decoded.projection_version == command.aggregate_version


async def test_repeated_record_for_same_artifact_returns_same_task(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    first = await record(pg_session, entity.entity_uuid, 1)
    again = await record(pg_session, entity.entity_uuid, 1)
    assert not again.created
    assert again.task.task_id == first.task.task_id
    assert again.task.projection_version == 1
    assert again.outbox_event.outbox_id == first.outbox_event.outbox_id
    async with pg_session.begin():
        refreshed = await entities.get_entity(pg_session, entity.entity_uuid)
        counts = [
            await _count(pg_session, model)
            for model in (ParseAttempt, NormalizedArtifact, ProjectionTask, OutboxEvent)
        ]
    assert refreshed is not None and refreshed.projection_version == 1
    assert counts == [1, 1, 1, 1]


async def test_record_parse_result_requires_known_entity_and_matching_artifact_owner(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    other = await make_entity(pg_session, item="item-2")
    await record(pg_session, entity.entity_uuid, 1)
    async with pg_session.begin():
        # Той самий object_key, але інша сутність — конфлікт, а не тихий «той самий task».
        with pytest.raises(ConflictError):
            await projection.record_parse_result(
                pg_session,
                attempt=attempt_record(artifact_ref(other.entity_uuid, 2)),
                artifact_ref=artifact_ref(other.entity_uuid, 2),
                object_key=f"normalized/{1:064x}.json",
                target_collection="catalog_items",
                target_schema_version="1.0",
                now=T0,
            )
    async with pg_session.begin():
        with pytest.raises(NotFoundError, match="entity_index"):
            await projection.record_parse_result(
                pg_session,
                attempt=attempt_record(artifact_ref(new_entity_id(), 3)),
                artifact_ref=artifact_ref(new_entity_id(), 3),
                object_key="normalized/unknown.json",
                target_collection="catalog_items",
                target_schema_version="1.0",
                now=T0,
            )


async def test_projection_queue_claim_heartbeat_retry_release_and_recover(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    task_id = (await record(pg_session, entity.entity_uuid, 1)).task.task_id
    async with pg_session.begin():
        [claimed] = await projection.claim_projection_tasks(pg_session, "proj-1", 30, now=T0)
        assert (claimed.task_id, claimed.attempt, claimed.lease_owner) == (task_id, 1, "proj-1")
        assert await projection.claim_projection_tasks(pg_session, "proj-2", 30, now=T0) == []
        with pytest.raises(LeaseNotOwnedError):
            await projection.heartbeat_projection_task(pg_session, task_id, "proj-2", 30, now=T0)
        expires = await projection.heartbeat_projection_task(
            pg_session, task_id, "proj-1", 60, now=T0 + timedelta(seconds=10)
        )
        assert expires == T0 + timedelta(seconds=70)
        # Плановий drain: інкремент claim скасовано (CR-5), помилка не пишеться, claimable одразу.
        released = await projection.release_projection_task(
            pg_session, task_id, "proj-1", now=T0 + timedelta(seconds=11)
        )
        assert (released.status, released.attempt, released.last_error_code) == ("pending", 0, None)
        [again] = await projection.claim_projection_tasks(
            pg_session, "proj-2", 30, now=T0 + timedelta(seconds=11)
        )
        assert again.attempt == 1
        retried = await projection.retry_projection_task(
            pg_session,
            task_id,
            "proj-2",
            error_code="mongo_timeout",
            now=T0 + timedelta(seconds=12),
        )
        assert retried.status == "retry" and retried.not_before > T0 + timedelta(seconds=12)
        [third] = await projection.claim_projection_tasks(
            pg_session, "proj-3", 30, now=T0 + timedelta(hours=1)
        )
        assert third.attempt == 2
        recovered = await projection.recover_expired_projection_leases(
            pg_session, now=T0 + timedelta(hours=2)
        )
        assert recovered == [task_id]
    async with pg_session.begin():
        task = await projection.get_projection_task(pg_session, task_id)
    assert task is not None
    assert (task.status, task.attempt, task.lease_owner) == ("pending", 2, None)


async def test_retry_on_last_attempt_quarantines_projection_task(pg_session: AsyncSession) -> None:
    entity = await make_entity(pg_session)
    async with pg_session.begin():
        result = await projection.record_parse_result(
            pg_session,
            attempt=attempt_record(artifact_ref(entity.entity_uuid, 1)),
            artifact_ref=artifact_ref(entity.entity_uuid, 1),
            object_key="normalized/one.json",
            target_collection="catalog_items",
            target_schema_version="1.0",
            max_attempts=1,
            now=T0,
        )
        await projection.claim_projection_tasks(pg_session, "p", 30, now=T0)
        task = await projection.retry_projection_task(
            pg_session, result.task.task_id, "p", error_code="bad_payload", now=T0
        )
    assert (task.status, task.last_error_code) == ("quarantined", "bad_payload")


async def test_out_of_order_acks_never_lower_confirmed_version(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_sessions() as session:
        entity = await make_entity(session)
        tasks = {n: (await record(session, entity.entity_uuid, n)).task for n in (1, 2, 3)}
    # Projector застосував 3 до current; 1 і 2 прийшли пізніше — exact version records є,
    # але current вони не змінили (`applied_to_current=false`, `state_changed=false`).
    receipts = {
        3: receipt(tasks[3].task_id, entity.entity_uuid, 3, applied=True, changed=True),
        1: receipt(
            tasks[1].task_id, entity.entity_uuid, 1, applied=False, changed=False, current_version=3
        ),
        2: receipt(
            tasks[2].task_id, entity.entity_uuid, 2, applied=False, changed=False, current_version=3
        ),
    }
    confirmed_over_time: list[int] = []
    async with pg_sessions() as session:
        for n in (3, 1, 2):
            async with session.begin():
                result = await projection.acknowledge_projection(
                    session, tasks[n].task_id, receipts[n], now=T0
                )
            assert result.created
            async with session.begin():
                confirmed_over_time.append(
                    await entities.get_confirmed_version(session, entity.entity_uuid)
                )
            assert result.confirmed_projection_version == 3
            # domain.changed — лише для applied AND changed.
            assert (result.change_event is not None) == (n == 3)
            assert (result.outbox_event is not None) == (n == 3)
    assert confirmed_over_time == [3, 3, 3]
    async with pg_sessions() as session, session.begin():
        statuses = {
            t.projection_version: t.status for t in await session.scalars(select(ProjectionTask))
        }
        domain_rows = list(
            await session.scalars(select(OutboxEvent).where(OutboxEvent.topic == "domain"))
        )
        change_rows = list(await session.scalars(select(ChangeEvent)))
        entity_row = await entities.get_entity(session, entity.entity_uuid)
    assert statuses == {1: "succeeded", 2: "succeeded", 3: "succeeded"}
    assert entity_row is not None
    assert (entity_row.mongo_collection, entity_row.mongo_document_id) == (
        "catalog_items",
        entity.entity_uuid,
    )
    # Bytes у outbox/change_events побайтово ті самі, що в receipt, — без reserialization.
    [domain_row] = domain_rows
    [change_row] = change_rows
    source = receipts[3]
    assert source.event_bytes is not None
    assert domain_row.payload_bytes == source.event_bytes
    assert change_row.event_bytes == source.event_bytes
    assert domain_row.payload_sha256 == change_row.event_sha256 == source.event_sha256
    assert domain_row.event_id == change_row.event_id == source.event_id
    assert (domain_row.aggregate_id, domain_row.aggregate_version) == (entity.entity_uuid, 3)
    assert domain_row.event_type == decode_event(source.event_bytes).event_type


async def test_repeated_ack_is_idempotent_and_emits_no_second_event(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    task = (await record(pg_session, entity.entity_uuid, 1)).task
    applied = receipt(task.task_id, entity.entity_uuid, 1, applied=True, changed=True)
    async with pg_session.begin():
        first = await projection.acknowledge_projection(pg_session, task.task_id, applied, now=T0)
    async with pg_session.begin():
        again = await projection.acknowledge_projection(
            pg_session, task.task_id, applied, now=T0 + timedelta(minutes=5)
        )
    assert first.created and not again.created
    assert again.acknowledgement.acknowledged_at == T0
    assert again.change_event is None and again.outbox_event is None
    async with pg_session.begin():
        counts = [
            await _count(pg_session, model) for model in (ProjectionAcknowledgement, ChangeEvent)
        ]
        domain = await pg_session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "domain")
        )
    assert counts == [1, 1] and domain == 1


async def test_applied_without_state_change_emits_no_domain_event(
    pg_session: AsyncSession,
) -> None:
    """Heartbeat-projection: current оновлено (версія зросла), але стан той самий."""
    entity = await make_entity(pg_session)
    task = (await record(pg_session, entity.entity_uuid, 1)).task
    same_state = receipt(task.task_id, entity.entity_uuid, 1, applied=True, changed=False)
    async with pg_session.begin():
        result = await projection.acknowledge_projection(
            pg_session, task.task_id, same_state, now=T0
        )
        domain = await pg_session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "domain")
        )
    assert result.confirmed_projection_version == 1
    assert result.change_event is None and domain == 0


async def test_ack_rejects_receipt_of_another_task_or_version(pg_session: AsyncSession) -> None:
    entity = await make_entity(pg_session)
    first = (await record(pg_session, entity.entity_uuid, 1)).task
    second = (await record(pg_session, entity.entity_uuid, 2)).task
    foreign = receipt(second.task_id, entity.entity_uuid, 2, applied=True, changed=True)
    async with pg_session.begin():
        with pytest.raises(ConflictError):
            await projection.acknowledge_projection(pg_session, first.task_id, foreign, now=T0)
    wrong_version = receipt(first.task_id, entity.entity_uuid, 2, applied=True, changed=True)
    async with pg_session.begin():
        with pytest.raises(ConflictError):
            await projection.acknowledge_projection(
                pg_session, first.task_id, wrong_version, now=T0
            )


async def test_crash_between_ack_steps_leaves_no_partial_rows(pg_session: AsyncSession) -> None:
    """Транзакція ack відкочена «між кроками» (після insert ack) → нуль часткових записів;
    replay після рестарту дає рівно той самий результат, що й перший успішний ack."""
    entity_uuid = (await make_entity(pg_session)).entity_uuid
    # Rollback робить expire усім ORM-об'єктам сесії — ключі зберігаємо як значення.
    task_id = (await record(pg_session, entity_uuid, 1)).task.task_id
    applied = receipt(task_id, entity_uuid, 1, applied=True, changed=True)

    with pytest.raises(_Crash):
        async with pg_session.begin():
            result = await projection.acknowledge_projection(pg_session, task_id, applied, now=T0)
            assert result.created and result.outbox_event is not None
            raise _Crash

    async with pg_session.begin():
        counts = [
            await _count(pg_session, model) for model in (ProjectionAcknowledgement, ChangeEvent)
        ]
        domain = await pg_session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.topic == "domain")
        )
        stored = await projection.get_projection_task(pg_session, task_id)
        confirmed = await entities.get_confirmed_version(pg_session, entity_uuid)
    assert counts == [0, 0] and domain == 0
    assert stored is not None and stored.status == "pending"
    assert confirmed == 0

    async with pg_session.begin():
        replay = await projection.acknowledge_projection(pg_session, task_id, applied, now=T0)
    assert replay.created and replay.confirmed_projection_version == 1
    assert replay.outbox_event is not None
    assert replay.outbox_event.payload_bytes == applied.event_bytes


async def test_repeated_ack_with_a_different_receipt_is_a_conflict(
    pg_session: AsyncSession,
) -> None:
    """Gate 2, F-3: той самий receipt → ідемпотентно; інший для того самого task → конфлікт."""
    entity = await make_entity(pg_session)
    task_id = (await record(pg_session, entity.entity_uuid, 1)).task.task_id
    entity_uuid = entity.entity_uuid
    first = receipt(task_id, entity_uuid, 1, applied=True, changed=True)
    async with pg_session.begin():
        await projection.acknowledge_projection(pg_session, task_id, first, now=T0)
    async with pg_session.begin():
        same = await projection.acknowledge_projection(pg_session, task_id, first, now=T0)
    assert not same.created
    # Інший результат projector-а для того самого task (новий event_id/bytes).
    other = receipt(task_id, entity_uuid, 1, applied=True, changed=True)
    async with pg_session.begin():
        with pytest.raises(ConflictError, match="event_id"):
            await projection.acknowledge_projection(pg_session, task_id, other, now=T0)
    not_applied = receipt(task_id, entity_uuid, 1, applied=False, changed=False, current_version=1)
    async with pg_session.begin():
        with pytest.raises(ConflictError, match="applied_to_current"):
            await projection.acknowledge_projection(pg_session, task_id, not_applied, now=T0)
        acks = await _count(pg_session, ProjectionAcknowledgement)
        events = await _count(pg_session, ChangeEvent)
    assert (acks, events) == (1, 1)


async def test_state_a_b_a_issues_new_versions_and_reuses_the_artifact_row(
    pg_session: AsyncSession,
) -> None:
    """Gate 3, CR-1: ідемпотентність — за ідентичністю parse-кроку, не за вмістом.

    Стан A (fetch 1) → B (fetch 2) → знову byte-identical A (fetch 3) дає версії 1, 2, 3; третій
    task посилається на той самий рядок `normalized_artifacts`, що й перший. Повтор parse-кроку
    fetch 3 → той самий task без нової версії.
    """
    entity = await make_entity(pg_session)
    first = await record(pg_session, entity.entity_uuid, 1, fetch=1)
    second = await record(pg_session, entity.entity_uuid, 2, fetch=2)
    third = await record(pg_session, entity.entity_uuid, 1, fetch=3)
    replay = await record(pg_session, entity.entity_uuid, 1, fetch=3)
    assert [r.task.projection_version for r in (first, second, third)] == [1, 2, 3]
    assert third.created and not replay.created
    assert replay.task.task_id == third.task.task_id
    assert third.artifact.artifact_id == first.artifact.artifact_id
    assert third.task.parse_key not in {first.task.parse_key, second.task.parse_key}
    async with pg_session.begin():
        counts = [
            await _count(pg_session, model)
            for model in (NormalizedArtifact, ProjectionTask, ParseAttempt, OutboxEvent)
        ]
        refreshed = await entities.get_entity(pg_session, entity.entity_uuid)
    assert counts == [2, 3, 3, 3]
    assert refreshed is not None and refreshed.projection_version == 3


async def test_same_bytes_under_another_object_key_reuse_the_row_without_integrity_error(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    first = await record(pg_session, entity.entity_uuid, 1, fetch=1)
    async with pg_session.begin():
        other_key = await projection.record_parse_result(
            pg_session,
            attempt=attempt_record(artifact_ref(entity.entity_uuid, 1, fetch=2)),
            artifact_ref=artifact_ref(entity.entity_uuid, 1, fetch=2),
            object_key="normalized/other-key.json",
            target_collection="catalog_items",
            target_schema_version="1.0",
            now=T0,
        )
    assert other_key.created and other_key.task.projection_version == 2
    assert other_key.artifact.artifact_id == first.artifact.artifact_id


async def test_conflicting_artifact_for_key_or_parse_is_typed_conflict(
    pg_session: AsyncSession,
) -> None:
    entity = await make_entity(pg_session)
    await record(pg_session, entity.entity_uuid, 1, fetch=1)
    # Той самий object_key, інші bytes — content-addressed ключ не може змінити вміст.
    async with pg_session.begin():
        with pytest.raises(ConflictError, match="object_key"):
            await projection.record_parse_result(
                pg_session,
                attempt=attempt_record(artifact_ref(entity.entity_uuid, 2, fetch=2)),
                artifact_ref=artifact_ref(entity.entity_uuid, 2, fetch=2),
                object_key=f"normalized/{1:064x}.json",
                target_collection="catalog_items",
                target_schema_version="1.0",
                now=T0,
            )
    # Той самий parse-крок (fetch 1), але parser видав інші bytes — недетермінованість.
    async with pg_session.begin():
        with pytest.raises(ConflictError, match="недетермінований"):
            await projection.record_parse_result(
                pg_session,
                attempt=attempt_record(artifact_ref(entity.entity_uuid, 5, fetch=1)),
                artifact_ref=artifact_ref(entity.entity_uuid, 5, fetch=1),
                object_key=f"normalized/{5:064x}.json",
                target_collection="catalog_items",
                target_schema_version="1.0",
                now=T0,
            )


async def test_parse_outcome_decides_between_result_and_failure(pg_session: AsyncSession) -> None:
    """Gate 3, CR-10: artifact лише для `succeeded`/`partial`; failed/skipped — окремий запис."""
    entity = await make_entity(pg_session)
    failed = projection.ParseAttemptRecord(
        raw_sha256="0" * 64,
        parser_version="parser-1.0",
        outcome="failed",
        domain="catalog",
        error_code="selector_missing",
    )
    async with pg_session.begin():
        with pytest.raises(InvalidValueError, match="record_parse_failure"):
            await projection.record_parse_result(
                pg_session,
                attempt=failed,
                artifact_ref=artifact_ref(entity.entity_uuid, 1),
                object_key="normalized/x.json",
                target_collection="catalog_items",
                target_schema_version="1.0",
                now=T0,
            )
        with pytest.raises(InvalidValueError, match="record_parse_result"):
            await projection.record_parse_failure(pg_session, attempt_record(), now=T0)
        row = await projection.record_parse_failure(pg_session, failed, now=T0)
        counts = [
            await _count(pg_session, model)
            for model in (ParseAttempt, NormalizedArtifact, ProjectionTask)
        ]
    assert (row.outcome, row.error_code) == ("failed", "selector_missing")
    assert counts == [1, 0, 0]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"fetch_id": None}, "fetch_id"),
        ({"fetch_id": UUID(int=999)}, "fetch_id"),
        ({"raw_sha256": "f" * 64}, "raw_sha256"),
        ({"parser_version": "parser-2.0"}, "parser_version"),
        ({"domain": "vehicle"}, "domain"),
    ],
)
async def test_attempt_and_artifact_lineage_must_describe_one_parse(
    pg_session: AsyncSession, change: dict[str, object], field: str
) -> None:
    """Gate 4, SR-2: розбіжність lineage `attempt` ↔ `artifact_ref` → `InvalidValueError` до
    першого запису; `fetch_id` обов'язковий."""
    entity = await make_entity(pg_session)
    ref = artifact_ref(entity.entity_uuid, 1)
    attempt = dataclasses.replace(attempt_record(ref), **change)
    async with pg_session.begin():
        with pytest.raises(InvalidValueError, match=field):
            await projection.record_parse_result(
                pg_session,
                attempt=attempt,
                artifact_ref=ref,
                object_key="normalized/x.json",
                target_collection="catalog_items",
                target_schema_version="1.0",
                now=T0,
            )
        counts = [
            await _count(pg_session, model)
            for model in (ParseAttempt, NormalizedArtifact, ProjectionTask)
        ]
    assert counts == [0, 0, 0]
