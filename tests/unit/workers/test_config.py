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


def test_fence_window_defaults_to_half_of_the_lease() -> None:
    """Self-fencing (F3): один пропущений heartbeat пробачається, два — вже ризик подвійної
    обробки, тому вікно за замовчуванням — половина lease TTL."""
    assert WorkerRuntimeConfig(role=WorkerRole.FETCH, lease_seconds=60).fence_after == 30.0
    explicit = WorkerRuntimeConfig(role=WorkerRole.FETCH, lease_seconds=60, fence_after_seconds=5.0)
    assert explicit.fence_after == 5.0
    assert (
        WorkerRuntimeConfig.from_env(
            WorkerRole.FETCH, {"COLLECTOR_WORKER_FENCE_AFTER_SECONDS": "7.5"}
        ).fence_after
        == 7.5
    )


def test_fence_window_longer_than_the_lease_is_rejected() -> None:
    """Вікно більше за lease TTL робить self-fencing марним: lease спливе раніше."""
    with pytest.raises(WorkerConfigError, match="fence_after_seconds"):
        WorkerRuntimeConfig(role=WorkerRole.FETCH, lease_seconds=60, fence_after_seconds=61)


def test_container_id_falls_back_to_docker_hostname() -> None:
    """F5: у контейнері `HOSTNAME` — короткий id контейнера, тому колонка
    `worker_instances.container_id` заповнюється без додаткової конфігурації."""
    from_hostname = WorkerRuntimeConfig.from_env(WorkerRole.FETCH, {"HOSTNAME": "830b526f8929"})
    assert from_hostname.container_id == "830b526f8929"
    explicit = WorkerRuntimeConfig.from_env(
        WorkerRole.FETCH, {"HOSTNAME": "830b526f8929", "COLLECTOR_CONTAINER_ID": "explicit-id"}
    )
    assert explicit.container_id == "explicit-id"
    assert WorkerRuntimeConfig.from_env(WorkerRole.FETCH, {}).container_id is None
