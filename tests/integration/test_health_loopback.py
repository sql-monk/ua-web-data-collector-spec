"""Реальні TCP/HTTP-перевірки health проти loopback-заглушок (WP-00 PR2; маркер integration).

Мережа дозволена лише 127.0.0.1/::1 (tests/conftest.py): постгрес імітує TCP listener,
MinIO — `http.server` з `/minio/health/live`. Справжні PostgreSQL/MongoDB/MinIO — рівень 14
(`docker compose up --wait`, CI job `docker`).
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from collector.api.health import check_minio, check_postgres

pytestmark = pytest.mark.integration


@pytest.fixture
def tcp_listener() -> Iterator[int]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield int(server.getsockname()[1])
    finally:
        server.close()


@pytest.fixture
def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _MinioLive(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — API http.server
        self.send_response(200 if self.path == "/minio/health/live" else 404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — API http.server
        return


@pytest.fixture
def minio_stub() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _MinioLive)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_check_postgres_tcp_ok(tcp_listener: int) -> None:
    status = check_postgres(
        {"COLLECTOR_POSTGRES_HOST": "127.0.0.1", "COLLECTOR_POSTGRES_PORT": str(tcp_listener)}
    )
    assert status.ok, status.detail
    assert "WP-01A" in status.detail, "чесно: лише TCP, без SQL"


def test_check_postgres_tcp_refused(free_port: int) -> None:
    status = check_postgres(
        {"COLLECTOR_POSTGRES_HOST": "127.0.0.1", "COLLECTOR_POSTGRES_PORT": str(free_port)},
        timeout=1.0,
    )
    assert not status.ok
    assert status.detail


def test_check_minio_live_ok(minio_stub: str) -> None:
    status = check_minio({"COLLECTOR_MINIO_URL": minio_stub})
    assert status.ok, status.detail


def test_check_minio_404_is_not_ok(minio_stub: str) -> None:
    status = check_minio({"COLLECTOR_MINIO_URL": minio_stub + "/wrong"})
    assert not status.ok
    assert "404" in status.detail


def test_check_minio_connection_refused(free_port: int) -> None:
    status = check_minio({"COLLECTOR_MINIO_URL": f"http://127.0.0.1:{free_port}"}, timeout=1.0)
    assert not status.ok
