"""Adversarial-сценарії WP-01C (незалежне тестування): фіксують контракт на межових входах.

Кожен розділ відповідає пункту картки `docs/plan/cards/WP-01C.md`; тести, що фіксують
*поточну* поведінку (а не вимогу картки), позначені коментарем «контракт зафіксовано».
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from factories import (
    ENTITY_A,
    ENTITY_B,
    ENTITY_C,
    ENTITY_D,
    EVENT_ID,
    GROUP_1,
    GROUP_2,
    TASK_ID,
    at,
)
from pydantic import TypeAdapter, ValidationError

from collector.contracts.artifacts import UploadClaim, can_commit
from collector.contracts.canonical import canonical_json_bytes
from collector.contracts.current import compute_state_hash_v1
from collector.contracts.enums import (
    STATE_AXES,
    ContactKind,
    ContentAccess,
    EntityLifecycle,
    FetchOutcome,
    ResolutionAction,
    RouteState,
    SourceState,
    UploadClaimStatus,
    map_research_access_state,
)
from collector.contracts.events import (
    EVENT_INLINE_LIMIT_BYTES,
    DomainChangedEvent,
    EventTooLargeError,
    decode_event,
    encode_event,
)
from collector.contracts.identity import SourceIdentity
from collector.contracts.resolution import ResolutionDecision, project_groups
from collector.contracts.temporal import (
    BitemporalInterval,
    EntityTime,
    SourceTime,
    SystemTime,
    VersionTimes,
    build_intervals,
    derive_effective_time,
)
from collector.contracts.values import ContactValue, Money

KYIV = timezone(timedelta(hours=3))

# --- 2. enum §5.5: дослівна рівність множин + синоніми --------------------------------------

SPEC_5_5: dict[type, set[str]] = {
    SourceState: {"enabled", "paused", "disabled", "blocked_anonymous"},
    RouteState: {"healthy", "degraded", "circuit_open", "unsupported"},
    EntityLifecycle: {"active", "inactive", "deleted", "unknown"},
    ContentAccess: {
        "full",
        "partial",
        "metadata_only",
        "blocked",
        "challenge",
        "premium",
        "gone",
        "unknown",
    },
    FetchOutcome: {"success", "retryable", "permanent_failure"},
}


def test_state_axes_value_sets_equal_spec_verbatim() -> None:
    assert set(STATE_AXES) == set(SPEC_5_5)
    for enum_type, expected in SPEC_5_5.items():
        assert {m.value for m in enum_type} == expected, enum_type.__name__
        # значення не перетинаються між осями окрім «unknown» (lifecycle і access)
    all_values = [m.value for e in STATE_AXES for m in e]
    duplicates = {v for v in all_values if all_values.count(v) > 1}
    assert duplicates == {"unknown"}


@pytest.mark.parametrize(
    ("enum_type", "synonym"),
    [
        (ContentAccess, "free"),
        (ContentAccess, "body_unavailable"),
        (ContentAccess, "FULL"),
        (FetchOutcome, "body_unavailable"),
        (FetchOutcome, "free"),
        (EntityLifecycle, "archived"),
        (SourceState, "enabled "),
    ],
)
def test_models_reject_research_synonyms_but_mapping_accepts(enum_type: type, synonym: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(enum_type).validate_python(synonym)
    with pytest.raises(ValueError):
        enum_type(synonym)
    if synonym.strip().lower() in {"free", "body_unavailable"}:
        mapped = map_research_access_state(synonym)
        assert isinstance(mapped, ContentAccess)


def test_mapping_does_not_accept_fetch_outcome_labels_except_retryable() -> None:
    # `success`/`permanent_failure` — не research-позначки доступу; лише `retryable` мапиться
    assert map_research_access_state("retryable") is FetchOutcome.RETRYABLE
    for label in ("success", "permanent_failure", "", "  "):
        with pytest.raises(ValueError, match="невідома"):
            map_research_access_state(label)


# --- 3. temporal ------------------------------------------------------------------------------


def test_non_utc_aware_datetime_is_rejected_not_normalized() -> None:
    """Картка: «обрати відхилення» — +03:00 не конвертується в UTC мовчки."""
    local = datetime(2026, 9, 1, 15, 0, tzinfo=KYIV)  # == 12:00Z
    with pytest.raises(ValidationError, match="offset"):
        SystemTime(observed_at=local, fetched_at=at(), ingested_at=at())
    with pytest.raises(ValidationError, match="offset"):
        SourceTime(source_event_at=local)
    with pytest.raises(ValidationError, match="offset"):
        BitemporalInterval(valid_from=local, known_from=at())
    with pytest.raises(ValidationError, match="offset"):
        SystemTime.model_validate_json(
            '{"observed_at":"2026-09-01T15:00:00+03:00",'
            '"fetched_at":"2026-09-01T12:00:00Z","ingested_at":"2026-09-01T12:00:00Z"}'
        )
    # aware з нульовим offset у будь-якому написанні — приймається без зміни значення
    ok = SystemTime(observed_at="2026-09-01T12:00:00+00:00", fetched_at=at(), ingested_at=at())  # type: ignore[arg-type]
    assert ok.observed_at == datetime(2026, 9, 1, 12, tzinfo=UTC)


def test_derive_effective_time_with_only_observed_at() -> None:
    system = SystemTime(observed_at=at(0), fetched_at=at(1), ingested_at=at(2))
    derived = derive_effective_time(SourceTime(), system)
    assert derived.effective_at == system.observed_at
    assert derived.effective_at_basis.value == "observed"
    assert derived.source_time_inferred is True
    # fetched_at та ingested_at ніколи не є basis
    assert derived.effective_at not in (system.fetched_at, system.ingested_at)


def test_entity_time_rejects_source_updated_equal_to_fetched_at() -> None:
    with pytest.raises(ValidationError, match="source_updated_at"):
        EntityTime(
            source_updated_at=at(1),
            observed_at=at(0),
            fetched_at=at(1),
            ingested_at=at(2),
        )


def versions(*rows: tuple[int, int, int]) -> list[VersionTimes]:
    return [
        VersionTimes(projection_version=v, effective_at=at(e), ingested_at=at(i))
        for v, e, i in rows
    ]


def test_build_intervals_single_version_both_upper_bounds_open() -> None:
    [only] = build_intervals(versions((1, 0, 5)))
    assert only.interval.valid_to is None
    assert only.interval.known_to is None
    assert only.interval.valid_from == at(0)
    assert only.interval.known_from == at(5)
    assert build_intervals([]) == []


def test_build_intervals_duplicate_effective_at_shares_valid_axis_but_not_known() -> None:
    result = {
        r.projection_version: r.interval for r in build_intervals(versions((1, 0, 0), (2, 0, 5)))
    }
    assert (result[1].valid_from, result[1].valid_to) == (at(0), None)
    assert (result[2].valid_from, result[2].valid_to) == (at(0), None)
    assert (result[1].known_from, result[1].known_to) == (at(0), at(5))
    assert (result[2].known_from, result[2].known_to) == (at(5), None)
    # обидві версії чинні за valid-віссю; лише known-вісь розрізняє їх
    assert result[1].contains(as_of_valid_time=at(1), as_known_at=at(1))
    assert not result[1].contains(as_of_valid_time=at(1), as_known_at=at(5))
    assert result[2].contains(as_of_valid_time=at(1), as_known_at=at(5))


def test_build_intervals_unordered_input_equals_sorted_and_output_sorted_by_version() -> None:
    rows = versions((3, 20, 20), (1, 0, 0), (2, 10, 10))
    result = build_intervals(rows)
    assert [r.projection_version for r in result] == [1, 2, 3]
    assert result == build_intervals(sorted(rows, key=lambda v: v.projection_version))
    assert [r.interval.valid_to for r in result] == [at(10), at(20), None]
    assert [r.interval.known_to for r in result] == [at(10), at(20), None]


def test_build_intervals_duplicate_ingested_at_shares_known_axis() -> None:
    # два записи, які система дізналась одночасно, але з різним effective_at
    result = {
        r.projection_version: r.interval for r in build_intervals(versions((1, 0, 0), (2, 5, 0)))
    }
    assert (result[1].valid_from, result[1].valid_to) == (at(0), at(5))
    assert (result[1].known_from, result[1].known_to) == (at(0), None)
    assert (result[2].known_from, result[2].known_to) == (at(0), None)


# --- 6. encode_event / canonical --------------------------------------------------------------


def event(**overrides: object) -> DomainChangedEvent:
    data: dict[str, object] = {
        "event_id": EVENT_ID,
        "aggregate_id": ENTITY_A,
        "aggregate_version": 3,
        "event_type": "catalog.item.changed",
        "payload_schema_version": "1.0",
        "occurred_at": at(),
        "projection_task_id": TASK_ID,
        "previous_state_hash": None,
        "result_state_hash": "v1:" + "e" * 64,
        "payload": {"a": 1},
    }
    data.update(overrides)
    return DomainChangedEvent.model_validate(data)


def test_encode_event_nested_reorder_gives_same_bytes_and_sha() -> None:
    first = event(payload={"a": 1, "b": {"x": [1, {"k": 1, "j": 2}], "y": "ї"}})
    second = event(payload={"b": {"y": "ї", "x": [1, {"j": 2, "k": 1}]}, "a": 1})
    assert first == second
    e1, e2 = encode_event(first), encode_event(second)
    assert e1.event_bytes == e2.event_bytes
    assert e1.event_sha256 == e2.event_sha256 == hashlib.sha256(e1.event_bytes).hexdigest()


def test_encode_event_list_order_is_significant() -> None:
    assert (
        encode_event(event(payload={"l": [1, 2]})).event_bytes
        != encode_event(event(payload={"l": [2, 1]})).event_bytes
    )


def test_encode_event_datetime_with_and_without_microseconds() -> None:
    whole = encode_event(event(occurred_at=at()))
    micro = encode_event(event(occurred_at=at() + timedelta(microseconds=1)))
    assert b'"occurred_at":"2026-09-01T12:00:00.000000Z"' in whole.event_bytes
    assert b'"occurred_at":"2026-09-01T12:00:00.000001Z"' in micro.event_bytes
    assert whole.event_bytes != micro.event_bytes
    # round-trip зберігає точність до мікросекунди і bytes
    assert encode_event(decode_event(micro.event_bytes)).event_bytes == micro.event_bytes
    # CR-01 (код-рев'ю): payload — strict JSON, datetime у ньому відхиляється на конструюванні;
    # datetime передається як уже канонічний рядок і після round-trip byte-equivalent
    with pytest.raises(ValidationError):
        event(payload={"d": at()})
    with_dt = encode_event(event(payload={"d": "2026-09-01T12:00:00.000000Z"}))
    assert b'"d":"2026-09-01T12:00:00.000000Z"' in with_dt.event_bytes
    assert encode_event(decode_event(with_dt.event_bytes)).event_bytes == with_dt.event_bytes


def test_encode_event_decimal_vs_float_are_distinct_and_round_trip_stable() -> None:
    """Контракт (після CR-01): у payload Decimal заборонений — лише рядок `"1.5"` або float `1.5`;
    у `canonical_json_bytes` Decimal → JSON string, float → JSON number."""
    with pytest.raises(ValidationError):
        event(payload={"p": Decimal("1.50")})
    assert canonical_json_bytes({"p": Decimal("1.50")}) == b'{"p":"1.5"}'
    as_decimal = encode_event(event(payload={"p": "1.5"}))
    as_float = encode_event(event(payload={"p": 1.5}))
    assert b'"p":"1.5"' in as_decimal.event_bytes
    assert b'"p":1.5' in as_float.event_bytes
    assert as_decimal.event_bytes != as_float.event_bytes
    assert canonical_json_bytes({"p": 1.0}) != canonical_json_bytes({"p": 1})
    for encoded in (as_decimal, as_float):
        assert encode_event(decode_event(encoded.event_bytes)).event_bytes == encoded.event_bytes


def test_encode_event_unicode_nfd_is_normalized_to_nfc() -> None:
    """Контракт зафіксовано: рядки нормалізуються до NFC → NFD-подія дає ті самі bytes."""
    nfd = unicodedata.normalize("NFD", "Київ їжак")
    nfc = unicodedata.normalize("NFC", "Київ їжак")
    assert nfd != nfc
    encoded_nfd = encode_event(event(payload={"t": nfd, nfd: 1}))
    encoded_nfc = encode_event(event(payload={"t": nfc, nfc: 1}))
    assert encoded_nfd.event_bytes == encoded_nfc.event_bytes
    assert nfc.encode("utf-8") in encoded_nfd.event_bytes
    # наслідок: модель після round-trip дорівнює NFC-варіанту, а не NFD-оригіналу
    assert decode_event(encoded_nfd.event_bytes) == event(payload={"t": nfc, nfc: 1})
    assert decode_event(encoded_nfd.event_bytes) != event(payload={"t": nfd, nfd: 1})


def test_encode_event_exactly_256_kib_ok_and_plus_one_rejected() -> None:
    baseline = len(encode_event(event(payload={"blob": ""})).event_bytes)
    pad = EVENT_INLINE_LIMIT_BYTES - baseline
    exact = encode_event(event(payload={"blob": "x" * pad}))
    assert len(exact.event_bytes) == EVENT_INLINE_LIMIT_BYTES == 262_144
    with pytest.raises(EventTooLargeError) as info:
        encode_event(event(payload={"blob": "x" * (pad + 1)}))
    assert info.value.size == EVENT_INLINE_LIMIT_BYTES + 1
    # multi-byte: ліміт рахується у bytes, а не в символах
    with pytest.raises(EventTooLargeError):
        encode_event(event(payload={"blob": "ї" * pad}))


# --- 7. state_hash ----------------------------------------------------------------------------


def test_state_hash_nested_dict_reorder_same_but_list_order_significant() -> None:
    base = compute_state_hash_v1({"a": {"x": 1, "y": {"p": [1, 2], "q": 2}}}, {}, {})
    reordered = compute_state_hash_v1({"a": {"y": {"q": 2, "p": [1, 2]}, "x": 1}}, {}, {})
    assert base == reordered
    # список — упорядкована послідовність: порядок значущий (контракт зафіксовано)
    assert compute_state_hash_v1({"a": [1, 2]}, {}, {}) != compute_state_hash_v1(
        {"a": [2, 1]}, {}, {}
    )


def test_state_hash_unicode_nfd_equals_nfc_for_values_and_keys() -> None:
    """Mutation M6 показала, що «Дриль» у test_current не має NFD-форми; тут — «Київ»/«ї»."""
    nfd_key, nfd_val = (unicodedata.normalize("NFD", s) for s in ("назва", "Київ ї"))
    nfc_key, nfc_val = (unicodedata.normalize("NFC", s) for s in ("назва", "Київ ї"))
    assert nfd_val != nfc_val
    assert compute_state_hash_v1({nfd_key: nfd_val}, {"a": nfd_val}, {}) == compute_state_hash_v1(
        {nfc_key: nfc_val}, {"a": nfc_val}, {}
    )


def test_state_hash_none_is_not_the_same_as_missing_key() -> None:
    """Контракт зафіксовано: `{"a": None}` ≠ `{}` (nullable body ≠ відсутнє поле, R-20)."""
    assert compute_state_hash_v1({"a": None}, {}, {}) != compute_state_hash_v1({}, {}, {})
    assert compute_state_hash_v1({}, {"a": None}, {}) != compute_state_hash_v1({}, {}, {})


def test_state_hash_golden_for_empty_blocks() -> None:
    expected_payload = b'{"attributes":{},"core":{},"latest_state":{},"v":1}'
    assert canonical_json_bytes({"v": 1, "core": {}, "attributes": {}, "latest_state": {}}) == (
        expected_payload
    )
    assert compute_state_hash_v1({}, {}, {}) == "v1:" + hashlib.sha256(expected_payload).hexdigest()


# --- 8. project_groups ------------------------------------------------------------------------


def decision(
    number: int,
    action: ResolutionAction,
    members: list[UUID],
    *,
    group: UUID | None = None,
    supersedes: UUID | None = None,
    effective: int | None = None,
    recorded: int | None = None,
) -> ResolutionDecision:
    return ResolutionDecision(
        decision_id=UUID(int=number),
        decision_version=1,
        action=action,
        member_entity_ids=members,
        canonical_group_id=group,
        score=Decimal("0.9") if action is ResolutionAction.MERGE else None,
        rule_or_model_version="matcher/1.0",
        actor="system:matcher",
        reason="test",
        effective_at=at(number if effective is None else effective),
        recorded_at=at(number if recorded is None else recorded),
        supersedes_decision_id=supersedes,
    )


MERGE, UNMERGE, BLOCK = (
    ResolutionAction.MERGE,
    ResolutionAction.UNMERGE,
    ResolutionAction.MANUAL_BLOCK,
)


def test_unmerge_without_prior_merge_is_noop_but_applied() -> None:
    snapshot = project_groups([decision(1, UNMERGE, [ENTITY_A])])
    assert snapshot.groups == {}
    assert snapshot.applied_decision_ids == [UUID(int=1)]
    assert snapshot.skipped_decision_ids == []


def test_two_manual_blocks_on_same_pair_are_idempotent() -> None:
    snapshot = project_groups(
        [
            decision(1, BLOCK, [ENTITY_A, ENTITY_B]),
            decision(2, BLOCK, [ENTITY_B, ENTITY_A]),
            decision(3, MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1),
        ]
    )
    assert snapshot.blocked_pairs == [(ENTITY_A, ENTITY_B)]
    assert snapshot.groups == {}
    assert snapshot.skipped_decision_ids == [UUID(int=3)]
    assert snapshot.applied_decision_ids == [UUID(int=1), UUID(int=2)]


def test_supersedes_unknown_decision_is_tolerated_and_reported() -> None:
    """Контракт зафіксовано: dangling `supersedes_decision_id` не ламає replay, але
    з'являється у `superseded_decision_ids` — споживач може виявити невідповідність."""
    phantom = UUID(int=999)
    snapshot = project_groups(
        [decision(1, MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1, supersedes=phantom)]
    )
    assert snapshot.groups == {GROUP_1: [ENTITY_A, ENTITY_B]}
    assert snapshot.superseded_decision_ids == [phantom]
    assert snapshot.applied_decision_ids == [UUID(int=1)]


