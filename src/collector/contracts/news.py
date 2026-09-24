"""News/translation contracts (§5.4, §5.5, §9.1, §9.3 п.7; R-04, R-20).

- `NewsVersionCreatedEvent` — payload outbox-події `news.version_created` (WP-01A PR3b пише її
  в одній транзакції з новою `news_article_versions`; WP-04 споживає). Canonical bytes —
  `collector.contracts.events.encode_event`.
- `NewsTranslation` — immutable версія перекладу (`news_translations`); статус —
  `TranslationStatus` (єдиний у проєкті), прапорці — `TranslationQualityFlag`.

`body_*` nullable (R-20): `metadata_only` стаття не має body, і переклад без body валідний.
Мови поза основними 16 (`ru`, `ca` тощо) — звичайні значення `LanguageCode` (U-2).
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Final, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from collector.contracts._base import SchemaVersion, VersionedDocument
from collector.contracts.artifacts import ArtifactRef
from collector.contracts.enums import ContentAccess, TranslationQualityFlag, TranslationStatus
from collector.contracts.identity import Sha256Hex, SourceIdentity, translation_idempotency_key
from collector.contracts.temporal import UtcDatetime
from collector.contracts.values import Money

NEWS_VERSION_CREATED_EVENT_TYPE: Final = "news.version_created"
NEWS_VERSION_CREATED_MEDIA_TYPE: Final = "application/vnd.ua-collector.news-version-created.v1+json"
LANGUAGE_CODE_PATTERN = r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$"

LanguageCode = Annotated[str, StringConstraints(pattern=LANGUAGE_CODE_PATTERN, max_length=35)]
"""BCP 47-подібний код мови з lowercase primary subtag (`uk`, `de`, `pt-BR`, `und`)."""

VersionLabel = Annotated[str, StringConstraints(min_length=1, max_length=128)]

MAX_TRANSLATED_TITLE_CHARS: Final = 2_048
MAX_TRANSLATED_LEAD_CHARS: Final = 16_384
MAX_TRANSLATED_BODY_TEXT_CHARS: Final = 65_536
"""Inline body перекладу ≤ 64 Ki символів (як `MAX_JSON_STRING_CHARS`); довший — `body_artifact`.

Сирий body обмежений 20 МБ (WP-02, `COLLECTOR_FETCH_MAX_BODY_BYTES`) — це межа HTML, не тексту;
inline-текст живе в рядку PostgreSQL/повідомленні, тож межа свідомо на порядки менша.
"""
MAX_RETRY_PLAN_CHARS: Final = 512

BODY_REQUIRED_ACCESS: Final = frozenset({ContentAccess.FULL})
BODY_FORBIDDEN_ACCESS: Final = frozenset(
    {
        ContentAccess.METADATA_ONLY,
        ContentAccess.BLOCKED,
        ContentAccess.CHALLENGE,
        ContentAccess.GONE,
    }
)
"""`content_access`, для яких body відсутній (§5.5: `metadata_only` ніколи не повнотекстовий)."""


class NewsVersionCreatedEvent(VersionedDocument):
    """`news.version_created` (§9.1, §10 п.8): нова immutable версія статті.

    Поля outbox (`event_id`, `event_type`, `aggregate_id`=`article_id`,
    `aggregate_version`=`version_number`, `payload_schema_version`=`schema_version`) —
    з цієї моделі. Тексти не inline: лише artifact refs title/lead/cleaned body; body
    обов'язковий для `content_access=full` і заборонений для `metadata_only`/`blocked`/
    `challenge`/`gone`.
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"
    media_type: ClassVar[str] = NEWS_VERSION_CREATED_MEDIA_TYPE

    event_id: UUID = Field(description="Стабільний ID події; той самий при replay outbox.")
    event_type: Literal["news.version_created"] = NEWS_VERSION_CREATED_EVENT_TYPE
    article_id: UUID
    article_version_id: UUID
    version_number: int = Field(ge=1, description="Монотонна версія статті (aggregate_version).")
    source: SourceIdentity
    original_language: LanguageCode
    source_locale_raw: Annotated[str, StringConstraints(max_length=64)] | None = Field(
        default=None, description="Declared locale джерела як отримано (`lang`, `og:locale`)."
    )
    content_access: ContentAccess
    content_hash: Sha256Hex
    title_artifact: ArtifactRef
    lead_artifact: ArtifactRef | None = None
    cleaned_body_artifact: ArtifactRef | None = None
    backfill: bool = False
    occurred_at: UtcDatetime = Field(description="Commit нової версії статті.")

    @model_validator(mode="after")
    def _body_matches_access(self) -> NewsVersionCreatedEvent:
        has_body = self.cleaned_body_artifact is not None
        if self.content_access in BODY_REQUIRED_ACCESS and not has_body:
            msg = f"content_access={self.content_access} вимагає cleaned_body_artifact"
            raise ValueError(msg)
        if self.content_access in BODY_FORBIDDEN_ACCESS and has_body:
            msg = f"content_access={self.content_access}: body заборонений (§5.5, R-20)"
            raise ValueError(msg)
        return self


