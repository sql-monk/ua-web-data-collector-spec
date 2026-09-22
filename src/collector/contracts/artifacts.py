"""Artifact contracts (§7.3, §9.1, §10 п.5, п.7; R-27, R-38, R-41).

- `ArtifactRef` — content-addressed посилання на immutable об'єкт у S3/MinIO.
- `RawArtifactRef` — raw HTML/XML/JSON + HTTP metadata fetch.
- `NormalizedArtifactRef` — normalized projection input: `entity_uuid`, `domain`,
  `parser_version` і lineage до raw/fetch; PostgreSQL зберігає лише цей pointer, не payload.
- `UploadClaim` + `can_commit()` — fencing для claimed/verified PUT protocol (§10 п.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from collector.contracts._base import ContractModel, SchemaVersion
from collector.contracts.enums import DataDomain, UploadClaimStatus
from collector.contracts.identity import EntityId, Sha256Hex
from collector.contracts.temporal import UtcDatetime

ArtifactUri = Annotated[str, StringConstraints(min_length=1, pattern=r"^[a-z][a-z0-9+.-]*://.+")]
"""URI об'єкта в artifact store (`s3://bucket/key`, `file://...` у тестах)."""

MediaType = Annotated[str, StringConstraints(min_length=3, pattern=r"^[\w.+-]+/[\w.+-]+(;.*)?$")]


class ArtifactRef(ContractModel):
    """Посилання на immutable content-addressed artifact.

    `schema_version` тут — версія схеми *вмісту* (наприклад, normalized payload), а не
    версія цього контракту (`ArtifactRef.contract_version`).
    """

    uri: ArtifactUri
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    media_type: MediaType
    schema_version: SchemaVersion | None = Field(
        default=None, description="Версія схеми вмісту artifact (для normalized/event payload)."
    )


class RawArtifactRef(ArtifactRef):
    """Raw artifact (§9.1 `raw_objects`) з HTTP metadata fetch."""

    fetch_id: UUID
    fetched_at: UtcDatetime
    requested_url: Annotated[str, StringConstraints(min_length=1)]
    final_url: Annotated[str, StringConstraints(min_length=1)]
    http_status: int = Field(ge=100, le=599)
    content_type: str | None = None
    content_encoding: str | None = None
    etag: str | None = None
    last_modified: str | None = Field(
        default=None, description="Заголовок Last-Modified як отримано (raw), без парсингу."
    )


class NormalizedArtifactRef(ArtifactRef):
    """Normalized projection artifact (§9.1 `normalized_artifacts`) з lineage до raw/fetch."""

    schema_version: SchemaVersion = Field(description="Версія схеми normalized payload.")
    entity_uuid: EntityId
    domain: DataDomain
    parser_version: Annotated[str, StringConstraints(min_length=1)]
    fetch_id: UUID
    raw_sha256: Sha256Hex
    raw_uri: ArtifactUri
    produced_at: UtcDatetime


class UploadClaim(ContractModel):
    """PostgreSQL upload claim з lease та монотонною `claim_generation` (§10 п.5, R-41)."""

    object_key: Annotated[str, StringConstraints(min_length=1)]
    owner: Annotated[str, StringConstraints(min_length=1)]
    status: UploadClaimStatus
    lease_expires_at: UtcDatetime
    claim_generation: int = Field(ge=1, description="Монотонний int64 fencing token.")


def can_commit(claim: UploadClaim, now: datetime, generation: int) -> bool:
    """Commit predicate §10 п.5: `object_key + generation + lease_expires_at > now()`.

    Stale generation (reacquire підняв лічильник), прострочений lease або claim не в стані
    `leased` не можуть створити DB reference.
    """
    return (
        claim.status is UploadClaimStatus.LEASED
        and claim.claim_generation == generation
        and claim.lease_expires_at > now
    )
