"""Регресії знахідок код-рев'ю (gate 3): CR-01…CR-09."""

from __future__ import annotations

import time
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from factories import ENTITY_A, ENTITY_B, GROUP_1, GROUP_2, SHA_A, at, current_document_payload
from pydantic import ValidationError

from collector.contracts.artifacts import ArtifactRef
from collector.contracts.canonical import (
    CanonicalEncodingError,
    canonical_json_bytes,
    format_utc_datetime,
)
from collector.contracts.current import CurrentDocumentBase, compute_state_hash_v1
from collector.contracts.enums import ContactKind, ReleaseState, ResolutionAction
from collector.contracts.events import DomainChangedEvent
from collector.contracts.identity import entity_id_timestamp, identity_hash_v1, new_entity_id
from collector.contracts.release import ReleaseManifest, transition_release
from collector.contracts.resolution import ResolutionDecision, project_groups
from collector.contracts.values import ContactValue, Money

# --- CR-01: strict JSON у core/attributes/latest_state і payload --------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        datetime(2026, 9, 1, 12, tzinfo=UTC),
        datetime(2026, 9, 1, 12),
        Decimal("1.5"),
        UUID(int=1),
        b"x",
    ],
    ids=lambda v: type(v).__name__,
)
def test_current_document_rejects_non_json_scalars_in_blocks(bad: object) -> None:
    for block in ("core", "attributes", "latest_state"):
        payload = current_document_payload()
        payload[block] = {**payload[block], "x": bad}
        with pytest.raises(ValidationError, match="x"):
            CurrentDocumentBase.model_validate(payload)
        payload[block] = {**payload[block], "nested": {"deep": [bad]}}
        with pytest.raises(ValidationError):
            CurrentDocumentBase.model_validate(payload)


def test_current_document_rejects_nan_and_bool_as_int_is_typed() -> None:
    payload = current_document_payload()
    payload["core"] = {**payload["core"], "f": float("nan")}
    with pytest.raises(ValidationError):
        CurrentDocumentBase.model_validate(payload)
    assert compute_state_hash_v1({"k": True}, {}, {}) != compute_state_hash_v1({"k": 1}, {}, {})


def test_state_hash_survives_json_round_trip() -> None:
    doc = CurrentDocumentBase.model_validate(current_document_payload())
    again = CurrentDocumentBase.model_validate_json(doc.model_dump_json(by_alias=True))
    assert again == doc
    assert again.state_hash == doc.state_hash
    assert compute_state_hash_v1(again.core, again.attributes, again.latest_state) == doc.state_hash


def test_domain_event_payload_is_strict_json() -> None:
    base = {
        "event_id": UUID(int=5),
        "aggregate_id": ENTITY_A,
        "aggregate_version": 1,
        "event_type": "catalog.item.changed",
        "payload_schema_version": "1.0",
        "occurred_at": at(),
        "projection_task_id": UUID(int=6),
        "result_state_hash": "v1:" + "e" * 64,
    }
    with pytest.raises(ValidationError):
        DomainChangedEvent.model_validate({**base, "payload": {"when": at()}})
    with pytest.raises(ValidationError):
        DomainChangedEvent.model_validate({**base, "payload": {"amount": Decimal("1")}})
    ok = DomainChangedEvent.model_validate(
        {**base, "payload": {"when": "2026-09-01T12:00:00.000000Z", "n": [1, 2.5]}}
    )
    assert ok.payload == {"when": "2026-09-01T12:00:00.000000Z", "n": [1, 2.5]}


# --- CR-02: project_groups лінійний -----------------------------------------------------------


def _uuid7(n: int) -> UUID:
    return UUID(int=(0x019997C0 << 96) | (0x7 << 76) | (0b10 << 62) | n)


