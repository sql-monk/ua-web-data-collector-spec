"""Translation memory (FR-017, Q-009; рішення оркестратора О-2): ключ і порт сховища.

TM key — SHA-256 canonical JSON рівно п'яти компонентів: `source_language`,
`target_language`, `normalized_segment_hash` (SHA-256 нормалізованого masked-тексту, тобто з
placeholder-ами замість тегів/чисел/URL/термінів), пара `provider` (`name`, `model_version`)
і `glossary_version`. Ключ обчислюється лише тут; колонка WP-01A зберігає готовий hex.
PostgreSQL-реалізація порту — WP-04 PR2 поверх репозиторію WP-01A PR3b.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from collector.contracts import canonical_json_bytes, sha256_hex
from collector.translation.normalize import normalize_text


def normalized_segment_hash(masked_text: str) -> str:
    """SHA-256 hex нормалізованого (NFC, пробіли, керівні символи) masked-тексту сегмента."""
    return sha256_hex(normalize_text(masked_text).encode("utf-8"))


def translation_memory_key(
    *,
    source_language: str,
    target_language: str,
    normalized_segment_hash: str,
    provider: str,
    model_version: str,
    glossary_version: str,
) -> str:
    """TM key (FR-017): SHA-256 hex canonical JSON п'яти компонентів."""
    payload = {
        "source_language": source_language,
        "target_language": target_language,
        "normalized_segment_hash": normalized_segment_hash,
        "provider": {"name": provider, "model_version": model_version},
        "glossary_version": glossary_version,
    }
    return sha256_hex(canonical_json_bytes(payload))


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """Переклад одного masked-сегмента (з placeholder-ами) під TM key; immutable."""

    key: str
    source_language: str
    target_language: str
    segment_hash: str
    provider: str
    model_version: str
    glossary_version: str
    translated_text: str


class TranslationMemoryStore(Protocol):
    """Порт TM. `put_many` — first-write-wins (`ON CONFLICT DO NOTHING`), записи не змінюються."""

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, MemoryEntry]: ...

    async def put_many(self, entries: Sequence[MemoryEntry]) -> None: ...


class InMemoryTranslationMemory:
    """In-memory TM для тестів і offline QA-runner-а (PR3)."""

    def __init__(self) -> None:
        self.entries: dict[str, MemoryEntry] = {}

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, MemoryEntry]:
        return {key: self.entries[key] for key in keys if key in self.entries}

    async def put_many(self, entries: Sequence[MemoryEntry]) -> None:
        for entry in entries:
            self.entries.setdefault(entry.key, entry)
