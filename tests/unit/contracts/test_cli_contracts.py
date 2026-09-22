"""`collector contracts export [--check]`: генерація snapshot-ів і виявлення drift."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from collector.cli import app
from collector.contracts.schema_export import EXPORTED_CONTRACTS, SCHEMA_GROUPS

runner = CliRunner()


def test_export_writes_all_snapshots_then_check_is_clean(tmp_path: Path) -> None:
    out = tmp_path / "schemas"
    result = runner.invoke(app, ["contracts", "export", "--output", str(out)])
    assert result.exit_code == 0, result.output
    written = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.json"))
    assert written == sorted(c.relative_path.as_posix() for c in EXPORTED_CONTRACTS)
    assert set(SCHEMA_GROUPS) == {"common", "events", "mongo", "releases"}
    assert f"exported {len(EXPORTED_CONTRACTS)} schemas" in result.output

    check = runner.invoke(app, ["contracts", "export", "--check", "--output", str(out)])
    assert check.exit_code == 0, check.output
    assert "up to date" in check.output


def test_check_detects_drift_missing_and_stale(tmp_path: Path) -> None:
    out = tmp_path / "schemas"
    assert runner.invoke(app, ["contracts", "export", "--output", str(out)]).exit_code == 0
    money = out / "common" / "money.v1.json"
    money.write_text(
        money.read_text(encoding="utf-8").replace('"integer"', '"number"'), encoding="utf-8"
    )
    (out / "events" / "projection_command.v1.json").unlink()
    (out / "mongo" / "legacy.v0.json").write_text("{}", encoding="utf-8")

    check = runner.invoke(app, ["contracts", "export", "--check", "--output", str(out)])
    assert check.exit_code == 1
    assert "drift: common/money.v1.json" in check.output
    assert "missing: events/projection_command.v1.json" in check.output
    assert "stale: mongo/legacy.v0.json" in check.output
    assert "schema drift: 3" in check.output


def test_check_tolerates_crlf_checkout(tmp_path: Path) -> None:
    out = tmp_path / "schemas"
    assert runner.invoke(app, ["contracts", "export", "--output", str(out)]).exit_code == 0
    money = out / "common" / "money.v1.json"
    money.write_bytes(money.read_bytes().replace(b"\n", b"\r\n"))
    assert (
        runner.invoke(app, ["contracts", "export", "--check", "--output", str(out)]).exit_code == 0
    )


def test_contracts_group_help_without_subcommand() -> None:
    result = runner.invoke(app, ["contracts"])
    assert result.exit_code == 0
    assert "export" in result.output
