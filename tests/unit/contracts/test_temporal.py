"""§9.6 / R-43 / R-49 (рівень 9 Temporal): UTC-only, fallback basis, bitemporal інтервали."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from factories import at
from pydantic import ValidationError

from collector.contracts.enums import EffectiveAtBasis, TimePrecision
from collector.contracts.temporal import (
    BitemporalInterval,
    EffectiveTime,
    EntityTime,
    SourceTime,
    SystemTime,
    VersionTimes,
    build_intervals,
    derive_effective_time,
)


def system(observed: int = 0, fetched: int = 0, ingested: int = 1) -> SystemTime:
    return SystemTime(observed_at=at(observed), fetched_at=at(fetched), ingested_at=at(ingested))


# --- UTC-only ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        datetime(2026, 9, 1, 12, 0),
        datetime(2026, 9, 1, 12, 0, tzinfo=timezone(timedelta(hours=3))),
        "2026-09-01T12:00:00",
        "2026-09-01T12:00:00+03:00",
    ],
)
def test_system_time_rejects_naive_and_non_utc(bad: datetime | str) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        SystemTime(observed_at=bad, fetched_at=at(), ingested_at=at())  # type: ignore[arg-type]


def test_source_time_rejects_naive_but_allows_null() -> None:
    assert SourceTime().source_event_at is None
    with pytest.raises(ValidationError, match="naive"):
        SourceTime(source_event_at=datetime(2026, 9, 1))
    ok = SourceTime(source_event_at="2026-09-01T10:00:00Z")  # type: ignore[arg-type]
    assert ok.source_event_at == datetime(2026, 9, 1, 10, tzinfo=UTC)


def test_system_time_ingested_not_before_fetched() -> None:
    with pytest.raises(ValidationError, match="ingested_at"):
        system(fetched=5, ingested=4)


# --- R-43: fetched_at ніколи не стає source_event_at ------------------------------------------


def test_entity_time_rejects_source_event_equal_to_fetched_at() -> None:
    with pytest.raises(ValidationError, match="fetched_at"):
        EntityTime(source_event_at=at(0), observed_at=at(0), fetched_at=at(0), ingested_at=at(1))
    with pytest.raises(ValidationError, match="fetched_at"):
        EntityTime(source_updated_at=at(0), observed_at=at(0), fetched_at=at(0), ingested_at=at(1))
    ok = EntityTime(source_event_at=at(-60), observed_at=at(0), fetched_at=at(0), ingested_at=at(1))
    assert ok.source_time_inferred is False


def test_derive_effective_time_never_uses_fetched_at() -> None:
    src = SourceTime()
    sys_time = system(observed=-5, fetched=0, ingested=1)
    derived = derive_effective_time(src, sys_time)
    assert derived.effective_at == sys_time.observed_at
    assert derived.effective_at != sys_time.fetched_at
    assert derived.effective_at_basis is EffectiveAtBasis.OBSERVED
    assert derived.source_time_inferred is True


def test_derive_effective_time_prefers_source_event_then_updated() -> None:
    sys_time = system()
    event = derive_effective_time(
        SourceTime(source_event_at=at(-100), source_updated_at=at(-50)), sys_time
    )
    assert (event.effective_at, event.effective_at_basis, event.source_time_inferred) == (
        at(-100),
        EffectiveAtBasis.SOURCE_EVENT,
        False,
    )
    updated = derive_effective_time(SourceTime(source_updated_at=at(-50)), sys_time)
    assert (updated.effective_at_basis, updated.source_time_inferred) == (
        EffectiveAtBasis.SOURCE_UPDATED,
        True,
    )
    flagged = derive_effective_time(
        SourceTime(source_event_at=at(-100), source_time_inferred=True), sys_time
    )
    assert flagged.source_time_inferred is True


def test_effective_time_requires_inferred_flag_when_basis_not_source_event() -> None:
    with pytest.raises(ValidationError, match="source_time_inferred"):
        EffectiveTime(
            effective_at=at(),
            effective_at_basis=EffectiveAtBasis.OBSERVED,
            source_time_inferred=False,
        )


def test_entity_time_combine_preserves_precision_and_tz() -> None:
    src = SourceTime(
        source_event_at=at(-30),
        source_timezone_raw="+03:00",
        source_time_precision=TimePrecision.DAY,
        source_time_raw_text="1 вересня",
    )
    combined = EntityTime.combine(src, system())
    assert combined.source_time_precision is TimePrecision.DAY
    assert combined.source_timezone_raw == "+03:00"


# --- bitemporal інтервали ---------------------------------------------------------------------


def versions(*rows: tuple[int, int, int]) -> list[VersionTimes]:
    return [
        VersionTimes(projection_version=v, effective_at=at(e), ingested_at=at(i))
        for v, e, i in rows
    ]


def by_version(rows: list[VersionTimes]) -> dict[int, BitemporalInterval]:
    return {item.projection_version: item.interval for item in build_intervals(rows)}


def test_intervals_simple_sequence() -> None:
    result = by_version(versions((1, 0, 10), (2, 20, 30)))
    assert result[1] == BitemporalInterval(
        valid_from=at(0), valid_to=at(20), known_from=at(10), known_to=at(30)
    )
    assert result[2] == BitemporalInterval(valid_from=at(20), valid_to=None, known_from=at(30))


def test_intervals_late_arrival_keeps_system_axis_of_earlier_versions() -> None:
    # v3 надходить пізно (ingested 50), але стосується моменту між v1 і v2 (effective 10).
    result = by_version(versions((1, 0, 10), (2, 20, 30), (3, 10, 50)))
    assert (result[1].valid_from, result[1].valid_to) == (at(0), at(10))
    assert (result[3].valid_from, result[3].valid_to) == (at(10), at(20))
    assert (result[2].valid_from, result[2].valid_to) == (at(20), None)
    # known-вісь не переписана: v1 стала відомою о 10 і була замінена знанням о 30, не о 50.
    assert (result[1].known_from, result[1].known_to) == (at(10), at(30))
    assert (result[2].known_from, result[2].known_to) == (at(30), at(50))
    assert (result[3].known_from, result[3].known_to) == (at(50), None)


def test_intervals_backdated_correction_shares_valid_axis() -> None:
    # v2 — виправлення з тим самим effective_at (backdated price / news correction).
    result = by_version(versions((1, 0, 10), (2, 0, 40), (3, 60, 70)))
    assert (result[1].valid_from, result[1].valid_to) == (at(0), at(60))
    assert (result[2].valid_from, result[2].valid_to) == (at(0), at(60))
    assert (result[1].known_from, result[1].known_to) == (at(10), at(40))
    assert (result[2].known_from, result[2].known_to) == (at(40), at(70))
    as_of = at(30)
    assert result[1].contains(as_of_valid_time=as_of, as_known_at=at(20))
    assert not result[1].contains(as_of_valid_time=as_of, as_known_at=at(45))
    assert result[2].contains(as_of_valid_time=as_of, as_known_at=at(45))


def test_intervals_relisting_keeps_both_axes_separate() -> None:
    # Оголошення: active (v1) → inactive (v2) → relisted active (v3) з source time пізніше.
    result = by_version(versions((1, 0, 5), (2, 100, 105), (3, 200, 400)))
    assert [(result[v].valid_from, result[v].valid_to) for v in (1, 2, 3)] == [
        (at(0), at(100)),
        (at(100), at(200)),
        (at(200), None),
    ]
    assert [(result[v].known_from, result[v].known_to) for v in (1, 2, 3)] == [
        (at(5), at(105)),
        (at(105), at(400)),
        (at(400), None),
    ]


def test_intervals_input_order_irrelevant_and_versions_unique() -> None:
    rows = versions((1, 0, 10), (2, 20, 30), (3, 10, 50))
    assert build_intervals(rows) == build_intervals(list(reversed(rows)))
    with pytest.raises(ValueError, match="унікальними"):
        build_intervals(versions((1, 0, 10), (1, 5, 15)))


def test_bitemporal_interval_validation() -> None:
    with pytest.raises(ValidationError, match="valid_to"):
        BitemporalInterval(valid_from=at(10), valid_to=at(5), known_from=at(0))
    with pytest.raises(ValidationError, match="known_to"):
        BitemporalInterval(valid_from=at(0), known_from=at(10), known_to=at(10))
