"""Adversarial-сценарії health-стаба і Compose-команд CLI (wp-tester, WP-00 PR2; §7.5, §13).

- probe відповів, але повільніше за timeout → компонент `ok=False` («slow»), readiness false;
- виняток у probe не «витікає» з HTTP-обробника: endpoint завжди відповідає JSON (503);
- `hello` без `setName`/з secondary → not ready (readiness false до `ensure-mongo`);
- `main()` з дублікатами/порядком аргументів фільтрує у канонічному порядку;
- `ensure-mongo`: відсутній файл секрету → ненульовий код, значення секретів не в output;
  `hello` без `isWritablePrimary` ключа → timeout → код 1;
- `db migrate` не друкує «not implemented» і не читає секрети.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pymongo
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure
from typer.testing import CliRunner

from collector.api import health
from collector.api.health import (
    HEALTH_PATH,
    ComponentStatus,
    _timed,
    check_components,
    check_mongo,
    create_app,
    main,
)
from collector.cli import MONGO_NOT_YET_INITIALIZED, app, ensure_mongo_replica_set

runner = CliRunner()


# --- _timed / slow probe ----------------------------------------------------------------------


def test_slow_probe_is_degraded_even_if_it_succeeds() -> None:
    def probe() -> str:
        time.sleep(0.05)
        return "late but fine"

    status = _timed("postgres", probe, timeout=0.01)
    assert not status.ok
    assert status.detail == "slow"
    assert status.latency_ms >= 10


def test_probe_exception_detail_is_truncated_and_typed() -> None:
    def probe() -> str:
        raise OSError("x" * 1000)

    status = _timed("minio", probe, timeout=1.0)
    assert not status.ok
    assert status.detail == "OSError"  # лише клас винятку, текст — у логах (SEC L-4)
    assert len(status.detail) <= 200


def test_unexpected_exception_type_propagates_not_swallowed() -> None:
    """Лише мережеві/driver-помилки → ok=False; програмна помилка не маскується."""

    def probe() -> str:
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        _timed("postgres", probe, timeout=1.0)


# --- endpoint під деградацією ----------------------------------------------------------------


def _status(name: health.ComponentName, ok: bool) -> Any:
    def check(environ: Mapping[str, str] | None = None, *, timeout: float = 1.0) -> ComponentStatus:
        return ComponentStatus(name=name, ok=ok, latency_ms=0.1, detail="x")

    return check


def test_endpoint_503_when_only_postgres_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сценарій `docker compose stop postgres`: 503, JSON із деталями, mongo/minio ok."""
    monkeypatch.setattr(
        health,
        "CHECKS",
        {
            "postgres": _status("postgres", False),
            "mongo": _status("mongo", True),
            "minio": _status("minio", True),
        },
    )
    response = TestClient(create_app()).get(HEALTH_PATH)
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert {c["name"]: c["ok"] for c in body["components"]} == {
        "postgres": False,
        "mongo": True,
        "minio": True,
    }
    assert response.headers["content-type"].startswith("application/json")


def test_endpoint_503_when_all_down_still_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECKS", {n: _status(n, False) for n in health.COMPONENT_NAMES})
    response = TestClient(create_app()).get(HEALTH_PATH)
    assert response.status_code == 503
    assert all(not c["ok"] for c in response.json()["components"])


def test_endpoint_rejects_non_get_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECKS", {n: _status(n, True) for n in health.COMPONENT_NAMES})
    client = TestClient(create_app())
    assert client.post(HEALTH_PATH).status_code == 405
    assert client.get(HEALTH_PATH + "/extra").status_code == 404


# --- mongo hello варіанти ----------------------------------------------------------------------


class _Admin:
    def __init__(self, hello: dict[str, Any]) -> None:
        self._hello = hello

    def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._hello


class _Client:
    hello: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        self.admin = _Admin(type(self).hello)
        self.kwargs = kwargs

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "hello",
    [
        {},  # standalone без RS: ключа немає
        {"isWritablePrimary": False, "secondary": True, "setName": "rs0"},
        {"ismaster": True},  # legacy-ключ не рахується
    ],
)
def test_check_mongo_not_ready_unless_writable_primary(
    monkeypatch: pytest.MonkeyPatch, hello: dict[str, Any]
) -> None:
    _Client.hello = hello
    monkeypatch.setattr(health, "MongoClient", _Client)
    status = check_mongo({})
    assert not status.ok
    assert status.detail == "not_primary"  # без текстів/host:port у відповіді (SEC L-4)


def test_check_mongo_uses_env_timeout_and_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.hello = {"isWritablePrimary": True, "setName": "rs0"}
    created: list[_Client] = []

    class Recording(_Client):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            created.append(self)

    monkeypatch.setattr(health, "MongoClient", Recording)
    assert check_mongo({}, timeout=0.25).ok
    kwargs = created[0].kwargs
    assert kwargs["serverSelectionTimeoutMS"] == 250
    assert kwargs["connectTimeoutMS"] == 250
    assert not {"username", "password", "authSource"} & set(kwargs)


# --- main() -----------------------------------------------------------------------------------


def test_main_filters_in_canonical_order_and_dedupes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(health, "CHECKS", {n: _status(n, True) for n in health.COMPONENT_NAMES})
    assert main(["minio", "postgres", "minio"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in lines] == ["postgres", "minio"]


def test_main_unknown_component_is_usage_error_before_any_probe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: Any, **kwargs: Any) -> ComponentStatus:
        raise AssertionError("probe must not run")

    monkeypatch.setattr(health, "CHECKS", {n: boom for n in health.COMPONENT_NAMES})
    assert main(["postgres", "redis"]) == 2
    assert "redis" in capsys.readouterr().err


