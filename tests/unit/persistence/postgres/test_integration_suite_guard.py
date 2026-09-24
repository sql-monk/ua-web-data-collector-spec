"""Вартовий проти мовчазного skip PostgreSQL integration-тестів у CI (PR3a).

Єдиний дозволений шлях пропуску — `_skip_or_fail` у `tests/integration/postgres/conftest.py`,
який під `COLLECTOR_TEST_REQUIRE_DOCKER=1` (job `integration-postgres`) перетворює skip на
fail. Отже:

- кожен модуль `test_*.py` набору має маркер `integration` (інакше його не вибере
  `pytest -m integration tests/integration/postgres` у CI і він тихо випаде);
- жоден модуль не пропускає тести власним `pytest.skip`/`skipif`/`importorskip` в обхід
  прапорця;
- conftest і CI-job досі тримають обидві половини механізму.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SUITE = REPO_ROOT / "tests" / "integration" / "postgres"
SKIP_CALL = re.compile(r"pytest\.skip\(|pytest\.mark\.skip|skipif|importorskip")
MODULES = sorted(SUITE.glob("test_*.py"))


def test_suite_is_found() -> None:
    assert len(MODULES) >= 20, "шлях до набору змінився — оновіть вартового"


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.name)
def test_every_module_is_marked_integration_and_does_not_skip_on_its_own(module: Path) -> None:
    source = module.read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.integration" in source, module.name
    assert not SKIP_CALL.search(source), f"{module.name}: власний skip обходить REQUIRE_DOCKER"


def test_conftest_turns_skip_into_failure_under_the_ci_flag() -> None:
    source = (SUITE / "conftest.py").read_text(encoding="utf-8")
    assert 'REQUIRE_DOCKER_ENV = "COLLECTOR_TEST_REQUIRE_DOCKER"' in source
    assert "pytest.fail(reason)" in source


def test_ci_job_runs_the_suite_with_the_flag() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci.split("integration-postgres:", 1)[1].split("\n  pre-commit:", 1)[0]
    assert 'COLLECTOR_TEST_REQUIRE_DOCKER: "1"' in job
    assert "uv run pytest -m integration tests/integration/postgres" in job
