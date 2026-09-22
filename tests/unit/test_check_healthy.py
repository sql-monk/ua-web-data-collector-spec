"""`deploy/compose/check-healthy.py` — assert CI «усі контейнери healthy» (gate 3, CR-2)."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "compose" / "check-healthy.py"


@pytest.fixture(scope="module")
def check_healthy() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_healthy", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(service: str, state: str, health: str = "", exit_code: int = 0) -> dict[str, object]:
    return {"Service": service, "State": state, "Health": health, "ExitCode": exit_code}


def test_unhealthy_is_not_counted_as_healthy(check_healthy: ModuleType) -> None:
    problems = check_healthy.unhealthy(
        [_row("api", "running", "unhealthy"), _row("fetch-worker", "running", "healthy")]
    )
    assert problems == ["api: state=running health=unhealthy exit=0"]


@pytest.mark.parametrize(
    "row",
    [
        _row("x", "running", "starting"),
        _row("x", "running", ""),
        _row("x", "restarting", "unhealthy"),
        _row("x", "created", ""),
        _row("x", "exited", "", 1),
        _row("x", "exited", "", 137),
    ],
)
def test_every_non_healthy_state_is_a_problem(
    check_healthy: ModuleType, row: dict[str, object]
) -> None:
    assert check_healthy.unhealthy([row])


def test_exited_zero_one_shot_and_healthy_running_pass(check_healthy: ModuleType) -> None:
    assert check_healthy.unhealthy([_row("m", "exited"), _row("api", "running", "healthy")]) == []


def test_parse_ps_accepts_json_lines_and_array(check_healthy: ModuleType) -> None:
    lines = [
        '{"Service":"a","State":"exited","ExitCode":0}',
        "",
        '[{"Service":"b","State":"running","Health":"healthy"}]',
    ]
    assert [c["Service"] for c in check_healthy.parse_ps(lines)] == ["a", "b"]


def test_main_exit_codes(check_healthy: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"Service":"a","State":"running","Health":"unhealthy"}\n')
    )
    assert check_healthy.main() == 1
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"Service":"a","State":"running","Health":"healthy"}\n')
    )
    assert check_healthy.main() == 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert check_healthy.main() == 1, "порожній ps — не успіх"
