"""Projection command / receipt / acknowledgement (§7.3 кроки 2–4, §9.1, §9.2; R-30, R-36, R-37).

Потік: PostgreSQL видає `ProjectionCommand` (внутрішня команда, не публікується) →
Mongo Projector у одній транзакції записує version record, умовно оновлює current document і
вставляє `AppliedProjectionReceipt` з готовими event bytes → після Mongo commit PostgreSQL
фіксує `ProjectionAcknowledgement`; `domain.changed` створюється лише за правилом
`should_emit_domain_changed(receipt)`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import SchemaVersion, VersionedDocument
from collector.contracts.artifacts import ArtifactRef, NormalizedArtifactRef
from collector.contracts.canonical import sha256_hex
from collector.contracts.events import EVENT_INLINE_LIMIT_BYTES
from collector.contracts.identity import EntityId, Sha256Hex
from collector.contracts.temporal import UtcDatetime

CollectionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=120)]
StateHash = Annotated[str, StringConstraints(pattern=r"^v[1-9][0-9]*:[0-9a-f]{64}$")]
"""`state_hash` — versioned (`v1:<sha256>`), див. `current.compute_state_hash_v1`."""


class ProjectionCommand(VersionedDocument):
    """`projection.command` — внутрішня команда projector (§7.3 п.2), не публічна подія (R-30)."""

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    task_id: UUID
    entity_uuid: EntityId
    projection_version: int = Field(ge=1, description="Монотонна per-entity версія (R-36).")
    target_collection: CollectionName
    target_schema_version: SchemaVersion = Field(
        description="Версія `$jsonSchema` цільової collection."
    )
    artifact: NormalizedArtifactRef
    priority: int = Field(default=0, ge=0, le=100)
    not_before: UtcDatetime | None = None
    issued_at: UtcDatetime

    @model_validator(mode="after")
    def _artifact_entity_matches(self) -> ProjectionCommand:
        if self.artifact.entity_uuid != self.entity_uuid:
            msg = "artifact.entity_uuid не збігається з entity_uuid команди"
            raise ValueError(msg)
        return self


class AppliedProjectionReceipt(VersionedDocument):
    """`applied_projection_receipts` (§9.2): результат однієї projection task у Mongo.

    Event descriptor (`event_bytes`/`event_media_type`/`event_sha256` або `event_artifact`)
    присутній тоді й лише тоді, коли `should_emit_domain_changed(receipt)`; reconciler копіює
    bytes без повторної серіалізації (R-37, R-42).
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    projection_task_id: UUID
    entity_uuid: EntityId
    projection_version: int = Field(ge=1)
    target_collection: CollectionName
    document_id: UUID
    applied_to_current: bool
    state_changed: bool
    previous_version: int | None = Field(default=None, ge=1)
    previous_hash: StateHash | None = None
    result_version: int = Field(ge=1, description="Current version після task.")
    result_hash: StateHash
    event_id: UUID | None = None
    event_bytes: bytes | None = None
    event_media_type: str | None = None
    event_sha256: Sha256Hex | None = None
    event_artifact: ArtifactRef | None = None
    committed_at: UtcDatetime
    cluster_time: Annotated[str, StringConstraints(min_length=1)] = Field(
        description="Mongo cluster time commit (рядок Timestamp `<t>:<i>`)."
    )

    @model_validator(mode="after")
    def _consistent(self) -> AppliedProjectionReceipt:
        emit = should_emit_domain_changed(self)
        event_bytes = self.event_bytes
        has_inline = event_bytes is not None
        has_artifact = self.event_artifact is not None
        if emit:
            if has_inline == has_artifact:
                msg = "applied+changed receipt вимагає рівно одне: event_bytes або event_artifact"
                raise ValueError(msg)
            if self.event_id is None:
                msg = "event_id обов'язковий, коли створюється domain.changed"
                raise ValueError(msg)
        elif has_inline or has_artifact or self.event_id is not None:
            msg = "event descriptor заборонений, якщо не applied_to_current AND state_changed"
            raise ValueError(msg)
        if event_bytes is not None:
            if len(event_bytes) > EVENT_INLINE_LIMIT_BYTES:
                msg = f"event_bytes > {EVENT_INLINE_LIMIT_BYTES}: використайте event_artifact"
                raise ValueError(msg)
            if self.event_media_type is None or self.event_sha256 is None:
                msg = "event_bytes вимагає event_media_type і event_sha256"
                raise ValueError(msg)
            if sha256_hex(event_bytes) != self.event_sha256:
                msg = "event_sha256 не збігається з sha256(event_bytes)"
                raise ValueError(msg)
        if self.applied_to_current and self.result_version != self.projection_version:
            msg = "applied_to_current вимагає result_version == projection_version"
            raise ValueError(msg)
        if not self.applied_to_current and self.result_version < self.projection_version:
            msg = "не applied task не може мати result_version < projection_version"
            raise ValueError(msg)
        if self.state_changed and self.previous_hash == self.result_hash:
            msg = "state_changed=true, але previous_hash == result_hash"
            raise ValueError(msg)
        return self


def should_emit_domain_changed(receipt: AppliedProjectionReceipt) -> bool:
    """§7.3 п.4 / §9.5: `domain.changed` лише для `applied_to_current AND state_changed`."""
    return receipt.applied_to_current and receipt.state_changed


class ProjectionAcknowledgement(VersionedDocument):
    """`projection_acknowledgements` (§9.1): PostgreSQL-фіксація Mongo receipt (§7.3 п.4)."""

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    task_id: UUID
    entity_uuid: EntityId
    projection_version: int = Field(ge=1)
    receipt_id: UUID = Field(description="`projection_task_id` receipt у Mongo.")
    receipt_cluster_time: Annotated[str, StringConstraints(min_length=1)]
    applied_to_current: bool
    state_changed: bool
    acknowledged_at: UtcDatetime
    result_hash: StateHash
    event_sha256: Sha256Hex | None = None

    @classmethod
    def from_receipt(
        cls, receipt: AppliedProjectionReceipt, acknowledged_at: UtcDatetime
    ) -> ProjectionAcknowledgement:
        """Побудувати acknowledgement з receipt без повторної серіалізації event."""
        event_sha256 = receipt.event_sha256
        if event_sha256 is None and receipt.event_artifact is not None:
            event_sha256 = receipt.event_artifact.sha256
        return cls(
            task_id=receipt.projection_task_id,
            entity_uuid=receipt.entity_uuid,
            projection_version=receipt.projection_version,
            receipt_id=receipt.projection_task_id,
            receipt_cluster_time=receipt.cluster_time,
            applied_to_current=receipt.applied_to_current,
            state_changed=receipt.state_changed,
            acknowledged_at=acknowledged_at,
            result_hash=receipt.result_hash,
            event_sha256=event_sha256,
        )
