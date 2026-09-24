"""PR3a п.1: правило `not_before` для `retry`/`release` черг (без БД)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone

import pytest

from collector.persistence.postgres.repositories.queue import (
    BackoffPolicy,
    clamp_not_before,
    next_attempt_at,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def test_clamp_not_before_never_goes_to_the_past() -> None:
    assert clamp_not_before(None, NOW) == NOW
    assert clamp_not_before(NOW - timedelta(days=1), NOW) == NOW
    assert clamp_not_before(NOW + timedelta(hours=2), NOW) == NOW + timedelta(hours=2)


def test_clamp_not_before_rejects_naive_and_normalizes_aware_offsets() -> None:
    naive = datetime(2026, 9, 24, 12)  # noqa: DTZ001 — contract under test
    with pytest.raises(ValueError, match="not_before.*aware"):
        clamp_not_before(naive, NOW)
    with pytest.raises(ValueError, match="now.*aware"):
        clamp_not_before(NOW, naive)

    kyiv = timezone(timedelta(hours=3))
    assert clamp_not_before(datetime(2026, 9, 24, 15, tzinfo=kyiv), NOW) == NOW


def test_explicit_not_before_wins_over_policy_in_both_directions() -> None:
    policy = BackoffPolicy(base=timedelta(minutes=10), jitter_ratio=0.0)
    # Коротша за policy табличну затримку §10 не «роздуває» дефолтний backoff…
    short = next_attempt_at(NOW, 1, policy=policy, not_before=NOW + timedelta(seconds=5))
    assert short == NOW + timedelta(seconds=5)
    # …а довший `Retry-After` не скорочується.
    long = next_attempt_at(NOW, 1, policy=policy, not_before=NOW + timedelta(hours=2))
    assert long == NOW + timedelta(hours=2)


def test_without_not_before_policy_backoff_applies() -> None:
    policy = BackoffPolicy(base=timedelta(seconds=30), jitter_ratio=0.0)
    assert next_attempt_at(NOW, 3, policy=policy) == NOW + timedelta(minutes=2)
    rng = random.Random(7)  # noqa: S311 — детермінований jitter у тесті
    jittered = next_attempt_at(NOW, 1, policy=BackoffPolicy(jitter_ratio=0.5), rng=rng)
    assert NOW + timedelta(seconds=30) <= jittered <= NOW + timedelta(seconds=45)
