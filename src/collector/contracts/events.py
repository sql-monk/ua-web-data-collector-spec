"""`domain.changed` event і canonical serialization (§7.3, §9.5; R-30, R-37, R-42).

`DomainChangedEvent` — єдина публічна подія домену; `projection.command` (`projection.py`)
є внутрішньою командою projector і ніколи не публікується (R-30). `encode_event()` один раз
формує ready-to-publish UTF-8 bytes зі стабільним `event_id`; receipt/outbox зберігають ці
bytes без повторної серіалізації, тож replay після crash byte-equivalent. Inline payload
обмежений 256 KiB — більший зберігається як immutable artifact (`payload_artifact`).
"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from collector.contracts._base import ContractModel, JsonObject, SchemaVersion, VersionedDocument
from collector.contracts.artifacts import ArtifactRef
from collector.contracts.canonical import canonical_json_bytes, sha256_hex
from collector.contracts.identity import EntityId, Sha256Hex
from collector.contracts.temporal import UtcDatetime

EVENT_INLINE_LIMIT_BYTES: Final = 256 * 1024
DOMAIN_CHANGED_MEDIA_TYPE: Final = "application/vnd.ua-collector.domain-changed.v1+json"
EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"

EventType = Annotated[str, StringConstraints(pattern=EVENT_TYPE_PATTERN, max_length=128)]
"""`<domain>.<aggregate>.<change>`, напр. `catalog.item.changed`."""


class EventTooLargeError(ValueError):
    """Canonical event bytes перевищують `EVENT_INLINE_LIMIT_BYTES`; payload → artifact."""

    def __init__(self, size: int) -> None:
        limit = EVENT_INLINE_LIMIT_BYTES
        super().__init__(f"event bytes {size} > {limit}: перенесіть payload у payload_artifact")
        self.size = size


class DomainChangedEvent(VersionedDocument):
    """Публічна подія зміни current state після Mongo commit (§7.3 п.4).

    Створюється лише для receipt з `applied_to_current=true AND state_changed=true`;
    consumer дедуплікує за `event_id`. `payload` або `payload_artifact` — рівно одне.
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    event_id: UUID = Field(description="Стабільний ID події; той самий при replay.")
    aggregate_id: EntityId
    aggregate_version: int = Field(ge=1, description="`projection_version`, що стала current.")
    event_type: EventType
    payload_schema_version: SchemaVersion
    occurred_at: UtcDatetime = Field(description="Mongo commit time receipt.")
    projection_task_id: UUID
    previous_state_hash: str | None = None
    result_state_hash: str
    payload: JsonObject | None = None
    payload_artifact: ArtifactRef | None = None

    @model_validator(mode="after")
    def _payload_xor_artifact(self) -> DomainChangedEvent:
        if (self.payload is None) == (self.payload_artifact is None):
            msg = "рівно одне з payload / payload_artifact має бути задане"
            raise ValueError(msg)
        return self


class EncodedEvent(ContractModel):
    """Ready-to-publish descriptor події: bytes, media type, SHA-256 (§7.3, R-42)."""

    event_id: UUID
    event_bytes: bytes
    event_media_type: str
    event_sha256: Sha256Hex

    @model_validator(mode="after")
    def _hash_matches(self) -> EncodedEvent:
        if sha256_hex(self.event_bytes) != self.event_sha256:
            msg = "event_sha256 не збігається з sha256(event_bytes)"
            raise ValueError(msg)
        if len(self.event_bytes) > EVENT_INLINE_LIMIT_BYTES:
            raise EventTooLargeError(len(self.event_bytes))
        return self


def encode_event(event: DomainChangedEvent) -> EncodedEvent:
    """Детерміновані UTF-8 bytes події + media type + SHA-256.

    Byte-equivalent для рівних подій незалежно від порядку полів/процесу; `EventTooLargeError`,
    якщо bytes > 256 KiB — викликач переносить payload в immutable artifact.
    """
    data = canonical_json_bytes(event)
    if len(data) > EVENT_INLINE_LIMIT_BYTES:
        raise EventTooLargeError(len(data))
    return EncodedEvent(
        event_id=event.event_id,
        event_bytes=data,
        event_media_type=DOMAIN_CHANGED_MEDIA_TYPE,
        event_sha256=sha256_hex(data),
    )


def decode_event(data: bytes) -> DomainChangedEvent:
    """Зворотне перетворення canonical bytes → модель (round-trip для тестів/consumers)."""
    return DomainChangedEvent.model_validate_json(data)
