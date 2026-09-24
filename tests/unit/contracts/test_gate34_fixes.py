"""Gate 3/4 WP-01C PR2: SR-1 retry plan, SR-2 `uk`, CR #1 str-ключі, CR #2 `1`/`1.0`, CR #4."""

from __future__ import annotations

from typing import Any

import pytest
from factories import news_translation_payload, normalized_payload
from pydantic import TypeAdapter, ValidationError

from collector.contracts._base import BoundedJsonObject, JsonObject
from collector.contracts.canonical import canonical_json_bytes
from collector.contracts.current import compute_state_hash_v1
from collector.contracts.news import (
    MAX_RETRY_PLAN_CHARS,
    MAX_TRANSLATED_BODY_TEXT_CHARS,
    MAX_TRANSLATED_LEAD_CHARS,
    MAX_TRANSLATED_TITLE_CHARS,
    NewsTranslation,
)
from collector.contracts.payload import NormalizedProjectionPayload

PLAN = "add language to EXTRA_SOURCE_LANGUAGES + golden pair"
FAILED: dict[str, Any] = {
    "status": "translation_failed",
    "title": None,
    "lead": None,
    "body_artifact": None,
}


def translation(**overrides: Any) -> NewsTranslation:
    return NewsTranslation.model_validate(news_translation_payload(**overrides))


# --- SR-1: retry plan ----------------------------------------------------------------------------


def test_failed_translation_requires_retry_plan() -> None:
    assert translation(**FAILED, retry_plan=PLAN).retry_plan == PLAN
    with pytest.raises(ValidationError, match="retry_plan"):
        translation(**FAILED)


@pytest.mark.parametrize("status", ["translated", "pending", "not_required"])
def test_retry_plan_forbidden_for_other_statuses(status: str) -> None:
    text: dict[str, Any] = (
        {} if status == "translated" else {"title": None, "lead": None, "body_artifact": None}
    )
    translation(status=status, **text)
    with pytest.raises(ValidationError, match="retry_plan"):
        translation(status=status, retry_plan=PLAN, **text)


def test_retry_plan_bounded_and_non_empty() -> None:
    translation(**FAILED, retry_plan="x" * MAX_RETRY_PLAN_CHARS)
    for bad in ("", "x" * (MAX_RETRY_PLAN_CHARS + 1)):
        with pytest.raises(ValidationError):
            translation(**FAILED, retry_plan=bad)


# --- SR-2: target_language = uk -----------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "de", "UK", "uk-UA"])
def test_target_language_only_uk(language: str) -> None:
    with pytest.raises(ValidationError, match="target_language"):
        translation(target_language=language)


# --- CR #4: межі тексту і канонічний порядок quality_flags ---------------------------------------


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("title", MAX_TRANSLATED_TITLE_CHARS),
        ("lead", MAX_TRANSLATED_LEAD_CHARS),
        ("body_text", MAX_TRANSLATED_BODY_TEXT_CHARS),
    ],
)
def test_translated_text_upper_bounds(field: str, limit: int) -> None:
    extra: dict[str, Any] = {"body_artifact": None} if field == "body_text" else {}
    translation(**{field: "я" * limit}, **extra)
    with pytest.raises(ValidationError, match=field):
        translation(**{field: "я" * (limit + 1)}, **extra)


def test_quality_flags_canonical_order_gives_identical_bytes() -> None:
    a = translation(quality_flags=["provider_truncated", "low_language_confidence"])
    b = translation(quality_flags=["low_language_confidence", "provider_truncated"])
    assert a == b
    assert canonical_json_bytes(a) == canonical_json_bytes(b)
    assert [f.value for f in a.quality_flags] == ["low_language_confidence", "provider_truncated"]


# --- CR #1: ключі строго str ---------------------------------------------------------------------


@pytest.mark.parametrize("adapter_type", [JsonObject, BoundedJsonObject])
def test_bytes_keys_rejected(adapter_type: Any) -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(adapter_type)
    with pytest.raises(ValidationError):
        adapter.validate_python({b"a": 1, "a": 2})
    with pytest.raises(ValidationError):
        adapter.validate_python({"outer": {b"inner": 1}})
    assert adapter.validate_python({"a": {"b": [1]}}) == {"a": {"b": [1]}}


def test_bytes_key_rejected_in_payload() -> None:
    with pytest.raises(ValidationError):
        NormalizedProjectionPayload.model_validate(normalized_payload(core={b"title": "x"}))


# --- CR #2: `1` і `1.0` — різні state_hash (задокументовано, не нормалізується) -----------------


def test_int_and_float_are_distinct_in_state_hash() -> None:
    assert compute_state_hash_v1({"a": 1}, {}, {}) != compute_state_hash_v1({"a": 1.0}, {}, {})
    # JSON-шлях зберігає тип числа: 1.0 після round-trip лишається float
    payload = NormalizedProjectionPayload.model_validate(normalized_payload(core={"a": 1.0}))
    again = NormalizedProjectionPayload.model_validate_json(canonical_json_bytes(payload))
    assert again.state_hash() == payload.state_hash()
