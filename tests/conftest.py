"""Спільна конфігурація pytest для всіх рівнів тестів (§16.1).

Політика мережі (pytest-socket, `--disable-socket --allow-unix-socket` у pyproject):

- звичайні (unit/contract) тести: socket заборонений повністю на POSIX; на Windows
  дозволений лише loopback, бо asyncio там емулює `socket.socketpair()` через
  AF_INET 127.0.0.1 і без цього не створюється жоден event loop;
- `integration`/`e2e`: дозволений лише loopback (127.0.0.1, ::1) — локальні
  PostgreSQL/MongoDB/MinIO; будь-який інший host кидає `SocketConnectBlockedError`;
- `live`: socket увімкнено; такі тести запускаються лише явно (`-m live`) з дозволу
  користувача і ніколи не входять у `pytest -m "not live"`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

import pytest

LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "::1")
IS_WINDOWS = sys.platform == "win32"


def _is_loopback_level(item: pytest.Item) -> bool:
    return (
        item.get_closest_marker("integration") is not None
        or item.get_closest_marker("e2e") is not None
    )


def pytest_collection_modifyitems(config: pytest.Config, items: Iterable[pytest.Item]) -> None:
    """Прив'язати політику мережі pytest-socket до маркерів рівнів тестів."""
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(pytest.mark.enable_socket)
        elif _is_loopback_level(item) or IS_WINDOWS:
            item.add_marker(pytest.mark.allow_hosts(list(LOOPBACK_HOSTS)))
