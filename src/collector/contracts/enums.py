"""Закриті enum shared-контрактів: чотири осі стану + `fetch_outcome` (§5.5, R-18) та інші.

Інші WP не створюють власних enum «стану» — лише ці (`docs/contracts.md`). Розширення
(наприклад, нові `EntityKind` для WP-07/WP-09) — через dependency-запит до WP-01C.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class SourceState(StrEnum):
    """Стан планування всього джерела (§5.5)."""

    ENABLED = "enabled"
    PAUSED = "paused"
    DISABLED = "disabled"
    BLOCKED_ANONYMOUS = "blocked_anonymous"


@unique
class RouteState(StrEnum):
    """Стан конкретного RSS/sitemap/category/detail/browser route (§5.5)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CIRCUIT_OPEN = "circuit_open"
    UNSUPPORTED = "unsupported"


@unique
class EntityLifecycle(StrEnum):
    """Життєвий цикл товару, оголошення або статті (§5.1 `status`, §5.5)."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"
    UNKNOWN = "unknown"


@unique
class ContentAccess(StrEnum):
    """Фактична повнота одного fetched item (§5.5, §5.4 `content_access`)."""

    FULL = "full"
    PARTIAL = "partial"
    METADATA_ONLY = "metadata_only"
    BLOCKED = "blocked"
    CHALLENGE = "challenge"
    PREMIUM = "premium"
    GONE = "gone"
    UNKNOWN = "unknown"


@unique
class FetchOutcome(StrEnum):
    """Результат однієї спроби fetch — не стан доступу (§5.5)."""

    SUCCESS = "success"
    RETRYABLE = "retryable"
    PERMANENT_FAILURE = "permanent_failure"


_RESEARCH_ACCESS_ALIASES: dict[str, ContentAccess | FetchOutcome] = {
    "free": ContentAccess.FULL,
    "body_unavailable": ContentAccess.METADATA_ONLY,
    "retryable": FetchOutcome.RETRYABLE,
}


def map_research_access_state(label: str) -> ContentAccess | FetchOutcome:
    """Мапінг research-позначень у закриті enum (§5.5).

    `free → full`, `body_unavailable → metadata_only`, `retryable → FetchOutcome.retryable`;
    `blocked/challenge/premium/gone` та інші значення `ContentAccess` — однойменні.
    Невідома позначка — `ValueError` (тихого `unknown` немає, щоб не маскувати помилку).
    """
    key = label.strip().lower()
    if key in _RESEARCH_ACCESS_ALIASES:
        return _RESEARCH_ACCESS_ALIASES[key]
    try:
        return ContentAccess(key)
    except ValueError:
        msg = f"невідома research-позначка доступу: {label!r}"
        raise ValueError(msg) from None


@unique
class TimePrecision(StrEnum):
    """Точність часу, заявленого джерелом (§9.6)."""

    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


@unique
class EffectiveAtBasis(StrEnum):
    """Джерело `effective_at` в аналітичному контракті (§9.6)."""

    SOURCE_EVENT = "source_event"
    SOURCE_UPDATED = "source_updated"
    OBSERVED = "observed"


@unique
class DataDomain(StrEnum):
    """Домен даних; збігається з `kind` у `docs/research/source-registry.yaml`."""

    NEWS = "news"
    VEHICLE = "vehicle"
    CATALOG = "catalog"


@unique
class EntityKind(StrEnum):
    """`entity_kind` current document (§9.2); розширюється WP-07/WP-09 через dependency-запит."""

    CATALOG_ITEM = "catalog_item"
    CATALOG_OFFER = "catalog_offer"
    VEHICLE_LISTING = "vehicle_listing"
    SELLER = "seller"


@unique
class ContactKind(StrEnum):
    """Тип контактного значення (§5.1 `contacts`)."""

    PHONE = "phone"
    EMAIL = "email"
    URL = "url"
    MESSENGER = "messenger"
    OTHER = "other"


@unique
class UploadClaimStatus(StrEnum):
    """Стан upload claim у artifact store (§10 п.5, R-38/R-41)."""

    LEASED = "leased"
    COMMITTED = "committed"
    RELEASED = "released"
    EXPIRED = "expired"


@unique
class ObservationReason(StrEnum):
    """Причина business observation (§9.2): зміна state hash або heartbeat."""

    CHANGED = "changed"
    HEARTBEAT = "heartbeat"


@unique
class ReviewQuestionKind(StrEnum):
    """Тип user-generated запису каталогу (§5.2, §9.2 `product_reviews`/`product_questions`)."""

    REVIEW = "review"
    QUESTION = "question"


@unique
class TranslationStatus(StrEnum):
    """Статус версії перекладу новини (§5.4); єдиний enum статусу перекладу в проєкті (§5.5).

    Закритий: рівно чотири значення; нове значення — major (споживачі читають як закритий).
    """

    PENDING = "pending"
    TRANSLATED = "translated"
    NOT_REQUIRED = "not_required"
    TRANSLATION_FAILED = "translation_failed"


@unique
class TranslationQualityFlag(StrEnum):
    """Прапорці якості перекладу (§5.4, §12.1); розширюється minor-версією через WP-01C."""

    PRESERVATION_FAILED = "preservation_failed"
    LOW_LANGUAGE_CONFIDENCE = "low_language_confidence"
    PROVIDER_TRUNCATED = "provider_truncated"
    LANGUAGE_UNSUPPORTED = "language_unsupported"


@unique
class ResolutionAction(StrEnum):
    """Дія entity resolution decision (§9.8)."""

    MERGE = "merge"
    UNMERGE = "unmerge"
    REJECT = "reject"
    MANUAL_LINK = "manual_link"
    MANUAL_BLOCK = "manual_block"


@unique
class ReleaseState(StrEnum):
    """Стан dataset release (§9.9)."""

    DRAFT = "draft"
    BUILDING = "building"
    VALIDATING = "validating"
    PUBLISHED = "published"
    FAILED = "failed"
    SUPERSEDED = "superseded"


STATE_AXES: tuple[type[StrEnum], ...] = (
    SourceState,
    RouteState,
    EntityLifecycle,
    ContentAccess,
    FetchOutcome,
)
"""Єдині дозволені enum стану (§5.5); тест перевіряє точні значення."""
