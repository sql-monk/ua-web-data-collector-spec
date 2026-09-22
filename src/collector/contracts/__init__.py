"""Спільні versioned контракти даних (IDs, temporal axes, artifacts, events); owner — WP-01C.

Чисті Pydantic v2 моделі без I/O (єдиний виняток — read-only loader
`docs/research/source-registry.yaml` для валідації `source_id`). Персистенція
(SQLAlchemy/PyMongo mapping) — WP-01A/01B; JSON Schema snapshots — `schemas/**`
(`uv run collector contracts export [--check]`). Процедура змін і ownership — `docs/contracts.md`.
"""

from __future__ import annotations

from collector.contracts._base import (
    ContractModel,
    JsonObject,
    SchemaVersion,
    VersionedDocument,
    parse_schema_version,
)
from collector.contracts.canonical import (
    CANONICAL_JSON_MEDIA_TYPE,
    CanonicalEncodingError,
    canonical_json_bytes,
    canonical_sha256,
    sha256_hex,
)
from collector.contracts.enums import (
    STATE_AXES,
    ContactKind,
    ContentAccess,
    DataDomain,
    EffectiveAtBasis,
    EntityKind,
    EntityLifecycle,
    FetchOutcome,
    ObservationReason,
    ReleaseState,
    ResolutionAction,
    RouteState,
    SourceState,
    TimePrecision,
    UploadClaimStatus,
    map_research_access_state,
)
from collector.contracts.identity import (
    EntityId,
    IdentityHash,
    NormalizedUrl,
    Sha256Hex,
    SourceIdentity,
    Uuid7Generator,
    entity_id_timestamp,
    fetch_idempotency_key,
    identity_hash_v1,
    new_entity_id,
    planned_at_bucket,
    raw_object_key,
    translation_idempotency_key,
)
from collector.contracts.source_registry import (
    SourceRegistry,
    SourceRegistryError,
    known_source_ids,
    load_source_registry,
)
from collector.contracts.temporal import (
    BitemporalInterval,
    EffectiveTime,
    EntityTime,
    SourceTime,
    SystemTime,
    UtcDatetime,
    VersionInterval,
    VersionTimes,
    build_intervals,
    derive_effective_time,
)
from collector.contracts.values import (
    ContactValue,
    MeasuredValue,
    Money,
    normalize_contact,
    normalize_email,
    normalize_phone,
)

CONTRACTS_VERSION = "1.0"
"""Версія набору shared-контрактів (для `collector version` і release manifest)."""

__all__ = [
    "CANONICAL_JSON_MEDIA_TYPE",
    "CONTRACTS_VERSION",
    "STATE_AXES",
    "BitemporalInterval",
    "CanonicalEncodingError",
    "ContactKind",
    "ContactValue",
    "ContentAccess",
    "ContractModel",
    "DataDomain",
    "EffectiveAtBasis",
    "EffectiveTime",
    "EntityId",
    "EntityKind",
    "EntityLifecycle",
    "EntityTime",
    "FetchOutcome",
    "IdentityHash",
    "JsonObject",
    "MeasuredValue",
    "Money",
    "NormalizedUrl",
    "ObservationReason",
    "ReleaseState",
    "ResolutionAction",
    "RouteState",
    "SchemaVersion",
    "Sha256Hex",
    "SourceIdentity",
    "SourceRegistry",
    "SourceRegistryError",
    "SourceState",
    "SourceTime",
    "SystemTime",
    "TimePrecision",
    "UploadClaimStatus",
    "UtcDatetime",
    "Uuid7Generator",
    "VersionInterval",
    "VersionTimes",
    "VersionedDocument",
    "build_intervals",
    "canonical_json_bytes",
    "canonical_sha256",
    "derive_effective_time",
    "entity_id_timestamp",
    "fetch_idempotency_key",
    "identity_hash_v1",
    "known_source_ids",
    "load_source_registry",
    "map_research_access_state",
    "new_entity_id",
    "normalize_contact",
    "normalize_email",
    "normalize_phone",
    "parse_schema_version",
    "planned_at_bucket",
    "raw_object_key",
    "sha256_hex",
    "translation_idempotency_key",
]
