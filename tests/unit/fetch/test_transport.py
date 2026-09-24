"""DNS pinning на рівні transport (ADR-0008): пул httpcore справді на нашому backend-і,
connect без pin неможливий, unix sockets заборонені. Падає, якщо оновлення httpx змінить
внутрішню будову `AsyncHTTPTransport` так, що підміна пулу перестане діяти."""

from __future__ import annotations

from ipaddress import ip_address

import httpcore
import pytest
from fetch_fakes import PUBLIC_IP, FakeNetwork

from collector.fetch.transport import PinnedNetworkBackend, PinnedTransport


def test_transport_pool_uses_pinned_backend() -> None:
    backend = PinnedNetworkBackend(FakeNetwork())
    transport = PinnedTransport(backend)
    pool = transport._pool  # noqa: SLF001 — саме цей інваріант і перевіряється
    assert isinstance(pool, httpcore.AsyncConnectionPool)
    assert pool._network_backend is backend  # noqa: SLF001


async def test_connect_without_pin_is_refused() -> None:
    inner = FakeNetwork()
    backend = PinnedNetworkBackend(inner)
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.org", 443)
    assert inner.connects == []


async def test_connect_goes_to_pinned_address() -> None:
    inner = FakeNetwork({(PUBLIC_IP, 443): lambda r: iter(())})
    backend = PinnedNetworkBackend(inner)
    backend.pin("Example.ORG", 443, ip_address(PUBLIC_IP))
    await backend.connect_tcp("example.org", 443)
    assert inner.connects == [(PUBLIC_IP, 443)]


async def test_pin_is_per_port() -> None:
    inner = FakeNetwork()
    backend = PinnedNetworkBackend(inner)
    backend.pin("example.org", 443, ip_address(PUBLIC_IP))
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.org", 8443)


async def test_unix_socket_is_refused() -> None:
    backend = PinnedNetworkBackend(FakeNetwork())
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_unix_socket("/var/run/docker.sock")
