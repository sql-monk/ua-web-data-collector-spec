"""Default desired state ролей (§7.6), ключ advisory lease і заборона локального стану (§15).

Три незалежні інваріанти WP-01D PR1, які перевіряються без БД:

1. `DEFAULT_POOL_SPECS` дослівно відтворює таблицю §7.6 і проходить валідацію
   `PoolDesiredState` — інакше перший boot на чистій БД впаде на CHECK-константі;
2. ключ advisory lease детермінований і вміщується в `oid` (`pg_locks.objid`);
3. модулі `collector.workers` не пишуть на локальний диск (§15, FR-031: «worker не зберігає
   job/data на локальному filesystem»; `worker_instance_id` генерується на boot).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from collector.persistence.postgres.repositories.pools import PoolDesiredState
from collector.workers.advisory import ADVISORY_CLASSID, advisory_key
from collector.workers.roles import (
    DEFAULT_POOL_SPECS,
    WorkerRole,
    default_pool_spec,
    parse_worker_concurrency,
)

WORKERS_PACKAGE = Path(__file__).resolve().parents[3] / "src" / "collector" / "workers"

# §7.6, колонка «Default replicas × concurrency» (`parse` — 2 × CPU count).
SPEC_7_6_DEFAULTS: dict[WorkerRole, tuple[int, int]] = {
    WorkerRole.DISCOVERY: (1, 4),
    WorkerRole.FETCH: (2, 8),
    WorkerRole.BROWSER: (0, 1),
    WorkerRole.PARSE: (2, parse_worker_concurrency()),
    WorkerRole.PROJECTOR: (1, 8),
    WorkerRole.TRANSLATION: (1, 4),
    WorkerRole.EXPORT: (1, 2),
    WorkerRole.MAINTENANCE: (1, 1),
}

# Стан на локальному диску заборонений §15: worker має бути замінним без перенесення файлів.
FORBIDDEN_LOCAL_STATE = re.compile(
    r"\bopen\(|\btempfile\b|\bshelve\b|\bpickle\b|\bsqlite3\b|\bshutil\b"
    r"|\.write_text\(|\.write_bytes\(|os\.makedirs|\.mkdir\("
)


def test_every_role_has_defaults_from_spec_7_6() -> None:
    assert set(DEFAULT_POOL_SPECS) == set(WorkerRole)
    for role, (replicas, concurrency) in SPEC_7_6_DEFAULTS.items():
        spec = default_pool_spec(role)
        assert (spec.desired_replicas, spec.desired_concurrency) == (replicas, concurrency), role


@pytest.mark.parametrize("role", list(WorkerRole))
def test_defaults_pass_pool_invariants(role: WorkerRole) -> None:
    spec = default_pool_spec(role)
    PoolDesiredState(
        desired_replicas=spec.desired_replicas,
        desired_concurrency=spec.desired_concurrency,
        min_replicas=spec.min_replicas,
        max_replicas=spec.max_replicas,
        resource_profile=spec.resource_profile,
    ).validate()


def test_parse_concurrency_follows_cpu_count_with_a_floor() -> None:
    assert parse_worker_concurrency() >= 2


def test_advisory_key_is_deterministic_and_fits_oid() -> None:
    first = advisory_key("scheduler")
    assert first == advisory_key("scheduler")
    assert 0 <= first < 2**31
    assert advisory_key("scheduler") != advisory_key("controller")
    assert 0 < ADVISORY_CLASSID < 2**31


def test_workers_package_writes_nothing_to_local_disk() -> None:
    offenders: list[str] = []
    for path in sorted(WORKERS_PACKAGE.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            if FORBIDDEN_LOCAL_STATE.search(code):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "локальний стан у worker runtime (§15): " + "; ".join(offenders)
