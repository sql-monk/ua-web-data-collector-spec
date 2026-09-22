"""Canonical serialization: детермінований UTF-8 JSON для hash і event bytes (§7.3, R-37/R-42).

Правила (див. `docs/contracts.md`, розділ «Canonical serialization»):

- ключі об'єктів відсортовані за code point, без пробілів (`separators=(",", ":")`);
- `ensure_ascii=False` — UTF-8 без `\\uXXXX`-escape; рядки нормалізуються до Unicode NFC;
- `datetime` — лише aware UTC, формат `YYYY-MM-DDTHH:MM:SS.ffffffZ` (завжди 6 цифр мікросекунд);
- `date` — `YYYY-MM-DD`; `UUID` — lowercase з дефісами; `Enum` — `.value`;
- `Decimal` — рядок без експоненти (`format(d, "f")`), нормалізований (`1.50` → `"1.5"`);
- `bytes` — base64 (standard alphabet, з padding); `float` — лише скінченні (NaN/inf відхиляються);
- `set`/`frozenset` — відсортований список; Pydantic-моделі — через `model_dump(mode="python")`.

Функція чиста: однакове значення → однакові bytes незалежно від порядку полів, локалі та процесу.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from collector.contracts._base import JsonValue

CANONICAL_JSON_MEDIA_TYPE = "application/json; charset=utf-8"
DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class CanonicalEncodingError(ValueError):
    """Значення не має детермінованого canonical-представлення."""


def format_utc_datetime(value: datetime) -> str:
    """Aware UTC datetime → `YYYY-MM-DDTHH:MM:SS.ffffffZ`; naive або не-UTC відхиляється."""
    offset = value.utcoffset()
    if offset is None:
        msg = f"canonical datetime має бути aware UTC, отримано naive {value!r}"
        raise CanonicalEncodingError(msg)
    if offset.total_seconds() != 0:
        msg = f"canonical datetime має бути UTC, отримано offset {offset} у {value!r}"
        raise CanonicalEncodingError(msg)
    return value.strftime(DATETIME_FORMAT)


def format_decimal(value: Decimal) -> str:
    """Decimal → рядок без експоненти та зайвих нулів (`Decimal("1.50")` → `"1.5"`)."""
    if not value.is_finite():
        msg = f"canonical Decimal має бути скінченним, отримано {value!r}"
        raise CanonicalEncodingError(msg)
    normalized = value.normalize()
    text = format(normalized, "f")
    return "0" if text in {"-0", "0"} else text


def to_canonical_value(value: object) -> JsonValue:
    """Рекурсивно перетворює довільне значення на JSON-дерево з канонічними скалярами."""
    if isinstance(value, BaseModel):
        return to_canonical_value(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, Enum):
        return to_canonical_value(value.value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = f"canonical float має бути скінченним, отримано {value!r}"
            raise CanonicalEncodingError(msg)
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, datetime):
        return format_utc_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format_decimal(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"canonical object key має бути str, отримано {type(key).__name__}"
                raise CanonicalEncodingError(msg)
            result[unicodedata.normalize("NFC", key)] = to_canonical_value(item)
        return result
    if isinstance(value, set | frozenset):
        items = [to_canonical_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, list | tuple):
        return [to_canonical_value(item) for item in value]
    msg = f"тип {type(value).__name__} не має canonical-представлення"
    raise CanonicalEncodingError(msg)


def canonical_json_bytes(value: object) -> bytes:
    """Детерміновані UTF-8 bytes для будь-якого значення, яке підтримує `to_canonical_value`."""
    tree = to_canonical_value(value)
    text = json.dumps(
        tree,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """SHA-256 у lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: object) -> str:
    """SHA-256 hex canonical JSON значення."""
    return sha256_hex(canonical_json_bytes(value))
