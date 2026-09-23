"""Domain change log і transactional outbox (§7.3 кроки 2/4, §9.1, §9.5; R-30, R-37, R-42).

Таблиці: `change_events`, `outbox_events`.

- `change_events` — детермінований журнал змін current state: рядок з'являється лише для
  receipt із `applied_to_current AND state_changed` (§9.5). Зберігає **готові** UTF-8 bytes
  події (`bytea`) і її SHA-256, скопійовані з Mongo receipt без повторної серіалізації
  (R-37/R-42), тому replay після crash дає byte-equivalent подію;
- `outbox_events` — черга доставки «щонайменше один раз» (§10 п.13). Несе два типи рядків:
  внутрішню команду `projection.command` (§7.3 п.2, R-30 — назовні не публікується, її топік
  `internal`) і публічну `domain.changed` (топік `domain`).

**Чому ці дві таблиці не партиціоновані по місяцях** (свідоме відхилення від «Спільних вимог»
картки): обидві мають нести справжній глобальний `UNIQUE (event_id)` — §9.1 «unique event ID»
і §7.3 «consumer дедуплікує за `event_id`», причому producer зобов'язаний не створювати
дублікатів узагалі. Declarative partitioning у PostgreSQL не вміє unique-обмеження без
partition key у ключі, тож `PARTITION BY RANGE (created_at)` перетворив би глобальну
унікальність на помісячну — тобто replay у наступному місяці тихо створив би другий рядок із
тим самим `event_id`. Розглянутий і відхилений варіант — зробити partition key
детермінованим від самої події (`occurred_at` з receipt), бо тоді інваріант тримається лише
доти, доки ніхто не змінить спосіб обчислення часу події, і база його не захищає.

Що робиться натомість: `outbox_events` — короткоживуча (publisher видаляє/архівує
опубліковані рядки), `change_events` — журнал, для якого PR3/WP-12 обирають archival за
`created_at` (той самий шлях, що й `version_archive_index`). Партиціонування повертається, якщо
з'явиться схема, що зберігає глобальний `event_id` (наприклад `PARTITION BY HASH (event_id)`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from collector.persistence.postgres.models.base import (
    Base,
    CreatedAt,
    UpdatedAt,
    UuidPk,
    enum_check,
)

OUTBOX_TOPICS: tuple[str, ...] = ("internal", "domain")
"""`internal` — `projection.command` (R-30: не публікується зовнішнім споживачам);
`domain` — `domain.changed` для зовнішніх consumers."""

PROJECTION_COMMAND_EVENT_TYPE = "projection.command"
"""`event_type` рядка outbox для внутрішньої команди projector-а (§7.3 п.2)."""

INLINE_EVENT_LIMIT_BYTES = 256 * 1024
"""`collector.contracts.events.EVENT_INLINE_LIMIT_BYTES` — більший payload іде в artifact."""


class ChangeEvent(Base):
    """Детермінований журнал `domain.changed` (§7.3 п.4).

    Рівно одне з `event_bytes` / `event_artifact_uri` — inline-ліміт 256 KiB той самий, що й у
    контракті `EncodedEvent`.
    """

    __tablename__ = "change_events"
    __table_args__ = (
        UniqueConstraint("event_id"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint(
            "(event_bytes IS NULL) <> (event_artifact_uri IS NULL)", name="inline_xor_artifact"
        ),
        CheckConstraint(
            f"event_bytes IS NULL OR octet_length(event_bytes) <= {INLINE_EVENT_LIMIT_BYTES}",
            name="inline_size",
        ),
        CheckConstraint("event_sha256 ~ '^[0-9a-f]{64}$'", name="event_sha256_hex"),
        Index("ix_change_events_aggregate_id_version", "aggregate_id", "aggregate_version"),
        Index("ix_change_events_created_at", "created_at"),
    )

    change_event_id: Mapped[UuidPk]
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    projection_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("projection_tasks.task_id", ondelete="RESTRICT"), nullable=False
    )
    previous_state_hash: Mapped[str | None] = mapped_column(String(80))
    result_state_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    event_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    event_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_artifact_uri: Mapped[str | None] = mapped_column(Text)
    event_artifact_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[CreatedAt]


class OutboxEvent(Base):
    """Transactional outbox: рядок пишеться в тій самій транзакції, що й зміна стану."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("event_id"),
        enum_check("topic", OUTBOX_TOPICS, "topic"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "(payload_bytes IS NULL) <> (payload_artifact_uri IS NULL)",
            name="inline_xor_artifact",
        ),
        CheckConstraint(
            f"payload_bytes IS NULL OR octet_length(payload_bytes) <= {INLINE_EVENT_LIMIT_BYTES}",
            name="inline_size",
        ),
        CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="payload_sha256_hex"),
        # Обов'язковий index §9.1: publisher-lookup `fetch_unpublished`.
        Index(
            "ix_outbox_events_published_at_available_at", "published_at", "available_at", "event_id"
        ),
        # Hot path publisher: лише неопубліковані й не припарковані рядки, у порядку ORDER BY.
        Index(
            "ix_outbox_events_unpublished",
            "available_at",
            "event_id",
            postgresql_where=text("published_at IS NULL AND parked_at IS NULL"),
        ),
        Index(
            "ix_outbox_events_parked_at",
            "parked_at",
            postgresql_where=text("parked_at IS NOT NULL"),
        ),
        Index("ix_outbox_events_aggregate_id", "aggregate_id"),
    )

    outbox_id: Mapped[UuidPk]
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    topic: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    payload_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_artifact_uri: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    published_at: Mapped[datetime | None]
    parked_at: Mapped[datetime | None]
    """Доставку зупинено після `max_attempts` (gate 3, CR-3): рядок чекає рішення оператора."""
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]
