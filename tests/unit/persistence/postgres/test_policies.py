"""Логіка без БД: backoff/jitter, таблиці переходів scale command/instance, clock."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from collector.persistence.postgres.clock import resolve_now
from collector.persistence.postgres.errors import InvalidValueError
from collector.persistence.postgres.models import (
    SCALE_COMMAND_STATUSES,
    SCALE_COMMAND_TERMINAL,
    SCALE_COMMAND_TRANSITIONS,
)
from collector.persistence.postgres.repositories.pools import (
    INSTANCE_TRANSITIONS,
    PoolDesiredState,
)
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


def test_backoff_never_exceeds_maximum_even_with_jitter() -> None:
    """L-6: cap застосовується після jitter, інакше фактична межа була б `maximum*(1+ratio)`."""
    policy = BackoffPolicy(
        base=timedelta(seconds=30),
        multiplier=2,
        maximum=timedelta(hours=6),
        jitter_ratio=0.2,
    )
    rng = random.Random(0)  # noqa: S311
    for attempt in range(1, 40):
        assert policy.delay_for(attempt, rng) <= policy.maximum, attempt


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


@pytest.mark.parametrize(
    "state",
    [
        PoolDesiredState(desired_replicas=1, desired_concurrency=0, max_replicas=4),
        PoolDesiredState(desired_replicas=5, desired_concurrency=1, max_replicas=4),
        PoolDesiredState(desired_replicas=1, desired_concurrency=1, max_replicas=4, min_replicas=2),
        PoolDesiredState(
            desired_replicas=1, desired_concurrency=1, max_replicas=4, min_replicas=-1
        ),
        PoolDesiredState(desired_replicas=1, desired_concurrency=1, max_replicas=4, mode="turbo"),
    ],
)
def test_pool_desired_state_validation_rejects_invalid_values(state: PoolDesiredState) -> None:
    """L-1: інваріанти перевіряються у репозиторії, а не лише CHECK-ами БД."""
    with pytest.raises(InvalidValueError):
        state.validate()


def test_pool_desired_state_validation_accepts_scale_to_zero() -> None:
    PoolDesiredState(
        desired_replicas=0, desired_concurrency=1, max_replicas=4, mode="autoscale"
    ).validate()
