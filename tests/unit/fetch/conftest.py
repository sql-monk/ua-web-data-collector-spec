"""Unit-фікстури fetch core (WP-02): жодної мережі, жодного реального DNS.

pytest-socket не перехоплює `getaddrinfo` (докстрінг `tests/conftest.py`), тому autouse-фікстура
підміняє `socket.getaddrinfo` і `loop.getaddrinfo` на функцію, що кидає: тест, який випадково
пішов би в системний резолвер, падає, а не резолвить непомітно. DNS — лише `FakeResolver`,
TCP — лише `FakeNetwork` (без сокетів), HTTP-семантика — `FakeNetwork` або `respx`.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "fetch"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))

from fetch_fakes import T0, FakeClock, FakeNetwork, FakePermits, FakeResolver  # noqa: E402

from collector.fetch.client import SafeFetcher  # noqa: E402
from collector.fetch.config import FetchConfig  # noqa: E402
from collector.fetch.guard import RouteGuard  # noqa: E402

USER_AGENT = "UAWebDataCollector/0.1.0-test"


class RealDnsForbiddenError(AssertionError):
    pass


def _forbidden_dns(*args: Any, **kwargs: Any) -> Any:
    raise RealDnsForbiddenError(f"реальний DNS у unit-тесті fetch заборонено: {args[:2]!r}")


async def _forbidden_loop_dns(self: Any, *args: Any, **kwargs: Any) -> Any:
    _forbidden_dns(*args)


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _forbidden_dns)
    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", _forbidden_loop_dns)


@pytest.fixture
def network() -> FakeNetwork:
    return FakeNetwork()


@pytest.fixture
def permits() -> FakePermits:
    return FakePermits()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


FetcherFactory = Callable[..., SafeFetcher]


@pytest.fixture
def make_fetcher(network: FakeNetwork, permits: FakePermits, clock: FakeClock) -> FetcherFactory:
    """`make_fetcher(resolver, **config_overrides, guard=...)` → `SafeFetcher` на fakes."""

    def make(
        resolver: FakeResolver, *, guard: RouteGuard | None = None, **overrides: Any
    ) -> SafeFetcher:
        config = FetchConfig(user_agent=USER_AGENT, **overrides)
        return SafeFetcher(
            config=config,
            permits=permits,
            guard=guard,
            resolver=resolver,
            network_backend=network,
            clock=clock,
            now=lambda: T0,
        )

    return make
