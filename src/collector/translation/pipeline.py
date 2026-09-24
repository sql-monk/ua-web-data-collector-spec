"""Виконання плану перекладу над TM і перекладачем (§10 кроки 11–12, FR-017, Q-009).

До перекладача йдуть лише masked-сегменти без TM hit (дублікати в межах статті — один раз);
кожен переклад (і з TM, і новий) проходить `validate_preservation`; валідні нові переклади
пишуться в TM навіть коли інші сегменти впали — повтор добирає лише решту. Версія (поля
title/lead/body) збирається лише з повного набору валідних сегментів: провал preservation,
непідтримувана мова чи неповна відповідь → `complete=False` з quality flags, без полів.

TODO(WP-04 PR2): `SegmentTranslator` замінюється протоколом `TranslationProvider`
(`name`, `model_version`, `max_request_chars`, класифіковані помилки); тут — мінімальний
async callable, щоб TM/Q-009 перевірялися без провайдера.
"""

from __future__ import annotations

import html
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from collector.translation.glossary import Glossary
from collector.translation.memory import (
    MemoryEntry,
    TranslationMemoryStore,
    normalized_segment_hash,
    translation_memory_key,
)
from collector.translation.planner import PlannedSegment, QualityFlag, TranslationPlan
from collector.translation.preservation import MaskedSegment, mask_segment, validate_preservation
from collector.translation.segmenter import reassemble

SegmentTranslator = Callable[[Sequence[str], str, str], Awaitable[Sequence[str]]]
"""`(masked_texts, source_language, target_language) -> translations` у тому самому порядку."""


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    name: str
    model_version: str


@dataclass(frozen=True, slots=True)
class TranslationOutcome:
    """Результат одного прогону. Поля заповнені лише при `complete and not not_required`."""

    not_required: bool
    complete: bool
    title: str | None = None
    lead: str | None = None
    body_html: str | None = None
    quality_flags: frozenset[QualityFlag] = field(default_factory=frozenset)
    memory_hits: int = 0
    provider_segments: int = 0
    provider_characters: int = 0


@dataclass(frozen=True, slots=True)
class _Job:
    planned: PlannedSegment
    masked: MaskedSegment
    key: str
    segment_hash: str


async def execute_plan(
    plan: TranslationPlan,
    *,
    glossary: Glossary,
    memory: TranslationMemoryStore,
    translator: SegmentTranslator,
    provider: ProviderIdentity,
) -> TranslationOutcome:
    flags: set[QualityFlag] = set(plan.quality_flags)
    if plan.not_required:
        return TranslationOutcome(not_required=True, complete=True, quality_flags=frozenset(flags))
    jobs = [_job(planned, plan, glossary, provider) for planned in plan.to_translate]
    hits = await memory.get_many(list(dict.fromkeys(job.key for job in jobs)))
    translated = {key: entry.translated_text for key, entry in hits.items()}
    pending: dict[str, dict[str, _Job]] = {}
    for job in jobs:
        if job.key not in translated:
            language = job.planned.language.language or ""
            pending.setdefault(language, {}).setdefault(job.key, job)
    sent_segments = sent_chars = 0
    for language, batch in pending.items():
        texts = [job.masked.text for job in batch.values()]
        sent_segments += len(texts)
        sent_chars += sum(len(text) for text in texts)
        results = list(await translator(texts, language, plan.target_language))
        if len(results) != len(texts):
            flags.add("provider_truncated")
            continue
        translated.update(zip(batch, results, strict=True))

    finals: dict[tuple[str, int], str] = {}
    new_entries: dict[str, MemoryEntry] = {}
    for job in jobs:
        text = translated.get(job.key)
        if text is None:
            continue
        result = validate_preservation(job.masked, text)
        if not result.ok:
            flags.add("preservation_failed")
            continue
        segment = job.planned.segment
        finals[(job.planned.field, segment.index)] = (
            html.unescape(result.html) if segment.kind == "attribute" else result.html
        )
        if job.key not in hits:
            new_entries[job.key] = _entry(job, plan, provider, glossary, text)
    await memory.put_many(list(new_entries.values()))

    memory_hits = sum(job.key in hits for job in jobs)
    if len(finals) != len(jobs) or flags & {"language_unsupported", "provider_truncated"}:
        return TranslationOutcome(
            not_required=False,
            complete=False,
            quality_flags=frozenset(flags),
            memory_hits=memory_hits,
            provider_segments=sent_segments,
            provider_characters=sent_chars,
        )
    assembled: dict[str, str] = {}
    for field_plan in plan.fields:
        translations = [
            finals.get((field_plan.field, planned.segment.index), _original(planned))
            for planned in field_plan.segments
        ]
        assembled[field_plan.field] = reassemble(field_plan.document, translations)
    title, lead = assembled.get("title"), assembled.get("lead")
    return TranslationOutcome(
        not_required=False,
        complete=True,
        title=html.unescape(title) if title is not None else None,
        lead=html.unescape(lead) if lead is not None else None,
        body_html=assembled.get("body"),
        quality_flags=frozenset(flags),
        memory_hits=memory_hits,
        provider_segments=sent_segments,
        provider_characters=sent_chars,
    )


def _job(
    planned: PlannedSegment, plan: TranslationPlan, glossary: Glossary, provider: ProviderIdentity
) -> _Job:
    language = planned.language.language or ""
    masked = mask_segment(planned.segment, glossary.entries_for(language))
    segment_hash = normalized_segment_hash(masked.text)
    key = translation_memory_key(
        source_language=language,
        target_language=plan.target_language,
        normalized_segment_hash=segment_hash,
        provider=provider.name,
        model_version=provider.model_version,
        glossary_version=glossary.version,
    )
    return _Job(planned, masked, key, segment_hash)


def _entry(
    job: _Job, plan: TranslationPlan, provider: ProviderIdentity, glossary: Glossary, text: str
) -> MemoryEntry:
    return MemoryEntry(
        key=job.key,
        source_language=job.planned.language.language or "",
        target_language=plan.target_language,
        segment_hash=job.segment_hash,
        provider=provider.name,
        model_version=provider.model_version,
        glossary_version=glossary.version,
        translated_text=text,
    )


def _original(planned: PlannedSegment) -> str:
    segment = planned.segment
    return segment.text if segment.kind == "attribute" else segment.html
