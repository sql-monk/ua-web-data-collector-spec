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


def test_heartbeat_must_leave_room_for_a_missed_beat() -> None:
    """У lease TTL має вміщатись три періоди heartbeat (L-1 код-рев'ю).

    Двох мало: другий тік припадав би рівно на момент експірації, і будь-який RTT робив би
    пропущений beat фатальним для живого worker-а.
    """
    for heartbeat in (6, 5):
        with pytest.raises(WorkerConfigError, match="третини"):
            WorkerRuntimeConfig(
                role=WorkerRole.FETCH, lease_seconds=10, heartbeat_seconds=heartbeat
            )
    # Рівно третина — дозволено; default Compose (20/60) теж проходить.
    assert (
        WorkerRuntimeConfig(
            role=WorkerRole.FETCH, lease_seconds=60, heartbeat_seconds=20
        ).fence_after
        == 30.0
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


def test_connection_ceiling_defaults_to_the_role_concurrency() -> None:
    """M-3: стеля слотів — те, під що процес створює pool з'єднань."""
    fetch = WorkerRuntimeConfig.from_env(WorkerRole.FETCH, {})
    assert fetch.max_concurrency == 8  # §7.6: fetch 2 × 8
    assert fetch.max_slots == 8
    raised = WorkerRuntimeConfig.from_env(
        WorkerRole.FETCH, {"COLLECTOR_WORKER_MAX_CONCURRENCY": "24"}
    )
    assert raised.max_slots == 24
    # Вбудований запуск без власного engine: стелі немає.
    assert WorkerRuntimeConfig(role=WorkerRole.FETCH).max_slots > 1000
    with pytest.raises(WorkerConfigError, match="max_concurrency"):
        WorkerRuntimeConfig(role=WorkerRole.FETCH, max_concurrency=0)


def test_driver_and_watchdog_budgets_are_derived_from_the_fencing_window() -> None:
    """H-1: жоден запит не живе довше за вікно fencing, а сторож прокидається раніше за бюджет."""
    config = WorkerRuntimeConfig(role=WorkerRole.FETCH, lease_seconds=60, heartbeat_seconds=20)
    assert config.fence_after == 30.0
    assert config.command_timeout == 30.0
    assert config.statement_timeout_ms == 30_000
    assert config.watchdog_interval <= config.fence_after
    assert config.heartbeat_tick_budget > config.fence_after, "сторож має спрацювати першим"