def _decision(
    number: int,
    action: ResolutionAction,
    members: list[UUID],
    *,
    version: int = 1,
    group: UUID | None = None,
    supersedes: int | None = None,
) -> ResolutionDecision:
    return ResolutionDecision(
        decision_id=UUID(int=number),
        decision_version=version,
        action=action,
        member_entity_ids=members,
        canonical_group_id=group,
        score=Decimal("0.9") if action is ResolutionAction.MERGE else None,
        rule_or_model_version="m/1",
        actor="system:m" if action is ResolutionAction.MERGE else "operator:x",
        reason="t",
        effective_at=at(number),
        recorded_at=at(number),
        supersedes_decision_id=None if supersedes is None else UUID(int=supersedes),
    )


def _disjoint_merges(count: int) -> list[ResolutionDecision]:
    return [
        _decision(
            i + 1,
            ResolutionAction.MERGE,
            [_uuid7(2 * i + 1), _uuid7(2 * i + 2)],
            group=UUID(int=10_000_000 + i),
        )
        for i in range(count)
    ]


def test_project_groups_scales_linearly_on_disjoint_merges() -> None:
    small, large = _disjoint_merges(2_000), _disjoint_merges(20_000)
    started = time.perf_counter()
    project_groups(small)
    small_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    snapshot = project_groups(large)
    large_elapsed = time.perf_counter() - started
    assert len(snapshot.groups) == 20_000
    # квадратичний алгоритм дав би ×100 (рев'ю: 4000 → 18 s); лінійний — ~×10; пороги щедрі
    assert large_elapsed < 5.0, large_elapsed
    assert large_elapsed < max(small_elapsed, 0.02) * 40, (small_elapsed, large_elapsed)


def test_project_groups_chained_merges_pull_whole_groups_via_index() -> None:
    ids = [_uuid7(i) for i in range(1, 6)]
    history = [
        _decision(i, ResolutionAction.MERGE, [ids[i - 1], ids[i]], group=UUID(int=100 + i))
        for i in range(1, 5)
    ]  # ланцюжок 1-2, 2-3, 3-4, 4-5 → одна група з 5
    snapshot = project_groups(history)
    assert list(snapshot.groups.values()) == [sorted(ids, key=lambda u: u.int)]
    assert list(snapshot.groups) == [UUID(int=104)]


# --- CR-09: decision_version і ланцюжок supersedes A ← B ← C -----------------------------------


def _pair() -> list[UUID]:
    return sorted([ENTITY_A, ENTITY_B], key=lambda u: u.int)


def test_supersedes_chain_restores_block_after_double_cancel() -> None:
    block = _decision(1, ResolutionAction.MANUAL_BLOCK, [ENTITY_A, ENTITY_B])
    cancel = _decision(2, ResolutionAction.REJECT, [ENTITY_A], supersedes=1)  # B ⊃ A
    merge = _decision(4, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1)
    assert project_groups([block, cancel, merge]).groups == {GROUP_1: _pair()}
    # C ⊃ B: B більше не діє → блок A відновлено
    recancel = _decision(3, ResolutionAction.REJECT, [ENTITY_A], supersedes=2)
    snapshot = project_groups([block, cancel, recancel, merge])
    assert snapshot.superseded_decision_ids == [UUID(int=2)]
    assert snapshot.blocked_pairs == [(ENTITY_A, ENTITY_B)]
    assert snapshot.groups == {}
    assert snapshot.skipped_decision_ids == [UUID(int=4)]
    # D ⊃ C: C не діє → B діє → A знову знято
    chain4 = _decision(5, ResolutionAction.REJECT, [ENTITY_A], supersedes=3)
    assert project_groups([block, cancel, recancel, chain4, merge]).groups == {GROUP_1: _pair()}


def test_supersedes_cycle_is_rejected() -> None:
    a = _decision(1, ResolutionAction.REJECT, [ENTITY_A], supersedes=2)
    b = _decision(2, ResolutionAction.REJECT, [ENTITY_A], supersedes=1)
    with pytest.raises(ValueError, match="цикл"):
        project_groups([a, b])


