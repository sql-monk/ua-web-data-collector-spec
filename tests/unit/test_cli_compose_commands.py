"""CLI-команди з реальною/placeholder-поведінкою після WP-00 PR2 (картка, PR2 вимоги 5 і 7).

- `db migrate` (після WP-01A PR1 — реальний Alembic): без DSN → 1 з назвою env; недоступний
  сервер → 1 без stdout (помилка драйвера підставляється, socket не створюється);
- `db ensure-mongo`: ідемпотентна ініціалізація single-member replica set (фейковий клієнт);
  `--validators/--indexes` після ініціалізації — міграції/indexes WP-01B PR1 (підмінені);
- `worker <role>`/`scheduler`: після WP-01D PR1 це справжній runtime, а placeholder-процес
  лишається rollback-шляхом за `COLLECTOR_WORKER_PLACEHOLDER=1` (живий до stop/SIGTERM, код 0,
  стаб-рядок у stderr); поведінку runtime перевіряє tests/unit/workers і
  tests/integration/scaling;
- `api`: запускає uvicorn з factory `collector.api.health:create_app` (uvicorn — фейк).
"""

from __future__ import annotations

import threading
from typing import Any, NoReturn

import asyncpg
import pymongo
import pytest
from pymongo.errors import OperationFailure
from typer.testing import CliRunner

from collector import cli
from collector.api import health
from collector.api.health import ComponentStatus
from collector.cli import (
    MONGO_NOT_YET_INITIALIZED,
    app,
    ensure_mongo_replica_set,
    placeholder_process,
)
from collector.workers.roles import WorkerRole

runner = CliRunner()


# cli імпортує pymongo/check_postgres лише в тілах команд (lazy, gate 3 CR-12), тому підміна
# робиться на модулях-джерелах: `pymongo.MongoClient`, `collector.api.health.check_postgres`.


# --- db migrate -------------------------------------------------------------------------------


def _postgres(ok: bool) -> Any:
    def check(environ: Any = None, *, timeout: float = 3.0) -> ComponentStatus:
        return ComponentStatus(name="postgres", ok=ok, latency_ms=0.2, detail="fake")

    return check


def test_db_migrate_requires_dsn_and_is_no_longer_a_tcp_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WP-01A PR1 замінив TCP-стаб на Alembic (docs/plan/deps/WP-01A-to-WP-00.md, п.1).

    One-shot `migrate-postgres` отримує DSN міграційної ролі через
    `COLLECTOR_POSTGRES_DSN_FILE` (Docker secret), тому доступність PostgreSQL більше не
    перевіряється окремим TCP-пробом, а помилка конфігурації має бути явною і без stub-рядка.
    Повний шлях `upgrade head` покрито tests/integration/postgres/test_cli_db.py.
    """
    monkeypatch.setattr(health, "check_postgres", _postgres(True))
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN_FILE", raising=False)
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 1, result.output
    assert "COLLECTOR_POSTGRES_DSN" in result.output
    assert "no migrations yet" not in result.output
    assert "not implemented" not in result.output


def test_db_migrate_exits_1_when_postgres_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступний сервер → exit 1 без stdout і без traceback (one-shot не пускає api далі).

    Відмова підставляється у `asyncpg.connect`, а не через реальний закритий порт: на POSIX
    `pytest-socket` блокує створення socket у звичайних тестах, тож спроба справжнього
    зʼєднання давала б `SocketBlockedError` (CI PR #3, job `python`). Реальний шлях до
    PostgreSQL перевіряють integration-тести.
    """
    monkeypatch.setattr(health, "check_postgres", _postgres(False))
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN_FILE", raising=False)
    monkeypatch.setenv("COLLECTOR_POSTGRES_DSN", "postgresql://nobody:x@postgres.invalid/void")

    async def _refuse(*args: object, **kwargs: object) -> NoReturn:
        raise ConnectionRefusedError("[Errno 111] Connect call failed")

    monkeypatch.setattr(asyncpg, "connect", _refuse)
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "postgres error" in result.stderr
    assert "Traceback" not in result.output


# --- db ensure-mongo --------------------------------------------------------------------------


