"""DNS pinning (§13, ADR-0008): TCP лише на IP, яку щойно перевірив SSRF-класифікатор.

`PinnedNetworkBackend` — власний `httpcore.AsyncNetworkBackend`: `connect_tcp(host, port)` не
резолвить ім'я, а бере адресу з pin-таблиці, заповненої `SafeFetcher` після
`ssrf.resolve_pinned` для **цього** hop-а; host без pin → `ConnectError`, а не тихий резолв.
TLS SNI і `Host` лишаються оригінальним ім'ям (httpcore передає `server_hostname=origin.host`).
Unix sockets заборонені.

`PinnedTransport` — `httpx.AsyncHTTPTransport` з `trust_env=False` і пулом httpcore на цьому
backend. httpx 0.28 не має параметра `network_backend`, тому пул замінюється після
`super().__init__()` тими самими параметрами, що будує httpx (без proxy — proxy з env
guard-у не обходять); `tests/unit/fetch/test_transport.py` падає, якщо оновлення httpx це зламає.
"""

from __future__ import annotations

import functools
import ssl
from collections.abc import Iterable

import httpcore
import httpx

from collector.fetch.ssrf import IPAddress


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner if inner is not None else httpcore.AnyIOBackend()
        self._pins: dict[tuple[str, int], str] = {}

    def pin(self, host: str, port: int, address: IPAddress) -> None:
        self._pins[(host.lower(), port)] = str(address)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        address = self._pins.get((host.lower().strip("[]"), port))
        if address is None:
            msg = f"немає перевіреної адреси для {host}:{port} — connect без pinning заборонено"
            raise httpcore.ConnectError(msg)
        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets заборонені для fetch")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


@functools.cache
def _ssl_context() -> ssl.SSLContext:
    """Спільний TLS-контекст (CA certifi, як у httpx; без `SSL_CERT_*` з env)."""
    return httpx.create_ssl_context(trust_env=False)


class PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: PinnedNetworkBackend) -> None:
        context = _ssl_context()
        super().__init__(verify=context, trust_env=False, http1=True, http2=False, retries=0)
        self.backend = backend
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            max_connections=10,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=backend,
        )