def test_replay_order_is_effective_at_not_list_order_nor_recorded_at() -> None:
    merge_late = decision(1, MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1, effective=5, recorded=1)
    unmerge_early = decision(2, UNMERGE, [ENTITY_A], effective=1, recorded=5)
    # у списку unmerge перший, recorded_at у unmerge пізніший — але effective_at раніший
    for order in ([unmerge_early, merge_late], [merge_late, unmerge_early]):
        snapshot = project_groups(order)
        assert snapshot.groups == {GROUP_1: [ENTITY_A, ENTITY_B]}
        assert snapshot.applied_decision_ids == [UUID(int=2), UUID(int=1)]
    # однаковий effective_at → tie-break за recorded_at
    merge_first = decision(1, MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1, effective=1, recorded=1)
    unmerge_second = decision(2, UNMERGE, [ENTITY_A], effective=1, recorded=2)
    assert project_groups([unmerge_second, merge_first]).groups == {}


def test_merge_unmerge_merge_replay_ends_in_new_group() -> None:
    history = [
        decision(1, MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1),
        decision(2, UNMERGE, [ENTITY_A]),
        decision(3, MERGE, [ENTITY_A, ENTITY_B], group=GROUP_2),
    ]
    snapshot = project_groups(history)
    assert snapshot.groups == {GROUP_2: [ENTITY_A, ENTITY_B]}
    assert project_groups(list(reversed(history))) == snapshot


