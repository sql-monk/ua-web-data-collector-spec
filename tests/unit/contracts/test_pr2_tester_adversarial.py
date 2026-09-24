"""WP-01C PR2 — незалежні adversarial-тести тестувальника (рівні §16.1: 2 Contract, 9 Temporal).

Покриває: межі `BoundedJsonObject` (межа/межа+1 на вкладених рівнях, масиви в масивах, NaN/inf,
не-UTF-8 bytes, lone surrogates), `TranslationStatus` проти ТЗ §5.4/§16, підміну ref у
`check_payload_matches_artifact`, temporal-осі (nullable source time ніколи не підміняється
system time, лише aware UTC) і UUID `_id` у нових Mongo-записах.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from factories import (
    ENTITY_B,
    RECORD_ID,
    entity_version_payload,
    news_translation_payload,
    news_version_created_payload,
    normalized_artifact,
    normalized_payload,
    observation_payload,
    review_question_payload,
    seller_contact_payload,
)
from pydantic import ValidationError

import collector.contracts as contracts_pkg
import collector.contracts.enums as enums_mod
from collector.contracts._base import (
    MAX_JSON_ARRAY_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_OBJECT_KEYS,
)
from collector.contracts.canonical import CanonicalEncodingError, canonical_json_bytes
from collector.contracts.enums import (
    DataDomain,
    EffectiveAtBasis,
    TranslationQualityFlag,
    TranslationStatus,
)
from collector.contracts.events import encode_event
from collector.contracts.news import NewsTranslation, NewsVersionCreatedEvent
from collector.contracts.payload import (
    NormalizedProjectionPayload,
    PayloadArtifactMismatchError,
    check_payload_matches_artifact,
)
from collector.contracts.records import (
    EntityProjectionVersion,
    ObservationRecord,
    ReviewQuestionRecord,
    SellerContactObservation,
)
from collector.contracts.temporal import derive_effective_time

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "TECHNICAL_SPECIFICATION.md"
BLOCKS = ("core", "attributes", "latest_state")
LONE_HIGH = chr(0xD800)
LONE_LOW = chr(0xDC80)


def build(**overrides: Any) -> NormalizedProjectionPayload:
    return NormalizedProjectionPayload.model_validate(normalized_payload(**overrides))


def chain(depth: int, leaf: Any = 1, *, via_list: bool = False) -> dict[str, Any]:
    """Об'єкт, у якому `leaf` лежить на рівні `depth` (корінь = 1); `via_list` — через масиви."""
    node: Any = leaf
    if via_list:
        for _ in range(depth - 2):
            node = [node]
        return {"root": node}
    for _ in range(depth - 1):
        node = {"n": node}
    result: dict[str, Any] = node
    return result


# --- BoundedJsonObject: межа / межа+1 -------------------------------------------------------


@pytest.mark.parametrize("block", BLOCKS)
def test_depth_limit_exact_boundary_for_objects(block: str) -> None:
    build(**{block: chain(MAX_JSON_DEPTH)})
    with pytest.raises(ValidationError, match="вкладеність"):
        build(**{block: chain(MAX_JSON_DEPTH + 1)})


@pytest.mark.parametrize("block", BLOCKS)
def test_depth_limit_counts_list_levels_too(block: str) -> None:
    # {"root": [[...[1]...]]}: кожен масив — теж рівень вкладеності.
    build(**{block: chain(MAX_JSON_DEPTH, via_list=True)})
    with pytest.raises(ValidationError, match="вкладеність"):
        build(**{block: chain(MAX_JSON_DEPTH + 1, via_list=True)})


def test_empty_containers_at_depth_boundary() -> None:
    # Порожній контейнер на глибині MAX — ок; на MAX+1 — відмова (depth рахує і порожні).
    build(core=chain(MAX_JSON_DEPTH, leaf={}))
    build(core=chain(MAX_JSON_DEPTH, leaf=[]))
    with pytest.raises(ValidationError, match="вкладеність"):
        build(core=chain(MAX_JSON_DEPTH + 1, leaf={}))
    with pytest.raises(ValidationError, match="вкладеність"):
        build(core=chain(MAX_JSON_DEPTH + 1, leaf=[]))


def test_key_limit_applies_to_nested_objects() -> None:
    ok = {f"k{i}": i for i in range(MAX_JSON_OBJECT_KEYS)}
    too_many = {f"k{i}": i for i in range(MAX_JSON_OBJECT_KEYS + 1)}
    build(core={"specs": ok})
    with pytest.raises(ValidationError, match="ключів"):
        build(core={"specs": too_many})
    with pytest.raises(ValidationError, match="ключів"):
        build(core={"list": [too_many]})


