"""Мінімальний контракт current document MongoDB (§9.2) і `state_hash` (§9.4).

`CurrentDocumentBase` відповідає YAML §9.2; доменні `core`/`attributes`/`latest_state`
лишаються bounded JSON-об'єктами — типізовані підмоделі vehicle/catalog додають WP-07/WP-09
через dependency-запит. `compute_state_hash_v1` — чиста детермінована функція над
`core + attributes + latest_state`, незалежна від порядку полів і локалі.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Final
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import ContractModel, JsonObject, parse_schema_version
from collector.contracts.canonical import canonical_json_bytes, sha256_hex
from collector.contracts.enums import EntityKind
from collector.contracts.identity import EntityId, IdentityHash, Sha256Hex, SourceIdentity
from collector.contracts.projection import StateHash
from collector.contracts.temporal import EntityTime, UtcDatetime

STATE_HASH_VERSION: Final = 1


def compute_state_hash_v1(
    core: Mapping[str, object],
    attributes: Mapping[str, object],
    latest_state: Mapping[str, object],
) -> str:
    """`state_hash` v1: `v1:` + SHA-256 canonical JSON `{attributes, core, latest_state, v}`.

    Детермінований незалежно від порядку ключів, unicode-форми рядків (NFC) і локалі процесу.
    """
    payload = {
        "v": STATE_HASH_VERSION,
        "core": dict(core),
        "attributes": dict(attributes),
        "latest_state": dict(latest_state),
    }
    return f"v{STATE_HASH_VERSION}:{sha256_hex(canonical_json_bytes(payload))}"


class SourceRef(SourceIdentity):
    """Блок `source` current document: identity + canonical URL (§9.2)."""

    canonical_url: Annotated[str, StringConstraints(min_length=1)]


class Lineage(ContractModel):
    """Блок `lineage` (§9.2): fetch → raw → parser → projection task."""

    fetch_id: UUID
    raw_sha256: Sha256Hex
    parser_version: Annotated[str, StringConstraints(min_length=1)]
    projection_task_id: UUID


class CurrentDocumentBase(ContractModel):
    """Будь-який `*_current` document (§9.2); `_id` = `entity_uuid` (UUIDv7).

    `schema_version` — int major за YAML §9.2 (`schema_version: 1`), а не `major.minor`
    інших контрактів: minor-версію документа несе `contract_version` класу і JSON Schema snapshot.
    """

    contract_version = "1.0"

    schema_version: int = Field(
        default=1,
        ge=1,
        strict=True,
        description="Major версія схеми документа (§9.2 `schema_version: 1`); лише int.",
    )
    id: EntityId = Field(alias="_id", description="entity_uuid; збігається з entity_index.")
    entity_kind: EntityKind
    source: SourceRef
    identity_hash: IdentityHash
    projection_version: int = Field(ge=1, description="Монотонна per-entity_uuid (int64).")
    state_hash: StateHash
    core: JsonObject = Field(description="Versioned normalized поля домену.")
    attributes: JsonObject = Field(default_factory=dict, description="Source-specific поля.")
    latest_state: JsonObject = Field(
        default_factory=dict, description="Price/status/mileage summary, якщо застосовно."
    )
    lineage: Lineage
    time: EntityTime
    first_seen_at: UtcDatetime
    last_seen_at: UtcDatetime

    @property
    def entity_uuid(self) -> UUID:
        """Синонім `_id` (§9.2: `UUID _id/entity_uuid`)."""
        return self.id

    @model_validator(mode="after")
    def _consistent(self) -> CurrentDocumentBase:
        major, _ = parse_schema_version(type(self).contract_version)
        if self.schema_version != major:
            msg = f"schema_version {self.schema_version} несумісна з major {major} контракту"
            raise ValueError(msg)
        expected = compute_state_hash_v1(self.core, self.attributes, self.latest_state)
        if self.state_hash != expected:
            msg = "state_hash не збігається з compute_state_hash_v1(core, attributes, latest_state)"
            raise ValueError(msg)
        if self.last_seen_at < self.first_seen_at:
            msg = "last_seen_at < first_seen_at"
            raise ValueError(msg)
        return self