def test_merge_across_partially_blocked_group_is_skipped_whole() -> None:
    snapshot = project_groups(
        [
            decision(1, MERGE, [ENTITY_A, ENTITY_B], group=GROUP_1),
            decision(2, BLOCK, [ENTITY_B, ENTITY_C]),
            decision(3, MERGE, [ENTITY_A, ENTITY_C], group=GROUP_1),  # тягне C до B → блок
            decision(4, MERGE, [ENTITY_C, ENTITY_D], group=GROUP_2),
        ]
    )
    assert snapshot.groups == {GROUP_1: [ENTITY_A, ENTITY_B], GROUP_2: [ENTITY_C, ENTITY_D]}
    assert snapshot.skipped_decision_ids == [UUID(int=3)]


# --- 1. SourceIdentity ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_id",
    ["catalog_ua_nope", "Catalog_ua_rozetka", "CATALOG_UA_ROZETKA", " catalog_ua_rozetka", ""],
)
def test_source_identity_rejects_unknown_case_and_padding(source_id: str) -> None:
    with pytest.raises(ValidationError):
        SourceIdentity(source_id=source_id, source_item_id="1")


@pytest.mark.parametrize("item", ["", "   ", "\t\n"])
def test_source_identity_rejects_blank_item_id(item: str) -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        SourceIdentity(source_id="catalog_ua_rozetka", source_item_id=item)