def test_array_limit_applies_inside_nested_object_in_array() -> None:
    build(core={"media": [{"urls": list(range(MAX_JSON_ARRAY_ITEMS))}]})
    with pytest.raises(ValidationError, match="unbounded"):
        build(core={"media": [{"urls": list(range(MAX_JSON_ARRAY_ITEMS + 1))}]})


def test_bounded_applies_to_version_snapshot_and_observation_records() -> None:
    too_long = list(range(MAX_JSON_ARRAY_ITEMS + 1))
    data = entity_version_payload()
    data["snapshot"]["latest_state"] = {"history": too_long}
    with pytest.raises(ValidationError, match="unbounded"):
        EntityProjectionVersion.model_validate(data)
    obs = observation_payload()
    obs["observed"] = {"values": {"deep": chain(MAX_JSON_DEPTH + 1)}}
    with pytest.raises(ValidationError, match="вкладеність"):
        ObservationRecord.model_validate(obs)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("block", BLOCKS)
def test_non_finite_floats_rejected_in_python_input(block: str, bad: float) -> None:
    with pytest.raises(ValidationError):
        build(**{block: {"x": bad}})
    with pytest.raises(ValidationError):
        build(**{block: {"x": [1.0, {"y": bad}]}})


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_literals_rejected(literal: str) -> None:
    text = json.dumps(normalized_payload(core={"x": 0.5}))
    text = text.replace('"x": 0.5', f'"x": {literal}')
    assert literal in text
    with pytest.raises(ValidationError):
        NormalizedProjectionPayload.model_validate_json(text)


def test_non_finite_in_observation_values_rejected() -> None:
    with pytest.raises(ValidationError):
        build(observation={"values": {"old_price": float("nan")}})


@pytest.mark.parametrize(
    "value",
    [Decimal("1.5"), datetime(2026, 1, 1, tzinfo=UTC), UUID(int=1), b"raw"],
    ids=["decimal", "datetime", "uuid", "bytes"],
)
def test_non_json_python_types_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        build(core={"x": value})


def test_non_utf8_payload_bytes_rejected() -> None:
    data = json.dumps(normalized_payload(), ensure_ascii=False).encode("utf-8")
    broken = data.replace("Дриль".encode(), b"\xff\xfe")
    assert broken != data
    with pytest.raises(ValidationError):
        NormalizedProjectionPayload.model_validate_json(broken)


def test_lone_surrogate_json_escape_rejected_by_projector_path() -> None:
    # Projector читає bytes artifact через model_validate_json: lone surrogate escape — відмова.
    escape = "\\" + "ud800"
    text = json.dumps(normalized_payload()).replace('"seller"', '"s' + escape + '"')
    assert escape in text
    with pytest.raises(ValidationError):
        NormalizedProjectionPayload.model_validate_json(text)


def test_lone_surrogate_in_python_input_never_produces_canonical_bytes() -> None:
    # Gate 2 L-1 (закрито): bounded-блоки відхиляють lone surrogate вже на валідації моделі;
    # поза блоками (event-рядки) canonical_json_bytes/encode_event кидають CanonicalEncodingError.
    with pytest.raises(ValidationError, match="сурогат"):
        build(core={"title": "x" + LONE_HIGH})
    with pytest.raises(ValidationError):  # constrained str (max_length=64) відхиляє сурогат
        NewsVersionCreatedEvent.model_validate(
            news_version_created_payload(source_locale_raw="de-" + LONE_LOW)
        )
    with pytest.raises(CanonicalEncodingError, match="сурогат"):
        canonical_json_bytes({"title": "x" + LONE_LOW})


def test_json_origin_payload_state_hash_is_deterministic() -> None:
    # Шлях projector-а (bytes → model_validate_json): масиви зберігають порядок, hash стабільний.
    payload = build(core={"tags": ["b", "a", "c"]})
    data = canonical_json_bytes(payload)
    again = NormalizedProjectionPayload.model_validate_json(data)
    assert again.core["tags"] == ["b", "a", "c"]
    assert again.state_hash() == payload.state_hash()


def test_bool_is_not_coerced_and_strings_not_numbers() -> None:
    payload = build(core={"flag": True, "n": 1, "s": "1"})
    assert payload.core == {"flag": True, "n": 1, "s": "1"}
    assert type(payload.core["flag"]) is bool
    assert type(payload.core["n"]) is int


# --- TranslationStatus / QualityFlag проти ТЗ -------------------------------------------------


def test_translation_status_exactly_four_closed_values() -> None:
    assert {s.value for s in TranslationStatus} == {
        "pending",
        "translated",
        "not_required",
        "translation_failed",
    }
    assert len(TranslationStatus) == 4
    for bad in ("failed", "TRANSLATED", "Translated", "done", "skipped", ""):
        with pytest.raises(ValidationError):
            NewsTranslation.model_validate(news_translation_payload(status=bad))


