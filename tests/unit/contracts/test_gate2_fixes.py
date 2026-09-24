"""Gate 2 WP-01C PR2: M-1 (set → недетермінований hash), M-2 (розмір/int64), L-1, L-2."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from factories import current_document_payload, news_version_created_payload, normalized_payload
from pydantic import ValidationError

from collector.contracts._base import (
    JSON_INT_MAX,
    JSON_INT_MIN,
    MAX_JSON_BLOCK_BYTES,
    MAX_JSON_KEY_CHARS,
    MAX_JSON_STRING_CHARS,
)
from collector.contracts.canonical import CanonicalEncodingError, canonical_json_bytes
from collector.contracts.current import CurrentDocumentBase
from collector.contracts.news import NewsVersionCreatedEvent
from collector.contracts.payload import NormalizedProjectionPayload

REPO_ROOT = Path(__file__).resolve().parents[3]


def build(**overrides: Any) -> NormalizedProjectionPayload:
    return NormalizedProjectionPayload.model_validate(normalized_payload(**overrides))


# --- M-1: set/frozenset/tuple відхиляються --------------------------------------------------------


@pytest.mark.parametrize("container", [{"a", "b"}, frozenset({"a", "b"}), ("a", "b")], ids=type)
def test_non_list_sequences_rejected_in_bounded_blocks(container: Any) -> None:
    with pytest.raises(ValidationError):
        build(core={"tags": container})
    with pytest.raises(ValidationError):
        build(core={"nested": {"tags": container}})


def test_non_list_sequences_rejected_in_pr1_json_blocks_too() -> None:
    # JsonValue спільний: CurrentDocumentBase (PR1) теж не приймає set.
    with pytest.raises(ValidationError):
        CurrentDocumentBase.model_validate(current_document_payload(core={"tags": {"a", "b"}}))


def test_json_arrays_still_accepted() -> None:
    assert build(core={"tags": ["b", "a"]}).core["tags"] == ["b", "a"]


PROBE = r"""
import json, sys
sys.path.insert(0, "tests/fixtures/contracts")
from factories import normalized_payload
from pydantic import ValidationError
from collector.contracts.payload import NormalizedProjectionPayload
tags = ["alpha", "beta", "gamma", "delta", "epsilon"]
listed = NormalizedProjectionPayload.model_validate(normalized_payload(core={"tags": tags}))
try:
    NormalizedProjectionPayload.model_validate(normalized_payload(core={"tags": set(tags)}))
    rejected = False
except ValidationError:
    rejected = True
print(json.dumps({"hash": listed.state_hash(), "set_rejected": rejected}))
"""


def test_state_hash_identical_across_hash_seeds_and_set_always_rejected() -> None:
    results = []
    for seed in ("0", "1", "2", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run(
            [sys.executable, "-c", PROBE],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
            env=env,
        )
        results.append(json.loads(out.stdout))
    assert len({r["hash"] for r in results}) == 1, results
    assert all(r["set_rejected"] for r in results), results


# --- M-2: рядки, ключі, int64, розмір блоку -----------------------------------------------------


def test_string_length_limit() -> None:
    build(core={"text": "я" * MAX_JSON_STRING_CHARS})
    with pytest.raises(ValidationError, match="рядок"):
        build(core={"text": "я" * (MAX_JSON_STRING_CHARS + 1)})
    with pytest.raises(ValidationError, match="рядок"):
        build(core={"list": ["x" * (MAX_JSON_STRING_CHARS + 1)]})


def test_key_length_limit() -> None:
    build(core={"k" * MAX_JSON_KEY_CHARS: 1})
    with pytest.raises(ValidationError, match="ключ"):
        build(core={"k" * (MAX_JSON_KEY_CHARS + 1): 1})


def test_int64_range() -> None:
    build(core={"min": JSON_INT_MIN, "max": JSON_INT_MAX, "flag": True})
    for value in (JSON_INT_MAX + 1, JSON_INT_MIN - 1, 2**70):
        with pytest.raises(ValidationError, match="int64"):
            build(latest_state={"n": value})
    with pytest.raises(ValidationError, match="int64"):
        build(observation={"values": {"n": [2**64]}})


def test_block_canonical_size_limit() -> None:
    chunk = "x" * 60_000
    ok_items = (MAX_JSON_BLOCK_BYTES - 10_000) // 60_010
    build(attributes={f"k{i}": chunk for i in range(ok_items)})
    too_many = MAX_JSON_BLOCK_BYTES // 60_000 + 1
    with pytest.raises(ValidationError, match="canonical bytes"):
        build(attributes={f"k{i}": chunk for i in range(too_many)})


def test_block_limit_is_above_event_inline_limit() -> None:
    from collector.contracts.events import EVENT_INLINE_LIMIT_BYTES

    # великий diff іде через payload_artifact, а не відхиляється контрактом
    assert MAX_JSON_BLOCK_BYTES > EVENT_INLINE_LIMIT_BYTES


# --- L-1, L-2 -----------------------------------------------------------------------------------


def test_lone_surrogate_is_canonical_encoding_error() -> None:
    with pytest.raises(CanonicalEncodingError, match="сурогат"):
        canonical_json_bytes({"a": "x\udfff"})


def test_source_locale_raw_is_bounded() -> None:
    NewsVersionCreatedEvent.model_validate(
        news_version_created_payload(source_locale_raw="de-DE" + "x" * 59)
    )
    with pytest.raises(ValidationError):
        NewsVersionCreatedEvent.model_validate(
            news_version_created_payload(source_locale_raw="x" * 65)
        )
