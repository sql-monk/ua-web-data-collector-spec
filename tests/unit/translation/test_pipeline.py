"""TM + fake-перекладач: `not_required`, Q-009, ідемпотентність, провал preservation.

Картка WP-04 PR1, вимоги 4, 5, 7, 9; FR-016, FR-017, R-08, R-20.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.unit.translation.fakes import UK_PREFIX, FakeTranslator, RecordingMemory

from collector.contracts import ContentAccess
from collector.translation.detection import LinguaClassifier
from collector.translation.glossary import Glossary
from collector.translation.pipeline import ProviderIdentity, TranslationOutcome, execute_plan
from collector.translation.planner import ArticleText, plan_article_translation

PROVIDER = ProviderIdentity("fake-provider", "fake-model-1")
PARAGRAPHS = [
    "Der Stadtrat hat am Dienstag den Haushalt für das kommende Jahr beschlossen.",
    "Die Ausgaben für Schulen steigen um 4,5 % auf 12.300.000 Euro.",
    "Mehrere Fraktionen kritisierten die geplanten Kürzungen bei Bibliotheken.",
    "Die Bürgermeisterin verteidigte den Entwurf in einer langen Rede.",
    "Details stehen auf https://example.org/haushalt?jahr=2025 bereit.",
]


def _article(paragraphs: list[str], **overrides: Any) -> ArticleText:
    article = ArticleText(
        content_access=ContentAccess.FULL,
        original_language="de",
        title="Stadtrat beschließt den neuen Haushalt",
        body_html="".join(f"<p>{text}</p>" for text in paragraphs),
    )
    return replace(article, **overrides)


async def _run(
    article: ArticleText,
    classifier: LinguaClassifier,
    glossary: Glossary,
    memory: RecordingMemory,
    translator: FakeTranslator,
    provider: ProviderIdentity = PROVIDER,
) -> TranslationOutcome:
    plan = plan_article_translation(article, classifier=classifier)
    return await execute_plan(
        plan, glossary=glossary, memory=memory, translator=translator, provider=provider
    )


async def test_uk_article_is_not_required_without_provider_or_tm(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    memory, translator = RecordingMemory(), FakeTranslator()
    article = ArticleText(
        content_access=ContentAccess.FULL,
        original_language="uk",
        title="Уряд оголосив нову програму підтримки",
        body_html="<p>Уряд оголосив нову програму підтримки малого бізнесу в регіонах.</p>",
    )
    outcome = await _run(article, classifier, glossary, memory, translator)
    assert outcome.not_required and outcome.complete
    assert translator.calls == []
    assert memory.get_calls == [] and memory.put_calls == []
    assert (outcome.title, outcome.lead, outcome.body_html) == (None, None, None)


async def test_full_translation_reassembles_all_fields(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    memory, translator = RecordingMemory(), FakeTranslator()
    outcome = await _run(_article(PARAGRAPHS), classifier, glossary, memory, translator)
    assert outcome.complete and not outcome.not_required
    assert outcome.title == UK_PREFIX + "Stadtrat beschließt den neuen Haushalt"
    assert outcome.body_html is not None
    assert outcome.body_html.count(f"<p>{UK_PREFIX}") == 5
    # Числа, відсотки, URL відновлено з placeholder-ів побайтово.
    for fragment in ("4,5 %", "12.300.000", "https://example.org/haushalt?jahr=2025"):
        assert fragment in outcome.body_html
    assert outcome.memory_hits == 0 and outcome.provider_segments == 6
    assert all(language == "de" for _, language, _ in translator.calls)


async def test_q009_changed_paragraph_is_the_only_segment_sent(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    memory = RecordingMemory()
    await _run(_article(PARAGRAPHS), classifier, glossary, memory, FakeTranslator())
    changed = list(PARAGRAPHS)
    changed[2] = "Mehrere Fraktionen lobten die geplanten Investitionen in Sporthallen."
    translator = FakeTranslator()
    outcome = await _run(_article(changed), classifier, glossary, memory, translator)
    assert translator.segments_sent == [changed[2]]  # masked-текст без чисел = оригінал
    assert outcome.complete and outcome.body_html is not None
    assert outcome.body_html.count(f"<p>{UK_PREFIX}") == 5
    assert UK_PREFIX + changed[2] in outcome.body_html
    assert outcome.memory_hits == 5  # title + 4 незмінні абзаци


async def test_repeat_of_same_version_is_full_tm_hit(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    memory = RecordingMemory()
    first = await _run(_article(PARAGRAPHS), classifier, glossary, memory, FakeTranslator())
    translator = FakeTranslator()
    second = await _run(_article(PARAGRAPHS), classifier, glossary, memory, translator)
    assert translator.calls == []
    assert second.memory_hits == 6 and second.provider_segments == 0
    assert (second.title, second.body_html) == (first.title, first.body_html)
    assert memory.put_calls[-1] == ()  # нічого нового не записано


async def test_other_model_version_does_not_reuse_tm(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    memory = RecordingMemory()
    await _run(_article(PARAGRAPHS), classifier, glossary, memory, FakeTranslator())
    translator = FakeTranslator()
    other = ProviderIdentity(PROVIDER.name, "fake-model-2")
    await _run(_article(PARAGRAPHS), classifier, glossary, memory, translator, other)
    assert len(translator.segments_sent) == 6


async def test_duplicate_segments_in_one_article_are_sent_once(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    translator = FakeTranslator()
    article = _article([PARAGRAPHS[0], PARAGRAPHS[0]], title=None)
    outcome = await _run(article, classifier, glossary, RecordingMemory(), translator)
    assert len(translator.segments_sent) == 1
    assert outcome.body_html is not None and outcome.body_html.count(UK_PREFIX) == 2


async def test_do_not_translate_glossary_term_survives_translation(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    translator = FakeTranslator()
    article = _article(
        ["Nach Angaben von Politico Europe hat der Bundestag die Vorlage am Abend beschlossen."],
        title=None,
    )
    outcome = await _run(article, classifier, glossary, RecordingMemory(), translator)
    assert "Politico Europe" not in translator.segments_sent[0]
    assert outcome.body_html is not None
    assert "Politico Europe" in outcome.body_html and "Бундестаг" in outcome.body_html


async def test_preservation_failure_blocks_version_but_keeps_valid_segments_in_tm(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    memory = RecordingMemory()

    def lose_placeholders(text: str) -> str:
        return text.replace('<x id="1"/>', "") if "Schulen" in text else text

    translator = FakeTranslator(mutate=lose_placeholders)
    outcome = await _run(_article(PARAGRAPHS), classifier, glossary, memory, translator)
    assert not outcome.complete
    assert "preservation_failed" in outcome.quality_flags
    assert (outcome.title, outcome.body_html) == (None, None)
    assert len(memory.entries) == 5  # пошкоджений сегмент у TM не потрапив
    retry = FakeTranslator()
    fixed = await _run(_article(PARAGRAPHS), classifier, glossary, memory, retry)
    assert fixed.complete and len(retry.segments_sent) == 1


async def test_truncated_provider_response_is_not_assembled(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    class Truncating(FakeTranslator):
        async def __call__(
            self, texts: Sequence[str], source_language: str, target_language: str
        ) -> list[str]:
            return (await super().__call__(texts, source_language, target_language))[:-1]

    memory = RecordingMemory()
    outcome = await _run(_article(PARAGRAPHS), classifier, glossary, memory, Truncating())
    assert not outcome.complete and "provider_truncated" in outcome.quality_flags
    assert memory.entries == {}


async def test_unsupported_language_segment_is_not_sent_and_blocks_version(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    translator = FakeTranslator()
    article = _article(
        [PARAGRAPHS[0]],
        title=None,
        body_html=f'<p>{PARAGRAPHS[0]}</p><p lang="sr">Влада је усвојила нова правила.</p>',
    )
    outcome = await _run(article, classifier, glossary, RecordingMemory(), translator)
    assert not outcome.complete
    assert "language_unsupported" in outcome.quality_flags
    assert all("Влада" not in text for text in translator.segments_sent)


@pytest.mark.parametrize("access", [ContentAccess.METADATA_ONLY, ContentAccess.PREMIUM])
async def test_metadata_only_translates_head_and_never_creates_body(
    classifier: LinguaClassifier, glossary: Glossary, access: ContentAccess
) -> None:
    translator = FakeTranslator()
    article = _article(PARAGRAPHS, content_access=access, lead=PARAGRAPHS[0])
    outcome = await _run(article, classifier, glossary, RecordingMemory(), translator)
    assert outcome.complete
    assert outcome.title is not None and outcome.lead is not None
    assert outcome.body_html is None
    assert len(translator.segments_sent) == 2


async def test_full_without_body_translates_head_only(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    translator = FakeTranslator()
    article = _article([], body_html=None)
    outcome = await _run(article, classifier, glossary, RecordingMemory(), translator)
    assert outcome.complete and outcome.body_html is None
    assert len(translator.segments_sent) == 1


async def test_mixed_uk_page_sends_exactly_two_foreign_segments(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    source = (
        Path(__file__).resolve().parents[2] / "fixtures/translation/html/mixed_languages.html"
    ).read_text(encoding="utf-8")
    article = ArticleText(
        content_access=ContentAccess.FULL,
        original_language="uk",
        title="Нова програма для громадського транспорту",
        body_html=source,
    )
    translator = FakeTranslator()
    outcome = await _run(article, classifier, glossary, RecordingMemory(), translator)
    assert sorted(language for _, language, _ in translator.calls) == ["en", "pl"]
    assert len(translator.segments_sent) == 2
    assert outcome.complete and outcome.body_html is not None
    assert outcome.body_html.count(UK_PREFIX) == 2
    # uk-сегменти і заголовок проходять як є.
    assert "<p>Міністерство оголосило" in outcome.body_html and "<p>Так</p>" in outcome.body_html
    assert outcome.title == "Нова програма для громадського транспорту"
