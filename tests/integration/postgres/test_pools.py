"""Worker pools §7.6: stale revision, scale command transitions, idempotent request_scale,
applied лише при відповідності heartbeat-derived capacity, stale instances."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import new_entity_id
from collector.persistence.postgres.errors import (
    InvalidTransitionError,
    InvalidValueError,
    NotFoundError,
    StaleRevisionError,
)
from collector.persistence.postgres.models import AuditLog, ScaleCommand
from collector.persistence.postgres.repositories import pools
from collector.persistence.postgres.repositories.audit import get_audit
from collector.workers.roles import WorkerRole

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
FETCH_STATE = pools.PoolDesiredState(desired_replicas=2, desired_concurrency=8, max_replicas=6)


async def _fetch_pool(session: AsyncSession) -> None:
    async with session.begin():
        await pools.upsert_pool(
            session,
            WorkerRole.FETCH,
            FETCH_STATE,
            actor="bootstrap",
            reason="defaults",
            expected_revision=None,
            now=T0,
        )


async def test_stale_revision_is_rejected(pg_session: AsyncSession) -> None:
    await _fetch_pool(pg_session)
    async with pg_session.begin():
        updated = await pools.upsert_pool(
            pg_session,
            WorkerRole.FETCH,
            pools.PoolDesiredState(desired_replicas=3, desired_concurrency=8, max_replicas=6),
            actor="op",
            reason="more",
            expected_revision=1,
            now=T0,
        )
    assert (updated.revision, updated.desired_replicas) == (2, 3)
    async with pg_session.begin():
        with pytest.raises(StaleRevisionError):
            await pools.upsert_pool(
                pg_session,
                WorkerRole.FETCH,
                FETCH_STATE,
                actor="op",
                reason="stale gui",
                expected_revision=1,
                now=T0,
            )
    async with pg_session.begin():
        with pytest.raises(StaleRevisionError):
            await pools.request_scale(
                pg_session,
                WorkerRole.FETCH,
                expected_revision=1,
                requested_replicas=4,
                requested_concurrency=8,
                actor="op",
                reason="stale",
                idempotency_key="s1",
                now=T0,
            )
        with pytest.raises(NotFoundError):
            await pools.request_scale(
                pg_session,
                WorkerRole.BROWSER,
                expected_revision=1,
                requested_replicas=1,
                requested_concurrency=1,
                actor="op",
                reason="no pool",
                idempotency_key="s2",
                now=T0,
            )


async def test_request_scale_is_one_transaction_and_idempotent(pg_session: AsyncSession) -> None:
    await _fetch_pool(pg_session)
    async with pg_session.begin():
        command = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=1,
            requested_replicas=4,
            requested_concurrency=8,
            actor="op",
            reason="load",
            idempotency_key="scale-1",
            request_id="req-1",
            now=T0,
        )
        again = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=1,
            requested_replicas=4,
            requested_concurrency=8,
            actor="op",
            reason="load",
            idempotency_key="scale-1",
            now=T0,
        )
    assert again.command_id == command.command_id
    assert (command.status, command.expected_pool_revision, command.applied_pool_revision) == (
        "requested",
        1,
        2,
    )
    assert command.cli_command == "docker compose up -d --no-recreate --scale worker-fetch=4"
    async with pg_session.begin():
        pool = await pools.get_pool(pg_session, WorkerRole.FETCH)
        assert pool is not None
        assert (pool.revision, pool.desired_replicas, pool.updated_by) == (2, 4, "op")
        audit = await get_audit(pg_session, command.audit_id, command.audit_created_at)
        assert audit is not None
        assert audit.action == "worker_pool.scale"
        assert audit.before_state == {
            "desired_replicas": 2,
            "desired_concurrency": 8,
            "revision": 1,
        }
        assert audit.after_state == {"desired_replicas": 4, "desired_concurrency": 8, "revision": 2}
        assert audit.request_id == "req-1"
        assert await pg_session.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert await pg_session.scalar(select(func.count()).select_from(ScaleCommand)) == 1

    # Нова команда supersede-ить активну попередню.
    async with pg_session.begin():
        newer = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=2,
            requested_replicas=1,
            requested_concurrency=8,
            actor="op",
            reason="down",
            idempotency_key="scale-2",
            now=T0,
        )
        previous = await pools.get_scale_command(pg_session, command.command_id)
    assert newer.applied_pool_revision == 3
    assert previous is not None and previous.status == "superseded"


async def test_scale_command_disallowed_transition_is_rejected(pg_session: AsyncSession) -> None:
    await _fetch_pool(pg_session)
    async with pg_session.begin():
        command = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=1,
            requested_replicas=1,
            requested_concurrency=8,
            actor="op",
            reason="x",
            idempotency_key="k",
            now=T0,
        )
    for bad in ("applied", "applying", "awaiting_manual_apply", "requested"):
        async with pg_session.begin():
            with pytest.raises(InvalidTransitionError):
                await pools.transition_scale_command(pg_session, command.command_id, bad, now=T0)
    async with pg_session.begin():
        cmd = await pools.transition_scale_command(
            pg_session, command.command_id, "draining", now=T0
        )
        cmd = await pools.transition_scale_command(
            pg_session, command.command_id, "awaiting_manual_apply", now=T0
        )
    assert cmd.status == "awaiting_manual_apply"
    async with pg_session.begin():
        failed = await pools.transition_scale_command(
            pg_session, command.command_id, "failed", result="compose exit 1", now=T0
        )
    assert failed.status == "failed"
    async with pg_session.begin():
        with pytest.raises(InvalidTransitionError):  # термінальний
            await pools.transition_scale_command(pg_session, command.command_id, "applying", now=T0)


async def test_applied_requires_heartbeat_derived_capacity_to_match(
    pg_session: AsyncSession,
) -> None:
    await _fetch_pool(pg_session)
    async with pg_session.begin():
        command = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=1,
            requested_replicas=2,
            requested_concurrency=4,
            actor="op",
            reason="x",
            idempotency_key="k",
            orchestrator="swarm",
            now=T0,
        )
        await pools.transition_scale_command(pg_session, command.command_id, "draining", now=T0)
        await pools.transition_scale_command(pg_session, command.command_id, "applying", now=T0)
    assert command.cli_command is None
    instances = [new_entity_id(), new_entity_id()]
    async with pg_session.begin():
        for instance_id in instances:
            await pools.register_instance(
                pg_session,
                instance_id,
                WorkerRole.FETCH,
                version="0.1.0",
                slots_total=4,
                pool_revision=1,
                now=T0,
            )
    # Instances ще `starting` і на старій revision → applied відхилено.
    async with pg_session.begin():
        with pytest.raises(InvalidTransitionError, match="applied відхилено"):
            await pools.transition_scale_command(pg_session, command.command_id, "applied", now=T0)
    async with pg_session.begin():
        for instance_id in instances:
            await pools.mark_ready(pg_session, instance_id, now=T0)
            await pools.heartbeat_instance(
                pg_session, instance_id, slots_active=0, active_leases=0, pool_revision=2, now=T0
            )
        observed = await pools.observed_capacity(pg_session, WorkerRole.FETCH, now=T0)
    assert observed == pools.ObservedCapacity(ready_replicas=2, total_slots=8, min_pool_revision=2)
    async with pg_session.begin():
        applied = await pools.transition_scale_command(
            pg_session, command.command_id, "applied", now=T0
        )
    assert applied.status == "applied" and applied.applied_at == T0


async def test_instance_lifecycle_and_stale_detection(pg_session: AsyncSession) -> None:
    await _fetch_pool(pg_session)
    instance_id = new_entity_id()
    async with pg_session.begin():
        await pools.register_instance(
            pg_session, instance_id, WorkerRole.FETCH, version="v", slots_total=8, now=T0
        )
        with pytest.raises(InvalidTransitionError):
            await pools.mark_draining(pg_session, instance_id, now=T0)  # starting → draining
        await pools.mark_ready(pg_session, instance_id, now=T0)
    late = T0 + timedelta(minutes=5)
    async with pg_session.begin():
        assert await pools.mark_stale_instances(pg_session, now=late) == [instance_id]
        assert await pools.observed_capacity(
            pg_session, WorkerRole.FETCH, now=late
        ) == pools.ObservedCapacity(0, 0, None)
        revived = await pools.heartbeat_instance(
            pg_session, instance_id, slots_active=2, active_leases=2, pool_revision=1, now=late
        )
        assert revived.status == "ready"
        await pools.mark_draining(pg_session, instance_id, now=late)
        stopped = await pools.mark_stopped(pg_session, instance_id, now=late)
        assert (stopped.status, stopped.slots_active, stopped.stopped_at) == ("stopped", 0, late)
        with pytest.raises(InvalidTransitionError):
            await pools.heartbeat_instance(
                pg_session, instance_id, slots_active=0, active_leases=0, pool_revision=1, now=late
            )


async def test_invalid_scale_values_raise_domain_error_without_writes(
    pg_session: AsyncSession,
) -> None:
    """L-1: невалідні значення → `InvalidValueError` до першого запису, не сирий
    `IntegrityError` на CHECK; desired state, audit і команди лишаються незмінними."""
    await _fetch_pool(pg_session)
    bad_values = [
        {"requested_replicas": 2, "requested_concurrency": 0},  # CHECK concurrency >= 1
        {"requested_replicas": -1, "requested_concurrency": 8},  # CHECK replicas >= 0
        {"requested_replicas": 99, "requested_concurrency": 8},  # поза max_replicas pool
    ]
    async with pg_session.begin():
        for index, values in enumerate(bad_values):
            with pytest.raises(InvalidValueError):
                await pools.request_scale(
                    pg_session,
                    WorkerRole.FETCH,
                    expected_revision=1,
                    actor="op",
                    reason="bad",
                    idempotency_key=f"bad-{index}",
                    now=T0,
                    **values,
                )
        # Транзакція жива (не IntegrityError) — можна працювати далі в ній же.
        pool = await pools.get_pool(pg_session, WorkerRole.FETCH)
        assert pool is not None
        assert (pool.revision, pool.desired_replicas, pool.desired_concurrency) == (1, 2, 8)
        assert await pg_session.scalar(select(func.count()).select_from(ScaleCommand)) == 0
        assert await pg_session.scalar(select(func.count()).select_from(AuditLog)) == 0

        # Валідне значення після відхилених — проходить у тій самій транзакції.
        ok = await pools.request_scale(
            pg_session,
            WorkerRole.FETCH,
            expected_revision=1,
            requested_replicas=3,
            requested_concurrency=4,
            actor="op",
            reason="ok",
            idempotency_key="ok",
            now=T0,
        )
    assert (ok.status, ok.requested_replicas) == ("requested", 3)


async def test_invalid_pool_desired_state_is_rejected_before_write(
    pg_session: AsyncSession,
) -> None:
    """`upsert_pool` валідовує desired state (ті самі інваріанти, що й CHECK) до запису."""
    invalid = [
        pools.PoolDesiredState(desired_replicas=1, desired_concurrency=0, max_replicas=4),
        pools.PoolDesiredState(desired_replicas=9, desired_concurrency=1, max_replicas=4),
        pools.PoolDesiredState(
            desired_replicas=1, desired_concurrency=1, max_replicas=0, min_replicas=2
        ),
        pools.PoolDesiredState(
            desired_replicas=1, desired_concurrency=1, max_replicas=4, mode="turbo"
        ),
    ]
    async with pg_session.begin():
        for state in invalid:
            with pytest.raises(InvalidValueError):
                await pools.upsert_pool(
                    pg_session,
                    WorkerRole.PARSE,
                    state,
                    actor="op",
                    reason="bad",
                    expected_revision=None,
                    now=T0,
                )
        assert await pools.get_pool(pg_session, WorkerRole.PARSE) is None
        created = await pools.upsert_pool(
            pg_session,
            WorkerRole.PARSE,
            pools.PoolDesiredState(desired_replicas=2, desired_concurrency=4, max_replicas=4),
            actor="op",
            reason="ok",
            expected_revision=None,
            now=T0,
        )
    assert created.revision == 1
