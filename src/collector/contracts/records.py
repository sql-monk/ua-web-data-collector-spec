"""Mongo version/observation/review records (§9.2, §9.3 п.5; R-36).

- `EntityProjectionVersion` — `entity_projection_versions`: запис для **кожної** projection
  task (R-36), exact-version read/export (§9.5); bounded snapshot або normalized artifact ref.
- `ObservationRecord` — offer/listing observation (`catalog_offer_observations`,
  `vehicle_observations`): лише при зміні state hash або heartbeat (§9.3 п.5).
- `SellerContactObservation` — `contact_observations`: typed + original contact values.
- `ReviewQuestionRecord` — `product_reviews`/`product_questions`: мінімальні обов'язкові поля.

Усі записи мають `_id` UUID (генерує projector) і `schema_version` `major.minor`. Доменні поля
observation/contact/review додають WP-07/WP-09 **optional**-полями minor-версії через
dependency-запит до WP-01C (`docs/contracts.md`, розділ 12).
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import (
    BoundedJsonObject,
    ContractModel,
    SchemaVersion,
    VersionedDocument,
)
from collector.contracts.artifacts import NormalizedArtifactRef
from collector.contracts.current import Lineage, SourceRef, compute_state_hash_v1
from collector.contracts.enums import EntityKind, ObservationReason, ReviewQuestionKind
from collector.contracts.identity import EntityId, IdentityHash, SourceIdentity
from collector.contracts.payload import ObservedValues
from collector.contracts.projection import CollectionName, StateHash
from collector.contracts.temporal import EntityTime, UtcDatetime
from collector.contracts.values import ContactValue

MAX_CONTACTS_PER_OBSERVATION: Final = 64
OBSERVED_ENTITY_KINDS: Final = frozenset({EntityKind.CATALOG_OFFER, EntityKind.VEHICLE_LISTING})
"""`entity_kind`, для яких існують offer/listing observations (§9.2)."""


def _check_lineage_task(lineage: Lineage, projection_task_id: UUID) -> None:
    if lineage.projection_task_id != projection_task_id:
        msg = "lineage.projection_task_id не збігається з projection_task_id запису"
        raise ValueError(msg)


class VersionSnapshot(ContractModel):
    """Bounded snapshot однієї версії (§9.2): те, що стало б current document цієї версії."""

    source: SourceRef
    identity_hash: IdentityHash
    core: BoundedJsonObject
    attributes: BoundedJsonObject = Field(default_factory=dict)
    latest_state: BoundedJsonObject = Field(default_factory=dict)
    time: EntityTime

    def state_hash(self) -> str:
        """`compute_state_hash_v1(core, attributes, latest_state)` snapshot-а."""
        return compute_state_hash_v1(self.core, self.attributes, self.latest_state)


class EntityProjectionVersion(VersionedDocument):
    """`entity_projection_versions` (§9.2, R-36): версія для кожної task, незалежно від CAS.

    `previous_version`/`previous_state_hash` — current document у момент task (`None` — current
    ще не існував). Інваріанти CAS: `applied_to_current` ⇔ current відсутній або старіший;
    `state_changed` ⇔ `state_hash` ≠ `previous_state_hash`. Потрібне хоча б одне з `snapshot`
    (bounded, §9.2) або `artifact` (immutable normalized artifact ref).
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    id: UUID = Field(alias="_id", description="ID запису (UUID, генерує projector).")
    entity_uuid: EntityId
    entity_kind: EntityKind
    projection_version: int = Field(ge=1, description="Монотонна per-entity_uuid (int64).")
    projection_task_id: UUID
    target_collection: CollectionName = Field(description="Current collection сутності.")
    state_hash: StateHash
    state_changed: bool
    previous_version: int | None = Field(default=None, ge=1)
    previous_state_hash: StateHash | None = None
    snapshot: VersionSnapshot | None = None
    artifact: NormalizedArtifactRef | None = None
    lineage: Lineage
    applied_to_current: bool
    recorded_at: UtcDatetime = Field(description="Час запису version record (Mongo commit).")

    @model_validator(mode="after")
    def _consistent(self) -> EntityProjectionVersion:
        _check_lineage_task(self.lineage, self.projection_task_id)
        if self.snapshot is None and self.artifact is None:
            msg = "потрібне snapshot або artifact (§9.2)"
            raise ValueError(msg)
        if self.snapshot is not None and self.snapshot.state_hash() != self.state_hash:
            msg = "state_hash не збігається з compute_state_hash_v1(snapshot)"
            raise ValueError(msg)
        if self.artifact is not None and self.artifact.entity_uuid != self.entity_uuid:
            msg = "artifact.entity_uuid не збігається з entity_uuid"
            raise ValueError(msg)
        if (self.previous_version is None) != (self.previous_state_hash is None):
            msg = "previous_version і previous_state_hash задаються разом"
            raise ValueError(msg)
        if self.state_changed == (self.previous_state_hash == self.state_hash):
            msg = "state_changed має дорівнювати state_hash ≠ previous_state_hash"
            raise ValueError(msg)
        newer_than_current = (
            self.previous_version is None or self.previous_version < self.projection_version
        )
        if self.applied_to_current != newer_than_current:
            msg = "applied_to_current має дорівнювати previous_version is None or < version (CAS)"
            raise ValueError(msg)
        return self


