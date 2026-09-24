"""Adversarial-тести retry/429 і redaction (WP-02 PR1, §10 retry-таблиця, R-53, §13 secrets).

Незалежний тестувальник: hostile `Retry-After`, 429 на redirect-hop-і іншого origin-а,
скінченність retry для кожного retryable-рішення, секрети в query/Location на будь-якому hop-і.
"""

from __future__ import annotations

import random
from datetime import timedelta
from email.utils import format_datetime

import pytest
import structlog
from fetch_fakes import PUBLIC_IP, PUBLIC_IP_2, T0, FakeResolver, static

from collector.contracts.enums import FetchOutcome
from collector.fetch.classify import (
    BACKOFF,
    MAX_ATTEMPTS,
    RETRYABLE_ERRORS,
    RETRYABLE_STATUSES,
    classify_error,
    classify_status,
    plan_retry,
)
from collector.fetch.client import FetchRequest

URL = "http://example.org/page"
ORIGIN = "http://example.org"


def _resolver() -> FakeResolver:
    return FakeResolver({"example.org": [PUBLIC_IP], "b.example": [PUBLIC_IP_2]})


# --- 429 / Retry-After end-to-end -------------------------------------------------------------


@pytest.mark.parametrize(
    ("retry_after", "delay"),
    [
        (format_datetime(T0 - timedelta(days=1), usegmt=True), timedelta(minutes=10)),  # минуле
        (format_datetime(T0 + timedelta(days=365), usegmt=True), timedelta(hours=24)),  # далеке
        ("9" * 400, timedelta(hours=24)),  # величезне число
        ("0", timedelta(seconds=5)),
        ("  60  ", timedelta(seconds=60)),
        ("1.5", timedelta(minutes=10)),
        ("Thu, 01 Jan 1970 00:00:00 GMT", timedelta(minutes=10)),
        ("Wed, 99 Foo 2026 99:99:99 GMT", timedelta(minutes=10)),
    ],
)
async def test_hostile_retry_after_is_clamped_and_blocks_origin_once(
    make_fetcher, network, permits, retry_after, delay
) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(429, b"", {"Retry-After": retry_after})

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.error_code == "http_429"
    assert result.decision.retry_after == delay
    assert permits.blocks == [(ORIGIN, T0 + delay, "http_429")]
    assert permits.live == {}


@pytest.mark.parametrize("retry_after", ["²", "¹²³"])
async def test_non_ascii_digit_retry_after_does_not_crash_fetch(
    make_fetcher, network, permits, retry_after
) -> None:
    """Header bytes поза ASCII httpx декодує як latin-1 (`b"\\xb2"` → `"²"`); `str.isdigit()`
    для `"²"` — True, а `int("²")` кидає `ValueError`. Hostile origin не має ламати fetch: 429
    мусить дати `block_origin` із default 10 хв, як для сміттєвого заголовка (§10 п.10)."""
    network.routes[(PUBLIC_IP, 80)] = static(429, b"", {"Retry-After": retry_after})

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.error_code == "http_429"
    assert result.decision.retry_after == timedelta(minutes=10)
    assert permits.blocks == [(ORIGIN, T0 + timedelta(minutes=10), "http_429")]


async def test_429_on_redirect_target_blocks_that_origin_not_the_first(
    make_fetcher, network, permits
) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(302, b"", {"Location": "http://b.example/"})
    network.routes[(PUBLIC_IP_2, 80)] = static(429, b"", {"Retry-After": "120"})

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.error_code == "http_429"
    assert permits.blocks == [("http://b.example", T0 + timedelta(seconds=120), "http_429")]
    assert permits.live == {}


@pytest.mark.parametrize("status", [503, 500, 408])
async def test_retry_after_on_non_429_does_not_block_origin(
    make_fetcher, network, permits, status
) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(status, b"", {"Retry-After": "3600"})

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert permits.blocks == []


# --- скінченність retry за таблицею §10 -------------------------------------------------------


def _retryable_decisions():
    for status in sorted(RETRYABLE_STATUSES):
        yield f"http_{status}", classify_status(status, now=T0)
    yield "http_429", classify_status(429, retry_after="999999", now=T0)
    yield "empty_body", classify_status(200, body_empty=True, now=T0)
    for code in sorted(RETRYABLE_ERRORS):
        yield code, classify_error(code)


