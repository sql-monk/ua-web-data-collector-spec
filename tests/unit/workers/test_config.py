"""Unit-тести env-конфігурації worker/scheduler runtime (WP-01D PR1; §7.5, §7.6).

Без БД і без мережі: перевіряється лише розбір env і інваріанти, які мають впасти до
будь-якого підключення (інакше контейнер стартує з конфігурацією, що мовчки втрачає leases).
"""

from __future__ import annotations

import pytest

from collector.workers.config import (
    SchedulerRuntimeConfig,
    WorkerConfigError,
    WorkerRuntimeConfig,
    instance_version,
    placeholder_requested,
)
from collector.workers.roles import WorkerRole


def test_defaults_match_documented_values() -> None:
    config = WorkerRuntimeConfig.from_env(WorkerRole.FETCH, {})
    assert config.role is WorkerRole.FETCH
    assert (config.lease_seconds, config.heartbeat_seconds) == (60, 20.0)
    assert (config.poll_seconds, config.stop_grace_seconds) == (1.0, 90.0)
    assert config.claim_batch == 8
    assert config.deployment == "compose"
    assert config.container_id is None
    # Docker hostname — лише metadata (§7.5), але він має бути заповнений для діагностики.
    assert config.hostname


def test_env_overrides_every_documented_knob() -> None:
    config = WorkerRuntimeConfig.from_env(
        WorkerRole.PARSE,
        {
            "COLLECTOR_WORKER_LEASE_SECONDS": "30",
            "COLLECTOR_WORKER_HEARTBEAT_SECONDS": "5",
            "COLLECTOR_WORKER_POLL_SECONDS": "0.25",
            "COLLECTOR_WORKER_STOP_GRACE_SECONDS": "12",
            "COLLECTOR_WORKER_CLAIM_BATCH": "3",
            "COLLECTOR_WORKER_DEPLOYMENT": "swarm",
            "COLLECTOR_CONTAINER_ID": "abc123",
        },
    )
    assert (config.lease_seconds, config.heartbeat_seconds) == (30, 5.0)
    assert (config.poll_seconds, config.stop_grace_seconds) == (0.25, 12.0)
    assert (config.claim_batch, config.deployment) == (3, "swarm")
    assert config.container_id == "abc123"


def test_heartbeat_must_leave_room_for_one_missed_beat() -> None:
    """heartbeat > lease/2 означає, що один пропущений тік коштує lease (§7.6)."""
    with pytest.raises(WorkerConfigError, match="половини"):
        WorkerRuntimeConfig(role=WorkerRole.FETCH, lease_seconds=10, heartbeat_seconds=6)
    # Рівно половина — дозволено.
    assert (
        WorkerRuntimeConfig(
            role=WorkerRole.FETCH, lease_seconds=10, heartbeat_seconds=5
        ).lease_seconds
        == 10
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("COLLECTOR_WORKER_LEASE_SECONDS", "0"),
        ("COLLECTOR_WORKER_LEASE_SECONDS", "не число"),
        ("COLLECTOR_WORKER_POLL_SECONDS", "-1"),
        ("COLLECTOR_WORKER_CLAIM_BATCH", "0"),
        ("COLLECTOR_WORKER_HEARTBEAT_SECONDS", "0"),
    ],
)
def test_invalid_env_fails_loudly(name: str, value: str) -> None:
    with pytest.raises(WorkerConfigError, match=name):
        WorkerRuntimeConfig.from_env(WorkerRole.FETCH, {name: value})


def test_scheduler_defaults_and_overrides() -> None:
    assert SchedulerRuntimeConfig.from_env({}) == SchedulerRuntimeConfig()
    config = SchedulerRuntimeConfig.from_env(
        {
            "COLLECTOR_SCHEDULER_LEASE_NAME": "scheduler-eu",
            "COLLECTOR_SCHEDULER_TICK_SECONDS": "0.5",
            "COLLECTOR_SCHEDULER_LEASE_RETRY_SECONDS": "0.2",
            "COLLECTOR_SCHEDULER_STALE_AFTER_SECONDS": "15",
            "COLLECTOR_SCHEDULER_RECOVER_LIMIT": "10",
        }
    )
    assert config == SchedulerRuntimeConfig(
        lease_name="scheduler-eu",
        tick_seconds=0.5,
        lease_retry_seconds=0.2,
        stale_after_seconds=15.0,
        recover_limit=10,
    )


def test_empty_lease_name_is_rejected() -> None:
    with pytest.raises(WorkerConfigError, match="lease_name"):
        SchedulerRuntimeConfig(lease_name="")


def test_instance_version_fits_the_column() -> None:
    value = instance_version()
    assert "+" in value and len(value) <= 128


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("", False), ("no", False)],
)
def test_placeholder_rollback_flag(value: str, expected: bool) -> None:
    assert placeholder_requested({"COLLECTOR_WORKER_PLACEHOLDER": value}) is expected
