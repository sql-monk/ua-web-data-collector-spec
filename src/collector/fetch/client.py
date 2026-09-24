"""SSRF-захищений HTTP fetch (§7.1 Fetcher, §10 кроки 4–6, §13; ADR-0008 — HTTPX).

Один `fetch()` = один логічний запит із ручними redirect-ами (≤ 5 hop). Кожен hop:
Route Guard → один DNS-резолв + SSRF-перевірка всіх адрес → pin IP у network backend →
permit origin-а (новий origin — новий permit; bucket відсутній → `redirect_origin_unknown` без
запиту) → запит. Downgrade `https→http` — `redirect_downgrade`. Total timeout 60 с охоплює всі
hop-и і body. Permits звільняються у `finally` (успіх, помилка, cancel).

Anonymous-only (Q-007): стабільний `User-Agent`, жодних cookies (jar відхиляє все, запит
будується без merge cookies клієнта), жодного `Authorization`, URL з userinfo відхиляється
guard-ом. Логи містять лише redacted URL, origin, IP і статус — без заголовків.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import UUID

import httpcore
import httpx
import structlog

from collector.contracts.enums import ContentAccess, FetchOutcome
from collector.fetch.classify import FetchDecision, classify_error, classify_status
from collector.fetch.config import FetchConfig
from collector.fetch.decoding import (
    Body,
    BodyLimitExceeded,
    BodyLimits,
    UnsupportedEncoding,
    read_body,
)
from collector.fetch.guard import RouteGuard
from collector.fetch.permits import Denied, OriginPermits, Permit
from collector.fetch.ssrf import (
    DnsResolutionError,
    PolicyBlocked,
    Resolver,
    SystemResolver,
    resolve_pinned,
)
from collector.fetch.transport import PinnedNetworkBackend, PinnedTransport
from collector.fetch.urls import normalize_origin
from collector.workers.handlers import redact

RequestKind = Literal["page", "sitemap", "robots", "api"]
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MEDIA_PREFIXES = ("image/", "video/", "audio/")
PERMIT_EXPIRY_MARGIN_SECONDS = 1.0
SAFE_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-encoding",
    "etag",
    "last-modified",
    "retry-after",
    "location",
)
_log = structlog.stdlib.get_logger("collector.fetch")


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """Вхід одного fetch; validators — з попереднього успішного fetch (FR-004, O-7)."""

    url: str
    request_kind: RequestKind = "page"
    job_id: UUID | None = None
    if_none_match: str | None = None
    if_modified_since: str | None = None
    total_timeout: float | None = None  # override лише з manifest (sitemap, §10)


@dataclass(frozen=True, slots=True)
class Hop:
    url: str
    address: str
    status: int


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Результат fetch. `body` — bytes для raw artifact (для sitemap `.gz` — стиснуті)."""

    decision: FetchDecision
    requested_url: str
    final_url: str | None = None
    status: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    decoded_bytes: int = 0
    body_compressed: bool = False
    media_type: str | None = None
    hops: tuple[Hop, ...] = ()
    denied: Denied | None = None

    @property
    def not_modified(self) -> bool:
        return self.decision.not_modified


class _Deadline(Exception):  # noqa: N818 — внутрішній сигнал total timeout
    pass


class _RedirectRefused(Exception):  # noqa: N818 — permanent відмова redirect-у з власним кодом
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


DETACHED_LOCATION = "collector_location"


async def _detach_location(response: httpx.Response) -> None:
    """Забрати `Location` redirect-у до того, як httpx спробує його розпарсити.

    Навіть з `follow_redirects=False` httpx будує `next_request` і на `Location`, який його
    парсер відкидає (`http://0177.0.0.01/`), кидає `RemoteProtocolError` — fetch класифікувався
    б як retryable `network_error` (gate 2, F-3). Location іде в `extensions` і перевіряється
    нашим guard/SSRF як будь-який інший hop.
    """
    location = response.headers.get("location")
    if response.status_code in REDIRECT_STATUSES and location is not None:
        response.extensions = {**response.extensions, DETACHED_LOCATION: location}
        del response.headers["location"]


def _no_cookies() -> CookieJar:
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


