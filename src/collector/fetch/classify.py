"""Класифікація результату fetch і скінченна retry policy (§10, §5.5, §3 п.4, §11).

Чисті функції без I/O. `classify_status`/`classify_error` → `FetchDecision` з окремими осями
§5.5: `outcome` (результат спроби) і `content_access` (повнота item-а) — не змішуються.
404/410 дають лише `content_access=gone` і **ніколи** не сигнал `entity_lifecycle`; 200 з
порожнім body — `retryable/empty_body`, а не `gone` і не ознака видалення (§11, FR-008).

`plan_retry` — табличний backoff 5 с / 30 с / 2 хв / 10 хв + jitter, максимум 4 спроби;
виконання (`not_before`) — runtime WP-01D і `queue.retry` WP-01A (PR2), тут лише рішення.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from collector.contracts.enums import ContentAccess, FetchOutcome

RETRYABLE_STATUSES = frozenset({408, 425, 500, 502, 503, 504})
RETRYABLE_ERRORS = frozenset({"timeout", "network_error", "dns_error", "permit_lease_too_short"})
QUARANTINE_ERRORS = frozenset({"body_too_large", "decompression_bomb", "content_decoding_error"})
RETRY_AFTER_DEFAULT = timedelta(minutes=10)
RETRY_AFTER_MIN = timedelta(seconds=5)
RETRY_AFTER_MAX = timedelta(hours=24)
BACKOFF: tuple[timedelta, ...] = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
)
MAX_ATTEMPTS = 4
JITTER_FRACTION = 0.2


@dataclass(frozen=True, slots=True)
class FetchDecision:
    """Рішення за однією спробою fetch.

    `error_code` — стабільний код для `fetches.error_code`/метрик; для guard/SSRF він завжди
    `policy_blocked`, а конкретна причина — у `reason`. `block_origin` — 429: викликач ставить
    `permits.block(origin, now + retry_after)`. `route_incident` — 401/403: circuit breaker
    route (PR2), `browser_candidate` — кандидат на anonymous browser fallback (PR2, лише якщо
    `browser_allowed`). `quarantine` — body карантиниться (§13).
    """

    outcome: FetchOutcome
    content_access: ContentAccess
    error_code: str | None = None
    reason: str | None = None
    retry_after: timedelta | None = None
    route_incident: bool = False
    block_origin: bool = False
    browser_candidate: bool = False
    quarantine: bool = False
    not_modified: bool = False


def parse_retry_after(value: str | None, now: datetime) -> timedelta:
    """`Retry-After` (секунди або HTTP-date) → затримка з clamp `[5 с, 24 год]`.

    Відсутній, сміттєвий чи від'ємний заголовок → 10 хв (консервативно для origin, що щойно
    відповів 429).
    """
    delay: timedelta | None = None
    text = (value or "").strip()
    if text.isascii() and text.isdigit():  # `isdigit` сам пускає `²` → `int()` падає (F-2)
        delay = timedelta(seconds=min(int(text), int(RETRY_AFTER_MAX.total_seconds()) + 1))
    elif text:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            when = None
        if when is not None and when.tzinfo is not None:
            delay = when - now
    if delay is None or delay < timedelta(0):
        return RETRY_AFTER_DEFAULT
    return min(max(delay, RETRY_AFTER_MIN), RETRY_AFTER_MAX)


def classify_status(
    status: int,
    *,
    body_empty: bool = False,
    retry_after: str | None = None,
    now: datetime,
) -> FetchDecision:
    """HTTP-статус (після redirect-ів) → рішення."""
    code = f"http_{status}"
    unknown = ContentAccess.UNKNOWN
    if status == 304:
        return FetchDecision(FetchOutcome.SUCCESS, unknown, not_modified=True)
    if 200 <= status < 300:
        if body_empty:
            return FetchDecision(FetchOutcome.RETRYABLE, unknown, "empty_body")
        access = ContentAccess.PARTIAL if status == 206 else ContentAccess.FULL
        return FetchDecision(FetchOutcome.SUCCESS, access)
    if status == 429:
        return FetchDecision(
            FetchOutcome.RETRYABLE,
            unknown,
            code,
            retry_after=parse_retry_after(retry_after, now),
            block_origin=True,
        )
    if status in RETRYABLE_STATUSES:
        return FetchDecision(FetchOutcome.RETRYABLE, unknown, code)
    if status in (401, 403):
        return FetchDecision(
            FetchOutcome.PERMANENT_FAILURE,
            ContentAccess.BLOCKED,
            code,
            route_incident=True,
            browser_candidate=True,
        )
    if status in (404, 410):
        return FetchDecision(FetchOutcome.PERMANENT_FAILURE, ContentAccess.GONE, code)
    if status == 451:
        return FetchDecision(FetchOutcome.PERMANENT_FAILURE, ContentAccess.BLOCKED, code)
    if 300 <= status < 400:
        return FetchDecision(FetchOutcome.PERMANENT_FAILURE, unknown, "redirect_invalid", code)
    return FetchDecision(FetchOutcome.PERMANENT_FAILURE, unknown, code)


def classify_error(error_code: str, *, policy: bool = False) -> FetchDecision:
    """Помилка без HTTP-статусу (policy/мережа/ліміти) → рішення.

    `policy=True` — відмова guard/SSRF/denylist: `error_code="policy_blocked"`, причина в
    `reason`. Redirect-коди (`redirect_downgrade`, `redirect_origin_unknown`,
    `too_many_redirects`) лишаються власними кодами — теж permanent, без запиту.
    """
    unknown = ContentAccess.UNKNOWN
    if policy:
        return FetchDecision(
            FetchOutcome.PERMANENT_FAILURE, unknown, "policy_blocked", reason=error_code
        )
    if error_code in RETRYABLE_ERRORS:
        return FetchDecision(FetchOutcome.RETRYABLE, unknown, error_code)
    if error_code == "media_binary_skipped":
        return FetchDecision(FetchOutcome.SUCCESS, ContentAccess.METADATA_ONLY, error_code)
    return FetchDecision(
        FetchOutcome.PERMANENT_FAILURE,
        unknown,
        error_code,
        quarantine=error_code in QUARANTINE_ERRORS,
    )


@dataclass(frozen=True, slots=True)
class RetryPlan:
    """`retry` із затримкою або кінець: `dead_letter` — permanent чи вичерпані спроби."""

    retry: bool
    delay: timedelta | None = None
    dead_letter: bool = False


def plan_retry(
    decision: FetchDecision,
    attempt: int,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    rng: random.Random | None = None,
) -> RetryPlan:
    """Рішення після спроби `attempt` (1-based): скінченне за побудовою.

    Затримка — `BACKOFF[attempt-1]` (далі — останній крок) + jitter до 20 %, але не менша за
    `Retry-After` (429).
    """
    if decision.outcome is FetchOutcome.SUCCESS:
        return RetryPlan(retry=False)
    if decision.outcome is FetchOutcome.PERMANENT_FAILURE or attempt >= max_attempts:
        return RetryPlan(retry=False, dead_letter=True)
    base = BACKOFF[min(max(attempt, 1), len(BACKOFF)) - 1]
    jitter = (rng or random.Random()).uniform(0, JITTER_FRACTION)  # noqa: S311 — не криптографія
    delay = base * (1 + jitter)
    if decision.retry_after is not None:
        delay = max(delay, decision.retry_after)
    return RetryPlan(retry=True, delay=delay)