def test_translation_statuses_named_in_spec_are_members() -> None:
    # ТЗ §5.4: `status = not_required`; §12 coverage: `translation_failed`.
    text = SPEC.read_text(encoding="utf-8")
    assert "status = not_required" in text
    assert "`translation_failed`" in text
    values = {s.value for s in TranslationStatus}
    assert {"not_required", "translation_failed"} <= values


def test_translation_status_is_the_only_translation_status_enum() -> None:
    # §5.5: єдиний enum статусу перекладу в проєкті — інших enum з цими значеннями немає.
    owners = [
        name
        for name, obj in vars(enums_mod).items()
        if isinstance(obj, type)
        and issubclass(obj, Enum)
        and {"translation_failed", "not_required"} & {m.value for m in obj}
    ]
    assert owners == ["TranslationStatus"]
    src = Path(contracts_pkg.__file__).resolve().parents[1]
    hits = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if re.search(r"""["']translation_failed["']""", p.read_text(encoding="utf-8"))
    ]
    assert hits == ["contracts/enums.py"], hits


def test_quality_flags_cover_wp04_decisions() -> None:
    # WP-04 О-5/U-2: `language_unsupported` + мінімум картки PR2 п.4.
    assert {
        "preservation_failed",
        "low_language_confidence",
        "provider_truncated",
        "language_unsupported",
    } <= {f.value for f in TranslationQualityFlag}


def test_failed_translation_with_language_unsupported_is_valid_without_text() -> None:
    tr = NewsTranslation.model_validate(
        news_translation_payload(
            status="translation_failed",
            title=None,
            lead=None,
            body_artifact=None,
            quality_flags=["language_unsupported"],
            retry_plan="add language to EXTRA_SOURCE_LANGUAGES + golden pair",
            cost=None,
            character_count=0,
        )
    )
    assert tr.status is TranslationStatus.TRANSLATION_FAILED


# --- check_payload_matches_artifact: підміна ref ------------------------------------------


def test_substituted_entity_in_ref_is_rejected() -> None:
    payload = build()
    check_payload_matches_artifact(payload, normalized_artifact())
    with pytest.raises(PayloadArtifactMismatchError, match="entity_uuid"):
        check_payload_matches_artifact(payload, normalized_artifact(ENTITY_B))


def test_substituted_domain_in_ref_is_rejected() -> None:
    swapped = normalized_artifact().model_copy(update={"domain": DataDomain.VEHICLE})
    with pytest.raises(PayloadArtifactMismatchError, match="domain"):
        check_payload_matches_artifact(build(), swapped)


def test_substituted_schema_version_in_ref_is_rejected() -> None:
    for version in ("1.1", "2.0", "0.9"):
        swapped = normalized_artifact().model_copy(update={"schema_version": version})
        with pytest.raises(PayloadArtifactMismatchError, match="schema_version"):
            check_payload_matches_artifact(build(), swapped)


def test_ref_without_schema_version_is_rejected() -> None:
    swapped = normalized_artifact().model_copy(update={"schema_version": None})
    with pytest.raises(PayloadArtifactMismatchError):
        check_payload_matches_artifact(build(), swapped)


def test_substituted_state_hash_in_version_record_is_rejected() -> None:
    with pytest.raises(ValidationError, match="state_hash"):
        EntityProjectionVersion.model_validate(entity_version_payload(state_hash="v1:" + "0" * 64))


def test_substituted_artifact_entity_in_records_is_rejected() -> None:
    other = normalized_artifact(ENTITY_B).model_dump(mode="json")
    with pytest.raises(ValidationError, match="artifact.entity_uuid"):
        EntityProjectionVersion.model_validate(entity_version_payload(artifact=other))
    with pytest.raises(ValidationError, match="artifact.entity_uuid"):
        ObservationRecord.model_validate(observation_payload(artifact=other))


def test_substituted_translation_key_components_rejected() -> None:
    for field, value in (
        ("provider", "deepl"),
        ("model_version", "nmt-2"),
        ("glossary_version", "0" * 64),
        ("article_version_id", str(RECORD_ID)),
    ):
        with pytest.raises(ValidationError, match="translation_idempotency_key"):
            NewsTranslation.model_validate(news_translation_payload(**{field: value}))


# --- Temporal (рівень 9) --------------------------------------------------------------------


def test_null_source_time_is_never_filled_with_system_time() -> None:
    payload = build(source_time={})
    time = payload.entity_time()
    assert time.source_event_at is None
    assert time.source_updated_at is None
    assert time.fetched_at == payload.system_time.fetched_at
    dumped = json.loads(canonical_json_bytes(payload))
    assert dumped["source_time"]["source_event_at"] is None
    assert dumped["source_time"]["source_updated_at"] is None
    effective = derive_effective_time(payload.source_time, payload.system_time)
    assert effective.effective_at == payload.system_time.observed_at
    assert effective.effective_at_basis is EffectiveAtBasis.OBSERVED
    assert effective.source_time_inferred is True