class SafeFetcher:
    """Спільний fetch layer; адаптери не створюють власних HTTP-клієнтів (§11)."""

    def __init__(
        self,
        *,
        config: FetchConfig,
        permits: OriginPermits,
        guard: RouteGuard | None = None,
        resolver: Resolver | None = None,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._permits = permits
        self._guard = guard or RouteGuard(allowed_ports=config.allowed_ports)
        self._resolver = resolver or SystemResolver()
        self._network_backend = network_backend
        self._clock = clock
        self._now = now

    async def fetch(self, request: FetchRequest) -> FetchResult:
        held: dict[str, Permit] = {}
        hops: list[Hop] = []
        total = request.total_timeout or self._config.total_timeout
        deadline = self._clock() + total
        backend = PinnedNetworkBackend(self._network_backend)
        timeout = httpx.Timeout(
            self._config.read_timeout, connect=self._config.connect_timeout, pool=5.0
        )
        try:
            async with (
                asyncio.timeout(total),
                httpx.AsyncClient(
                    transport=PinnedTransport(backend),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=timeout,
                    cookies=_no_cookies(),
                    event_hooks={"response": [_detach_location]},
                ) as client,
            ):
                return await self._run(client, backend, request, held, hops, deadline)
        except PolicyBlocked as exc:
            return self._failed(request, hops, classify_error(exc.error_code, policy=True), exc)
        except _RedirectRefused as exc:
            return self._failed(request, hops, classify_error(exc.error_code), exc)
        except DnsResolutionError as exc:
            return self._failed(request, hops, classify_error("dns_error"), exc)
        except (TimeoutError, _Deadline, httpx.TimeoutException) as exc:
            return self._failed(request, hops, classify_error("timeout"), exc)
        except httpx.TransportError as exc:
            return self._failed(request, hops, classify_error("network_error"), exc)
        finally:
            for permit in held.values():
                await self._release(permit)

    async def _run(
        self,
        client: httpx.AsyncClient,
        backend: PinnedNetworkBackend,
        request: FetchRequest,
        held: dict[str, Permit],
        hops: list[Hop],
        deadline: float,
    ) -> FetchResult:
        url = request.url
        for hop in range(self._config.max_redirects + 1):
            self._check_deadline(deadline)
            guarded = self._guard.check(url)
            origin = normalize_origin(guarded.url)
            address = await resolve_pinned(guarded.host, guarded.port, self._resolver)
            backend.pin(guarded.host, guarded.port, address)
            if origin not in held:
                granted = await self._permits.acquire(origin, request.job_id)
                if isinstance(granted, Denied):
                    return self._denied(request, hops, granted, redirected=hop > 0)
                held[origin] = granted
            permit = held[origin]
            # Permit мусить покривати весь залишок logical fetch. Інакше manifest timeout
            # (напр. великий sitemap) переживе lease, інша replica отримає slot і R-53 буде
            # порушено. Відмовляємо до connect; runtime може повторити з довшим fenced lease.
            lease_left = (permit.lease_expires_at - self._now()).total_seconds()
            fetch_left = max(deadline - self._clock(), 0.0)
            if lease_left < fetch_left + PERMIT_EXPIRY_MARGIN_SECONDS:
                return self._failed(request, hops, classify_error("permit_lease_too_short"), None)
            response = await client.send(
                httpx.Request("GET", guarded.url, headers=self._headers(request, hop)),
                stream=True,
            )
            try:
                hops.append(Hop(redact(guarded.url), str(address), response.status_code))
                _log.info(
                    "fetch.hop",
                    origin=origin,
                    hop=hop,
                    address=str(address),
                    status=response.status_code,
                    url=redact(guarded.url),
                )
                location = response.extensions.get(DETACHED_LOCATION)
                if response.status_code not in REDIRECT_STATUSES or not location:
                    return await self._complete(response, request, origin, hops, deadline)
                url = self._next_url(guarded.url, location)
            finally:
                await response.aclose()
        return self._failed(request, hops, classify_error("too_many_redirects"), None)

    def _headers(self, request: FetchRequest, hop: int) -> dict[str, str]:
        headers = {
            "User-Agent": self._config.user_agent,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
        }
        if hop == 0 and request.if_none_match:
            headers["If-None-Match"] = request.if_none_match
        if hop == 0 and request.if_modified_since:
            headers["If-Modified-Since"] = request.if_modified_since
        return headers

    @staticmethod
    def _next_url(current: str, location: str) -> str:
        try:
            joined = urlsplit(urljoin(current, location.strip()))
        except ValueError as exc:
            raise _RedirectRefused("redirect_location_invalid") from exc
        if urlsplit(current).scheme == "https" and joined.scheme.lower() == "http":
            raise _RedirectRefused("redirect_downgrade")
        return urlunsplit(joined._replace(fragment=""))

    async def _complete(
        self,
        response: httpx.Response,
        request: FetchRequest,
        origin: str,
        hops: list[Hop],
        deadline: float,
    ) -> FetchResult:
        status = response.status_code
        media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        # `redact`: Location/ETag можуть нести credentials чи токени в URL (§13).
        headers = {k: redact(v) for k in SAFE_RESPONSE_HEADERS if (v := response.headers.get(k))}

        def result(decision: FetchDecision, body: Body | None = None) -> FetchResult:
            return FetchResult(
                decision=decision,
                requested_url=redact(request.url),
                final_url=hops[-1].url,
                status=status,
                headers=headers,
                body=body.data if body is not None else None,
                decoded_bytes=body.decoded_bytes if body is not None else 0,
                body_compressed=body.stored_compressed if body is not None else False,
                media_type=media_type or None,
                hops=tuple(hops),
            )

        now = self._now()
        if not 200 <= status < 300:
            decision = classify_status(
                status, retry_after=response.headers.get("retry-after"), now=now
            )
            if decision.block_origin and decision.retry_after is not None:
                await self._permits.block(origin, now + decision.retry_after, "http_429")
            return result(decision)
        if media_type.startswith(MEDIA_PREFIXES) and not self._config.media_binaries:
            return result(classify_error("media_binary_skipped"))
        sitemap = request.request_kind == "sitemap"
        limit = self._config.max_sitemap_bytes if sitemap else self._config.max_body_bytes
        declared = response.headers.get("content-length", "")
        if declared.isascii() and declared.isdigit() and int(declared) > limit:
            return result(classify_error("body_too_large"))
        limits = BodyLimits(
            max_bytes=limit,
            max_ratio=self._config.max_decompression_ratio,
            ratio_min_bytes=self._config.ratio_guard_min_bytes,
            sitemap=sitemap,
        )
        try:
            body = await read_body(
                response.aiter_raw(),
                response.headers.get("content-encoding"),
                limits,
                on_chunk=lambda: self._check_deadline(deadline),
            )
        except BodyLimitExceeded as exc:
            _log.warning("fetch.body_rejected", origin=origin, error_code=exc.error_code)
            return result(classify_error(exc.error_code))
        except UnsupportedEncoding:
            return result(classify_error("content_encoding_unsupported"))
        return result(classify_status(status, body_empty=body.decoded_bytes == 0, now=now), body)

    def _check_deadline(self, deadline: float) -> None:
        if self._clock() > deadline:
            raise _Deadline

    def _denied(
        self, request: FetchRequest, hops: list[Hop], denied: Denied, *, redirected: bool
    ) -> FetchResult:
        if denied.reason == "unknown_origin":
            code = "redirect_origin_unknown" if redirected else "origin_unknown"
            return self._failed(request, hops, classify_error(code), None)
        retry_after = None
        if denied.retry_after is not None:
            retry_after = max(denied.retry_after - self._now(), timedelta(0))
        decision = FetchDecision(
            FetchOutcome.RETRYABLE,
            ContentAccess.UNKNOWN,
            f"permit_{denied.reason}",
            retry_after=retry_after,
        )
        return FetchResult(
            decision=decision, requested_url=redact(request.url), hops=tuple(hops), denied=denied
        )

    def _failed(
        self,
        request: FetchRequest,
        hops: list[Hop],
        decision: FetchDecision,
        exc: BaseException | None,
    ) -> FetchResult:
        _log.info(
            "fetch.failed",
            error_code=decision.error_code,
            reason=decision.reason,
            detail=redact(str(exc)) if exc is not None else None,
            url=redact(request.url),
        )
        return FetchResult(decision=decision, requested_url=redact(request.url), hops=tuple(hops))

    async def _release(self, permit: Permit) -> None:
        try:
            await asyncio.shield(self._permits.release(permit))
        except Exception as exc:  # release не має маскувати результат; lease спливе сам
            _log.warning("fetch.permit_release_failed", origin=permit.origin, error=repr(exc))