@pytest.mark.parametrize(("name", "decision"), list(_retryable_decisions()))
def test_every_retryable_decision_ends_in_dead_letter_after_max_attempts(name, decision) -> None:
    """Симуляція черги: спроба за спробою, доки policy каже retry. Має бути рівно
    `MAX_ATTEMPTS` (=4) спроб, затримки — табличні 5 с/30 с/2 хв (+≤20 % jitter) або
    `Retry-After`, сумарне очікування обмежене."""
    assert MAX_ATTEMPTS == 4
    rng = random.Random(1)  # noqa: S311 — детермінований jitter
    attempt, delays = 1, []
    while True:
        plan = plan_retry(decision, attempt, rng=rng)
        if not plan.retry:
            assert plan.dead_letter is True
            break
        assert plan.delay is not None
        delays.append(plan.delay)
        attempt += 1
        assert attempt <= 50, "retry policy не скінченна"
    assert attempt == MAX_ATTEMPTS
    assert len(delays) == MAX_ATTEMPTS - 1
    for delay, base in zip(delays, BACKOFF, strict=False):
        floor = max(base, decision.retry_after or timedelta(0))
        assert floor <= delay <= max(base * 1.2, floor)
    assert sum(delays, timedelta(0)) <= timedelta(hours=24) * 3


@pytest.mark.parametrize(
    "code",
    [
        "policy_blocked",
        "body_too_large",
        "decompression_bomb",
        "redirect_downgrade",
        "too_many_redirects",
        "redirect_origin_unknown",
        "content_encoding_unsupported",
    ],
)
def test_permanent_errors_are_never_retried(code: str) -> None:
    decision = classify_error(code, policy=code == "policy_blocked")
    assert plan_retry(decision, 1).dead_letter is True
    assert plan_retry(decision, 1).retry is False


# --- redaction: секрети на будь-якому hop-і ------------------------------------------------------

SECRETS = {
    "token": "TOK_SECRET_1",
    "access_token": "TOK_SECRET_2",
    "api_key": "APIKEY_SECRET_3",
    "client_secret": "CLIENT_SECRET_4",
    "X-Amz-Signature": "SIG_SECRET_5",
    "password": "PWD_SECRET_6",
}


async def test_secret_query_values_on_redirect_hop_do_not_reach_logs_or_result(
    make_fetcher, network
) -> None:
    query = "&".join(f"{k}={v}" for k, v in SECRETS.items())
    network.routes[(PUBLIC_IP, 80)] = static(
        302, b"", {"Location": f"http://b.example/next?{query}", "Set-Cookie": "sid=COOKIE_7"}
    )
    network.routes[(PUBLIC_IP_2, 80)] = static(
        200, b"body", {"Location": f"http://b.example/x?{query}", "ETag": '"e"'}
    )

    with structlog.testing.capture_logs() as logs:
        result = await make_fetcher(_resolver()).fetch(FetchRequest(f"{URL}?{query}"))

    assert result.body == b"body"
    dumped = repr(logs) + repr(result)
    for secret in [*SECRETS.values(), "COOKIE_7"]:
        assert secret not in dumped, secret
    assert [e["event"] for e in logs].count("fetch.hop") == 2


async def test_failed_hop_log_detail_does_not_leak_secrets(make_fetcher, network) -> None:
    """Відмова на hop-і з userinfo і токеном: `fetch.failed.detail` і `requested_url` чисті."""
    network.routes[(PUBLIC_IP, 80)] = static(
        301, b"", {"Location": "http://admin:HUNTER2@127.0.0.1/?token=TOK_SECRET_8"}
    )

    with structlog.testing.capture_logs() as logs:
        result = await make_fetcher(_resolver()).fetch(FetchRequest(f"{URL}?password=PWD_SECRET_9"))

    assert result.decision.error_code == "policy_blocked"
    dumped = repr(logs) + repr(result)
    for secret in ("HUNTER2", "admin:", "TOK_SECRET_8", "PWD_SECRET_9"):
        assert secret not in dumped, secret


async def test_no_request_headers_or_bodies_are_logged(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(200, b"BODY_MARKER", {"X-Secret": "HDR_MARKER"})

    with structlog.testing.capture_logs() as logs:
        await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    dumped = repr(logs)
    assert "BODY_MARKER" not in dumped
    assert "HDR_MARKER" not in dumped
    assert "User-Agent" not in dumped
