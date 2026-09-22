"""Ідентичність та ідемпотентність (§5.1, §9.3): UUIDv7, source identity, hash-ключі.

- `EntityId` — UUIDv7 (RFC 9562); `new_entity_id()` — монотонний у межах процесу генератор
  (48-bit unix ms + 12-bit лічильник у `rand_a` + 62 випадкові біти); Python 3.13 не має
  `uuid.uuid7`, тому генератор власний і без зовнішніх залежностей.
- `SourceIdentity(source_id, source_item_id)` — первинний природний ключ (§9.3 п.1);
  `source_id` має існувати в `docs/research/source-registry.yaml`.
- `identity_hash_v1` — versioned hash canonical URL + відсортованих стабільних атрибутів
  (§9.3 п.2); алгоритм у `docs/contracts.md`.
- ключі ідемпотентності fetch (§9.3 п.3), translation (п.7) і raw object (п.4).
"""

from __future__ import annotations

import secrets
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final
from uuid import UUID

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from collector.contracts._base import ContractModel
from collector.contracts.canonical import (
    canonical_json_bytes,
    format_utc_datetime,
    sha256_hex,
)
from collector.contracts.source_registry import SourceIdString, require_known_source_id

IDENTITY_HASH_VERSION: Final = 1
IDENTITY_HASH_PATTERN = r"^v[1-9][0-9]*:[0-9a-f]{64}$"
SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"

Sha256Hex = Annotated[str, StringConstraints(pattern=SHA256_HEX_PATTERN)]
"""SHA-256 у lowercase hex (raw object key, artifact hash, event hash)."""

IdentityHash = Annotated[str, StringConstraints(pattern=IDENTITY_HASH_PATTERN)]
"""`v<N>:<sha256 hex>` — версія алгоритму вбудована у значення."""

TRACKING_QUERY_PREFIXES: Final[tuple[str, ...]] = ("utm_",)
TRACKING_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {"fbclid", "gclid", "dclid", "yclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "igshid"}
)


# --- UUIDv7 ---------------------------------------------------------------------------------


def _require_uuid7(value: UUID) -> UUID:
    if value.version != 7:
        msg = f"EntityId має бути UUIDv7, отримано version={value.version}"
        raise ValueError(msg)
    return value


EntityId = Annotated[UUID, AfterValidator(_require_uuid7)]
"""UUIDv7 внутрішньої сутності (§5.1); інша версія UUID відхиляється."""


class Uuid7Generator:
    """Монотонний у межах процесу генератор UUIDv7 (RFC 9562 §5.7, метод 1 + лічильник).

    Layout: 48 біт unix-time у мс | ver=7 (4) | 12 біт `rand_a` як лічильник | var=10 (2) |
    62 біти `rand_b`. Кожен виклик у тій самій мілісекунді збільшує лічильник; переповнення
    лічильника або крок годинника назад переносять timestamp на +1 мс, тож послідовність
    значень строго зростає при порівнянні як 128-bit int (а отже й лексикографічно).
    """

    _COUNTER_MAX = (1 << 12) - 1

    def __init__(self, clock_ms: Callable[[], int] | None = None) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._lock = threading.Lock()
        self._last_ms = -1
        self._counter = 0

    def __call__(self) -> UUID:
        with self._lock:
            now_ms = self._clock_ms()
            if now_ms > self._last_ms:
                self._last_ms = now_ms
                self._counter = secrets.randbits(11)  # старший біт 0 — запас для інкремента
            elif self._counter < self._COUNTER_MAX:
                self._counter += 1
            else:
                self._last_ms += 1
                self._counter = secrets.randbits(11)
            rand_b = secrets.randbits(62)
            value = (
                ((self._last_ms & ((1 << 48) - 1)) << 80)
                | (0x7 << 76)
                | (self._counter << 64)
                | (0b10 << 62)
                | rand_b
            )
            return UUID(int=value)


_default_generator = Uuid7Generator()


def new_entity_id() -> UUID:
    """Новий UUIDv7; монотонний у межах процесу (див. `Uuid7Generator`)."""
    return _default_generator()


def entity_id_timestamp(value: UUID) -> datetime:
    """Unix-time (мс) з UUIDv7 як aware UTC datetime."""
    _require_uuid7(value)
    ms = value.int >> 80
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)


# --- Source identity --------------------------------------------------------------------------


SourceItemId = Annotated[
    str, StringConstraints(min_length=1, max_length=512, strip_whitespace=True)
]


class SourceIdentity(ContractModel):
    """Первинний природний ключ `(source_id, source_item_id)` (§9.3 п.1)."""

    source_id: Annotated[SourceIdString, AfterValidator(require_known_source_id)] = Field(
        description="ID джерела з docs/research/source-registry.yaml."
    )
    source_item_id: SourceItemId = Field(description="Стабільний ID сутності на джерелі.")


