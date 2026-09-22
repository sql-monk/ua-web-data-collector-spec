"""CLI-контракт §16.2: `--help` містить усі команди, стаби повертають 2 і owner-WP.

Після WP-00 PR2 `db ensure-mongo`, `api`, `scheduler` і `worker <role>` мають мінімальну
реальну/placeholder-поведінку (tests/unit/test_cli_compose_commands.py), а після WP-01A PR1
`db migrate` і `db roles` — повну (tests/unit/persistence/postgres/test_cli_db.py,
tests/integration/postgres/test_cli_db.py). Стабами лишаються `e2e`, `release build|verify`,
`controller` і прапорці `ensure-mongo --validators/--indexes`.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from collector.cli import app
from collector.contracts import CONTRACTS_VERSION
from collector.core import version as version_module
from collector.core.version import git_sha, version_info
from collector.workers.roles import WorkerRole

runner = CliRunner()

TOP_LEVEL_COMMANDS = ("version", "db", "e2e", "release", "worker", "api", "scheduler", "controller")
# §16.2 — контракт CI-команд, не вичерпний список CLI; розширення foundation через dependency-запит.
FOUNDATION_EXTENSIONS = ("contracts",)  # WP-01C
WORKER_ROLES = (
    "discovery",
    "fetch",
    "browser",
    "parse",
    "projector",
    "translation",
    "export",
    "maintenance",
)

# (argv, owner-WP) — стаби контракту §16.2, що лишилися після PR2, і їхні власники.
STUBS: tuple[tuple[list[str], str], ...] = (
    # `db` тут немає: `ensure-mongo` після WP-00 PR2 реальна (прапорці `--validators`/
    # `--indexes` стають стабом WP-01B лише після ініціалізації RS — це вимагає живої Mongo,
    # тому перевіряється в tests/unit/test_cli_compose_commands.py), а `db migrate`/`db roles`
    # реалізовані WP-01A (docs/plan/deps/WP-01A-to-WP-00.md).
    (["e2e", "--source", "fixtures", "--offline"], "WP-14"),
    (["release", "build", "--watermark", "test", "--output", ".artifacts/release"], "WP-11A"),
    (["release", "verify", "--manifest", ".artifacts/release/manifest.json"], "WP-11A"),
    (["controller"], "WP-01D"),
)


def test_help_lists_all_contract_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for command in (*TOP_LEVEL_COMMANDS, *FOUNDATION_EXTENSIONS):
        assert f"\n  {command} " in result.output, f"{command} відсутня у --help"


@pytest.mark.parametrize(
    ("group", "subcommands"),
    [("db", ("ensure-mongo", "migrate", "roles")), ("release", ("build", "verify"))],
)
def test_group_help_lists_subcommands(group: str, subcommands: tuple[str, ...]) -> None:
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0, result.output
    for subcommand in subcommands:
        assert f"\n  {subcommand} " in result.output


def test_worker_help_lists_all_roles_from_spec_7_6() -> None:
    result = runner.invoke(app, ["worker", "--help"])
    assert result.exit_code == 0, result.output
    assert tuple(WorkerRole) == WORKER_ROLES
    for role in WORKER_ROLES:
        assert role in result.output


@pytest.mark.parametrize(("argv", "owner"), STUBS, ids=[" ".join(a) for a, _ in STUBS])
def test_stub_returns_exit_code_2_and_owner(argv: list[str], owner: str) -> None:
    result = runner.invoke(app, argv)
    assert result.exit_code == 2  # контракт картки WP-00/§16.2, не константа
    assert result.output.strip() == f"not implemented: owned by {owner}"


def test_worker_rejects_unknown_role() -> None:
    result = runner.invoke(app, ["worker", "mailer"])
    assert result.exit_code != 0
    assert "not implemented" not in result.output


@pytest.mark.parametrize("argv", [[], ["db"], ["release"]], ids=["root", "db", "release"])
def test_group_without_subcommand_prints_help_with_exit_0(argv: list[str]) -> None:
    """Виклик групи без підкоманди — не стаб і не usage-помилка: help, код 0."""
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert "Usage: collector" in result.output
    assert "not implemented" not in result.output


def test_version_prints_package_git_sha_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(version_module.GIT_SHA_ENV, "deadbeef")
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("package_version=") and lines[0] != "package_version="
    assert lines[1] == "git_sha=deadbeef"
    assert lines[2] == f"schema_version={CONTRACTS_VERSION}"


def test_version_git_sha_defaults_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(version_module.GIT_SHA_ENV, raising=False)
    assert git_sha() == "unknown"
    assert git_sha({version_module.GIT_SHA_ENV: "   "}) == "unknown"
    assert version_info().git_sha == "unknown"