class ObservationRecord(VersionedDocument):
    """Offer/listing observation (§9.2, §9.3 п.5): `changed` або `heartbeat`.

    Створюється лише для `applied_to_current` task, коли змінився state hash (`changed`) або
    сплив heartbeat interval (`heartbeat`); unique `projection_task_id` робить replay
    ідемпотентним.
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    id: UUID = Field(alias="_id", description="ID observation (UUID, генерує projector).")
    entity_uuid: EntityId = Field(description="Parent offer/listing entity UUID.")
    entity_kind: EntityKind
    projection_version: int = Field(ge=1)
    projection_task_id: UUID
    reason: ObservationReason
    observed_at: UtcDatetime
    state_hash: StateHash
    observed: ObservedValues
    artifact: NormalizedArtifactRef | None = None
    lineage: Lineage

    @model_validator(mode="after")
    def _consistent(self) -> ObservationRecord:
        _check_lineage_task(self.lineage, self.projection_task_id)
        if self.entity_kind not in OBSERVED_ENTITY_KINDS:
            msg = f"observation лише для {sorted(OBSERVED_ENTITY_KINDS)}, не {self.entity_kind}"
            raise ValueError(msg)
        if self.artifact is not None and self.artifact.entity_uuid != self.entity_uuid:
            msg = "artifact.entity_uuid не збігається з entity_uuid"
            raise ValueError(msg)
        return self


class SellerContactObservation(VersionedDocument):
    """`contact_observations` (§9.2): typed contact values (+ raw) продавця на момент observation.

    Публічні контакти живуть лише в domain collections (§18); у логах/fixtures — ні.
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    id: UUID = Field(alias="_id", description="ID observation (UUID, генерує projector).")
    seller_id: EntityId = Field(description="Seller entity UUID (назва поля — index §9.2).")
    source: SourceIdentity
    contacts: list[ContactValue] = Field(min_length=1, max_length=MAX_CONTACTS_PER_OBSERVATION)
    observed_at: UtcDatetime
    projection_version: int = Field(ge=1)
    projection_task_id: UUID
    lineage: Lineage

    @model_validator(mode="after")
    def _consistent(self) -> SellerContactObservation:
        _check_lineage_task(self.lineage, self.projection_task_id)
        return self


class ReviewQuestionRecord(VersionedDocument):
    """`product_reviews`/`product_questions` (§5.2, §9.2): мінімальні обов'язкові поля.

    `content_version` — source update version/timestamp або deterministic content hash; unique
    `(source.source_id, source.source_item_id, content_version)`. Автор/rating/text/status
    додає WP-09 optional-полями minor-версії.
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    id: UUID = Field(alias="_id", description="ID запису (UUID).")
    record_kind: ReviewQuestionKind
    parent_item_id: EntityId = Field(description="Parent catalog item UUID (назва — index §9.2).")
    source: SourceIdentity
    content_version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    published_at: UtcDatetime | None = Field(default=None, description="Source time; nullable.")
    updated_at: UtcDatetime | None = Field(default=None, description="Source time; nullable.")
    observed_at: UtcDatetime
    projection_task_id: UUID
    lineage: Lineage

    @model_validator(mode="after")
    def _consistent(self) -> ReviewQuestionRecord:
        _check_lineage_task(self.lineage, self.projection_task_id)
        return self