class NormalizedUrl(ContractModel):
    """Пара URL: вихідний як отримано і нормалізований (нормалізація — WP-02).

    Контракт фіксує лише інваріант: у `normalized` немає tracking-параметрів (`utm_*`,
    `fbclid`, `gclid`, ...), а `original` зберігається без змін.
    """

    original: Annotated[str, StringConstraints(min_length=1)]
    normalized: Annotated[str, StringConstraints(min_length=1)]

    @model_validator(mode="after")
    def _no_tracking_params(self) -> NormalizedUrl:
        offending = tracking_query_keys(self.normalized)
        if offending:
            msg = f"normalized URL містить tracking-параметри: {sorted(offending)}"
            raise ValueError(msg)
        return self


def tracking_query_keys(url: str) -> frozenset[str]:
    """Ключі query-рядка, які є tracking-параметрами (без парсингу повного URL)."""
    _, sep, query = url.partition("?")
    if not sep:
        return frozenset()
    query = query.split("#", 1)[0]
    keys = {part.split("=", 1)[0].lower() for part in query.split("&") if part}
    return frozenset(
        key for key in keys if key in TRACKING_QUERY_KEYS or key.startswith(TRACKING_QUERY_PREFIXES)
    )


# --- identity_hash ----------------------------------------------------------------------------


def _normalize_attribute_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def identity_hash_v1(canonical_url: str, stable_attributes: Mapping[str, object]) -> str:
    """`identity_hash` v1 (§9.3 п.2): SHA-256 canonical JSON `{url, attributes}`.

    Кроки (документ: `docs/contracts.md`):
    1. `canonical_url` — NFC, без пробілів на краях (case не змінюється: URL уже canonical);
    2. кожен атрибут: ключ → NFC + casefold; значення → `str`, NFC, пробіли стиснуті,
       casefold; порожні значення/`None` відкидаються; ключі, що збігаються після
       нормалізації (`Brand`/`brand`), — `ValueError` (помилка викликача, CR-04);
    3. `{"attributes": {...відсортовано...}, "url": ..., "v": 1}` → `canonical_json_bytes`;
    4. результат `v1:<sha256 hex>`.
    """
    attributes: dict[str, str] = {}
    for key, raw_value in stable_attributes.items():
        if raw_value is None:
            continue
        text = _normalize_attribute_text(str(raw_value))
        if not text:
            continue
        normalized_key = _normalize_attribute_text(key)
        if normalized_key in attributes:
            msg = f"identity_hash_v1: ключ {normalized_key!r} дублюється після NFC+casefold"
            raise ValueError(msg)
        attributes[normalized_key] = text
    payload = {
        "v": IDENTITY_HASH_VERSION,
        "url": unicodedata.normalize("NFC", canonical_url).strip(),
        "attributes": attributes,
    }
    return f"v{IDENTITY_HASH_VERSION}:{sha256_hex(canonical_json_bytes(payload))}"


# --- idempotency keys -------------------------------------------------------------------------


def planned_at_bucket(planned_at: datetime, bucket: timedelta) -> datetime:
    """Округлює `planned_at` (aware UTC) униз до межі `bucket`."""
    if planned_at.utcoffset() != timedelta(0):
        msg = "planned_at має бути aware UTC"
        raise ValueError(msg)
    if bucket <= timedelta(0):
        msg = "bucket має бути додатним"
        raise ValueError(msg)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = planned_at - epoch
    floored = (elapsed // bucket) * bucket
    return epoch + floored


def fetch_idempotency_key(
    source_id: str,
    normalized_url: str,
    planned_at_bucket: datetime,
    request_variant: str,
) -> str:
    """Ключ ідемпотентності fetch (§9.3 п.3): SHA-256 canonical JSON чотирьох складників."""
    payload = {
        "source_id": source_id,
        "normalized_url": normalized_url,
        "planned_at_bucket": format_utc_datetime(planned_at_bucket),
        "request_variant": request_variant,
    }
    return sha256_hex(canonical_json_bytes(payload))


def translation_idempotency_key(
    article_version_id: UUID,
    target_language: str,
    provider: str,
    model_version: str,
    glossary_version: str,
) -> str:
    """Ключ ідемпотентності перекладу (§9.3 п.7)."""
    payload = {
        "article_version_id": str(article_version_id),
        "target_language": target_language.strip().lower(),
        "provider": provider,
        "model_version": model_version,
        "glossary_version": glossary_version,
    }
    return sha256_hex(canonical_json_bytes(payload))


def raw_object_key(body: bytes) -> str:
    """Ключ raw object — `sha256(body)` (§9.3 п.4); однакові bytes не дублюються."""
    return sha256_hex(body)