class FakeAdmin:
    """Мінімальна модель стану RS: not-initialized → replSetInitiate → primary."""

    def __init__(self, *, initialized_as: str | None, primary_after: int = 1) -> None:
        self.set_name = initialized_as
        self.primary_after = primary_after
        self.commands: list[str] = []
        self.hello_calls = 0

    def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.commands.append(name)
        if name == "replSetGetStatus":
            if self.set_name is None:
                raise OperationFailure("no replset config", code=MONGO_NOT_YET_INITIALIZED)
            return {"set": self.set_name}
        if name == "replSetInitiate":
            assert self.set_name is None
            config = args[0]
            assert config["members"] == [{"_id": 0, "host": "mongo:27017"}]
            self.set_name = config["_id"]
            return {"ok": 1}
        if name == "hello":
            self.hello_calls += 1
            primary = self.set_name is not None and self.hello_calls >= self.primary_after
            return {"isWritablePrimary": primary, "setName": self.set_name}
        raise AssertionError(name)


class FakeClient:
    last: FakeClient | None = None

    def __init__(self, admin: FakeAdmin | None = None, **kwargs: Any) -> None:
        self.admin = admin or FakeAdmin(initialized_as=None)
        self.kwargs = kwargs
        self.closed = False
        FakeClient.last = self

    def close(self) -> None:
        self.closed = True


def test_ensure_replica_set_initiates_when_not_initialized() -> None:
    client = FakeClient(FakeAdmin(initialized_as=None, primary_after=3))
    initiated = ensure_mongo_replica_set(
        client,  # type: ignore[arg-type]  # фейк замість MongoClient
        replica_set="rs0",
        member_host="mongo:27017",
        wait_seconds=5,
    )
    assert initiated is True
    assert client.admin.commands[:2] == ["replSetGetStatus", "replSetInitiate"]
    assert client.admin.hello_calls == 3, "чекає, поки член стане primary"


def test_ensure_replica_set_is_idempotent() -> None:
    client = FakeClient(FakeAdmin(initialized_as="rs0"))
    initiated = ensure_mongo_replica_set(
        client,  # type: ignore[arg-type]  # фейк замість MongoClient
        replica_set="rs0",
        member_host="mongo:27017",
    )
    assert initiated is False
    assert "replSetInitiate" not in client.admin.commands


def test_ensure_replica_set_rejects_other_set_name() -> None:
    client = FakeClient(FakeAdmin(initialized_as="other"))
    with pytest.raises(ValueError, match="already initialised as 'other'"):
        ensure_mongo_replica_set(
            client,  # type: ignore[arg-type]  # фейк замість MongoClient
            replica_set="rs0",
            member_host="mongo:27017",
        )


def test_ensure_replica_set_times_out_if_never_primary() -> None:
    client = FakeClient(FakeAdmin(initialized_as="rs0", primary_after=10**6))
    with pytest.raises(TimeoutError):
        ensure_mongo_replica_set(
            client,  # type: ignore[arg-type]  # фейк замість MongoClient
            replica_set="rs0",
            member_host="mongo:27017",
            wait_seconds=0.6,
        )


def test_ensure_replica_set_reraises_other_operation_failures() -> None:
    class Unauthorized(FakeAdmin):
        def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise OperationFailure("requires authentication", code=13)

    with pytest.raises(OperationFailure):
        ensure_mongo_replica_set(
            FakeClient(Unauthorized(initialized_as=None)),  # type: ignore[arg-type]
            replica_set="rs0",
            member_host="mongo:27017",
        )


@pytest.fixture
def fake_mongo_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> type[FakeClient]:
    FakeClient.last = None
    monkeypatch.setattr(pymongo, "MongoClient", FakeClient)
    password_file = tmp_path / "mongo_root_password"
    password_file.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_USERNAME", "collector_root")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_PASSWORD_FILE", str(password_file))
    monkeypatch.delenv("COLLECTOR_MONGO_ROOT_PASSWORD", raising=False)
    monkeypatch.setenv("COLLECTOR_MONGO_HOST", "mongo")
    monkeypatch.setenv("COLLECTOR_MONGO_PORT", "27017")
    monkeypatch.setenv("COLLECTOR_MONGO_REPLICA_SET", "rs0")
    return FakeClient


def test_db_ensure_mongo_initiates_and_exits_0(fake_mongo_client: type[FakeClient]) -> None:
    result = runner.invoke(app, ["db", "ensure-mongo"])
    assert result.exit_code == 0, result.output
    client = fake_mongo_client.last
    assert client is not None and client.closed
    assert client.kwargs["username"] == "collector_root"
    # S105: тестове значення з tmp-файлу, не справжній секрет.
    assert client.kwargs["password"] == "s3cret", "секрет читається з *_FILE"  # noqa: S105
    assert client.kwargs["authSource"] == "admin"
    assert client.kwargs["directConnection"] is True
    assert "replSetInitiate" in client.admin.commands
    assert "s3cret" not in result.output, "секрет не потрапляє у логи"
    assert "not implemented" not in result.output


