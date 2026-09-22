"""§7.3 (R-30, R-37, R-42): canonical serialization, byte-equivalent encode_event, ліміт 256 KiB."""

from __future__ import annotations

import hashlib
import json
import locale
import unicodedata
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from factories import ENTITY_A, EVENT_ID, SHA_A, TASK_ID, at
from pydantic import ValidationError

from collector.contracts.artifacts import ArtifactRef
from collector.contracts.canonical import (
    CanonicalEncodingError,
    canonical_json_bytes,
    canonical_sha256,
    format_decimal,
    format_utc_datetime,
)
from collector.contracts.events import (
    DOMAIN_CHANGED_MEDIA_TYPE,
    EVENT_INLINE_LIMIT_BYTES,
    DomainChangedEvent,
    EncodedEvent,
    EventTooLargeError,
    decode_event,
    encode_event,
)


def event(**overrides: object) -> DomainChangedEvent:
    data: dict[str, object] = {
        "event_id": EVENT_ID,
        "aggregate_id": ENTITY_A,
        "aggregate_version": 3,
        "event_type": "catalog.item.changed",
        "payload_schema_version": "1.0",
        "occurred_at": at(),
        "projection_task_id": TASK_ID,
        "previous_state_hash": "v1:" + "d" * 64,
        "result_state_hash": "v1:" + "e" * 64,
        "payload": {"latest_state": {"price": {"amount_minor": 259900, "currency": "UAH"}}},
    }
    data.update(overrides)
    return DomainChangedEvent.model_validate(data)


# --- canonical --------------------------------------------------------------------------------


def test_canonical_json_sorted_compact_utf8_and_scalar_formats() -> None:
    value = {
        "b": [Decimal("1.50"), Decimal("1E+2"), Decimal("-0")],
        "a": {"z": datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC), "y": UUID(int=1)},
        "с": "Привіт",  # кириличний ключ
        "bytes": b"\x00\xff",
        "set": {3, 1, 2},
        "none": None,
        "flag": True,
    }
    data = canonical_json_bytes(value)
    assert (
        data
        == (
            '{"a":{"y":"00000000-0000-0000-0000-000000000001","z":"2026-09-01T12:00:00.000000Z"},'
            '"b":["1.5","100","0"],"bytes":"AP8=","flag":true,"none":null,"set":[1,2,3],"с":"Привіт"}'
        ).encode()
    )
    assert b" " not in data and b"\\u" not in data
    assert canonical_sha256(value) == hashlib.sha256(data).hexdigest()


def test_canonical_independent_of_key_order_and_unicode_form() -> None:
    nfc = {"назва": "Київ", "brand": "Bosch"}
    nfd = {
        unicodedata.normalize("NFD", "назва"): unicodedata.normalize("NFD", "Київ"),
        "brand": "Bosch",
    }
    reordered = {"brand": "Bosch", "назва": "Київ"}
    assert canonical_json_bytes(nfc) == canonical_json_bytes(nfd) == canonical_json_bytes(reordered)


def test_canonical_rejects_non_utc_nan_and_unknown_types() -> None:
    with pytest.raises(CanonicalEncodingError, match="naive"):
        canonical_json_bytes({"t": datetime(2026, 1, 1)})
    with pytest.raises(CanonicalEncodingError, match="UTC"):
        canonical_json_bytes({"t": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2)))})
    with pytest.raises(CanonicalEncodingError):
        canonical_json_bytes({"f": float("nan")})
    with pytest.raises(CanonicalEncodingError):
        canonical_json_bytes({"d": Decimal("Infinity")})
    with pytest.raises(CanonicalEncodingError):
        canonical_json_bytes({1: "int key"})
    with pytest.raises(CanonicalEncodingError):
        canonical_json_bytes({"o": object()})


def test_format_helpers() -> None:
    assert (
        format_utc_datetime(datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC))
        == "2026-01-02T03:04:05.000006Z"
    )
    assert format_decimal(Decimal("0.000")) == "0"
    assert format_decimal(Decimal("12.3400")) == "12.34"
    assert format_decimal(Decimal("1e-7")) == "0.0000001"


