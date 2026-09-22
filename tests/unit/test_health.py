"""Стаб health `GET /api/v1/health/components` і перевірки компонентів (WP-00 PR2, §7.5).

Без мережі: PyMongo підміняється фейком, TCP/HTTP-перевірки — через monkeypatch `CHECKS`;
реальні loopback-перевірки — `tests/integration/test_health_loopback.py`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from collector.api import health
from collector.api.health import (
    COMPONENT_NAMES,
    HEALTH_PATH,
    ComponentStatus,
    check_components,
    check_mongo,
    create_app,
    env_or_file,
    main,
    minio_health_url,
    mongo_address,
    postgres_address,
)


class FakeAdmin:
    def __init__(self, hello: dict[str, Any] | Exception) -> None:
        self._hello = hello

    def command(self, name: str, *args: object, **kwargs: object) -> dict[str, Any]:
        assert name == "hello"
        if isinstance(self._hello, Exception):
            raise self._hello
        return self._hello


class FakeMongoClient:
    instances: list[FakeMongoClient] = []
    hello: dict[str, Any] | Exception = {"isWritablePrimary": True, "setName": "rs0"}

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.admin = FakeAdmin(type(self).hello)
        type(self).instances.append(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_mongo(monkeypatch: pytest.MonkeyPatch) -> type[FakeMongoClient]:
    FakeMongoClient.instances = []
    FakeMongoClient.hello = {"isWritablePrimary": True, "setName": "rs0"}
    monkeypatch.setattr(health, "MongoClient", FakeMongoClient)
    return FakeMongoClient


def _ok(name: health.ComponentName) -> Callable[..., ComponentStatus]:
    def check(environ: Mapping[str, str] | None = None, *, timeout: float = 1.0) -> ComponentStatus:
        return ComponentStatus(name=name, ok=True, latency_ms=0.1, detail="ok")

    return check


def _fail(name: health.ComponentName) -> Callable[..., ComponentStatus]:
    def check(environ: Mapping[str, str] | None = None, *, timeout: float = 1.0) -> ComponentStatus:
        return ComponentStatus(name=name, ok=False, latency_ms=0.1, detail="down")

    return check


@pytest.fixture
def all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "CHECKS", {name: _ok(name) for name in COMPONENT_NAMES})


@pytest.fixture
def mongo_down(monkeypatch: pytest.MonkeyPatch) -> None:
    checks = {name: _ok(name) for name in COMPONENT_NAMES}
    checks["mongo"] = _fail("mongo")
    monkeypatch.setattr(health, "CHECKS", checks)


# --- env / secrets ----------------------------------------------------------------------------


def test_env_or_file_prefers_env_then_file(tmp_path: Path) -> None:
    secret = tmp_path / "pw"
    secret.write_text("from-file\n", encoding="utf-8")
    assert env_or_file("X", {"X": "from-env", "X_FILE": str(secret)}) == "from-env"
    assert env_or_file("X", {"X_FILE": str(secret)}) == "from-file"
    assert env_or_file("X", {}) is None
    assert env_or_file("X", {"X": ""}) is None


def test_addresses_default_to_compose_service_names() -> None:
    assert postgres_address({}) == ("postgres", 5432)
    assert mongo_address({}) == ("mongo", 27017)
    assert minio_health_url({}) == "http://minio:9000/minio/health/live"
    env = {
        "COLLECTOR_POSTGRES_HOST": "127.0.0.1",
        "COLLECTOR_POSTGRES_PORT": "15432",
        "COLLECTOR_MONGO_HOST": "::1",
        "COLLECTOR_MONGO_PORT": "1",
        "COLLECTOR_MINIO_URL": "http://127.0.0.1:9/",
    }
    assert postgres_address(env) == ("127.0.0.1", 15432)
    assert mongo_address(env) == ("::1", 1)
    assert minio_health_url(env) == "http://127.0.0.1:9/minio/health/live"


# --- mongo `hello` ----------------------------------------------------------------------------


def test_check_mongo_ok_on_writable_primary(fake_mongo: type[FakeMongoClient]) -> None:
    status = check_mongo({}, timeout=1.5)
    assert status.ok and status.name == "mongo"
    assert status.detail == "writable primary of replica set"  # без назви RS/host (SEC L-4)
    client = fake_mongo.instances[0]
    assert client.closed
    assert client.kwargs["directConnection"] is True
    assert client.kwargs["serverSelectionTimeoutMS"] == 1500
    assert "username" not in client.kwargs, "health не потребує credentials (§13)"


def test_check_mongo_not_ok_before_replica_set_init(fake_mongo: type[FakeMongoClient]) -> None:
    fake_mongo.hello = {"isWritablePrimary": False, "setName": None}
    status = check_mongo({})
    assert not status.ok
    assert status.detail == "not_primary"
    assert fake_mongo.instances[0].closed


def test_check_mongo_not_ok_on_driver_error(fake_mongo: type[FakeMongoClient]) -> None:
    from pymongo.errors import ServerSelectionTimeoutError

    fake_mongo.hello = ServerSelectionTimeoutError("no servers")
    status = check_mongo({})
    assert not status.ok
    assert status.detail == "ServerSelectionTimeoutError", "клас без тексту (SEC L-4)"


# --- report / app / main ----------------------------------------------------------------------


def test_check_components_ready_only_if_all_ok(all_ok: None) -> None:
    report = check_components()
    assert report.ready
    assert tuple(c.name for c in report.components) == COMPONENT_NAMES
    assert report.version.schema_version


def test_check_components_not_ready_if_any_failed(mongo_down: None) -> None:
    report = check_components()
    assert not report.ready
    assert {c.name: c.ok for c in report.components} == {
        "postgres": True,
        "mongo": False,
        "minio": True,
    }


def test_health_endpoint_200_when_ready(all_ok: None) -> None:
    client = TestClient(create_app())
    response = client.get(HEALTH_PATH)
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert [c["name"] for c in body["components"]] == list(COMPONENT_NAMES)
    assert set(body["version"]) == {"package_version", "git_sha", "schema_version"}


def test_health_endpoint_503_when_not_ready(mongo_down: None) -> None:
    client = TestClient(create_app())
    response = client.get(HEALTH_PATH)
    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_stub_app_exposes_only_health_route(all_ok: None) -> None:
    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert paths == {HEALTH_PATH}, "стаб WP-00 має лише health; решта — WP-11A"
    assert TestClient(app).get("/docs").status_code == 404


def test_main_exit_codes_and_component_filter(
    all_ok: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["postgres", "minio"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in out] == ["postgres", "minio"]
    assert main(["nope"]) == 2


def test_main_returns_1_when_component_down(mongo_down: None) -> None:
    assert main([]) == 1
    assert main(["postgres"]) == 0
