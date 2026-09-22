"""Спільна конфігурація pytest для всіх рівнів тестів (§16.1).

Політика мережі (pytest-socket, `--disable-socket --allow-unix-socket` у pyproject):

- звичайні (unit/contract) тести: socket заборонений повністю на POSIX; на Windows
  дозволений лише loopback, бо asyncio там емулює `socket.socketpair()` через
  AF_INET 127.0.0.1 і без цього не створюється жоден event loop;
- `integration`/`e2e`: дозволений лише loopback (127.0.0.1, ::1) — локальні
  PostgreSQL/MongoDB/MinIO; будь-який інший host кидає `SocketConnectBlockedError`;
- `live`: socket увімкнено; такі тести запускаються лише явно (`-m live`) з дозволу
  користувача і ніколи не входять у `pytest -m "not live"`.

Event loop для async-тестів — завжди `SelectorEventLoop`: hook `pytest_asyncio_loop_factories`
для тестів pytest-asyncio і, на Windows, `WindowsSelectorEventLoopPolicy` у `pytest_configure`
для `asyncio.run(...)` у sync-тестах/helpers (напр. CLI-команди під `CliRunner`).
На Windows default `ProactorEventLoop` з'єднується через `_overlapped.ConnectEx`, минаючи
`socket.connect`, тому `asyncio.open_connection`/`httpx.AsyncClient` обходили б блокування
у режимі allow-hosts. Selector loop іде через `sock_connect` → `socket.connect`, і асинхронний
шлях блокується так само, як синхронний. Межі: (а) на Windows selector loop не підтримує
asyncio subprocess/pipes — тестам, що цього потребують, робити окремий loop явно;
(б) код, що ЯВНО створює `asyncio.ProactorEventLoop()` / `WindowsProactorEventLoopPolicy()`,
policy не покриває — на Windows такий тест обійде блок мережі; у CI (Linux) діє повний
`disable_socket`, тож витік лишається локальним.

Відомі межі pytest-socket у режимі allow-hosts (integration/e2e; на Windows — усі тести):
не перехоплюються `socket.connect_ex`, UDP `sendto`, `socket.getaddrinfo` (DNS-резолв імен)
і subprocess. У CI (Linux) для звичайних тестів діє повне `disable_socket`.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterable, Mapping

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


def pytest_configure(config: pytest.Config) -> None:
    """Windows: selector policy і для `asyncio.run()` поза pytest-asyncio (див. docstring).

    Policy API asyncio deprecated з Python 3.14; проєкт pinned `<3.14` (pyproject), при
    переході на 3.14 замінити на `asyncio.Runner(loop_factory=...)` у місцях виклику.
    """
    if IS_WINDOWS:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """Один loop factory для всіх async-тестів: SelectorEventLoop (див. docstring модуля)."""
    return {"selector": asyncio.SelectorEventLoop}
