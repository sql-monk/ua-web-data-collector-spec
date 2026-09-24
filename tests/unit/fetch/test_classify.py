"""Класифікація і retry decisions (§10, §5.5): таблиця статусів/помилок, `Retry-After`,
скінченна policy (4 спроби, 5 с/30 с/2 хв/10 хв + jitter)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from collector.contracts.enums import ContentAccess, FetchOutcome
from collector.fetch.classify import (
    BACKOFF,
    FetchDecision,
    classify_error,
    classify_status,
    parse_retry_after,
    plan_retry,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
S, R, P = FetchOutcome.SUCCESS, FetchOutcome.RETRYABLE, FetchOutcome.PERMANENT_FAILURE
A = ContentAccess


@pytest.mark.parametrize(
    ("status", "empty", "outcome", "access", "code"),
    [
        (200, False, S, A.FULL, None),
        (203, False, S, A.FULL, None),
        (206, False, S, A.PARTIAL, None),
        (304, True, S, A.UNKNOWN, None),
        (200, True, R, A.UNKNOWN, "empty_body"),
        (204, True, R, A.UNKNOWN, "empty_body"),
        (408, False, R, A.UNKNOWN, "http_408"),
        (425, False, R, A.UNKNOWN, "http_425"),
        (429, False, R, A.UNKNOWN, "http_429"),
        (500, False, R, A.UNKNOWN, "http_500"),
        (502, False, R, A.UNKNOWN, "http_502"),
        (503, False, R, A.UNKNOWN, "http_503"),
        (504, False, R, A.UNKNOWN, "http_504"),
        (401, False, P, A.BLOCKED, "http_401"),
        (403, False, P, A.BLOCKED, "http_403"),
        (404, False, P, A.GONE, "http_404"),
        (410, False, P, A.GONE, "http_410"),
        (451, False, P, A.BLOCKED, "http_451"),
        (400, False, P, A.UNKNOWN, "http_400"),
        (501, False, P, A.UNKNOWN, "http_501"),
        (300, False, P, A.UNKNOWN, "redirect_invalid"),
    ],
)
def test_status_table(status, empty, outcome, access, code) -> None:
    decision = classify_status(status, body_empty=empty, now=NOW)
    assert (decision.outcome, decision.content_access, decision.error_code) == (
        outcome,
        access,
        code,
    )


def test_304_is_not_modified() -> None:
    assert classify_status(304, now=NOW).not_modified is True


def test_empty_200_is_not_gone_and_not_a_deletion_signal() -> None:
    decision = classify_status(200, body_empty=True, now=NOW)
    assert decision.content_access is not ContentAccess.GONE
    assert decision.outcome is FetchOutcome.RETRYABLE
    assert not decision.route_incident


def test_404_only_sets_content_access_gone() -> None:
    """404/410 — лише `content_access=gone`; жодного поля/сигналу `entity_lifecycle`."""
    decision = classify_status(404, now=NOW)
    assert decision.content_access is ContentAccess.GONE
    assert not any("lifecycle" in name for name in FetchDecision.__dataclass_fields__)
    assert not decision.route_incident and not decision.block_origin


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_open_route_incident_and_are_browser_candidates(status: int) -> None:
    decision = classify_status(status, now=NOW)
    assert decision.route_incident and decision.browser_candidate
    assert not decision.block_origin


def test_429_blocks_origin() -> None:
    decision = classify_status(429, retry_after="120", now=NOW)
    assert decision.block_origin
    assert decision.retry_after == timedelta(seconds=120)


@pytest.mark.parametrize(
    ("code", "outcome", "error_code", "quarantine"),
    [
        ("timeout", R, "timeout", False),
        ("network_error", R, "network_error", False),
        ("dns_error", R, "dns_error", False),
        ("body_too_large", P, "body_too_large", True),
        ("decompression_bomb", P, "decompression_bomb", True),
        ("content_decoding_error", P, "content_decoding_error", True),
        ("too_many_redirects", P, "too_many_redirects", False),
        ("redirect_downgrade", P, "redirect_downgrade", False),
        ("redirect_origin_unknown", P, "redirect_origin_unknown", False),
        ("media_binary_skipped", S, "media_binary_skipped", False),
    ],
)
def test_error_table(code, outcome, error_code, quarantine) -> None:
    decision = classify_error(code)
    assert (decision.outcome, decision.error_code, decision.quarantine) == (
        outcome,
        error_code,
        quarantine,
    )


@pytest.mark.parametrize("reason", ["ssrf_forbidden_address", "scheme_forbidden", "route_denied"])
def test_policy_errors_are_policy_blocked(reason: str) -> None:
    decision = classify_error(reason, policy=True)
    assert decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert decision.error_code == "policy_blocked"
    assert decision.reason == reason


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("120", timedelta(seconds=120)),
        (format_datetime(NOW + timedelta(minutes=3), usegmt=True), timedelta(minutes=3)),
        ("-5", timedelta(minutes=10)),
        ("abc", timedelta(minutes=10)),
        (None, timedelta(minutes=10)),
        ("", timedelta(minutes=10)),
        ("999999999", timedelta(hours=24)),
        ("1", timedelta(seconds=5)),
        ("0", timedelta(seconds=5)),
        (format_datetime(NOW - timedelta(minutes=3), usegmt=True), timedelta(minutes=10)),
        (format_datetime(NOW + timedelta(days=3), usegmt=True), timedelta(hours=24)),
    ],
)
def test_retry_after_parsing_and_clamp(header, expected) -> None:
    assert parse_retry_after(header, NOW) == expected


def test_retry_policy_is_finite_with_table_backoff() -> None:
    rng = random.Random(7)  # noqa: S311 — детермінований jitter у тесті
    retryable = classify_status(503, now=NOW)
    delays = []
    for attempt in (1, 2, 3):
        plan = plan_retry(retryable, attempt, rng=rng)
        assert plan.retry and not plan.dead_letter and plan.delay is not None
        delays.append(plan.delay)
    for delay, base in zip(delays, BACKOFF, strict=False):
        assert base <= delay <= base * 1.2
    final = plan_retry(retryable, 4, rng=rng)
    assert final == type(final)(retry=False, dead_letter=True)


def test_retry_policy_never_retries_forever() -> None:
    retryable = classify_error("network_error")
    assert all(not plan_retry(retryable, n).retry for n in range(4, 50))


def test_permanent_goes_to_dead_letter_immediately() -> None:
    plan = plan_retry(classify_status(403, now=NOW), 1)
    assert plan.dead_letter and not plan.retry


def test_success_needs_no_retry() -> None:
    plan = plan_retry(classify_status(200, now=NOW), 1)
    assert not plan.retry and not plan.dead_letter


def test_retry_after_overrides_shorter_backoff() -> None:
    plan = plan_retry(classify_status(429, retry_after="3600", now=NOW), 1)
    assert plan.delay == timedelta(hours=1)
