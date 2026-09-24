"""WP-01C PR2 п.4–5: `NewsTranslation`, `TranslationStatus`, `news.version_created` (§5.4)."""

from __future__ import annotations

from typing import Any

import pytest
from factories import (
    ARTICLE_VERSION_ID,
    artifact_ref_payload,
    news_translation_payload,
    news_version_created_payload,
)
from pydantic import ValidationError

from collector.contracts.canonical import sha256_hex
from collector.contracts.enums import (
    STATE_AXES,
    ContentAccess,
    TranslationQualityFlag,
    TranslationStatus,
)
from collector.contracts.events import (
    DOMAIN_CHANGED_MEDIA_TYPE,
    decode_event,
    decode_news_version_created,
    encode_event,
)
from collector.contracts.identity import translation_idempotency_key
from collector.contracts.news import (
    NEWS_VERSION_CREATED_MEDIA_TYPE,
    NewsTranslation,
    NewsVersionCreatedEvent,
)


def translation(**overrides: Any) -> NewsTranslation:
    return NewsTranslation.model_validate(news_translation_payload(**overrides))


def event(**overrides: Any) -> NewsVersionCreatedEvent:
    return NewsVersionCreatedEvent.model_validate(news_version_created_payload(**overrides))


# --- enums --------------------------------------------------------------------------------------


def test_translation_status_has_exactly_four_values_golden() -> None:
    assert [s.value for s in TranslationStatus] == [
        "pending",
        "translated",
        "not_required",
        "translation_failed",
    ]


def test_translation_status_is_not_a_state_axis() -> None:
    # §5.5: осі стану — рівно п'ять; статус перекладу — окремий enum контракту перекладу.
    assert TranslationStatus not in STATE_AXES
    assert len(STATE_AXES) == 5


def test_quality_flags_contain_required_minimum() -> None:
    values = {f.value for f in TranslationQualityFlag}
    assert {"preservation_failed", "low_language_confidence", "provider_truncated"} <= values
    assert "language_unsupported" in values  # WP-04 О-5 / U-2


# --- NewsTranslation ---------------------------------------------------------------------------


def test_translation_valid_with_body_artifact() -> None:
    record = translation()
    assert record.status is TranslationStatus.TRANSLATED
    assert record.body_text is None
    assert record.body_artifact is not None


def test_translation_with_body_none_is_valid_r20() -> None:
    record = translation(body_artifact=None, body_text=None)
    assert record.body_artifact is None
    assert record.body_text is None


def test_translation_inline_body_text_is_valid_but_not_with_artifact() -> None:
    translation(body_artifact=None, body_text="Перекладений текст")
    with pytest.raises(ValidationError, match="взаємовиключні"):
        translation(body_text="Перекладений текст")


def test_translation_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError, match="naive"):
        translation(created_at="2026-09-01T12:05:00")


def test_translation_idempotency_key_must_match_components() -> None:
    with pytest.raises(ValidationError, match="translation_idempotency_key"):
        translation(translation_idempotency_key="0" * 64)
    with pytest.raises(ValidationError, match="translation_idempotency_key"):
        translation(model_version="nmt-2")  # компонент змінено, ключ старий
    key = translation_idempotency_key(
        ARTICLE_VERSION_ID, "uk", "google-translate-v3", "nmt-2", "f" * 64
    )
    assert translation(model_version="nmt-2", translation_idempotency_key=key).model_version == (
        "nmt-2"
    )


def test_translated_requires_title() -> None:
    with pytest.raises(ValidationError, match="title"):
        translation(title=None)


@pytest.mark.parametrize("status", ["pending", "not_required"])
def test_pending_and_not_required_carry_no_text(status: str) -> None:
    record = translation(status=status, title=None, lead=None, body_artifact=None)
    assert record.title is None
    with pytest.raises(ValidationError, match="тексту"):
        translation(status=status, title="x", lead=None, body_artifact=None)
    with pytest.raises(ValidationError, match="тексту"):
        translation(status=status, title=None, lead=None)  # body_artifact лишився


def test_not_required_uk_original_without_cost() -> None:
    record = translation(
        status="not_required",
        source_language="uk",
        title=None,
        lead=None,
        body_artifact=None,
        character_count=0,
        cost=None,
    )
    assert record.cost is None


