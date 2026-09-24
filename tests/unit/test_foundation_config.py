"""Unit-тести конфігурації foundation (картка WP-00 PR1, вимоги 1, 2, 4, 5, 7; §16.2, §18).

Перевіряють репозиторій як артефакт: layout Додатка A з docstring про owner-WP, pinned
Python, відсутність доменних залежностей у foundation, політику мережі й маркери pytest,
правила ruff/mypy, команди-контракт §16.2 у CI без hardcoded secrets і `.gitignore` для `.env`.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

APPENDIX_A_PACKAGES = (
    "collector",
    "collector.contracts",
    "collector.core",
    "collector.fetch",
    "collector.discovery",
    "collector.adapters",
    "collector.adapters.news",
    "collector.adapters.vehicles",
    "collector.adapters.catalogs",
    "collector.normalization",
    "collector.translation",
    "collector.persistence",
    "collector.persistence.postgres",
    "collector.persistence.mongo",
    "collector.workers",
    "collector.orchestration",
    "collector.orchestration.compose",
    "collector.orchestration.swarm",
    "collector.api",
    "collector.telemetry",
)
# Залежності, які додають лише власники відповідних WP (картка PR1, вимога 2).
# WP-00 PR2 додав fastapi/uvicorn (стаб health для image `collector`) і pymongo
# (`ensure-mongo` ініціалізує replica set; health `hello`) —
# див. docs/decisions/0002-docker-compose-single-host.md.
# WP-01A PR1 додав sqlalchemy[asyncio]/alembic/asyncpg як owner PostgreSQL foundation
# (картка WP-01A, docs/plan/deps/WP-01A-to-WP-00.md), тож вони вибули зі списку;
# `psycopg` лишається забороненим — runtime підтримує лише драйвер asyncpg
# (collector.persistence.postgres.config.normalize_async_url).
# WP-02 PR1 додав httpx як fetch core (docs/decisions/0008-fetch-core-on-httpx-not-scrapy.md,
# рішення користувача U-1), тож він вибув зі списку; `scrapy` лишається забороненим тим самим
# ADR-0008 — fetch core не використовує Scrapy в жодному компоненті.
FORBIDDEN_FOUNDATION_DEPS = ("scrapy", "psycopg")
SPEC_16_2_PYTHON_COMMANDS = (
    "uv sync --frozen",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run mypy src",
    'uv run pytest -m "not live"',
)


@pytest.mark.parametrize("package", APPENDIX_A_PACKAGES)
def test_appendix_a_package_exists_with_owner_docstring(package: str) -> None:
    module = importlib.import_module(package)
    assert module.__file__ is not None and module.__file__.endswith("__init__.py")
    assert module.__doc__, f"{package}: порожній docstring"
    assert re.search(r"WP-\d{2}", module.__doc__), f"{package}: docstring без owner-WP"


def test_package_is_typed_and_python_pinned() -> None:
    assert (REPO_ROOT / "src" / "collector" / "py.typed").is_file()
    assert (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.13"
    assert PYPROJECT["project"]["requires-python"].startswith(">=3.13")


def test_foundation_dependencies_exclude_domain_libraries() -> None:
    deps = [d.lower() for d in PYPROJECT["project"]["dependencies"]]
    dev = [d.lower() for d in PYPROJECT["dependency-groups"]["dev"]]
    for forbidden in FORBIDDEN_FOUNDATION_DEPS:
        assert not any(d.startswith(forbidden) for d in deps), forbidden
        assert not any(d.startswith(forbidden) for d in dev), forbidden
    assert any(d.startswith("pydantic>=2") for d in deps)
    for required in (
        "ruff",
        "mypy",
        "pytest",
        "pytest-asyncio",
        "pytest-socket",
        "respx",
        "pre-commit",
    ):
        assert any(re.match(rf"{re.escape(required)}\b", d) for d in dev), required


def test_pytest_network_policy_and_markers_are_configured() -> None:
    ini = PYPROJECT["tool"]["pytest"]["ini_options"]
    assert "--disable-socket" in ini["addopts"]
    assert "--strict-markers" in ini["addopts"]
    assert ini["asyncio_mode"] == "auto"
    marker_names = {m.split(":", 1)[0] for m in ini["markers"]}
    assert {"live", "integration", "e2e"} <= marker_names


def test_ruff_and_mypy_configuration() -> None:
    ruff = PYPROJECT["tool"]["ruff"]
    assert ruff["line-length"] == 100
    assert {"E", "F", "I", "B", "UP", "S"} <= set(ruff["lint"]["select"])
    # S-правила вимкнені лише для tests/**, не для src.
    ignores = ruff["lint"].get("per-file-ignores", {})
    assert all(not pattern.startswith("src") for pattern in ignores)
    assert PYPROJECT["tool"]["mypy"]["strict"] is True


def test_ci_runs_spec_16_2_commands_without_hardcoded_secrets() -> None:
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    ci_text = ci_path.read_text(encoding="utf-8")
    ci = yaml.safe_load(ci_text)

    assert "push" in ci[True] and "pull_request" in ci[True]  # yaml: `on` → True
    assert "${{ github.ref }}" in ci["concurrency"]["group"]

    run_steps = [
        step["run"] for job in ci["jobs"].values() for step in job["steps"] if "run" in step
    ]
    for command in SPEC_16_2_PYTHON_COMMANDS:
        assert any(command in run for run in run_steps), command

    # Секрети лише через ${{ secrets.* }}; жодних literal токенів/паролів.
    for line in ci_text.splitlines():
        if re.search(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]", line):
            assert "${{ secrets." in line or line.lstrip().startswith("#"), line
    assert not re.search(r"(?i)(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}", ci_text)


def test_env_files_are_gitignored_but_example_is_not() -> None:
    def ignored(path: str) -> bool:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0

    for secret_file in (".env", ".env.local", ".env.production", "deploy/compose/.env"):
        assert ignored(secret_file), secret_file
    assert not ignored(".env.example")

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert not [f for f in tracked if re.search(r"(^|/)\.env($|\.(?!example$))", f)]


def test_tests_layout_follows_appendix_a() -> None:
    for level in ("unit", "contract", "integration", "e2e", "fixtures"):
        assert (REPO_ROOT / "tests" / level).is_dir(), level
