"""Worker pools і масштабування §7.6: `worker_pools`, `worker_instances`, `scale_commands`.

- `worker_pools.role` — PK зі значень `WorkerRole`; desired state з optimistic `revision`;
  current replicas/concurrency не зберігаються — виводяться з heartbeat `worker_instances`
  (`repositories.pools.observed_capacity`);
- `worker_instances.instance_id` — boot UUID; `status`: `starting | ready | draining | stopped |
  stale`; `pool_revision` — остання desired revision, яку instance підтвердив;
  `drain_requested_at` — момент drain-запиту; поки він не скинутий, instance не може повернутись
  у `ready` через heartbeat (лише через явний `mark_ready` оператора/контролера);
- `scale_commands` — idempotency key, expected/applied pool revision, requested values,
  стани `requested | draining | awaiting_manual_apply | applying | applied | failed | superseded`
  (переходи `SCALE_COMMAND_TRANSITIONS`, валідація у репозиторії), `cli_command` для Compose
  mode, audit link (`audit_id`, `audit_created_at` — без FK на партиційований `audit_log`, щоб
  drop старої партиції не ламав команди).
"""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from collector.persistence.postgres.models.base import (
    Base,
    CreatedAt,
    Revision,
    UpdatedAt,
    UuidPk,
    enum_check,
)
from collector.workers.roles import WorkerRole

POOL_MODES: tuple[str, ...] = ("manual", "autoscale")
INSTANCE_STATUSES: tuple[str, ...] = ("starting", "ready", "draining", "stopped", "stale")
SCALE_COMMAND_STATUSES: tuple[str, ...] = (
    "requested",
    "draining",
    "awaiting_manual_apply",
    "applying",
    "applied",
    "failed",
    "superseded",
)
SCALE_COMMAND_TRANSITIONS: Final = MappingProxyType(
    {
        "requested": frozenset({"draining", "failed", "superseded"}),
        "draining": frozenset({"awaiting_manual_apply", "applying", "failed", "superseded"}),
        "awaiting_manual_apply": frozenset({"applying", "applied", "failed", "superseded"}),
        "applying": frozenset({"applied", "failed", "superseded"}),
        "applied": frozenset[str](),
        "failed": frozenset[str](),
        "superseded": frozenset[str](),
    }
)
"""`requested → draining → awaiting_manual_apply | applying → applied | failed | superseded`."""
SCALE_COMMAND_TERMINAL: frozenset[str] = frozenset({"applied", "failed", "superseded"})


class WorkerPool(Base):
    __tablename__ = "worker_pools"
    __table_args__ = (
        enum_check("role", WorkerRole, "role"),
        enum_check("mode", POOL_MODES, "mode"),
        CheckConstraint(
            "min_replicas >= 0 AND max_replicas >= min_replicas "
            "AND desired_replicas BETWEEN min_replicas AND max_replicas",
            name="replicas_range",
        ),
        CheckConstraint("desired_concurrency >= 1", name="concurrency"),
    )

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    desired_replicas: Mapped[int] = mapped_column(Integer, nullable=False)
    desired_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    min_replicas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_replicas: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="manual")
    resource_profile: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="default"
    )
    revision: Mapped[Revision]
    updated_by: Mapped[str | None] = mapped_column(String(128))
    update_reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class WorkerInstance(Base):
    __tablename__ = "worker_instances"
    __table_args__ = (
        enum_check("status", INSTANCE_STATUSES, "status"),
        CheckConstraint(
            "slots_total >= 0 AND slots_active >= 0 AND active_leases >= 0", name="slots"
        ),
        Index(
            "ix_worker_instances_role_status_last_heartbeat_at",
            "role",
            "status",
            "last_heartbeat_at",
        ),
    )

    instance_id: Mapped[UuidPk]
    role: Mapped[str] = mapped_column(
        ForeignKey("worker_pools.role", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="starting")
    deployment: Mapped[str | None] = mapped_column(String(128))
    container_id: Mapped[str | None] = mapped_column(String(128))
    hostname: Mapped[str | None] = mapped_column(String(256))
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    slots_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    slots_active: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    active_leases: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    pool_revision: Mapped[int | None] = mapped_column(BigInteger)
    # Намір drain зберігається окремо від `status`: інакше пара переходів
    # `draining → stale → ready` (heartbeat після паузи) мовчки скасовувала б drain,
    # ініційований scale-командою (M-2 код-рев'ю).
    drain_requested_at: Mapped[datetime | None]
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(nullable=False)
    stopped_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class ScaleCommand(Base):
    __tablename__ = "scale_commands"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        enum_check("status", SCALE_COMMAND_STATUSES, "status"),
        CheckConstraint(
            "requested_replicas >= 0 AND requested_concurrency >= 1", name="requested_values"
        ),
        Index("ix_scale_commands_role_status_created_at", "role", "status", "created_at"),
    )

    command_id: Mapped[UuidPk]
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(
        ForeignKey("worker_pools.role", ondelete="RESTRICT"), nullable=False
    )
    expected_pool_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    applied_pool_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requested_replicas: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="requested")
    cli_command: Mapped[str | None] = mapped_column(String(1024))
    result: Mapped[str | None] = mapped_column(String(2048))
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(512), nullable=False)
    audit_id: Mapped[UUID] = mapped_column(nullable=False)
    audit_created_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
    applied_at: Mapped[datetime | None]
