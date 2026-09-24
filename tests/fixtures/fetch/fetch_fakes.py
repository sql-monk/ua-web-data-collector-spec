"""Fakes для тестів fetch core (WP-02). Provenance: synthetic, згенеровано тестами в репозиторії.

- `FakeResolver` — DNS без мережі; послідовність відповідей на host (DNS rebinding), лічильник
  викликів;
- `FakeNetwork` — `httpcore.AsyncNetworkBackend`, що **не створює сокетів**: записує кожен
  `connect_tcp(ip, port)` і TLS SNI, а відповіді віддає зі скриптів (сирі HTTP/1.1 bytes, які
  розбирає справжній h11 у httpcore) — так перевіряється весь стек pinning без loopback, і
  тести однаково поводяться на Windows і Linux (`--disable-socket` блокує навіть loopback);
- `FakePermits` — `OriginPermits` у пам'яті з журналом acquire/release/block;
- `FakeClock` — монотонний годинник, який рухає сам тест.
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpcore

from collector.fetch.permits import Denied, Permit

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "93.184.216.35"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeResolver:
    """`answers[host]` — або список адрес (завжди той самий), або список списків (по виклику)."""

    def __init__(self, answers: dict[str, Sequence[str] | Sequence[Sequence[str]]]) -> None:
        self._answers = answers
        self.calls: list[str] = []

    async def resolve(self, host: str, port: int) -> Sequence[str]:
        self.calls.append(host)
        answer = self._answers.get(host)
        if answer is None:
            raise OSError(f"fake NXDOMAIN {host}")
        if answer and not isinstance(answer[0], str):
            index = min(self.calls.count(host), len(answer)) - 1
            return list(answer[index])
        return list(answer)  # type: ignore[arg-type]


@dataclass
class FakeRequest:
    method: str
    target: str
    headers: dict[str, str]


Responder = Callable[[FakeRequest], Iterable[bytes]]


def http_response(
    status: int,
    body: bytes | None = b"",
    headers: dict[str, str] | None = None,
    *,
    chunks: Iterable[bytes] | None = None,
    reason: str = "X",
) -> Iterator[bytes]:
    """Сира HTTP/1.1 відповідь; `chunks` — тіло без Content-Length (до EOF), лениво."""
    lines = [f"HTTP/1.1 {status} {reason}", "Connection: close"]
    for key, value in (headers or {}).items():
        lines.append(f"{key}: {value}")
    if chunks is None and body is not None:
        lines.append(f"Content-Length: {len(body)}")
    yield ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
    if chunks is not None:
        yield from chunks
    elif body:
        yield body


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self, network: FakeNetwork, responder: Responder) -> None:
        self._network = network
        self._responder = responder
        self._buffer = b""
        self._out: deque[bytes] = deque()
        self._offset = 0
        self._source: Iterator[bytes] | None = None
        self.closed = False

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._buffer += buffer
        if self._source is None and b"\r\n\r\n" in self._buffer:
            head = self._buffer.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
            method, target, _ = head[0].split(" ", 2)
            headers: dict[str, str] = {}
            for line in head[1:]:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            request = FakeRequest(method, target, headers)
            self._network.requests.append(request)
            self._source = iter(self._responder(request))

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if not self._out and self._source is not None:
            chunk = next(self._source, None)
            if chunk is None:
                return b""
            self._network.chunks_served += 1
            self._out.append(chunk)
        if not self._out:
            return b""
        view = memoryview(self._out[0])[self._offset : self._offset + max_bytes]
        self._offset += len(view)
        if self._offset >= len(self._out[0]):
            self._out.popleft()
            self._offset = 0
        return bytes(view)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: Any,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._network.sni.append(server_hostname)
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class FakeNetwork(httpcore.AsyncNetworkBackend):
    """Network backend без сокетів: `routes[(ip, port)]` → responder."""

    def __init__(self, routes: dict[tuple[str, int], Responder] | None = None) -> None:
        self.routes: dict[tuple[str, int], Responder] = dict(routes or {})
        self.connects: list[tuple[str, int]] = []
        self.requests: list[FakeRequest] = []
        self.sni: list[str | None] = []
        self.chunks_served = 0

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connects.append((host, port))
        responder = self.routes.get((host, port))
        if responder is None:
            raise httpcore.ConnectError(f"fake: немає маршруту до {host}:{port}")
        return FakeStream(self, responder)

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Any = None
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("fake: unix socket")

    async def sleep(self, seconds: float) -> None:
        return None


def static(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> Responder:
    return lambda request: http_response(status, body, headers)


@dataclass
class FakePermits:
    """`OriginPermits` у пам'яті. `known=None` — bucket є для будь-якого origin."""

    known: set[str] | None = None
    deny: dict[str, Denied] = field(default_factory=dict)
    acquired: list[str] = field(default_factory=list)
    released: list[Permit] = field(default_factory=list)
    blocks: list[tuple[str, datetime, str]] = field(default_factory=list)
    live: dict[uuid.UUID, Permit] = field(default_factory=dict)
    lease_seconds: int = 90

    async def acquire(self, origin: str, job_id: uuid.UUID | None) -> Permit | Denied:
        if self.known is not None and origin not in self.known:
            return Denied("unknown_origin")
        if origin in self.deny:
            return self.deny[origin]
        permit = Permit(uuid.uuid4(), origin, T0 + timedelta(seconds=self.lease_seconds))
        self.acquired.append(origin)
        self.live[permit.permit_id] = permit
        return permit

    async def release(self, permit: Permit) -> None:
        self.released.append(permit)
        self.live.pop(permit.permit_id, None)

    async def block(self, origin: str, until: datetime, reason: str) -> None:
        self.blocks.append((origin, until, reason))
