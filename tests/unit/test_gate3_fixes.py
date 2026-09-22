"""Регресійні тести на знахідки gate 3 WP-00 PR2 (код-рев'ю CR-4/5/9/10, security L-4).

- `ensure_mongo_replica_set`: гонка `AlreadyInitialized` (23) → успіх без initiate; transient
  `AutoReconnect`/`NotPrimaryError` під час election → повтор до deadline;
- `db ensure-mongo`: клієнт має connect/socket timeouts;
- `placeholder_process` відновлює попередні signal handlers;
- `http.client.HTTPException` у probe → 503 з JSON, а не 500; `detail` без тексту винятку.
"""

from __future__ import annotations

import http.client
import signal
import threading
import urllib.request
from typing import Any

import pymongo
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect, OperationFailure
from typer.testing import CliRunner

from collector.api import health
from collector.api.health import HEALTH_PATH, ComponentStatus, check_minio, create_app
from collector.cli import (
    MONGO_ALREADY_INITIALIZED,
    MONGO_CLIENT_TIMEOUT_MS,
    MONGO_NOT_YET_INITIALIZED,
    app,
    ensure_mongo_replica_set,
    placeholder_process,
)

runner = CliRunner()


class RacingAdmin:
    """replSetGetStatus → 94, replSetInitiate → 23 (хтось встиг), hello → primary rs0."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.commands.append(name)
        if name == "replSetGetStatus":
            raise OperationFailure("no replset config", code=MONGO_NOT_YET_INITIALIZED)
        if name == "replSetInitiate":
            raise OperationFailure("already initialized", code=MONGO_ALREADY_INITIALIZED)
        if name == "hello":
            return {"isWritablePrimary": True, "setName": "rs0"}
        raise AssertionError(name)


class FlappingAdmin:
    """RS існує; перші два `hello` кидають transient помилки, третій — primary."""

    def __init__(self) -> None:
        self.hello_calls = 0

    def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if name == "replSetGetStatus":
            return {"set": "rs0"}
        if name == "hello":
            self.hello_calls += 1
            if self.hello_calls == 1:
                raise AutoReconnect("connection reset during election")
            if self.hello_calls == 2:
                raise pymongo.errors.NotPrimaryError("not primary yet")
            return {"isWritablePrimary": True, "setName": "rs0"}
        raise AssertionError(name)


class Client:
    def __init__(self, admin: Any) -> None:
        self.admin = admin

    def close(self) -> None:
        return None


def test_ensure_replica_set_tolerates_already_initialized_race() -> None:
    client = Client(RacingAdmin())
    initiated = ensure_mongo_replica_set(
        client,  # type: ignore[arg-type]  # фейк замість MongoClient
        replica_set="rs0",
        member_host="mongo:27017",
        wait_seconds=2,
    )
    assert initiated is False
    assert client.admin.commands[:2] == ["replSetGetStatus", "replSetInitiate"]


def test_ensure_replica_set_survives_transient_errors_during_election() -> None:
    client = Client(FlappingAdmin())
    initiated = ensure_mongo_replica_set(
        client,  # type: ignore[arg-type]  # фейк замість MongoClient
        replica_set="rs0",
        member_host="mongo:27017",
        wait_seconds=5,
    )
    assert initiated is False
    assert client.admin.hello_calls == 3


def test_db_ensure_mongo_sets_network_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Recording(Client):
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            super().__init__(FlappingAdmin())

    monkeypatch.setattr(pymongo, "MongoClient", Recording)
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_USERNAME", "root")
    monkeypatch.setenv("COLLECTOR_MONGO_ROOT_PASSWORD", "x")
    result = runner.invoke(app, ["db", "ensure-mongo"])
    assert result.exit_code == 0, result.output
    assert captured["connectTimeoutMS"] == MONGO_CLIENT_TIMEOUT_MS
    assert captured["socketTimeoutMS"] == MONGO_CLIENT_TIMEOUT_MS
    assert captured["serverSelectionTimeoutMS"] > 0


def test_placeholder_process_restores_signal_handlers() -> None:
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    stop = threading.Event()
    threading.Timer(0.1, stop.set).start()
    placeholder_process("worker.parse", "WP-01D", stop=stop, heartbeat_seconds=0.02)
    after = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    assert after == before, "handlers pytest/embedding мають бути відновлені"


def test_minio_probe_http_exception_is_503_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad_status_line(*args: Any, **kwargs: Any) -> Any:
        raise http.client.BadStatusLine("not http")

    monkeypatch.setattr(urllib.request, "urlopen", bad_status_line)
    status = check_minio({"COLLECTOR_MINIO_URL": "http://minio:9000"})
    assert not status.ok
    assert status.detail == "BadStatusLine", "лише клас, без тексту (SEC L-4)"

    ok = ComponentStatus(name="postgres", ok=True, latency_ms=0.1, detail="ok")
    monkeypatch.setattr(
        health,
        "CHECKS",
        {
            "postgres": lambda *a, **k: ok,
            "mongo": lambda *a, **k: ComponentStatus(
                name="mongo", ok=True, latency_ms=0.1, detail="ok"
            ),
            "minio": check_minio,
        },
    )
    response = TestClient(create_app()).get(HEALTH_PATH)
    assert response.status_code == 503
    assert response.json()["components"][2]["detail"] == "BadStatusLine"