@pytest.mark.parametrize("field", ["source_event_at", "source_updated_at"])
def test_source_time_equal_to_fetched_at_rejected_r43(field: str) -> None:
    fetched = normalized_payload()["system_time"]["fetched_at"]
    with pytest.raises(ValidationError, match="fetched_at"):
        build(source_time={field: fetched})


@pytest.mark.parametrize(
    "value",
    ["2026-09-01T12:00:00", "2026-09-01T14:00:00+02:00", "2026-09-01T07:00:00-05:00"],
    ids=["naive", "plus2", "minus5"],
)
def test_non_utc_datetimes_rejected_everywhere(value: str) -> None:
    cases: list[tuple[type[Any], dict[str, Any]]] = []
    data = normalized_payload()
    data["system_time"]["ingested_at"] = value
    cases.append((NormalizedProjectionPayload, data))
    data = normalized_payload()
    data["source_time"]["source_event_at"] = value
    cases.append((NormalizedProjectionPayload, data))
    cases.append((EntityProjectionVersion, entity_version_payload(recorded_at=value)))
    cases.append((ObservationRecord, observation_payload(observed_at=value)))
    cases.append((SellerContactObservation, seller_contact_payload(observed_at=value)))
    cases.append((ReviewQuestionRecord, review_question_payload(published_at=value)))
    cases.append((NewsTranslation, news_translation_payload(created_at=value)))
    cases.append((NewsVersionCreatedEvent, news_version_created_payload(occurred_at=value)))
    for model, payload in cases:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_utc_offset_zero_forms_are_equivalent_and_canonical_z() -> None:
    forms: list[Any] = [
        "2026-09-01T12:00:00+00:00",
        "2026-09-01T12:00:00Z",
        datetime(2026, 9, 1, 12, tzinfo=timezone.utc),  # noqa: UP017 — саме інший tzinfo-об'єкт
    ]
    encoded = [
        encode_event(
            NewsVersionCreatedEvent.model_validate(news_version_created_payload(occurred_at=f))
        )
        for f in forms
    ]
    assert encoded[0] == encoded[1] == encoded[2]
    assert b'"occurred_at":"2026-09-01T12:00:00.000000Z"' in encoded[0].event_bytes


def test_python_non_utc_tzinfo_rejected() -> None:
    kyiv = timezone(timedelta(hours=3))
    with pytest.raises(ValidationError):
        NewsTranslation.model_validate(
            news_translation_payload(created_at=datetime(2026, 9, 1, 15, tzinfo=kyiv))
        )


def test_review_source_times_stay_null_and_independent_of_observed_at() -> None:
    rec = ReviewQuestionRecord.model_validate(
        review_question_payload(published_at=None, updated_at=None)
    )
    assert rec.published_at is None
    assert rec.updated_at is None
    assert rec.observed_at is not None


# --- UUID `_id` у нових Mongo-записах -----------------------------------------------------

RECORDS: list[tuple[type[Any], Any]] = [
    (EntityProjectionVersion, entity_version_payload),
    (ObservationRecord, observation_payload),
    (SellerContactObservation, seller_contact_payload),
    (ReviewQuestionRecord, review_question_payload),
]
RECORD_IDS = [model.__name__ for model, _ in RECORDS]


@pytest.mark.parametrize(("model", "factory"), RECORDS, ids=RECORD_IDS)
def test_record_id_is_uuid_and_dumped_as_mongo_id(model: Any, factory: Any) -> None:
    rec = model.model_validate(factory())
    assert isinstance(rec.id, UUID)
    dumped = rec.model_dump(by_alias=True)
    assert "_id" in dumped
    assert "id" not in dumped
    assert isinstance(dumped["_id"], UUID)
    assert json.loads(rec.model_dump_json(by_alias=True))["_id"] == str(RECORD_ID)


@pytest.mark.parametrize(("model", "factory"), RECORDS, ids=RECORD_IDS)
@pytest.mark.parametrize(
    "bad_id",
    ["507f1f77bcf86cd799439011", "not-a-uuid", "", 12345, None],
    ids=["objectid", "text", "empty", "int", "none"],
)
def test_record_rejects_non_uuid_id(model: Any, factory: Any, bad_id: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(factory(_id=bad_id))


@pytest.mark.parametrize(("model", "factory"), RECORDS, ids=RECORD_IDS)
def test_record_requires_id(model: Any, factory: Any) -> None:
    data = factory()
    del data["_id"]
    with pytest.raises(ValidationError):
        model.model_validate(data)