class NewsTranslation(VersionedDocument):
    """Immutable версія перекладу статті (§5.4, `news_translations`).

    Зміна оригіналу → нова версія перекладу (інший `source_content_hash`/`article_version_id`);
    старий переклад не перезаписується. `translation_idempotency_key` має дорівнювати
    `translation_idempotency_key(article_version_id, target_language, provider, model_version,
    glossary_version)` (§9.3 п.7). Body — inline `body_text` **або** `body_artifact`, або
    жодного (nullable, R-20).

    Інваріанти статусу: `translated` вимагає `title`; `pending`/`not_required` не несуть
    перекладеного тексту (для `not_required` read API віддає оригінал без повторного
    зберігання, §5.4); `translation_failed` вимагає `retry_plan` (§12.1), інші статуси — ні.
    `quality_flags` зберігаються у відсортованому порядку (однакові canonical bytes).
    """

    contract_version = "1.0"
    schema_version: SchemaVersion = "1.0"

    article_id: UUID
    article_version_id: UUID
    target_language: Literal["uk"] = Field(
        default="uk", description="Цільова мова — лише `uk` (§5.4); розширення — minor."
    )
    source_language: LanguageCode | None = Field(
        default=None, description="Мова оригіналу статті (article-level), якщо відома."
    )
    provider: VersionLabel
    model_version: VersionLabel
    glossary_version: VersionLabel
    source_content_hash: Sha256Hex
    status: TranslationStatus
    title: Annotated[str, StringConstraints(max_length=MAX_TRANSLATED_TITLE_CHARS)] | None = None
    lead: Annotated[str, StringConstraints(max_length=MAX_TRANSLATED_LEAD_CHARS)] | None = None
    body_text: (
        Annotated[str, StringConstraints(max_length=MAX_TRANSLATED_BODY_TEXT_CHARS)] | None
    ) = Field(default=None, description="Inline body; довший за межу — `body_artifact`.")
    body_artifact: ArtifactRef | None = None
    quality_flags: list[TranslationQualityFlag] = Field(
        default_factory=list, description="Без дублікатів; канонічний порядок — за значенням."
    )
    retry_plan: (
        Annotated[str, StringConstraints(min_length=1, max_length=MAX_RETRY_PLAN_CHARS)] | None
    ) = Field(
        default=None,
        description=(
            "Явний retry plan (§12.1, WP-04 О-5): обов'язковий для `translation_failed`, "
            "заборонений для інших статусів."
        ),
    )
    character_count: int = Field(default=0, ge=0, description="Символи, надіслані провайдеру.")
    cost: Money | None = Field(default=None, description="Вартість (`amount_minor` ≥ 0).")
    translation_idempotency_key: Sha256Hex
    created_at: UtcDatetime

    @field_validator("quality_flags", mode="after")
    @classmethod
    def _canonical_flags(cls, flags: list[TranslationQualityFlag]) -> list[TranslationQualityFlag]:
        if len(set(flags)) != len(flags):
            msg = "quality_flags містять дублікати"
            raise ValueError(msg)
        return sorted(flags, key=lambda flag: flag.value)

    @model_validator(mode="after")
    def _consistent(self) -> NewsTranslation:
        expected_key = translation_idempotency_key(
            self.article_version_id,
            self.target_language,
            self.provider,
            self.model_version,
            self.glossary_version,
        )
        if self.translation_idempotency_key != expected_key:
            msg = "translation_idempotency_key не збігається з translation_idempotency_key(...)"
            raise ValueError(msg)
        if self.body_text is not None and self.body_artifact is not None:
            msg = "body_text і body_artifact взаємовиключні"
            raise ValueError(msg)
        if self.cost is not None and self.cost.amount_minor < 0:
            msg = "cost.amount_minor не може бути від'ємним"
            raise ValueError(msg)
        texts = (self.title, self.lead, self.body_text, self.body_artifact)
        has_text = any(value is not None for value in texts)
        if self.status is TranslationStatus.TRANSLATED and self.title is None:
            msg = "status=translated вимагає перекладений title"
            raise ValueError(msg)
        failed = self.status is TranslationStatus.TRANSLATION_FAILED
        if failed != (self.retry_plan is not None):
            msg = "retry_plan обов'язковий для translation_failed і заборонений інакше (§12.1)"
            raise ValueError(msg)
        if self.status in {TranslationStatus.PENDING, TranslationStatus.NOT_REQUIRED} and has_text:
            msg = f"status={self.status} не несе перекладеного тексту"
            raise ValueError(msg)
        return self
