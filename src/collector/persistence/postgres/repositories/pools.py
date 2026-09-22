"""Worker pools §7.6: desired state з optimistic revision, instances/heartbeat/stale,
scale commands зі станами `requested → draining → awaiting_manual_apply | applying → applied |
failed | superseded`.

Transaction boundaries:

- `request_scale` — desired-state update, supersede попередніх команд, audit-запис і insert
  команди в **одній** транзакції викликача (§7.6);
- `transition_scale_command('applied')` перевіряє heartbeat-derived capacity у тій самій
  транзакції (read-only перевірка);
- решта — одиночні UPDATE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from collector.contracts import JsonObject, new_entity_id
from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import (
    InvalidTransitionError,
    NotFoundError,
    StaleRevisionError,
)
from collector.persistence.postgres.models import (
    SCALE_COMMAND_TERMINAL,
    SCALE_COMMAND_TRANSITIONS,
    ScaleCommand,
    WorkerInstance,
    WorkerPool,
)
from collector.persistence.postgres.repositories.audit import append_audit
from collector.workers.roles import WorkerRole

INSTANCE_TRANSITIONS: dict[str, frozenset[str]] = {
    "starting": frozenset({"ready", "stopped", "stale"}),
    "ready": frozenset({"draining", "stopped", "stale"}),
    "draining": frozenset({"stopped", "stale"}),
    "stale": frozenset({"ready", "draining", "stopped"}),
    "stopped": frozenset[str](),
}
DEFAULT_HEARTBEAT_TTL = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class PoolDesiredState:
    desired_replicas: int
    desired_concurrency: int
    max_replicas: int
    min_replicas: int = 0
    mode: str = "manual"
    resource_profile: str = "default"


@dataclass(frozen=True, slots=True)
class ObservedCapacity:
    """Heartbeat-derived поточний стан pool (§7.6 «current replicas/concurrency»)."""

    ready_replicas: int
    total_slots: int
    min_pool_revision: int | None


async def upsert_pool(
    session: AsyncSession,
    role: WorkerRole,
    state: PoolDesiredState,
    *,
    actor: str,
    reason: str,
    expected_revision: int | None,
    now: datetime | None = None,
) -> WorkerPool:
    """Створює pool (`expected_revision=None`, revision=1) або оновлює desired state з
    optimistic revision (`StaleRevisionError` при розбіжності)."""
    current = resolve_now(now)
    if expected_revision is None:
        pool = WorkerPool(
            role=role.value,
            desired_replicas=state.desired_replicas,
            desired_concurrency=state.desired_concurrency,
            min_replicas=state.min_replicas,
            max_replicas=state.max_replicas,
            mode=state.mode,
            resource_profile=state.resource_profile,
            revision=1,
            updated_by=actor,
            update_reason=reason,
            created_at=current,
            updated_at=current,
        )
        session.add(pool)
        await session.flush()
        return pool
    updated = await session.scalar(
        update(WorkerPool)
        .where(WorkerPool.role == role.value, WorkerPool.revision == expected_revision)
        .values(
            desired_replicas=state.desired_replicas,
            desired_concurrency=state.desired_concurrency,
            min_replicas=state.min_replicas,
            max_replicas=state.max_replicas,
            mode=state.mode,
            resource_profile=state.resource_profile,
            revision=WorkerPool.revision + 1,
            updated_by=actor,
            update_reason=reason,
            updated_at=current,
        )
        .returning(WorkerPool)
    )
    if updated is None:
        await _raise_stale_or_missing_pool(session, role, expected_revision)
    return updated


async def get_pool(session: AsyncSession, role: WorkerRole) -> WorkerPool | None:
    return await session.get(WorkerPool, role.value)


async def list_pools(session: AsyncSession) -> list[WorkerPool]:
    return list((await session.execute(select(WorkerPool).order_by(WorkerPool.role))).scalars())


async def register_instance(
    session: AsyncSession,
    instance_id: UUID,
    role: WorkerRole,
    *,
    version: str,
    slots_total: int,
    deployment: str | None = None,
    container_id: str | None = None,
    hostname: str | None = None,
    pool_revision: int | None = None,
    now: datetime | None = None,
) -> WorkerInstance:
    """Реєструє boot instance у статусі `starting` (повторний виклик того самого
    `instance_id` — помилка викликача: boot UUID унікальний на процес)."""
    current = resolve_now(now)
    instance = WorkerInstance(
        instance_id=instance_id,
        role=role.value,
        status="starting",
        deployment=deployment,
        container_id=container_id,
        hostname=hostname,
        version=version,
        slots_total=slots_total,
        slots_active=0,
        active_leases=0,
        pool_revision=pool_revision,
        started_at=current,
        last_heartbeat_at=current,
        created_at=current,
        updated_at=current,
    )
    session.add(instance)
    await session.flush()
    return instance


async def heartbeat_instance(
    session: AsyncSession,
    instance_id: UUID,
    *,
    slots_total: int | None = None,
    slots_active: int,
    active_leases: int,
    pool_revision: int | None,
    now: datetime | None = None,
) -> WorkerInstance:
    """Оновлює heartbeat/slots/leases; `stale` → `ready` автоматично (instance повернувся);
    `stopped` не оживає (`InvalidTransitionError`)."""
    current = resolve_now(now)
    instance = await _lock_instance(session, instance_id)
    if instance.status == "stopped":
        msg = f"instance {instance_id} зупинений — heartbeat відхилено"
        raise InvalidTransitionError(msg)
    if instance.status == "stale":
        instance.status = "ready"
    instance.slots_active = slots_active
    instance.active_leases = active_leases
    if slots_total is not None:
        instance.slots_total = slots_total
    instance.pool_revision = pool_revision
    instance.last_heartbeat_at = current
    instance.updated_at = current
    await session.flush()
    return instance


async def set_instance_status(
    session: AsyncSession,
    instance_id: UUID,
    status: str,
    *,
    now: datetime | None = None,
) -> WorkerInstance:
    """`mark_ready/mark_draining/mark_stopped` — один перехід за `INSTANCE_TRANSITIONS`."""
    current = resolve_now(now)
    instance = await _lock_instance(session, instance_id)
    allowed = INSTANCE_TRANSITIONS.get(instance.status, frozenset())
    if status not in allowed:
        msg = f"instance {instance_id}: перехід {instance.status} → {status} недозволений"
        raise InvalidTransitionError(msg)
    instance.status = status
    instance.updated_at = current
    if status == "stopped":
        instance.stopped_at = current
        instance.slots_active = 0
        instance.active_leases = 0
    await session.flush()
    return instance


async def mark_draining(
    session: AsyncSession, instance_id: UUID, *, now: datetime | None = None
) -> WorkerInstance:
    return await set_instance_status(session, instance_id, "draining", now=now)


async def mark_stopped(
    session: AsyncSession, instance_id: UUID, *, now: datetime | None = None
) -> WorkerInstance:
    return await set_instance_status(session, instance_id, "stopped", now=now)


async def mark_ready(
    session: AsyncSession, instance_id: UUID, *, now: datetime | None = None
) -> WorkerInstance:
    return await set_instance_status(session, instance_id, "ready", now=now)


async def mark_stale_instances(
    session: AsyncSession,
    *,
    heartbeat_ttl: timedelta = DEFAULT_HEARTBEAT_TTL,
    now: datetime | None = None,
) -> list[UUID]:
    """Живі instances без heartbeat довше `heartbeat_ttl` → `stale`; повертає їх id
    (maintenance/controller tick)."""
    current = resolve_now(now)
    result = await session.execute(
        update(WorkerInstance)
        .where(
            WorkerInstance.status.in_(["starting", "ready", "draining"]),
            WorkerInstance.last_heartbeat_at < current - heartbeat_ttl,
        )
        .values(status="stale", updated_at=current)
        .returning(WorkerInstance.instance_id)
    )
    return list(result.scalars().all())


async def observed_capacity(
    session: AsyncSession,
    role: WorkerRole,
    *,
    heartbeat_ttl: timedelta = DEFAULT_HEARTBEAT_TTL,
    now: datetime | None = None,
) -> ObservedCapacity:
    """Ready instances зі свіжим heartbeat: кількість, сума slots, мінімальна підтверджена
    pool revision."""
    current = resolve_now(now)
    row = (
        await session.execute(
            select(
                func.count(WorkerInstance.instance_id),
                func.coalesce(func.sum(WorkerInstance.slots_total), 0),
                func.min(WorkerInstance.pool_revision),
            ).where(
                WorkerInstance.role == role.value,
                WorkerInstance.status == "ready",
                WorkerInstance.last_heartbeat_at >= current - heartbeat_ttl,
            )
        )
    ).one()
    return ObservedCapacity(
        ready_replicas=int(row[0]),
        total_slots=int(row[1]),
        min_pool_revision=None if row[2] is None else int(row[2]),
    )


async def request_scale(
    session: AsyncSession,
    role: WorkerRole,
    *,
    expected_revision: int,
    requested_replicas: int,
    requested_concurrency: int,
    actor: str,
    reason: str,
    idempotency_key: str,
    orchestrator: str = "compose",
    request_id: str | None = None,
    now: datetime | None = None,
) -> ScaleCommand:
    """Scale command §7.6 в одній транзакції: desired state (revision+1) + supersede активних
    команд role + `audit_log` + `scale_commands(requested)`.

    Повторний виклик з тим самим `idempotency_key` повертає існуючу команду без змін;
    stale `expected_revision` → `StaleRevisionError`; `orchestrator='compose'` заповнює
    `cli_command` (exact CLI для awaiting_manual_apply), `'swarm'` — контролер застосує сам.
    """
    if orchestrator not in {"compose", "swarm"}:
        msg = f"orchestrator має бути compose|swarm, отримано {orchestrator!r}"
        raise ValueError(msg)
    existing = await session.scalar(
        select(ScaleCommand).where(ScaleCommand.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing
    current = resolve_now(now)
    pool = await session.scalar(
        select(WorkerPool)
        .where(WorkerPool.role == role.value, WorkerPool.revision == expected_revision)
        .with_for_update()
    )
    if pool is None:
        await _raise_stale_or_missing_pool(session, role, expected_revision)
    before: JsonObject = {
        "desired_replicas": pool.desired_replicas,
        "desired_concurrency": pool.desired_concurrency,
        "revision": pool.revision,
    }
    pool.desired_replicas = requested_replicas
    pool.desired_concurrency = requested_concurrency
    pool.revision += 1
    pool.updated_by = actor
    pool.update_reason = reason
    pool.updated_at = current
    after: JsonObject = {
        "desired_replicas": pool.desired_replicas,
        "desired_concurrency": pool.desired_concurrency,
        "revision": pool.revision,
    }
    await session.flush()

    await session.execute(
        update(ScaleCommand)
        .where(
            ScaleCommand.role == role.value,
            ScaleCommand.status.not_in(list(SCALE_COMMAND_TERMINAL)),
        )
        .values(status="superseded", result="superseded by newer command", updated_at=current)
    )
    audit = await append_audit(
        session,
        actor=actor,
        action="worker_pool.scale",
        resource_type="worker_pool",
        resource_id=role.value,
        before=before,
        after=after,
        request_id=request_id,
        idempotency_key=idempotency_key,
        now=current,
    )
    command = ScaleCommand(
        command_id=new_entity_id(),
        idempotency_key=idempotency_key,
        role=role.value,
        expected_pool_revision=expected_revision,
        applied_pool_revision=pool.revision,
        requested_replicas=requested_replicas,
        requested_concurrency=requested_concurrency,
        status="requested",
        cli_command=(
            f"docker compose up -d --no-recreate --scale worker-{role.value}={requested_replicas}"
            if orchestrator == "compose"
            else None
        ),
        actor=actor,
        reason=reason,
        audit_id=audit.audit_id,
        audit_created_at=audit.created_at,
        created_at=current,
        updated_at=current,
    )
    session.add(command)
    await session.flush()
    return command


async def transition_scale_command(
    session: AsyncSession,
    command_id: UUID,
    status: str,
    *,
    result: str | None = None,
    heartbeat_ttl: timedelta = DEFAULT_HEARTBEAT_TTL,
    now: datetime | None = None,
) -> ScaleCommand:
    """Перехід стану за `SCALE_COMMAND_TRANSITIONS`; недозволений → `InvalidTransitionError`.
    `applied` дозволений лише коли heartbeat-derived ready replicas і сумарні slots
    відповідають requested values і всі ready instances підтвердили `applied_pool_revision`."""
    current = resolve_now(now)
    command = await session.scalar(
        select(ScaleCommand).where(ScaleCommand.command_id == command_id).with_for_update()
    )
    if command is None:
        msg = f"scale command {command_id} не знайдено"
        raise NotFoundError(msg)
    allowed = SCALE_COMMAND_TRANSITIONS.get(command.status, frozenset())
    if status not in allowed:
        msg = f"scale command {command_id}: перехід {command.status} → {status} недозволений"
        raise InvalidTransitionError(msg)
    if status == "applied":
        observed = await observed_capacity(
            session, WorkerRole(command.role), heartbeat_ttl=heartbeat_ttl, now=current
        )
        expected_slots = command.requested_replicas * command.requested_concurrency
        revision_ok = (
            observed.min_pool_revision is not None
            and observed.min_pool_revision >= command.applied_pool_revision
        ) or command.requested_replicas == 0
        if (
            observed.ready_replicas != command.requested_replicas
            or observed.total_slots != expected_slots
            or not revision_ok
        ):
            msg = (
                f"scale command {command_id}: applied відхилено — observed "
                f"replicas={observed.ready_replicas}/{command.requested_replicas}, "
                f"slots={observed.total_slots}/{expected_slots}, "
                f"min_pool_revision={observed.min_pool_revision}/{command.applied_pool_revision}"
            )
            raise InvalidTransitionError(msg)
        command.applied_at = current
    command.status = status
    command.result = result
    command.updated_at = current
    await session.flush()
    return command


async def get_scale_command(session: AsyncSession, command_id: UUID) -> ScaleCommand | None:
    return await session.get(ScaleCommand, command_id)


async def _lock_instance(session: AsyncSession, instance_id: UUID) -> WorkerInstance:
    instance = await session.get(WorkerInstance, instance_id, with_for_update=True)
    if instance is None:
        msg = f"worker instance {instance_id} не знайдено"
        raise NotFoundError(msg)
    return instance


async def _raise_stale_or_missing_pool(
    session: AsyncSession, role: WorkerRole, expected_revision: int
) -> NoReturn:
    actual = await session.scalar(select(WorkerPool.revision).where(WorkerPool.role == role.value))
    if actual is None:
        msg = f"worker pool {role.value!r} не знайдено"
        raise NotFoundError(msg)
    msg = f"worker pool {role.value!r}: revision {expected_revision} застаріла (поточна {actual})"
    raise StaleRevisionError(msg)