def test_failed_translation_with_flags() -> None:
    record = translation(
        status="translation_failed",
        title=None,
        lead=None,
        body_artifact=None,
        quality_flags=["language_unsupported", "preservation_failed"],
    )
    assert TranslationQualityFlag.LANGUAGE_UNSUPPORTED in record.quality_flags
    with pytest.raises(ValidationError, match="дублікати"):
        translation(quality_flags=["provider_truncated", "provider_truncated"])
    with pytest.raises(ValidationError):
        translation(quality_flags=["unknown_flag"])


def test_cost_is_non_negative_money_without_float() -> None:
    with pytest.raises(ValidationError, match="від'ємним"):
        translation(cost={"amount_minor": -1, "currency": "USD"})
    with pytest.raises(ValidationError):
        translation(cost={"amount_minor": 0.37, "currency": "USD"})
    with pytest.raises(ValidationError):
        translation(character_count=-1)


@pytest.mark.parametrize("language", ["ru", "ca", "de", "pt-BR", "und"])
def test_any_well_formed_source_language_is_valid_u2(language: str) -> None:
    assert translation(source_language=language).source_language == language


@pytest.mark.parametrize("language", ["UK", "english", "u", "uk_UA", ""])
def test_malformed_language_codes_are_rejected(language: str) -> None:
    with pytest.raises(ValidationError):
        translation(source_language=language)


# --- NewsVersionCreatedEvent -------------------------------------------------------------------


def test_event_valid_and_outbox_fields_present() -> None:
    evt = event()
    assert evt.event_type == "news.version_created"
    assert evt.version_number == 2
    assert evt.content_access is ContentAccess.FULL


def test_full_access_requires_body() -> None:
    with pytest.raises(ValidationError, match="cleaned_body_artifact"):
        event(cleaned_body_artifact=None)


@pytest.mark.parametrize("access", ["metadata_only", "blocked", "challenge", "gone"])
def test_body_forbidden_without_full_text(access: str) -> None:
    evt = event(content_access=access, cleaned_body_artifact=None)
    assert evt.cleaned_body_artifact is None
    with pytest.raises(ValidationError, match="body заборонений"):
        event(content_access=access)


@pytest.mark.parametrize("access", ["partial", "premium", "unknown"])
def test_body_optional_for_partial_access(access: str) -> None:
    event(content_access=access)
    event(content_access=access, cleaned_body_artifact=None)


def test_metadata_only_without_lead_is_valid() -> None:
    evt = event(content_access="metadata_only", cleaned_body_artifact=None, lead_artifact=None)
    assert evt.lead_artifact is None


def test_event_rejects_other_type_naive_time_and_unknown_source() -> None:
    with pytest.raises(ValidationError):
        event(event_type="news.article.changed")
    with pytest.raises(ValidationError, match="naive"):
        event(occurred_at="2026-09-01T12:00:00")
    with pytest.raises(ValidationError):
        event(source={"source_id": "news_de_unknown", "source_item_id": "1"})


def test_encode_news_event_byte_equivalent_after_round_trip() -> None:
    evt = event()
    encoded = encode_event(evt)
    assert encoded.event_media_type == NEWS_VERSION_CREATED_MEDIA_TYPE
    assert encoded.event_id == evt.event_id
    assert encoded.event_sha256 == sha256_hex(encoded.event_bytes)
    decoded = decode_news_version_created(encoded.event_bytes)
    assert decoded == evt
    assert encode_event(decoded).event_bytes == encoded.event_bytes
    reordered = dict(reversed(list(news_version_created_payload().items())))
    assert encode_event(NewsVersionCreatedEvent.model_validate(reordered)) == encoded


def test_news_event_bytes_are_not_a_domain_changed_event() -> None:
    encoded = encode_event(event())
    assert encoded.event_media_type != DOMAIN_CHANGED_MEDIA_TYPE
    with pytest.raises(ValidationError):
        decode_event(encoded.event_bytes)


def test_artifact_refs_must_be_content_addressed() -> None:
    bad = artifact_ref_payload()
    bad["sha256"] = "not-a-hash"
    with pytest.raises(ValidationError):
        event(title_artifact=bad)
