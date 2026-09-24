"""TM key і glossary (картка WP-04 PR1, вимоги 5–6; FR-017, О-2)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from collector.contracts import canonical_json_bytes, sha256_hex
from collector.translation.glossary import load_glossary, parse_glossary
from collector.translation.memory import (
    InMemoryTranslationMemory,
    MemoryEntry,
    normalized_segment_hash,
    translation_memory_key,
)
from collector.translation.preservation import mask_segment
from collector.translation.segmenter import segment_html

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[2] / "fixtures" / "translation" / "tm_key_golden.json"
    ).read_text(encoding="utf-8")
)
BASE: dict[str, str] = {
    "source_language": "de",
    "target_language": "uk",
    "normalized_segment_hash": normalized_segment_hash("Der Stadtrat tagt heute."),
    "provider": "google-cloud-translation-v3",
    "model_version": "nmt",
    "glossary_version": "a" * 64,
}
# 6 випадків: 5 компонентів FR-017, де provider/model — пара з двох полів.
VARIATIONS: list[tuple[str, str]] = [
    ("source_language", "pl"),
    ("target_language", "en"),
    ("normalized_segment_hash", normalized_segment_hash("Der Stadtrat tagt morgen.")),
    ("provider", "local-nllb"),
    ("model_version", "general/translation-llm"),
    ("glossary_version", "b" * 64),
]
assert len(VARIATIONS) == 6


def _key(**overrides: str) -> str:
    return translation_memory_key(**(BASE | overrides))


@pytest.mark.parametrize(("component", "value"), VARIATIONS, ids=[c for c, _ in VARIATIONS])
def test_changing_any_component_changes_key(component: str, value: str) -> None:
    assert BASE[component] != value
    assert _key(**{component: value}) != _key()


def test_key_payload_has_exactly_five_components() -> None:
    payload = {
        "source_language": "de",
        "target_language": "uk",
        "normalized_segment_hash": BASE["normalized_segment_hash"],
        "provider": {"name": BASE["provider"], "model_version": BASE["model_version"]},
        "glossary_version": BASE["glossary_version"],
    }
    assert len(payload) == 5
    assert _key() == sha256_hex(canonical_json_bytes(payload))


def test_whitespace_and_nfc_variants_give_same_key() -> None:
    nfc = "Café am Rhein  heute"
    nfd = "Cafe\u0301 am\u00a0Rhein\n\theute "
    assert normalized_segment_hash(nfc) == normalized_segment_hash(nfd)
    assert _key(normalized_segment_hash=normalized_segment_hash(nfd)) == _key(
        normalized_segment_hash=normalized_segment_hash(nfc)
    )


def test_masked_numbers_share_segment_hash_but_not_text() -> None:
    first = mask_segment(segment_html("<p>Es kamen 5 Gäste.</p>").segments[0])
    second = mask_segment(segment_html("<p>Es kamen 7 Gäste.</p>").segments[0])
    assert normalized_segment_hash(first.text) == normalized_segment_hash(second.text)
    assert first.unmask(first.text) != second.unmask(second.text)


def test_golden_key_is_stable_across_processes() -> None:
    assert normalized_segment_hash(GOLDEN["masked_text"]) == GOLDEN["normalized_segment_hash"]
    key = translation_memory_key(
        normalized_segment_hash=GOLDEN["normalized_segment_hash"], **GOLDEN["components"]
    )
    assert key == GOLDEN["key"]


async def test_in_memory_store_is_first_write_wins() -> None:
    memory = InMemoryTranslationMemory()
    entry = MemoryEntry("k", "de", "uk", "h", "p", "m", "g", "перший")
    await memory.put_many([entry, replace(entry, translated_text="другий")])
    assert (await memory.get_many(["k", "missing"])) == {"k": entry}


# --- glossary -----------------------------------------------------------------------------


def _glossary_data() -> dict[str, Any]:
    return {
        "schema": 1,
        "target_language": "uk",
        "pairs": {
            "*": [{"term": "Politico Europe", "keep": True}],
            "de": [{"term": "Bundestag", "target": "Бундестаг"}],
        },
    }


def test_default_glossary_loads_and_version_is_content_hash() -> None:
    glossary = load_glossary()
    raw = yaml.safe_load(
        (
            Path(__file__).resolve().parents[3] / "src/collector/translation/glossary/default.yaml"
        ).read_text(encoding="utf-8")
    )
    assert glossary.version == sha256_hex(canonical_json_bytes(raw))
    assert any(entry.term == "Bundestag" for entry in glossary.entries_for("de"))
    assert all(entry.term != "Bundestag" for entry in glossary.entries_for("fr"))


def test_changing_one_term_changes_version_and_tm_key() -> None:
    before = parse_glossary(_glossary_data())
    data = _glossary_data()
    data["pairs"]["de"][0]["target"] = "Бундестаґ"
    after = parse_glossary(data)
    assert before.version != after.version
    assert _key(glossary_version=before.version) != _key(glossary_version=after.version)


def test_glossary_file_path_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "glossary.yaml"
    path.write_text(yaml.safe_dump(_glossary_data(), allow_unicode=True), encoding="utf-8")
    assert load_glossary(path).version == parse_glossary(_glossary_data()).version


@pytest.mark.parametrize(
    "broken",
    [
        {"schema": 2},
        {"target_language": "en"},
        {"pairs": {"xx-long": []}},
        {"pairs": {"de": [{"term": "A", "target": "Б", "keep": True}]}},
        {"pairs": {"de": [{"term": "A"}]}},
        {"pairs": {"de": [{"term": "A", "target": "Б"}, {"term": "A", "target": "В"}]}},
        {"pairs": {"de": [{"term": "A", "target": " "}]}},
    ],
)
def test_invalid_glossary_is_rejected(broken: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="glossary"):
        parse_glossary(_glossary_data() | broken)