def test_source_identity_item_id_is_stripped_and_case_preserved() -> None:
    identity = SourceIdentity(source_id="catalog_ua_rozetka", source_item_id="  AbC-42 ")
    assert identity.source_item_id == "AbC-42"


# --- 4. ContactValue / Money ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("067 123 45 67", "+380671234567"),  # без коду країни → default UA
        ("(044) 123-45-67", "+380441234567"),
        ("380671234567", "+380671234567"),  # без «+»
        ("+48 601 234 567", "+48601234567"),  # інша країна з явним кодом
        ("12345", None),  # закороткий
        ("не телефон", None),
        ("+380 00 000 00 00", None),  # невалідний для UA
    ],
)
def test_phone_normalization_default_ua_and_invalid_keeps_raw(
    raw: str, expected: str | None
) -> None:
    value = ContactValue.from_raw(ContactKind.PHONE, raw)
    assert value.raw == raw
    assert value.normalized == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("John.Doe@Example.COM", "john.doe@example.com"),
        ("Іван@Пошта.УКР", "іван@xn--80a1acn3a.xn--j1amh"),
        ("user@пошта.укр", "user@xn--80a1acn3a.xn--j1amh"),
        ("a b@x.com", None),
        ("nope", None),
        ("@x.com", None),
        ("a@", None),
    ],
)
def test_email_normalization_idna_lowercase_and_invalid(raw: str, expected: str | None) -> None:
    value = ContactValue.from_raw(ContactKind.EMAIL, raw)
    assert value.raw == raw
    assert value.normalized == expected


