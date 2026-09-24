"""Normalized projection payload — вміст normalized artifact, вхід Mongo Projector (§7.3, §9.2).

Parser (WP-05/06/08/10) пише `NormalizedProjectionPayload` як canonical JSON bytes у immutable
normalized artifact; PostgreSQL зберігає лише `NormalizedArtifactRef` (без domain payload, R-24).
Projector (WP-01B) читає bytes, перевіряє hash/size за ref, валідує payload цим контрактом і
`check_payload_matches_artifact()`, будує current document, version record і observation.

`core`/`attributes`/`latest_state` — `BoundedJsonObject` (§9.2: bounded snapshot без unbounded
arrays); доменна типізація vehicle/catalog — WP-07/WP-09 через dependency-запит (minor).
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import (
    BoundedJsonObject,
    ContractModel,
    SchemaVersion,
    VersionedDocument,
)
from collector.contracts.artifacts import NormalizedArtifactRef
from collector.contracts.current import SourceRef, compute_state_hash_v1
from collector.contracts.enums import DataDomain, EntityKind
from collector.contracts.identity import EntityId, IdentityHash
from collector.contracts.temporal import EntityTime, SourceTime, SystemTime
from collector.contracts.values import Money

ENTITY_KIND_DOMAINS: Final[dict[EntityKind, frozenset[DataDomain]]] = {
    EntityKind.CATALOG_ITEM: frozenset({DataDomain.CATALOG}),
    EntityKind.CATALOG_OFFER: frozenset({DataDomain.CATALOG}),
    EntityKind.VEHICLE_LISTING: frozenset({DataDomain.VEHICLE}),
    EntityKind.SELLER: frozenset({DataDomain.CATALOG, DataDomain.VEHICLE}),
}
"""Дозволені `NormalizedArtifactRef.domain` для кожного `entity_kind`."""


class ObservedValues(ContractModel):
    """Спостережені значення offer/listing snapshot (§5.2, §5.3): ціна, наявність, решта bounded.

    `observed_at` не дублюється: час спостереження — `SystemTime.observed_at` payload-а
    (одне джерело правди). Доменні поля (old price, mileage, promoted, view counters) до
    типізації WP-07/WP-09 лежать у `values`.
    """

    price: Money | None = None
    availability: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = Field(
        default=None, description="Наявність/stock text як нормалізовано адаптером."
    )
    values: BoundedJsonObject = Field(
        default_factory=dict, description="Інші observed значення (bounded, §9.2)."
    )


class NormalizedProjectionPayload(VersionedDocument):
    """Вміст normalized artifact (§7.3 п.1–3): усе, з чого projector будує Mongo-документи.

    `schema_version` payload-а дорівнює `NormalizedArtifactRef.schema_version` (перевіряє
    `check_payload_matches_artifact`). `source` — `SourceRef` (identity + `canonical_url`), бо
    current document §9.2 вимагає `source.canonical_url`. Час — окремо `source_time` (§9.6,
    з raw text/locale) і `system_time`; `entity_time()` збирає блок `time` current document
    і при валідації payload відхиляє підміну source time через `fetched_at` (R-43).
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    entity_kind: EntityKind
    entity_uuid: EntityId
    source: SourceRef
    identity_hash: IdentityHash
    core: BoundedJsonObject = Field(description="Versioned normalized поля домену (bounded).")
    attributes: BoundedJsonObject = Field(
        default_factory=dict, description="Source-specific поля (bounded)."
    )
    latest_state: BoundedJsonObject = Field(
        default_factory=dict, description="Price/status/mileage summary (bounded)."
    )
    source_time: SourceTime = Field(default_factory=SourceTime)
    system_time: SystemTime
    observation: ObservedValues | None = Field(
        default=None,
        description="Observation-поля offer/listing; None — сутність без observations.",
    )

    @model_validator(mode="after")
    def _time_axes_consistent(self) -> NormalizedProjectionPayload:
        self.entity_time()  # R-43: source_event_at/source_updated_at ≠ fetched_at
        return self

    def entity_time(self) -> EntityTime:
        """Блок `time` current document/version snapshot (§9.2) з двох осей payload-а."""
        return EntityTime.combine(self.source_time, self.system_time)

    def state_hash(self) -> str:
        """`compute_state_hash_v1(core, attributes, latest_state)` цього payload-а."""
        return compute_state_hash_v1(self.core, self.attributes, self.latest_state)


class PayloadArtifactMismatchError(ValueError):
    """Payload не відповідає своєму `NormalizedArtifactRef` — projector: permanent (quarantine)."""


def check_payload_matches_artifact(
    payload: NormalizedProjectionPayload, artifact: NormalizedArtifactRef
) -> None:
    """Перевірка payload проти ref, який живе окремо (PostgreSQL / `ProjectionCommand.artifact`).

    Відхиляє (`PayloadArtifactMismatchError`): інший `entity_uuid`; `schema_version` payload-а
    ≠ `artifact.schema_version`; `entity_kind`, несумісний з `artifact.domain`
    (`ENTITY_KIND_DOMAINS`). Hash/size bytes перевіряє викликач до парсингу
    (`collector.storage.get(key, expected_sha256)`).
    """
    if payload.entity_uuid != artifact.entity_uuid:
        msg = (
            f"payload.entity_uuid {payload.entity_uuid} не збігається з "
            f"artifact.entity_uuid {artifact.entity_uuid}"
        )
        raise PayloadArtifactMismatchError(msg)
    if payload.schema_version != artifact.schema_version:
        msg = (
            f"payload.schema_version {payload.schema_version} не збігається з "
            f"artifact.schema_version {artifact.schema_version}"
        )
        raise PayloadArtifactMismatchError(msg)
    if artifact.domain not in ENTITY_KIND_DOMAINS[payload.entity_kind]:
        msg = f"entity_kind {payload.entity_kind} несумісний з artifact.domain {artifact.domain}"
        raise PayloadArtifactMismatchError(msg)
