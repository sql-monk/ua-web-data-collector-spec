"""§5.5 (R-18): п'ять закритих enum з точними значеннями та мапінг research-позначень."""

from __future__ import annotations

import pytest

from collector.contracts.enums import (
    STATE_AXES,
    ContentAccess,
    EntityKind,
    EntityLifecycle,
    FetchOutcome,
    ReleaseState,
    ResolutionAction,
    RouteState,
    SourceState,
    TimePrecision,
    map_research_access_state,
)

EXACT_VALUES: dict[type, tuple[str, ...]] = {
    SourceState: ("enabled", "paused", "disabled", "blocked_anonymous"),
    RouteState: ("healthy", "degraded", "circuit_open", "unsupported"),
    EntityLifecycle: ("active", "inactive", "deleted", "unknown"),
    ContentAccess: (
        "full",
        "partial",
        "metadata_only",
        "blocked",
        "challenge",
        "premium",
        "gone",
        "unknown",
    ),
    FetchOutcome: ("success", "retryable", "permanent_failure"),
}


@pytest.mark.parametrize(
    ("enum_type", "values"), EXACT_VALUES.items(), ids=lambda x: getattr(x, "__name__", "")
)
def test_state_axes_have_exact_values(enum_type: type, values: tuple[str, ...]) -> None:
    assert tuple(member.value for member in enum_type) == values
    assert all(isinstance(member, str) for member in enum_type)


def test_exactly_five_state_axes() -> None:
    assert STATE_AXES == (SourceState, RouteState, EntityLifecycle, ContentAccess, FetchOutcome)
    assert len({tuple(e) for e in STATE_AXES}) == 5


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("free", ContentAccess.FULL),
        ("body_unavailable", ContentAccess.METADATA_ONLY),
        ("retryable", FetchOutcome.RETRYABLE),
        ("blocked", ContentAccess.BLOCKED),
        ("challenge", ContentAccess.CHALLENGE),
        ("premium", ContentAccess.PREMIUM),
        ("gone", ContentAccess.GONE),
        ("full", ContentAccess.FULL),
        ("Metadata_Only ", ContentAccess.METADATA_ONLY),
    ],
)
def test_map_research_access_state(label: str, expected: ContentAccess | FetchOutcome) -> None:
    assert map_research_access_state(label) is expected


def test_map_research_access_state_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="невідома"):
        map_research_access_state("paywalled")


def test_other_enums_exact_values() -> None:
    assert tuple(TimePrecision) == ("second", "minute", "hour", "day", "month", "year", "unknown")
    assert tuple(EntityKind) == ("catalog_item", "catalog_offer", "vehicle_listing", "seller")
    assert tuple(ResolutionAction) == ("merge", "unmerge", "reject", "manual_link", "manual_block")
    assert tuple(ReleaseState) == (
        "draft",
        "building",
        "validating",
        "published",
        "failed",
        "superseded",
    )
