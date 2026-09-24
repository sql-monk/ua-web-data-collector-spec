"""Вартовий проти мовчазного skip integration-тестів Mongo (картка WP-01B, «Спільні вимоги»).

(1) CI job `integration-mongo` існує, вимагає Docker (`COLLECTOR_TEST_REQUIRE_DOCKER: "1"`), має
адресу Mongo і крок `pytest -m integration tests/integration/mongo` з `-rs`; (2) session-hook
у `tests/integration/mongo/conftest.py` перетворює skip/недобір тестів на провал.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
CI = REPO / ".github" / "workflows" / "ci.yml"
MONGO_CONFTEST = REPO / "tests" / "integration" / "mongo" / "conftest.py"
MONGO_DIGEST = "sha256:4968f22d0c6c10ef29952f3e807f62872ba22b3312f25803564fbfc08255efc2"


def _job() -> dict[str, Any]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    job: dict[str, Any] = workflow["jobs"]["integration-mongo"]
    return job


def _run_steps(job: dict[str, Any]) -> list[str]:
    return [str(step.get("run", "")) for step in job["steps"]]


def test_ci_job_requires_docker_and_mongo_address() -> None:
    job = _job()
    env = job["env"]
    assert env["COLLECTOR_TEST_REQUIRE_DOCKER"] == "1"
    assert env["COLLECTOR_TEST_MONGO_URI"].startswith("mongodb://127.0.0.1:")
    assert "directConnection=true" in env["COLLECTOR_TEST_MONGO_URI"]


def test_ci_job_runs_mongo_suite_with_skip_report() -> None:
    runs = [" ".join(r.split()) for r in _run_steps(_job())]
    suite = [r for r in runs if "pytest -m integration tests/integration/mongo" in r]
    assert suite, "немає кроку pytest -m integration tests/integration/mongo"
    assert all("-rs" in r.split() for r in suite)
    assert all("-k" not in r.split() and "--deselect" not in r for r in suite)


def test_ci_job_starts_pinned_replica_set_with_test_commands() -> None:
    joined = "\n".join(_run_steps(_job()))
    assert f"mongo:8.0@{MONGO_DIGEST}" in joined  # той самий digest, що в compose
    assert "--replSet rs0" in joined
    assert "enableTestCommands=1" in joined
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert MONGO_DIGEST in compose
    assert "enableTestCommands" not in compose  # лише в тестах


def _conftest() -> ModuleType:
    """Conftest як звичайний модуль (у sys.modules — інакше `@dataclass` не знайде модуль)."""
    name = "wp01b_mongo_conftest"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MONGO_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_conftest_fails_instead_of_skipping_when_docker_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _conftest()
    monkeypatch.setenv("COLLECTOR_TEST_REQUIRE_DOCKER", "1")
    with pytest.raises(pytest.fail.Exception):
        module._skip_or_fail("docker down")
    monkeypatch.delenv("COLLECTOR_TEST_REQUIRE_DOCKER")
    with pytest.raises(pytest.skip.Exception):
        module._skip_or_fail("docker down")


def test_session_guard_flags_skips_and_missing_tests() -> None:
    guard = _conftest().guard_failures
    minimum = _conftest().MIN_COLLECTED_TESTS
    assert guard(required=False, collected=0, skipped=["a"], minimum=minimum) == []
    assert guard(required=True, collected=minimum, skipped=[], minimum=minimum) == []
    skipped = guard(required=True, collected=minimum, skipped=["t::x"], minimum=minimum)
    assert skipped == ["skipped/xfail у tests/integration/mongo: t::x"]
    missing = guard(required=True, collected=minimum - 1, skipped=[], minimum=minimum)
    assert len(missing) == 1 and str(minimum) in missing[0]


def test_minimum_matches_collected_suite_size() -> None:
    """Мінімум не вищий за фактичну кількість тестів (інакше CI червоний без причини)."""
    count = 0
    for path in MONGO_CONFTEST.parent.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        count += text.count("\ndef test_") + text.count("\nasync def test_")
    assert _conftest().MIN_COLLECTED_TESTS <= count