def test_check_components_subset_only_probes_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def rec(name: health.ComponentName) -> Any:
        def check(environ: Any = None, *, timeout: float = 1.0) -> ComponentStatus:
            calls.append(name)
            return ComponentStatus(name=name, ok=True, latency_ms=0.1, detail="x")

        return check

    monkeypatch.setattr(health, "CHECKS", {n: rec(n) for n in health.COMPONENT_NAMES})
    report = check_components(("postgres", "minio"))
    assert calls == ["postgres", "minio"]
    assert report.ready


# --- CLI: ensure-mongo / migrate adversarial --------------------------------------------------


def test_ensure_mongo_missing_secret_file_fails_without_leaking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_USERNAME", "collector_root")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_PASSWORD_FILE", str(tmp_path / "absent"))
    monkeypatch.delenv("COLLECTOR_MONGO_ROOT_PASSWORD", raising=False)
    connected: list[bool] = []

    class NeverConnect:
        def __init__(self, **kwargs: Any) -> None:
            connected.append(True)

    monkeypatch.setattr(pymongo, "MongoClient", NeverConnect)
    result = runner.invoke(app, ["db", "ensure-mongo"])
    assert result.exit_code != 0
    assert not connected, "без секрету клієнт не створюється"
    assert isinstance(result.exception, FileNotFoundError)


def test_ensure_replica_set_hello_without_primary_key_times_out() -> None:
    class Admin:
        def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if name == "replSetGetStatus":
                return {"set": "rs0"}
            return {"setName": "rs0"}  # без isWritablePrimary

    class Client:
        admin = Admin()

        def close(self) -> None:
            pass

    with pytest.raises(TimeoutError):
        ensure_mongo_replica_set(
            Client(),  # type: ignore[arg-type]
            replica_set="rs0",
            member_host="mongo:27017",
            wait_seconds=0.3,
        )


def test_ensure_replica_set_initiate_failure_propagates() -> None:
    """replSetInitiate впав (напр., код 103 NodeNotFound) → OperationFailure → exit 1 у CLI."""

    class Admin:
        def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if name == "replSetGetStatus":
                raise OperationFailure("no config", code=MONGO_NOT_YET_INITIALIZED)
            if name == "replSetInitiate":
                raise OperationFailure("NodeNotFound", code=74)
            raise AssertionError(name)

    class Client:
        admin = Admin()

        def close(self) -> None:
            pass

    with pytest.raises(OperationFailure):
        ensure_mongo_replica_set(
            Client(),  # type: ignore[arg-type]
            replica_set="rs0",
            member_host="mongo:27017",
        )


def test_db_ensure_mongo_cli_exit_1_when_set_name_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    class Admin:
        def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if name == "replSetGetStatus":
                return {"set": "legacy"}
            return {"isWritablePrimary": True, "setName": "legacy"}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            self.admin = Admin()

        def close(self) -> None:
            pass

    (tmp_path / "pw").write_text("topsecret\n", encoding="utf-8")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_PASSWORD_FILE", str(tmp_path / "pw"))
    monkeypatch.setenv("COLLECTOR_MONGO_REPLICA_SET", "rs0")
    monkeypatch.setattr(pymongo, "MongoClient", Client)
    result = runner.invoke(app, ["db", "ensure-mongo"])
    assert result.exit_code == 1
    assert "legacy" in result.stderr
    assert "topsecret" not in result.output


def test_db_migrate_never_prints_stub_line_nor_reads_secrets(  # noqa: D103 — див. тіло
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        health,
        "check_postgres",
        lambda *a, **k: ComponentStatus(name="postgres", ok=True, latency_ms=0.1, detail="tcp"),
    )
    monkeypatch.setenv("COLLECTOR_POSTGRES_PASSWORD_FILE", "/definitely/absent")
    monkeypatch.setenv("COLLECTOR_POSTGRES_DSN_FILE", "/definitely/absent")
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN", raising=False)
    result = runner.invoke(app, ["db", "migrate"])
    # Після WP-01A PR1 команда реальна: відсутній secret-файл DSN — явна помилка конфігурації
    # (exit 1), а не stub-рядок; сам шлях до секрету не читається як пароль і не логується.
    assert result.exit_code == 1
    assert "not implemented" not in result.output
    assert "no migrations yet" not in result.output
    assert "COLLECTOR_POSTGRES_DSN_FILE" in result.output
    assert "Traceback" not in result.output


def test_ensure_replica_set_does_not_initiate_on_non_94_status_error() -> None:
    """`replSetGetStatus` впав з іншим кодом (13 Unauthorized) → помилка, БЕЗ replSetInitiate.

    Існуючі фейки кидають виняток на кожній команді, тому мутація
    `if exc.code != MONGO_NOT_YET_INITIALIZED` → `if False` лишалася зеленою.
    """
    commands: list[str] = []

    class Admin:
        def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            commands.append(name)
            if name == "replSetGetStatus":
                raise OperationFailure("Unauthorized", code=13)
            if name == "replSetInitiate":
                return {"ok": 1}
            return {"isWritablePrimary": True, "setName": "rs0"}

    class Client:
        admin = Admin()

        def close(self) -> None:
            pass

    with pytest.raises(OperationFailure) as info:
        ensure_mongo_replica_set(
            Client(),  # type: ignore[arg-type]
            replica_set="rs0",
            member_host="mongo:27017",
        )
    assert info.value.code == 13
    assert commands == ["replSetGetStatus"], "жодного replSetInitiate під Unauthorized"