def test_contact_value_direct_construction_validates_normalized_shape() -> None:
    with pytest.raises(ValidationError, match="E.164"):
        ContactValue(kind=ContactKind.PHONE, raw="x", normalized="0671234567")
    with pytest.raises(ValidationError, match="E.164"):
        ContactValue(kind=ContactKind.PHONE, raw="x", normalized="+0671234567")
    with pytest.raises(ValidationError, match="lowercase"):
        ContactValue(kind=ContactKind.EMAIL, raw="x", normalized="A@b.c")
    with pytest.raises(ValidationError):
        ContactValue(kind=ContactKind.EMAIL, raw="", normalized=None)


@pytest.mark.parametrize("amount", [1.0, 1.5, 100.0])
def test_money_rejects_float_python_and_json(amount: float) -> None:
    with pytest.raises(ValidationError, match="float"):
        Money(amount_minor=amount, currency="UAH")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="float"):
        Money.model_validate_json(f'{{"amount_minor": {amount}, "currency": "UAH"}}')


def test_money_negative_allowed_currency_lowercase_rejected() -> None:
    assert Money(amount_minor=-100, currency="UAH").amount_minor == -100  # знижка/повернення
    with pytest.raises(ValidationError, match="pattern"):
        Money(amount_minor=100, currency="uah")
    with pytest.raises(ValidationError):
        Money(amount_minor=100, currency="UAHH")


# --- 5. can_commit ----------------------------------------------------------------------------


def claim(**overrides: object) -> UploadClaim:
    base: dict[str, object] = {
        "object_key": "raw/aa/" + "a" * 64,
        "owner": "fetcher-1",
        "status": UploadClaimStatus.LEASED,
        "lease_expires_at": at(10),
        "claim_generation": 5,
    }
    base.update(overrides)
    return UploadClaim.model_validate(base)


def test_can_commit_expired_lease_boundary_stale_and_equal_generation() -> None:
    assert can_commit(claim(), at(0), 5) is True
    assert can_commit(claim(), at(9, seconds=59), 5) is True
    assert can_commit(claim(), at(10), 5) is False  # lease_expires_at == now → прострочено
    assert can_commit(claim(), at(11), 5) is False
    assert can_commit(claim(), at(0), 4) is False  # stale generation
    assert can_commit(claim(), at(0), 6) is False  # «майбутня» generation — теж не та
    for status in (
        UploadClaimStatus.COMMITTED,
        UploadClaimStatus.RELEASED,
        UploadClaimStatus.EXPIRED,
    ):
        assert can_commit(claim(status=status), at(0), 5) is False