def test_db_ensure_mongo_validators_indexes_run_after_init(
    fake_mongo_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP-01B PR1 замінив стаб на міграції/indexes (docs/plan/deps/WP-01B-to-WP-00.md, п.1).

    Інваріант лишився: схема застосовується лише після ініціалізації RS, стаб-рядка немає.
    Реальний шлях покрито tests/integration/mongo/test_cli_ensure_mongo.py.
    """
    from collector.persistence.mongo import admin

    calls: list[dict[str, Any]] = []

    def _apply(client: Any, database: str, **kwargs: Any) -> admin.SchemaResult:
        assert "replSetInitiate" in client.admin.commands
        calls.append({"database": database, **kwargs})
        return admin.SchemaResult()

    monkeypatch.setattr(admin, "apply_mongo_schema", _apply)
    monkeypatch.delenv("COLLECTOR_MONGO_DATABASE", raising=False)
    result = runner.invoke(app, ["db", "ensure-mongo", "--validators", "--indexes"])
    assert result.exit_code == 0, result.output
    assert "not implemented" not in result.output
    assert calls == [
        {"database": "collector", "validators": True, "indexes": True, "credentials": None}
    ]
    client = fake_mongo_client.last
    assert client is not None and client.closed


def test_db_ensure_mongo_exits_1_on_driver_error(
    fake_mongo_client: type[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing(FakeClient):
        def __init__(self, **kwargs: Any) -> None:
            class Admin(FakeAdmin):
                def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
                    raise OperationFailure("Authentication failed", code=18)

            super().__init__(Admin(initialized_as=None), **kwargs)

    monkeypatch.setattr(pymongo, "MongoClient", Failing)
    result = runner.invoke(app, ["db", "ensure-mongo"])
    assert result.exit_code == 1
    assert "ensure_mongo.failed" in result.stderr
    assert "s3cret" not in result.output


# --- placeholders: worker <role>, scheduler ---------------------------------------------------


def test_placeholder_process_runs_until_stopped(capsys: pytest.CaptureFixture[str]) -> None:
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    placeholder_process("worker.fetch", "WP-01D", stop=stop, heartbeat_seconds=0.05)
    err = capsys.readouterr().err
    assert err.splitlines()[0] == "not implemented: owned by WP-01D"
    assert '"placeholder.started"' in err
    assert '"placeholder.heartbeat"' in err
    assert '"placeholder.stopped"' in err


@pytest.mark.parametrize("role", [role.value for role in WorkerRole])
def test_worker_command_falls_back_to_placeholder_under_rollback_flag(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP-01D PR1: placeholder лишається rollback-шляхом за `COLLECTOR_WORKER_PLACEHOLDER=1`."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("COLLECTOR_WORKER_PLACEHOLDER", "1")
    monkeypatch.setattr(cli, "placeholder_process", lambda n, o: calls.append((n, o)))
    result = runner.invoke(app, ["worker", role])
    assert result.exit_code == 0, result.output
    assert calls == [(f"worker.{role}", "WP-01D")]


def test_scheduler_command_falls_back_to_placeholder_under_rollback_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("COLLECTOR_WORKER_PLACEHOLDER", "1")
    monkeypatch.setattr(cli, "placeholder_process", lambda n, o: calls.append((n, o)))
    result = runner.invoke(app, ["scheduler"])
    assert result.exit_code == 0, result.output
    assert calls == [("scheduler", "WP-01D")]


# --- api --------------------------------------------------------------------------------------


def test_api_command_runs_uvicorn_with_health_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    captured: dict[str, Any] = {}

    def fake_run(target: str, **kwargs: Any) -> None:
        captured["target"] = target
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("COLLECTOR_API_HOST", "127.0.0.1")
    monkeypatch.setenv("COLLECTOR_API_PORT", "18000")
    result = runner.invoke(app, ["api"])
    assert result.exit_code == 0, result.output
    assert captured["target"] == "collector.api.health:create_app"
    assert captured["factory"] is True
    assert (captured["host"], captured["port"]) == ("127.0.0.1", 18000)
    assert captured["log_config"] is None, "логи uvicorn — через structlog"
