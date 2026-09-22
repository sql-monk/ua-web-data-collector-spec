"""Доказ, що звичайний тест не може відкрити мережеве з'єднання (pytest-socket).

Інваріант на всіх платформах: connect до будь-якого не-loopback host кидає виняток.
На POSIX додатково заборонено саме створення socket (див. tests/conftest.py).
"""

from __future__ import annotations

import asyncio
import socket
import sys

import pytest
import pytest_socket

# TEST-NET-1 (RFC 5737): гарантовано немаршрутизована адреса, DNS не потрібен.
NON_LOOPBACK_HOST = "192.0.2.1"
BLOCKED_ERRORS = (pytest_socket.SocketBlockedError, pytest_socket.SocketConnectBlockedError)
BLOCK_WARNING = "A test tried to use socket"


def test_connect_to_non_loopback_host_raises_in_plain_test() -> None:
    with pytest.warns(UserWarning, match=BLOCK_WARNING), pytest.raises(BLOCKED_ERRORS):
        socket.create_connection((NON_LOOPBACK_HOST, 80), timeout=0.1)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows: loopback потрібен asyncio")
def test_socket_creation_is_blocked_on_posix() -> None:
    with (
        pytest.warns(UserWarning, match=BLOCK_WARNING),
        pytest.raises(pytest_socket.SocketBlockedError),
    ):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


@pytest.mark.integration
def test_integration_marker_allows_only_loopback() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass

    with (
        pytest.warns(UserWarning, match=BLOCK_WARNING),
        pytest.raises(pytest_socket.SocketConnectBlockedError),
    ):
        socket.create_connection((NON_LOOPBACK_HOST, 80), timeout=0.1)


async def test_asyncio_event_loop_works_under_network_block() -> None:
    """Event loop створюється і працює попри блокування мережі (Windows socketpair)."""
    await asyncio.sleep(0)
    assert asyncio.get_running_loop().is_running()
