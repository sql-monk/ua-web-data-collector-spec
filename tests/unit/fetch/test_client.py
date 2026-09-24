"""Поведінка `SafeFetcher`: conditional GET (FR-004), 429/`Retry-After` → `block`, permits
(R-53: жоден байт без permit, release у `finally`), anonymous-only (Q-007), env proxy
ігнорується, секрети не потрапляють у логи (§13)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from email.utils import format_datetime

import httpx
import pytest
import respx
import structlog
from fetch_fakes import PUBLIC_IP, T0, FakeResolver, http_response, static

from collector.contracts.enums import FetchOutcome
from collector.fetch.client import FetchRequest
from collector.fetch.config import FetchConfig
from collector.fetch.permits import Denied

URL = "http://example.org/page"
ORIGIN = "http://example.org"


def _resolver() -> FakeResolver:
    return FakeResolver({"example.org": [PUBLIC_IP]})


# --- conditional GET -------------------------------------------------------------------------


async def test_validators_are_sent_and_304_is_not_modified_without_body(
    make_fetcher, network
) -> None:
    served = []

    def responder(request):
        served.append(request)
        return http_response(304, None, {"ETag": '"v1"'}, chunks=iter([b"MUST-NOT-READ"]))

    network.routes[(PUBLIC_IP, 80)] = responder
    request = FetchRequest(
        URL, if_none_match='"v1"', if_modified_since="Tue, 22 Sep 2026 10:00:00 GMT"
    )

    result = await make_fetcher(_resolver()).fetch(request)

    assert served[0].headers["if-none-match"] == '"v1"'
    assert served[0].headers["if-modified-since"] == "Tue, 22 Sep 2026 10:00:00 GMT"
    assert result.not_modified is True
    assert result.decision.outcome is FetchOutcome.SUCCESS
    assert result.body is None
    assert network.chunks_served == 1


async def test_200_with_validators_is_the_normal_path(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(200, b"new", {"ETag": '"v2"'})
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL, if_none_match='"v1"'))
    assert result.not_modified is False
    assert result.body == b"new"
    assert result.headers["etag"] == '"v2"'


async def test_no_validators_no_conditional_headers_and_none_on_redirect_hop(
    make_fetcher, network
) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: (
        static(302, b"", {"Location": "/b"})(r) if r.target == "/page" else static(200, b"x")(r)
    )
    await make_fetcher(_resolver()).fetch(FetchRequest(URL, if_none_match='"v1"'))
    assert "if-none-match" in network.requests[0].headers
    assert "if-none-match" not in network.requests[1].headers


# --- 429 / Retry-After -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("retry_after", "delay"),
    [
        ("120", timedelta(seconds=120)),
        (format_datetime(T0 + timedelta(minutes=5), usegmt=True), timedelta(minutes=5)),
        ("-5", timedelta(minutes=10)),
        ("abc", timedelta(minutes=10)),
        ("999999999", timedelta(hours=24)),
    ],
)
async def test_429_blocks_origin_once_with_clamped_until(
    make_fetcher, network, permits, retry_after, delay
) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(429, b"slow down", {"Retry-After": retry_after})

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == "http_429"
    assert result.decision.retry_after == delay
    assert permits.blocks == [(ORIGIN, T0 + delay, "http_429")]
    assert permits.live == {}


# --- permits ---------------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["rate", "concurrency", "blocked"])
async def test_denied_permit_means_zero_requests(make_fetcher, network, permits, reason) -> None:
    permits.deny[ORIGIN] = Denied(reason, T0 + timedelta(seconds=30))
    network.routes[(PUBLIC_IP, 80)] = static(200, b"x")

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(URL).mock(return_value=httpx.Response(200, content=b"x"))
        result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert route.call_count == 0
    assert network.connects == []
    assert result.denied == permits.deny[ORIGIN]
    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == f"permit_{reason}"
    assert result.decision.retry_after == timedelta(seconds=30)


async def test_origin_without_bucket_is_permanent_without_request(
    make_fetcher, network, permits
) -> None:
    permits.known = set()
    network.routes[(PUBLIC_IP, 80)] = static(200, b"x")
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert result.decision.error_code == "origin_unknown"
    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert network.connects == []


async def test_permit_is_taken_before_connect_and_released_after(
    make_fetcher, network, permits
) -> None:
    events: list[str] = []
    original_acquire = permits.acquire

    async def acquire(origin, job_id):
        events.append("acquire")
        return await original_acquire(origin, job_id)

    permits.acquire = acquire  # type: ignore[method-assign]

    def responder(request):
        events.append("request")
        return static(200, b"ok")(request)

    network.routes[(PUBLIC_IP, 80)] = responder

    await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert events == ["acquire", "request"]
    assert len(permits.released) == 1 and permits.live == {}


async def test_exception_during_body_releases_permit(make_fetcher, network, permits) -> None:
    def broken():
        yield b"partial"
        raise RuntimeError("stream exploded")

    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(200, None, chunks=broken())

    with pytest.raises(RuntimeError, match="stream exploded"):
        await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert len(permits.released) == 1 and permits.live == {}


async def test_network_error_releases_permit(make_fetcher, network, permits) -> None:
    # Маршруту немає → ConnectError у fake backend.
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert result.decision.error_code == "network_error"
    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert permits.live == {} and len(permits.released) == 1


async def test_cancel_during_body_releases_permit(make_fetcher, permits) -> None:
    started = asyncio.Event()

    async def hang():
        yield b"x"
        started.set()
        await asyncio.Event().wait()

    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, content=hang()))
        task = asyncio.create_task(make_fetcher(_resolver()).fetch(FetchRequest(URL)))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(permits.released) == 1 and permits.live == {}


async def test_release_failure_does_not_mask_result(make_fetcher, network, permits) -> None:
    async def failing_release(permit):
        raise ConnectionError("db down")

    permits.release = failing_release  # type: ignore[method-assign]
    network.routes[(PUBLIC_IP, 80)] = static(200, b"ok")

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.body == b"ok"


# --- anonymous-only / env proxy / UA -----------------------------------------------------------


async def test_no_cookies_no_authorization_and_stable_user_agent(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: (
        static(302, b"", {"Location": "/b", "Set-Cookie": "sid=abc; Path=/"})(r)
        if r.target == "/page"
        else static(200, b"ok", {"Set-Cookie": "sid2=def; Path=/"})(r)
    )
    fetcher = make_fetcher(_resolver())

    await fetcher.fetch(FetchRequest(URL))
    await fetcher.fetch(FetchRequest(URL))

    assert len(network.requests) == 4
    for request in network.requests:
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
    assert {r.headers["user-agent"] for r in network.requests} == {"UAWebDataCollector/0.1.0-test"}


async def test_env_proxy_is_ignored(make_fetcher, network, monkeypatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://10.9.9.9:3128")
    monkeypatch.setenv("NO_PROXY", "")
    network.routes[(PUBLIC_IP, 80)] = static(200, b"direct")
    network.routes[("10.9.9.9", 3128)] = static(200, b"PROXIED")

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.body == b"direct"
    assert network.connects == [(PUBLIC_IP, 80)]
    assert network.requests[0].target == "/page"  # не absolute-form proxy-запит


def test_user_agent_from_env_and_default_contains_name_and_version() -> None:
    assert FetchConfig.from_env({"COLLECTOR_FETCH_USER_AGENT": "Bot/1.2"}).user_agent == "Bot/1.2"
    default = FetchConfig.from_env({}).user_agent
    assert default.startswith("UAWebDataCollector/") and default.split("/")[1]
    assert FetchConfig.from_env({}).media_binaries is False
    assert FetchConfig.from_env({"COLLECTOR_FETCH_MEDIA_BINARIES": "1"}).media_binaries is True
    assert FetchConfig.from_env({"COLLECTOR_FETCH_MAX_BODY_BYTES": "10"}).max_body_bytes == 10


# --- secrets у логах ---------------------------------------------------------------------------


async def test_logs_and_result_do_not_leak_secrets(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: static(
        302,
        b"",
        {"Location": "http://u:p@example.org/next", "Set-Cookie": "sid=COOKIEVALUE"},
    )(r)
    url = "http://example.org/page?token=SECRETTOKEN&api_key=APIKEYVALUE&x=1"

    with structlog.testing.capture_logs() as logs:
        result = await make_fetcher(_resolver()).fetch(FetchRequest(url))

    assert result.decision.reason == "url_userinfo_forbidden"
    dumped = repr(logs) + repr(result)
    for secret in ("SECRETTOKEN", "APIKEYVALUE", "COOKIEVALUE", "u:p@", "Set-Cookie", "Cookie"):
        assert secret not in dumped, secret
    assert "authorization" not in dumped.lower()
    assert any(entry["event"] == "fetch.hop" for entry in logs)


async def test_safe_headers_are_redacted(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(
        404, b"", {"Location": "http://u:pw@example.org/x?token=LEAKED", "Set-Cookie": "a=b"}
    )
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert "set-cookie" not in result.headers
    assert "LEAKED" not in result.headers["location"]
    assert "u:pw@" not in result.headers["location"]
