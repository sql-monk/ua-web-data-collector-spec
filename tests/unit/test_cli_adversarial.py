"""Adversarial-перевірки CLI-контракту §16.2 поверх tests/unit/test_cli.py.

Що доводиться додатково:

- стаб пише повідомлення саме в stderr, stdout порожній (скрипти/CI не сплутають
  повідомлення стаба з корисним виводом);
- усі ролі §7.6 і жодної зайвої: `worker` для кожної ролі приймається (placeholder після
  PR2 — див. test_cli_compose_commands.py); невідома роль — usage error без «not implemented»;
- типізовані стаби валідують обов'язкові параметри (usage error, а не стаб);
- перелік команд у `--help` збігається з §16.2 точно (без зайвих команд);
- `version` працює через реальні entry points (`python -m collector.cli`, console script)
  і без env `COLLECTOR_GIT_SHA`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from importlib import metadata

import pytest
from typer.testing import CliRunner

from collector.cli import app
from collector.contracts import CONTRACTS_VERSION
from collector.core.version import GIT_SHA_ENV, UNKNOWN_GIT_SHA
from collector.workers.roles import WorkerRole

runner = CliRunner()

SPEC_7_6_ROLES = frozenset(
    {"discovery", "fetch", "browser", "parse", "projector", "translation", "export", "maintenance"}
)
SPEC_16_2_TOP_LEVEL = frozenset(
    {"version", "db", "e2e", "release", "worker", "api", "scheduler", "controller"}
)
# §16.2 — контракт CI-команд, а не вичерпний список CLI. Явний allowlist розширень foundation;
# нова група потрапляє сюди лише через approved dependency-запит (docs/plan/deps/).
FOUNDATION_EXTENSIONS = frozenset({"contracts"})  # WP-01C: `collector contracts export [--check]`
STUB_OWNER_PATTERN = re.compile(r"^not implemented: owned by WP-\d{2}[A-Z]?$")

STUB_ARGV: tuple[list[str], ...] = (
    ["e2e", "--source", "fixtures", "--offline"],
    ["release", "build", "--watermark", "test", "--output", ".artifacts/release"],
    ["release", "verify", "--manifest", ".artifacts/release/manifest.json"],
    ["controller"],
)


def _commands_from_help(help_text: str) -> set[str]:
    """Імена команд із секції `Commands:` plain-text help."""
    section = help_text.split("Commands:", 1)[1]
    return {line.split()[0] for line in section.splitlines() if line.startswith("  ")}


@pytest.mark.parametrize("argv", STUB_ARGV, ids=[" ".join(a) for a in STUB_ARGV])
def test_stub_message_goes_to_stderr_only(argv: list[str]) -> None:
    result = runner.invoke(app, argv)
    # Літерал 2 навмисно (картка, вимога 3): тест не має залежати від константи, яку перевіряє.
    assert result.exit_code == 2
    assert result.stdout == "", "стаб не має писати у stdout"
    assert STUB_OWNER_PATTERN.match(result.stderr.strip()), result.stderr


def test_worker_role_enum_matches_spec_7_6_exactly() -> None:
    assert {role.value for role in WorkerRole} == SPEC_7_6_ROLES


@pytest.mark.parametrize("bad_role", ["mailer", "Fetch", "fetch ", "", "fetch,parse"])
def test_worker_unknown_or_malformed_role_is_usage_error(bad_role: str) -> None:
    result = runner.invoke(app, ["worker", bad_role])
    assert result.exit_code != 0
    assert "not implemented" not in result.output
    assert "Usage: collector worker" in result.output


def test_worker_without_role_is_usage_error() -> None:
    result = runner.invoke(app, ["worker"])
    assert result.exit_code != 0
    assert "not implemented" not in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["e2e"],
        ["release", "build"],
        ["release", "build", "--watermark", "w"],
        ["release", "verify"],
    ],
    ids=lambda a: " ".join(a),
)
def test_typed_stubs_reject_missing_required_options(argv: list[str]) -> None:
    """Стаб типізований: без обов'язкових опцій — usage error, а не «not implemented»."""
    result = runner.invoke(app, argv)
    assert result.exit_code != 0
    assert "not implemented" not in result.output
    assert "Missing option" in result.output


def test_unknown_top_level_command_is_rejected() -> None:
    result = runner.invoke(app, ["deploy"])
    assert result.exit_code != 0
    assert "not implemented" not in result.output


def test_help_command_set_equals_spec_16_2_exactly() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert _commands_from_help(result.output) == SPEC_16_2_TOP_LEVEL | FOUNDATION_EXTENSIONS


@pytest.mark.parametrize(
    ("group", "expected"),
    [("db", {"ensure-mongo", "migrate"}), ("release", {"build", "verify"})],
)
def test_group_command_set_is_exact(group: str, expected: set[str]) -> None:
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0
    assert _commands_from_help(result.output) == expected


def test_console_script_entry_point_is_registered() -> None:
    entry_points = [
        e for e in metadata.entry_points(group="console_scripts") if e.name == "collector"
    ]
    assert [e.value for e in entry_points] == ["collector.cli:app"]


def test_version_via_module_entry_point_without_git_sha_env() -> None:
    """`python -m collector.cli version` у реальному процесі без COLLECTOR_GIT_SHA."""
    env = {k: v for k, v in os.environ.items() if k != GIT_SHA_ENV}
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "collector.cli", "version"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        f"package_version={metadata.version('collector')}",
        f"git_sha={UNKNOWN_GIT_SHA}",
        f"schema_version={CONTRACTS_VERSION}",
    ]


def test_version_cli_ignores_whitespace_only_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GIT_SHA_ENV, " \t ")
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"git_sha={UNKNOWN_GIT_SHA}" in result.output.splitlines()


def test_version_rejects_unknown_option() -> None:
    result = runner.invoke(app, ["version", "--verbose"])
    assert result.exit_code != 0
    assert "No such option" in result.output