def test_canonical_independent_of_process_locale() -> None:
    value = {"amount": Decimal("1234.5"), "when": at(), "text": "ї"}
    before = canonical_json_bytes(value)
    original = locale.setlocale(locale.LC_ALL)
    for candidate in (
        "uk_UA.UTF-8",
        "Ukrainian_Ukraine.1251",
        "de_DE.UTF-8",
        "German_Germany.1252",
    ):
        try:
            locale.setlocale(locale.LC_ALL, candidate)
        except locale.Error:
            continue
        try:
            assert canonical_json_bytes(value) == before
        finally:
            locale.setlocale(locale.LC_ALL, original)


# --- encode_event -----------------------------------------------------------------------------


def test_encode_event_deterministic_and_byte_equivalent_after_round_trip() -> None:
    first = encode_event(event())
    second = encode_event(event())
    assert first.event_bytes == second.event_bytes
    assert (
        first.event_sha256 == second.event_sha256 == hashlib.sha256(first.event_bytes).hexdigest()
    )
    assert first.event_media_type == DOMAIN_CHANGED_MEDIA_TYPE
    decoded = decode_event(first.event_bytes)
    assert decoded == event()
    assert encode_event(decoded).event_bytes == first.event_bytes
    # bytes — валідний UTF-8 JSON з відсортованими ключами і без пробілів
    text = first.event_bytes.decode("utf-8")
    parsed = json.loads(text)
    assert list(parsed) == sorted(parsed)
    assert json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == text


def test_encode_event_independent_of_payload_key_order() -> None:
    a = event(payload={"x": 1, "y": {"b": 2, "a": 1}})
    b = event(payload={"y": {"a": 1, "b": 2}, "x": 1})
    assert encode_event(a).event_bytes == encode_event(b).event_bytes


def test_encode_event_limit_256_kib_and_artifact_alternative() -> None:
    big = event(payload={"blob": "x" * (EVENT_INLINE_LIMIT_BYTES + 1)})
    with pytest.raises(EventTooLargeError) as info:
        encode_event(big)
    assert info.value.size > EVENT_INLINE_LIMIT_BYTES
    small_enough = event(payload={"blob": "x" * (EVENT_INLINE_LIMIT_BYTES - 1024)})
    assert len(encode_event(small_enough).event_bytes) <= EVENT_INLINE_LIMIT_BYTES
    via_artifact = event(
        payload=None,
        payload_artifact=ArtifactRef(
            uri="s3://events/" + SHA_A,
            sha256=SHA_A,
            size_bytes=EVENT_INLINE_LIMIT_BYTES + 1,
            media_type="application/json",
            schema_version="1.0",
        ),
    )
    assert len(encode_event(via_artifact).event_bytes) < 2048


def test_encoded_event_validates_hash_and_size() -> None:
    with pytest.raises(ValidationError, match="event_sha256"):
        EncodedEvent(
            event_id=EVENT_ID,
            event_bytes=b"{}",
            event_media_type="application/json",
            event_sha256="0" * 64,
        )
    with pytest.raises(ValidationError, match="payload_artifact"):
        EncodedEvent(
            event_id=EVENT_ID,
            event_bytes=b"x" * (EVENT_INLINE_LIMIT_BYTES + 1),
            event_media_type="application/json",
            event_sha256=hashlib.sha256(b"x" * (EVENT_INLINE_LIMIT_BYTES + 1)).hexdigest(),
        )


def test_domain_changed_event_shape() -> None:
    with pytest.raises(ValidationError, match="рівно одне"):
        event(payload=None)
    with pytest.raises(ValidationError):
        event(event_type="CatalogChanged")
    with pytest.raises(ValidationError, match="UTC"):
        event(occurred_at=datetime(2026, 9, 1, 12))
    with pytest.raises(ValidationError, match="schema_version"):
        event(schema_version="2.0")
