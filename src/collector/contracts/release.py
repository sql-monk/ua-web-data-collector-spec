"""Dataset release contract (§9.9, R-46): immutable manifest, parts і state machine.

Release проходить `draft → building → validating → published`; будь-який крок до
`published` може завершитися `failed`; `published` лише переходить у `superseded` новим
release і ніколи не перезаписується — `validate_manifest_update()` відхиляє зміну будь-якого
поля опублікованого manifest, крім `state`/`superseding_release_id`.
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import ContractModel, JsonObject, SchemaVersion, VersionedDocument
from collector.contracts.artifacts import ArtifactRef, ArtifactUri
from collector.contracts.enums import ReleaseState
from collector.contracts.identity import EntityId, Sha256Hex
from collector.contracts.source_registry import SourceIdString
from collector.contracts.temporal import UtcDatetime

NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

RELEASE_TRANSITIONS: Final[dict[ReleaseState, frozenset[ReleaseState]]] = {
    ReleaseState.DRAFT: frozenset({ReleaseState.BUILDING, ReleaseState.FAILED}),
    ReleaseState.BUILDING: frozenset({ReleaseState.VALIDATING, ReleaseState.FAILED}),
    ReleaseState.VALIDATING: frozenset({ReleaseState.PUBLISHED, ReleaseState.FAILED}),
    ReleaseState.PUBLISHED: frozenset({ReleaseState.SUPERSEDED}),
    ReleaseState.FAILED: frozenset(),
    ReleaseState.SUPERSEDED: frozenset(),
}
"""Дозволені переходи станів release (§9.9)."""

_MUTABLE_AFTER_PUBLISH: Final = frozenset({"state", "superseding_release_id"})


class ReleaseTransitionError(ValueError):
    """Недозволений перехід стану або зміна опублікованого release."""


class EntityVersionRef(ContractModel):
    """Пара `(entity_uuid, projection_version)` у watermark списку release (§9.5)."""

    entity_uuid: EntityId
    projection_version: int = Field(ge=1)


class ReleaseWatermark(ContractModel):
    """PostgreSQL snapshot/export watermark (§9.5, §9.9)."""

    snapshot_at: UtcDatetime
    postgres_snapshot: NonEmptyStr = Field(description="LSN або snapshot id транзакції export.")
    mongo_cluster_time: str | None = None
    resolution_snapshot_id: UUID | None = Field(
        default=None, description="Snapshot entity resolution, з яким зібрано release (§9.8)."
    )


class SourceInclusion(ContractModel):
    """Джерело в release та причина, якщо воно excluded/degraded (§9.9)."""

    source_id: SourceIdString
    policy_version: NonEmptyStr
    reason: str | None = None


class ComponentVersions(ContractModel):
    """Версії коду/моделей, що впливають на відтворюваність (§9.9)."""

    schema_version: SchemaVersion
    parser_version: NonEmptyStr
    normalizer_version: NonEmptyStr
    matcher_version: NonEmptyStr
    resolution_version: NonEmptyStr
    translation_provider: NonEmptyStr | None = None
    translation_model_version: NonEmptyStr | None = None
    glossary_version: NonEmptyStr | None = None


class ReleasePart(ContractModel):
    """Один Parquet part release (§9.9)."""

    uri: ArtifactUri
    format: NonEmptyStr = Field(description="Напр. `parquet`.")
    partition: dict[str, str] = Field(default_factory=dict, description="Ключі партиції.")
    row_count: int = Field(ge=0)
    min_effective_at: UtcDatetime | None = None
    max_effective_at: UtcDatetime | None = None
    min_system_at: UtcDatetime | None = None
    max_system_at: UtcDatetime | None = None
    size_bytes: int = Field(ge=0)
    sha256: Sha256Hex

    @model_validator(mode="after")
    def _ranges(self) -> ReleasePart:
        for low, high in (
            (self.min_effective_at, self.max_effective_at),
            (self.min_system_at, self.max_system_at),
        ):
            if low is not None and high is not None and high < low:
                msg = "max time < min time у ReleasePart"
                raise ValueError(msg)
        return self


class ReleaseManifest(VersionedDocument):
    """Immutable manifest dataset release з усіма полями §9.9."""

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    release_id: UUID
    tag: Annotated[str, StringConstraints(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    state: ReleaseState = ReleaseState.DRAFT
    created_at: UtcDatetime
    published_at: UtcDatetime | None = None
    owner: NonEmptyStr
    purpose: NonEmptyStr
    watermark: ReleaseWatermark
    entity_versions: list[EntityVersionRef] | None = Field(
        default=None, description="Повний список пар або None, якщо є partition_index_sha256."
    )
    partition_index_sha256: Sha256Hex | None = Field(
        default=None, description="Hash partition index замість повного списку пар."
    )
    source_registry_version: NonEmptyStr
    included_sources: list[SourceInclusion] = Field(default_factory=list)
    excluded_sources: list[SourceInclusion] = Field(default_factory=list)
    degraded_sources: list[SourceInclusion] = Field(default_factory=list)
    versions: ComponentVersions
    git_commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,64}$")]
    image_digests: dict[str, str] = Field(default_factory=dict, description="image → digest.")
    config_hash: Sha256Hex = Field(description="Hash sanitized config (без секретів).")
    build_command: NonEmptyStr
    parts: list[ReleasePart] = Field(default_factory=list)
    quality_report: JsonObject | ArtifactRef | None = None
    reconciliation_result: JsonObject | ArtifactRef | None = None
    previous_release_id: UUID | None = None
    superseding_release_id: UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ReleaseManifest:
        if (self.entity_versions is None) == (self.partition_index_sha256 is None):
            msg = "рівно одне з entity_versions / partition_index_sha256 має бути задане"
            raise ValueError(msg)
        for inclusion in (*self.excluded_sources, *self.degraded_sources):
            if not inclusion.reason:
                msg = f"excluded/degraded source {inclusion.source_id} потребує reason"
                raise ValueError(msg)
        if self.state in {ReleaseState.PUBLISHED, ReleaseState.SUPERSEDED}:
            missing = [
                name
                for name, value in (
                    ("published_at", self.published_at),
                    ("parts", self.parts),
                    ("quality_report", self.quality_report),
                    ("reconciliation_result", self.reconciliation_result),
                )
                if not value
            ]
            if missing:
                msg = f"{self.state.value} release потребує: {', '.join(missing)}"
                raise ValueError(msg)
        elif self.published_at is not None:
            msg = "published_at дозволений лише для published/superseded"
            raise ValueError(msg)
        if self.state is ReleaseState.SUPERSEDED and self.superseding_release_id is None:
            msg = "superseded release потребує superseding_release_id"
            raise ValueError(msg)
        if self.state is not ReleaseState.SUPERSEDED and self.superseding_release_id is not None:
            msg = "superseding_release_id дозволений лише для superseded"
            raise ValueError(msg)
        if self.superseding_release_id == self.release_id:
            msg = "release не може supersede сам себе"
            raise ValueError(msg)
        return self


def can_transition(current: ReleaseState, target: ReleaseState) -> bool:
    """Чи дозволений перехід `current → target` (§9.9)."""
    return target in RELEASE_TRANSITIONS[current]


def transition_release(
    manifest: ReleaseManifest, target: ReleaseState, **changes: object
) -> ReleaseManifest:
    """Новий manifest у стані `target`; недозволений перехід → `ReleaseTransitionError`.

    `changes` — поля, які дозволено виставити разом із переходом (`published_at`,
    `superseding_release_id`, `parts`, `quality_report`, ...); для `published` manifest
    дозволені лише `state`/`superseding_release_id` (`validate_manifest_update`).
    """
    if not can_transition(manifest.state, target):
        msg = f"перехід {manifest.state.value} → {target.value} не дозволений"
        raise ReleaseTransitionError(msg)
    candidate = manifest.model_copy(update={"state": target, **changes})
    candidate = ReleaseManifest.model_validate(candidate.model_dump(by_alias=True))
    validate_manifest_update(manifest, candidate)
    return candidate


def validate_manifest_update(previous: ReleaseManifest, candidate: ReleaseManifest) -> None:
    """Опублікований release immutable: змінюються лише `state` і `superseding_release_id`."""
    if previous.release_id != candidate.release_id:
        msg = "release_id не змінюється"
        raise ReleaseTransitionError(msg)
    if previous.state is not ReleaseState.PUBLISHED:
        return
    before = previous.model_dump(mode="json", by_alias=True)
    after = candidate.model_dump(mode="json", by_alias=True)
    changed = sorted(key for key in before if before[key] != after.get(key))
    illegal = [key for key in changed if key not in _MUTABLE_AFTER_PUBLISH]
    if illegal:
        msg = f"published release immutable: змінені поля {illegal}"
        raise ReleaseTransitionError(msg)
