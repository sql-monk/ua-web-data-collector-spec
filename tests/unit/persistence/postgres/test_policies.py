"""Логіка без БД: backoff/jitter, таблиці переходів scale command/instance, clock."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.models import (
    SCALE_COMMAND_STATUSES,
    SCALE_COMMAND_TERMINAL,
    SCALE_COMMAND_TRANSITIONS,
)
from collector.persistence.postgres.repositories.pools import INSTANCE_TRANSITIONS
from collector.persistence.postgres.repositories.queue import BackoffPolicy


def test_backoff_is_exponential_capped_and_jittered_deterministically() -> None:
    policy = BackoffPolicy(
        base=timedelta(seconds=10), multiplier=2, maximum=timedelta(seconds=35), jitter_ratio=0.5
    )
    no_jitter = BackoffPolicy(
        base=timedelta(seconds=10), multiplier=2, maximum=timedelta(seconds=35), jitter_ratio=0
    )
    rng = random.Random(42)  # noqa: S311 — детермінований тест
    assert [no_jitter.delay_for(a, rng) for a in (1, 2, 3, 4)] == [
        timedelta(seconds=10),
        timedelta(seconds=20),
        timedelta(seconds=35),
        timedelta(seconds=35),
    ]
    first = policy.delay_for(1, random.Random(7))  # noqa: S311
    second = policy.delay_for(1, random.Random(7))  # noqa: S311
    assert first == second
    assert timedelta(seconds=10) <= first <= timedelta(seconds=15)


def test_scale_command_transition_table_matches_card() -> None:
    assert set(SCALE_COMMAND_TRANSITIONS) == set(SCALE_COMMAND_STATUSES)
    for terminal in SCALE_COMMAND_TERMINAL:
        assert SCALE_COMMAND_TRANSITIONS[terminal] == frozenset()
    assert "draining" in SCALE_COMMAND_TRANSITIONS["requested"]
    assert "applied" not in SCALE_COMMAND_TRANSITIONS["requested"]
    assert "applied" not in SCALE_COMMAND_TRANSITIONS["draining"]
    assert {"awaiting_manual_apply", "applying"} <= SCALE_COMMAND_TRANSITIONS["draining"]
    assert "applied" in SCALE_COMMAND_TRANSITIONS["awaiting_manual_apply"]
    assert "applied" in SCALE_COMMAND_TRANSITIONS["applying"]
    for state, targets in SCALE_COMMAND_TRANSITIONS.items():
        assert state not in targets
        if state not in SCALE_COMMAND_TERMINAL:
            assert {"failed", "superseded"} <= targets


def test_instance_transitions_have_no_resurrection_from_stopped() -> None:
    assert INSTANCE_TRANSITIONS["stopped"] == frozenset()
    assert "ready" in INSTANCE_TRANSITIONS["starting"]
    assert "draining" not in INSTANCE_TRANSITIONS["starting"]


def test_resolve_now_requires_aware_utc() -> None:
    aware = datetime(2026, 9, 22, 12, tzinfo=UTC)
    assert resolve_now(aware) == aware
    assert resolve_now(None).tzinfo is UTC
    with pytest.raises(ValueError, match="aware"):
        resolve_now(datetime(2026, 9, 22, 12))  # naive навмисно
