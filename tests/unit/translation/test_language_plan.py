"""Мова по сегментах, `not_required`, змішані сторінки, R-20 (картка WP-04 PR1, вимоги 3, 4, 8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.translation.fakes import FixedClassifier

from collector.contracts import ContentAccess
from collector.translation.detection import (
    LinguaClassifier,
    detect_article_language,
    detect_segment_language,
)
from collector.translation.languages import (
    CORE_SOURCE_LANGUAGES,
    EXTRA_SOURCE_LANGUAGES,
    TARGET_LANGUAGE,
    normalize_language_tag,
    parse_extra_source_languages,
    supported_source_languages,
)
from collector.translation.planner import ArticleText, plan_article_translation

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "translation" / "html"
# Незалежний запис 16 мов із §8 ТЗ (друга точка правди, не імпорт константи).
SPEC_SOURCE_LANGUAGES = {
    "de", "fr", "en", "lt", "lv", "et", "pl", "hu", "ro", "cs", "sk", "sl", "hr", "it", "es", "nl",
}  # fmt: skip
SAMPLES: dict[str, str] = {
    "de": "Die Regierung hat heute neue Regeln für den Nahverkehr in den Städten beschlossen.",
    "en": "The minister said the agreement would be signed next week after long talks.",
    "pl": "Rząd zapowiedział nowe przepisy dotyczące transportu publicznego w miastach.",
    "ru": "Правительство объявило о новых мерах поддержки малого бизнеса в регионах.",
    "ca": "El govern ha anunciat noves mesures per al transport públic a les ciutats.",
    "uk": "Уряд оголосив нову програму підтримки малого бізнесу в регіонах країни.",
}


def test_language_constants_match_spec_and_user_decision_u2() -> None:
    assert CORE_SOURCE_LANGUAGES == SPEC_SOURCE_LANGUAGES
    assert len(CORE_SOURCE_LANGUAGES) == 16
    assert EXTRA_SOURCE_LANGUAGES == {"ru", "ca"}
    assert TARGET_LANGUAGE not in supported_source_languages()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, {"ru", "ca"}), ("", set()), ("ru", {"ru"}), (" ru , ca ,be ", {"ru", "ca", "be"})],
)
def test_extra_languages_config(raw: str | None, expected: set[str]) -> None:
    assert parse_extra_source_languages(raw) == expected


@pytest.mark.parametrize("raw", ["uk", "rus", "ru-RU", "1a"])
def test_extra_languages_config_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError, match="COLLECTOR_TRANSLATION_EXTRA_SOURCE_LANGUAGES"):
        parse_extra_source_languages(raw)


def test_language_tag_normalization() -> None:
    assert normalize_language_tag("de-AT") == "de"
    assert normalize_language_tag("pt_BR") == "pt"
    assert normalize_language_tag("EN") == "en"
    assert normalize_language_tag("") is None
    assert normalize_language_tag("x") is None


@pytest.mark.parametrize("language", sorted(SAMPLES), ids=sorted(SAMPLES))
def test_classifier_detects_core_extra_and_uk(classifier: LinguaClassifier, language: str) -> None:
    detected, confidence = classifier.classify(SAMPLES[language])
    assert detected == language
    assert confidence >= 0.6


def test_classifier_is_limited_to_uk_core_and_extra(classifier: LinguaClassifier) -> None:
    assert classifier.languages == CORE_SOURCE_LANGUAGES | EXTRA_SOURCE_LANGUAGES | {"uk"}


def test_article_language_order_metadata_then_lang_then_classifier() -> None:
    fake = FixedClassifier({}, default="pl")
    by_meta = detect_article_language(declared="de-DE", root_lang="fr", text="x", classifier=fake)
    assert (by_meta.language, by_meta.method) == ("de", "metadata")
    by_lang = detect_article_language(declared=None, root_lang="fr", text="x", classifier=fake)
    assert (by_lang.language, by_lang.method) == ("fr", "lang_attribute")
    assert fake.calls == []
    by_classifier = detect_article_language(
        declared=None, root_lang=None, text="tekst", classifier=fake
    )
    assert (by_classifier.language, by_classifier.method) == ("pl", "classifier")


def test_uncertain_segment_keeps_article_language_and_records_uncertainty() -> None:
    fake = FixedClassifier({"zweifel": ("nl", 0.41)})
    article = detect_article_language(declared="de", root_lang=None, text="", classifier=fake)
    decision = detect_segment_language(
        "ein ziemlich langer zweifelhafter Satz ohne klare Sprache",
        lang_attribute=None,
        article=article,
        classifier=fake,
    )
    assert decision.language == "de"
    assert decision.uncertain is True
    assert decision.candidate == "nl"
    assert decision.confidence == pytest.approx(0.41)


# --- not_required і змішані сторінки (R-08) -----------------------------------------------


def test_uk_article_without_foreign_segments_is_not_required(
    classifier: LinguaClassifier,
) -> None:
    article = ArticleText(
        content_access=ContentAccess.FULL,
        original_language="uk",
        title="Уряд оголосив нову програму підтримки",
        lead="Програма діятиме в усіх регіонах країни протягом трьох років.",
        body_html=f"<p>{SAMPLES['uk']}</p><p>Так</p>",
    )
    plan = plan_article_translation(article, classifier=classifier)
    assert plan.not_required
    assert plan.to_translate == ()


def test_mixed_uk_page_translates_exactly_foreign_segments(classifier: LinguaClassifier) -> None:
    article = ArticleText(
        content_access=ContentAccess.FULL,
        original_language="uk",
        title="Нова програма для громадського транспорту",
        body_html=(FIXTURES / "mixed_languages.html").read_text(encoding="utf-8"),
    )
    plan = plan_article_translation(article, classifier=classifier)
    assert not plan.not_required
    translated = [(p.language.language, p.segment.text[:9]) for p in plan.to_translate]
    assert translated == [("en", "We expect"), ("pl", "Rząd w Wa")]
    short = next(p for p in plan.segments if p.segment.text == "Так")
    assert (short.language.language, short.language.method, short.action) == (
        "uk",
        "inherited",
        "keep",
    )


def test_de_article_with_uk_quote_does_not_send_uk_segment(classifier: LinguaClassifier) -> None:
    article = ArticleText(
        content_access=ContentAccess.FULL,
        original_language="de",
        title="Neue Regeln für den Nahverkehr",
        body_html=(
            f"<p>{SAMPLES['de']}</p><blockquote><p>{SAMPLES['uk']}</p></blockquote><p>Ja.</p>"
        ),
    )
    plan = plan_article_translation(article, classifier=classifier)
    actions = [(p.segment.text[:4], p.language.language, p.action) for p in plan.segments]
    assert ("Уряд", "uk", "keep") in actions
    assert all(p.language.language != "uk" for p in plan.to_translate)
    short = next(p for p in plan.segments if p.segment.text == "Ja.")
    assert (short.language.language, short.action) == ("de", "translate")


@pytest.mark.parametrize("language", ["ru", "ca"])
def test_extra_languages_are_translated_u2(classifier: LinguaClassifier, language: str) -> None:
    article = ArticleText(
        content_access=ContentAccess.FULL, body_html=f"<p>{SAMPLES[language]}</p>"
    )
    plan = plan_article_translation(article, classifier=classifier)
    assert [p.language.language for p in plan.to_translate] == [language]
    assert plan.quality_flags == frozenset()


def test_language_outside_core_and_extra_is_unsupported(classifier: LinguaClassifier) -> None:
    article = ArticleText(
        content_access=ContentAccess.FULL,
        original_language="de",
        body_html=f'<p>{SAMPLES["de"]}</p><p lang="sr">Влада је усвојила нова правила.</p>',
    )
    plan = plan_article_translation(article, classifier=classifier)
    unsupported = [p for p in plan.segments if p.action == "unsupported"]
    assert [p.language.language for p in unsupported] == ["sr"]
    assert "language_unsupported" in plan.quality_flags
    # Той самий сегмент перекладається, якщо мову додано в extra-конфіг.
    extended = plan_article_translation(
        article,
        classifier=classifier,
        supported_languages=supported_source_languages(parse_extra_source_languages("ru,ca,sr")),
    )
    assert "sr" in {p.language.language for p in extended.to_translate}


def test_root_lang_is_article_language_not_segment_override(classifier: LinguaClassifier) -> None:
    article = ArticleText(
        content_access=ContentAccess.FULL,
        body_html=f'<article lang="uk"><p>{SAMPLES["uk"]}</p><p>{SAMPLES["en"]}</p></article>',
    )
    plan = plan_article_translation(article, classifier=classifier)
    assert plan.article_language.method == "lang_attribute"
    assert [p.language.language for p in plan.to_translate] == ["en"]


# --- body nullable (R-20, FR-016) ---------------------------------------------------------

HEAD_ONLY_ACCESS = [
    ContentAccess.METADATA_ONLY,
    ContentAccess.BLOCKED,
    ContentAccess.CHALLENGE,
    ContentAccess.PREMIUM,
    ContentAccess.GONE,
    ContentAccess.UNKNOWN,
]


@pytest.mark.parametrize("access", HEAD_ONLY_ACCESS, ids=[a.value for a in HEAD_ONLY_ACCESS])
def test_head_only_access_plans_title_and_lead_without_body(
    classifier: LinguaClassifier, access: ContentAccess
) -> None:
    article = ArticleText(
        content_access=access,
        original_language="de",
        title="Neue Regeln für den Nahverkehr",
        lead=SAMPLES["de"],
        body_html="<p>Dieser Text darf nicht übersetzt werden, obwohl er vorhanden ist.</p>",
    )
    plan = plan_article_translation(article, classifier=classifier)
    assert [f.field for f in plan.fields] == ["title", "lead"]


@pytest.mark.parametrize("access", [ContentAccess.FULL, ContentAccess.PARTIAL])
def test_full_access_without_body_plans_no_body(
    classifier: LinguaClassifier, access: ContentAccess
) -> None:
    article = ArticleText(content_access=access, original_language="de", title="Neue Regeln")
    plan = plan_article_translation(article, classifier=classifier)
    assert [f.field for f in plan.fields] == ["title"]
    assert all(p.field != "body" for p in plan.segments)


def test_full_access_with_body_plans_body(classifier: LinguaClassifier) -> None:
    article = ArticleText(
        content_access=ContentAccess.PARTIAL, original_language="de", body_html="<p>Hallo.</p>"
    )
    plan = plan_article_translation(article, classifier=classifier)
    assert [f.field for f in plan.fields] == ["body"]
