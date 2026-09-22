"""Adversarial-перевірки блокування мережі (§8: «мережа заборонена у звичайних tests»).

Інваріант, який мають задовольняти ВСІ платформи для звичайного (unit/contract) тесту:
будь-який спосіб установити TCP-з'єднання з не-loopback host кидає виняток pytest-socket
ДО того, як пакет покине host. Перевіряються не лише `socket.create_connection`, а й
реальні клієнтські шляхи, якими користуватиметься код проєкту: `asyncio.open_connection`
(ProactorEventLoop на Windows з'єднується через `_overlapped.ConnectEx`, а не через
`socket.connect`), `httpx.AsyncClient` (anyio → asyncio), `httpx.Client` і `urllib`.

TEST-NET-1 (RFC 5737, 192.0.2.0/24) немаршрутизована; DNS не потрібен.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.request

import httpx
import pytest
import pytest_socket

NON_LOOPBACK_HOST = "192.0.2.1"
NON_LOOPBACK_URL = f"http://{NON_LOOPBACK_HOST}/"
BLOCKED_ERRORS = (pytest_socket.SocketBlockedError, pytest_socket.SocketConnectBlockedError)
# Достатньо, щоб pytest-socket спрацював миттєво; замало, щоб «дочекатися» реального SYN.
TIMEOUT_SECONDS = 0.5


def _assert_blocked(exc: BaseException) -> None:
    """Виняток є блокуванням pytest-socket або групою (anyio TaskGroup), що його містить."""
    if isinstance(exc, BaseExceptionGroup):
        assert exc.subgroup(BLOCKED_ERRORS) is not None, repr(exc)
    else:
        assert isinstance(exc, BLOCKED_ERRORS), repr(exc)


async def test_asyncio_open_connection_to_non_loopback_is_blocked() -> None:
    """`asyncio.open_connection` до не-loopback host кидає виняток pytest-socket.

    На Windows ProactorEventLoop минає `socket.socket.connect` (використовує ConnectEx),
    тому цей тест доводить, що політика мережі покриває і асинхронний шлях.
    """
    with pytest.raises(BaseException) as excinfo:  # noqa: B017 — тип перевіряється нижче
        await asyncio.wait_for(
            asyncio.open_connection(NON_LOOPBACK_HOST, 80), timeout=TIMEOUT_SECONDS
        )
    _assert_blocked(excinfo.value)


async def test_httpx_async_client_to_non_loopback_is_blocked() -> None:
    """`httpx.AsyncClient` (основний HTTP-клієнт §8) не може дістатися не-loopback host."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        with pytest.raises(BaseException) as excinfo:  # noqa: B017
            await client.get(NON_LOOPBACK_URL)
    _assert_blocked(excinfo.value)


def test_httpx_sync_client_to_non_loopback_is_blocked() -> None:
    with pytest.raises(BLOCKED_ERRORS):
        httpx.Client(timeout=TIMEOUT_SECONDS).get(NON_LOOPBACK_URL)


def test_urllib_to_non_loopback_is_blocked() -> None:
    with pytest.raises(BLOCKED_ERRORS):
        urllib.request.urlopen(NON_LOOPBACK_URL, timeout=TIMEOUT_SECONDS)


def test_raw_socket_connect_to_non_loopback_is_blocked() -> None:
    """Низькорівневий `socket.connect` (без create_connection) теж заблокований."""
    with pytest.raises(BLOCKED_ERRORS):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with sock:
            sock.settimeout(TIMEOUT_SECONDS)
            sock.connect((NON_LOOPBACK_HOST, 80))


@pytest.mark.integration
async def test_asyncio_loopback_connection_still_works() -> None:
    """Loopback-політика integration/e2e не ламає локальні asyncio-з'єднання."""
    server = await asyncio.start_server(lambda r, w: w.close(), host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=TIMEOUT_SECONDS
        )
        writer.close()
        await writer.wait_closed()
        assert reader is not None
