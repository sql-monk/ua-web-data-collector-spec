"""Межі desired state pool-а (§7.6) — інваріанти, які runtime читає на кожному heartbeat.

Окремо фіксується семантика «паузи»: `desired_concurrency = 0` контрактом **заборонений**
(`worker_pools` вимагає >= 1), тому pool ставиться на паузу або `desired_replicas = 0`
(scale-to-zero), або role-wide drain barrier — але не нульовою concurrency. Без цього тесту
GUI/контролер PR3 могли б «паузити» pool значенням, яке БД відкине лише на CHECK.
"""

from __future__ import annotations

import pytest

from collector.persistence.postgres.errors import InvalidValueError
from collector.persistence.postgres.repositories.pools import PoolDesiredState
from collector.workers.config import WorkerConfigError, WorkerRuntimeConfig
from collector.workers.roles import WorkerRole, default_pool_spec


def state(**overrides: int | str) -> PoolDesiredState:
    values: dict[str, int | str] = {
        "desired_replicas": 1,
        "desired_concurrency": 1,
        "min_replicas": 0,
        "max_replicas": 4,
    }
    values.update(overrides)
    return PoolDesiredState(**values)  # type: ignore[arg-type]


def test_zero_concurrency_is_not_a_pause_switch() -> None:
    with pytest.raises(InvalidValueError, match="desired_concurrency"):
        state(desired_concurrency=0).validate()


def test_scale_to_zero_replicas_is_allowed() -> None:
    """Пауза pool-а — це `desired_replicas = 0` у межах min/max, а не нульова concurrency."""
    state(desired_replicas=0, min_replicas=0).validate()


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"min_replicas": -1}, "min_replicas"),
        ({"min_replicas": 2, "max_replicas": 1}, "max_replicas"),
        ({"desired_replicas": 9}, "desired_replicas"),
        ({"min_replicas": 2, "desired_replicas": 1, "max_replicas": 4}, "desired_replicas"),
        ({"mode": "autoscale-always"}, "mode"),
    ],
)
def test_out_of_range_desired_state_is_rejected_before_any_write(
    overrides: dict[str, int | str], match: str
) -> None:
    with pytest.raises(InvalidValueError, match=match):
        state(**overrides).validate()


def test_browser_pool_default_is_zero_replicas_but_never_zero_concurrency() -> None:
    """§7.6: browser — `0 × 1`; нульовими мають бути replicas, не concurrency."""
    spec = default_pool_spec(WorkerRole.BROWSER)
    assert (spec.desired_replicas, spec.desired_concurrency) == (0, 1)
    state(
        desired_replicas=spec.desired_replicas,
        desired_concurrency=spec.desired_concurrency,
        min_replicas=spec.min_replicas,
        max_replicas=spec.max_replicas,
    ).validate()


def test_stop_grace_must_fit_into_the_compose_grace_period() -> None:
    """§7.5: `stop_grace_period` контейнера має бути довшим за drain-бюджет процесу.

    Compose дає worker-сервісам 120 с; конфіг за замовчуванням — 90 с, тобто лишається запас
    на SIGKILL. Тест фіксує саме це співвідношення, а не конкретне число.
    """
    compose_grace_seconds = 120.0
    config = WorkerRuntimeConfig(role=WorkerRole.FETCH)
    assert config.stop_grace_seconds < compose_grace_seconds


def test_heartbeat_faster_than_half_the_lease_survives_one_missed_beat() -> None:
    """Один пропущений heartbeat не повинен коштувати lease (інакше живий worker її втрачає)."""
    config = WorkerRuntimeConfig(role=WorkerRole.FETCH)
    assert config.heartbeat_seconds * 2 <= config.lease_seconds
    with pytest.raises(WorkerConfigError, match="heartbeat_seconds"):
        WorkerRuntimeConfig(role=WorkerRole.FETCH, lease_seconds=10, heartbeat_seconds=6.0)