def test_only_latest_decision_version_is_replayed() -> None:
    v1 = _decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1, version=1)
    v2 = _decision(1, ResolutionAction.REJECT, [ENTITY_A], version=2)
    snapshot = project_groups([v1, v2])
    assert snapshot.groups == {}
    assert snapshot.applied_decision_ids == [UUID(int=1)]
    assert project_groups([v2, v1]) == snapshot
    v3 = _decision(1, ResolutionAction.MERGE, [ENTITY_A, ENTITY_B], group=GROUP_2, version=3)
    assert project_groups([v1, v2, v3]).groups == {GROUP_2: _pair()}


# --- CR-03/CR-04: колізії ключів після нормалізації ------------------------------------------


def test_canonical_rejects_keys_colliding_after_nfc() -> None:
    nfd, nfc = unicodedata.normalize("NFD", "é"), unicodedata.normalize("NFC", "é")
    with pytest.raises(CanonicalEncodingError, match="дублюється"):
        canonical_json_bytes({nfd: 1, nfc: 2})
    with pytest.raises(CanonicalEncodingError, match="дублюється"):
        canonical_json_bytes({nfc: 2, nfd: 1})


def test_identity_hash_rejects_keys_colliding_after_casefold() -> None:
    with pytest.raises(ValueError, match="дублюється"):
        identity_hash_v1("https://x/1", {"Brand": "A", "brand": "B"})


# --- CR-05: quality_report / *_artifact не схлопуються після round-trip -----------------------


def test_release_report_artifact_ref_survives_round_trip() -> None:
    from release_factories import draft, manifest_payload, part

    ref = ArtifactRef(uri="s3://q/" + SHA_A, sha256=SHA_A, size_bytes=1, media_type="text/csv")
    manifest = ReleaseManifest.model_validate(manifest_payload(quality_report_artifact=ref))
    again = ReleaseManifest.model_validate_json(manifest.model_dump_json(by_alias=True))
    assert isinstance(again.quality_report_artifact, ArtifactRef)
    assert again.quality_report is None
    moved = transition_release(manifest, ReleaseState.BUILDING)
    assert isinstance(moved.quality_report_artifact, ArtifactRef)
    with pytest.raises(ValidationError, match="не обидва"):
        ReleaseManifest.model_validate(
            manifest_payload(quality_report={"a": 1}, quality_report_artifact=ref)
        )
    building = transition_release(draft(), ReleaseState.BUILDING)
    validating = transition_release(
        building,
        ReleaseState.VALIDATING,
        parts=[part()],
        quality_report_artifact=ref,
        reconciliation_result_artifact=ref,
    )
    published = transition_release(validating, ReleaseState.PUBLISHED, published_at=at(5))
    assert published.state is ReleaseState.PUBLISHED


# --- CR-06/07/08 та спрощення ----------------------------------------------------------------


def test_format_utc_datetime_pads_year_without_strftime() -> None:
    value = datetime(999, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
    assert format_utc_datetime(value) == "0999-01-02T03:04:05.000006Z"


def test_e164_validation_is_ascii_only() -> None:
    with pytest.raises(ValidationError, match="E.164"):
        ContactValue(kind=ContactKind.PHONE, raw="x", normalized="+٣٨٠١٢٣٤٥٦٧")
    assert ContactValue(kind=ContactKind.PHONE, raw="x", normalized="+380501234567").normalized


def test_money_strict_rejects_bool_str_float_decimal() -> None:
    for bad in (True, "100", 1.0, Decimal("1")):
        with pytest.raises(ValidationError):
            Money.model_validate({"amount_minor": bad, "currency": "UAH"})
    assert Money(amount_minor=100, currency="UAH").amount_minor == 100


def test_entity_id_timestamp_exact_milliseconds() -> None:
    value = new_entity_id()
    ms = value.int >> 80
    expected = datetime.fromtimestamp(ms // 1000, tz=UTC).replace(microsecond=(ms % 1000) * 1000)
    assert entity_id_timestamp(value) == expected
