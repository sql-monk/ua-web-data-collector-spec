"""Adversarial-тести TM key, glossary version, плану і детектора (тестувальник WP-04 PR1).

FR-016, FR-017, R-04, R-08, R-20, §5.4, §12.2; рішення користувача U-2 (`ru`, `ca`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest
import yaml
from tests.unit.translation.fakes import FakeTranslator, RecordingMemory

from collector.contracts import ContentAccess, canonical_json_bytes
from collector.translation import memory as memory_module
from collector.translation.detection import LinguaClassifier
from collector.translation.glossary import Glossary, parse_glossary
from collector.translation.memory import normalized_segment_hash, translation_memory_key
from collector.translation.pipeline import ProviderIdentity, execute_plan
from collector.translation.planner import ArticleText, plan_article_translation

PROVIDER = ProviderIdentity("fake-provider", "fake-model-1")
BASE: dict[str, str] = {
    "source_language": "de",
    "target_language": "uk",
    "normalized_segment_hash": normalized_segment_hash("Der Rat tagt."),
    "provider": "google-cloud-translation-v3",
    "model_version": "nmt",
    "glossary_version": "a" * 64,
}


def _key(**overrides: str) -> str:
    return translation_memory_key(**(BASE | overrides))


# --- TM key: рівно 5 компонентів і чутливість до кожного ---------------------------------------


def test_actual_key_payload_has_exactly_five_components(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []
    original = canonical_json_bytes

    def spy(value: Any) -> bytes:
        captured.append(value)
        return original(value)

    monkeypatch.setattr(memory_module, "canonical_json_bytes", spy)
    _key()
    assert len(captured) == 1
    payload = captured[0]
    assert set(payload) == {
        "source_language",
        "target_language",
        "normalized_segment_hash",
        "provider",
        "glossary_version",
    }
    assert payload["provider"] == {"name": BASE["provider"], "model_version": BASE["model_version"]}


@pytest.mark.parametrize("component", sorted(BASE))
def test_every_component_alone_changes_key(component: str) -> None:
    changed = BASE[component] + "x"
    assert _key(**{component: changed}) != _key()


def test_swapping_values_between_components_changes_key() -> None:
    assert _key(source_language="uk", target_language="de") != _key()
    assert _key(provider="nmt", model_version="google-cloud-translation-v3") != _key()


def test_provider_model_boundary_is_not_ambiguous() -> None:
    assert _key(provider="a/b", model_version="c") != _key(provider="a", model_version="b/c")
    assert _key(provider="ab", model_version="") != _key(provider="a", model_version="b")


def test_all_keys_are_distinct_across_a_grid() -> None:
    keys = {
        _key(source_language=s, provider=p, model_version=m, glossary_version=g)
        for s in ("de", "fr", "ru", "ca")
        for p in ("google", "nllb")
        for m in ("nmt", "llm")
        for g in ("a" * 64, "b" * 64)
    }
    assert len(keys) == 4 * 2 * 2 * 2


# --- нормалізація сегмента: еквівалентні форми → один hash, різний текст → різні ---------------

EQUIVALENT: list[tuple[str, str]] = [
    ("Café", "Cafe\N{COMBINING ACUTE ACCENT}"),
    ("Straße  heute", "Straße\N{NO-BREAK SPACE}heute"),
    ("Bundestag", "Bundes\N{SOFT HYPHEN}tag"),
    ("a b", " a\t\n b "),
    ("Țară", "T\N{COMBINING COMMA BELOW}ara\N{COMBINING BREVE}"),
    ("ö", "o\N{COMBINING DIAERESIS}"),
]


@pytest.mark.parametrize(("left", "right"), EQUIVALENT)
def test_equivalent_forms_share_segment_hash(left: str, right: str) -> None:
    assert normalized_segment_hash(left) == normalized_segment_hash(right)


DISTINCT: list[tuple[str, str]] = [
    ("Bank", "bank"),
    ("ab", "a b"),
    ("\N{LATIN SMALL LIGATURE FI}nal", "final"),
    ("１２", "12"),
    ("Müller", "Muller"),
    ("Mueller", "Müller"),
    ("a", "\N{CYRILLIC SMALL LETTER A}"),
    ("1.5", "1,5"),
    ("Er kommt.", "Er kommt!"),
    ('<x id="1"/> und <x id="2"/>', '<x id="2"/> und <x id="1"/>'),
    ("ß", "ss"),
    ("İ", "I"),
    ("", " x"),
]


@pytest.mark.parametrize(("left", "right"), DISTINCT)
def test_distinct_text_does_not_collide_after_normalization(left: str, right: str) -> None:
    assert normalized_segment_hash(left) != normalized_segment_hash(right)


def test_zwj_emoji_sequence_does_not_collide_with_separate_emoji() -> None:
    family = "\N{MAN}\N{ZERO WIDTH JOINER}\N{WOMAN}\N{ZERO WIDTH JOINER}\N{GIRL}"
    separate = "\N{MAN}\N{WOMAN}\N{GIRL}"
    assert normalized_segment_hash(family) != normalized_segment_hash(separate)


# --- glossary version -------------------------------------------------------------------------

GLOSSARY_YAML = """\
schema: 1
target_language: uk
pairs:
  "*":
    - term: NATO
      target: НАТО
  de:
    - term: Bundestag
      target: Бундестаг
    - term: Tagesschau
      keep: true
"""

REORDERED_KEYS_YAML = """\
# той самий вміст: інший порядок ключів, коментарі й форматування
pairs:
  de:
    - {target: Бундестаг, term: Bundestag}
    - {keep: true, term: Tagesschau}
  "*":
    - target: НАТО
      term: NATO
target_language: uk
schema: 1
"""


def test_glossary_version_is_stable_to_mapping_order_comments_and_formatting() -> None:
    first = parse_glossary(yaml.safe_load(GLOSSARY_YAML))
    second = parse_glossary(yaml.safe_load(REORDERED_KEYS_YAML))
    assert first.version == second.version
    assert len(first.version) == 64
    assert parse_glossary(yaml.safe_load(GLOSSARY_YAML)).version == first.version


def test_glossary_version_depends_on_entry_list_order_fact() -> None:
    """Факт для рев'ю: порядок записів у списку — частина вмісту (canonical JSON списку),
    тож перестановка записів без зміни змісту дає нову версію і інвалідує TM пари."""
    data = yaml.safe_load(GLOSSARY_YAML)
    swapped = yaml.safe_load(GLOSSARY_YAML)
    swapped["pairs"]["de"].reverse()
    assert parse_glossary(data).version != parse_glossary(swapped).version


@pytest.mark.parametrize(
    "mutation",
    ["target", "term", "keep_to_target", "new_pair", "remove_entry"],
)
def test_any_semantic_glossary_change_changes_version(mutation: str) -> None:
    base = yaml.safe_load(GLOSSARY_YAML)
    data = yaml.safe_load(GLOSSARY_YAML)
    if mutation == "target":
        data["pairs"]["de"][0]["target"] = "Бундестаґ"
    elif mutation == "term":
        data["pairs"]["de"][0]["term"] = "Bundesrat"
    elif mutation == "keep_to_target":
        data["pairs"]["de"][1] = {"term": "Tagesschau", "target": "Тагесшау"}
    elif mutation == "new_pair":
        data["pairs"]["pl"] = [{"term": "Sejm", "target": "Сейм"}]
    else:
        data["pairs"]["de"].pop()
    assert parse_glossary(base).version != parse_glossary(data).version


# --- R-20: body nullable, None ≠ порожній рядок -----------------------------------------------


async def _outcome(
    article: ArticleText, classifier: LinguaClassifier, glossary: Glossary
) -> tuple[Any, FakeTranslator, RecordingMemory]:
    memory, translator = RecordingMemory(), FakeTranslator()
    plan = plan_article_translation(article, classifier=classifier)
    outcome = await execute_plan(
        plan, glossary=glossary, memory=memory, translator=translator, provider=PROVIDER
    )
    return outcome, translator, memory


TITLE = "Der Stadtrat beschließt einen neuen Haushalt für die Schulen"


async def test_body_none_stays_none_and_empty_body_is_not_invented(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    none_body, t1, _ = await _outcome(
        ArticleText(ContentAccess.FULL, "de", title=TITLE, body_html=None), classifier, glossary
    )
    assert none_body.complete and none_body.body_html is None
    assert all(text.startswith("Der Stadtrat") for text in t1.segments_sent)

    empty_body, _, _ = await _outcome(
        ArticleText(ContentAccess.FULL, "de", title=TITLE, body_html=""), classifier, glossary
    )
    assert empty_body.complete
    assert empty_body.body_html == ""  # порожній body лишається порожнім, а не None і не текстом


@pytest.mark.parametrize(
    "access",
    [ContentAccess.METADATA_ONLY, ContentAccess.PREMIUM, ContentAccess.GONE],
    ids=lambda a: a.value,
)
async def test_head_only_access_never_returns_body_even_if_provided(
    access: ContentAccess, classifier: LinguaClassifier, glossary: Glossary
) -> None:
    article = ArticleText(access, "de", title=TITLE, body_html="<p>Geheimer Volltext hier.</p>")
    outcome, translator, _ = await _outcome(article, classifier, glossary)
    assert outcome.complete and outcome.body_html is None
    assert all("Geheimer" not in text for text in translator.segments_sent)


# --- uk → not_required, короткі/числові сегменти -----------------------------------------------


async def test_uk_article_with_short_and_numeric_segments_is_not_required(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    article = ArticleText(
        ContentAccess.FULL,
        "uk",
        title="Уряд оголосив нову програму підтримки громад",
        lead="2024",
        body_html="<p>Так</p><p>OK</p><p>10 %</p><p>—</p><p>12.03.2024</p><p>NATO</p>",
    )
    outcome, translator, memory = await _outcome(article, classifier, glossary)
    assert outcome.not_required and outcome.complete
    assert (outcome.title, outcome.lead, outcome.body_html) == (None, None, None)
    assert translator.calls == []
    assert memory.get_calls == [] and memory.put_calls == []


def test_short_foreign_segment_in_uk_article_inherits_uk(classifier: LinguaClassifier) -> None:
    plan = plan_article_translation(
        ArticleText(
            ContentAccess.FULL,
            "uk",
            body_html="<p>Уряд ухвалив рішення про фінансування шкіл.</p><p>Yes, indeed.</p>",
        ),
        classifier=classifier,
    )
    short = next(p for p in plan.segments if p.segment.text == "Yes, indeed.")
    assert (short.language.language, short.language.method, short.action) == (
        "uk",
        "inherited",
        "keep",
    )
    assert plan.not_required


# --- змішані мови по сегментах (R-08, U-2) -----------------------------------------------------

MIXED = {
    "de": "Der Stadtrat hat am Dienstag den Haushalt für das kommende Jahr beschlossen.",
    "fr": "Le conseil municipal a adopté mardi le budget pour l'année prochaine.",
    "nl": "De gemeenteraad heeft dinsdag de begroting voor het komende jaar goedgekeurd.",
    "ru": "Городской совет во вторник утвердил бюджет на следующий год для всех школ.",
    "ca": "L'ajuntament va aprovar dimarts el pressupost per a l'any vinent a la ciutat.",
    "uk": "Міська рада у вівторок ухвалила бюджет на наступний рік для всіх шкіл.",
    "en": "The city council approved the budget for the coming year on Tuesday.",
}


async def test_mixed_language_article_is_split_per_segment_language(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    body = "".join(f"<p>{text}</p>" for text in MIXED.values())
    article = ArticleText(ContentAccess.FULL, "de", body_html=body)
    plan = plan_article_translation(article, classifier=classifier)
    detected = {p.segment.text: (p.language.language, p.action) for p in plan.segments}
    for language, text in MIXED.items():
        expected_action = "keep" if language == "uk" else "translate"
        assert detected[text] == (language, expected_action), language
    outcome, translator, _ = await _outcome(article, classifier, glossary)
    assert outcome.complete
    sent_languages = sorted(source for _, source, _ in translator.calls)
    assert sent_languages == sorted(set(MIXED) - {"uk"})
    assert all(target == "uk" for _, _, target in translator.calls)
    assert outcome.body_html is not None and MIXED["uk"] in outcome.body_html


def test_lang_attribute_overrides_classifier_per_segment(classifier: LinguaClassifier) -> None:
    body = f'<div lang="de"><p>{MIXED["de"]}</p><p lang="ru">{MIXED["ru"]}</p></div>'
    plan = plan_article_translation(
        ArticleText(ContentAccess.FULL, None, body_html=body), classifier=classifier
    )
    assert [(p.language.language, p.language.method) for p in plan.segments] == [
        ("de", "classifier"),
        ("ru", "lang_attribute"),
    ]


# --- детермінованість детектора між запусками ----------------------------------------------------

_DETECT_SCRIPT = """
import json, sys
from collector.translation.detection import lingua_classifier
from collector.translation.languages import supported_source_languages
texts = json.loads(sys.stdin.read())
c = lingua_classifier(supported_source_languages() | {"uk"})
print(json.dumps([list(c.classify(t)) for t in texts]))
"""
_DETECT_TEXTS = [*MIXED.values(), "Kurz", "Das ist kurz aber doch", "ok ok ok", "Ну"]


def _detect_in_subprocess(hash_seed: str) -> list[list[Any]]:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONIOENCODING="utf-8")
    done = subprocess.run(  # noqa: S603 - власний інтерпретатор і статичний скрипт тесту
        [sys.executable, "-c", _DETECT_SCRIPT],
        input=json.dumps(_DETECT_TEXTS),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
        timeout=300,
    )
    result: list[list[Any]] = json.loads(done.stdout)
    return result


def _assert_same_detection(left: list[list[Any]], right: list[list[Any]]) -> None:
    """Мова — точно; confidence — до 1e-12: lingua дає розкид в останньому ulp навіть у
    межах одного процесу (finding T-6), тож точна рівність float не гарантується."""
    assert [language for language, _ in left] == [language for language, _ in right]
    for (_, a), (_, b) in zip(left, right, strict=True):
        assert a == pytest.approx(b, abs=1e-12, rel=0)


def test_detector_is_deterministic_across_processes(classifier: LinguaClassifier) -> None:
    in_process = [list(classifier.classify(text)) for text in _DETECT_TEXTS]
    _assert_same_detection(in_process, [list(classifier.classify(t)) for t in _DETECT_TEXTS])
    _assert_same_detection(_detect_in_subprocess("0"), in_process)
    _assert_same_detection(_detect_in_subprocess("4242"), in_process)


def test_plan_decisions_are_identical_between_runs(classifier: LinguaClassifier) -> None:
    body = "".join(f"<p>{text}</p>" for text in MIXED.values())
    article = ArticleText(ContentAccess.FULL, None, title="Kurz", body_html=body)
    runs = [
        [
            (p.segment.text, p.language.language, p.language.method, p.action, p.language.uncertain)
            for p in plan_article_translation(article, classifier=classifier).segments
        ]
        for _ in range(5)
    ]
    assert all(run == runs[0] for run in runs)


async def test_unclosed_code_does_not_yield_complete_version_with_untranslated_text(
    classifier: LinguaClassifier, glossary: Glossary
) -> None:
    body = (
        f"<p>{MIXED['de']} Befehl <code>ls</p>"
        "<p>Dieser zweite Absatz muss ebenfalls ins Ukrainische übersetzt werden.</p>"
    )
    outcome, translator, _ = await _outcome(
        ArticleText(ContentAccess.FULL, "de", body_html=body), classifier, glossary
    )
    sent = " ".join(translator.segments_sent)
    assert "Dieser zweite Absatz" in sent or not outcome.complete


# --- classifier на всіх 19 мовах (16 основних + extra U-2 + uk) --------------------------------
# Авторські синтетичні речення; незалежний від константи список мов (друга точка правди).
ALL_LANGUAGE_SAMPLES: dict[str, str] = {
    "de": "Der Stadtrat hat am Dienstag den Haushalt für das kommende Jahr beschlossen.",
    "fr": "Le conseil municipal a adopté mardi le budget pour l'année prochaine.",
    "en": "The city council approved the budget for the coming year on Tuesday.",
    "lt": "Miesto taryba antradienį patvirtino ateinančių metų biudžetą ir "
    "naujas mokyklų programas.",
    "lv": "Pilsētas dome otrdien apstiprināja nākamā gada budžetu un jaunas skolu programmas.",
    "et": "Linnavolikogu kinnitas teisipäeval järgmise aasta eelarve ja uued koolide programmid.",
    "pl": "Rada miasta zatwierdziła we wtorek budżet na przyszły rok oraz nowe programy dla szkół.",
    "hu": "A városi tanács kedden elfogadta a jövő évi költségvetést és az iskolák új programjait.",
    "ro": "Consiliul local a aprobat marți bugetul pentru anul viitor și noi "
    "programe pentru școli.",
    "cs": "Městské zastupitelstvo v úterý schválilo rozpočet na příští rok a "
    "nové programy pro školy.",
    "sk": "Mestské zastupiteľstvo v utorok schválilo rozpočet na budúci rok a "
    "nové programy pre školy.",
    "sl": "Mestni svet je v torek potrdil proračun za prihodnje leto in nove programe za šole.",
    "hr": "Gradsko vijeće u utorak je usvojilo proračun za sljedeću godinu i "
    "nove programe za škole.",
    "it": "Il consiglio comunale ha approvato martedì il bilancio per il prossimo anno e nuovi "
    "programmi per le scuole.",
    "es": "El ayuntamiento aprobó el martes el presupuesto para el próximo año y nuevos programas "
    "para las escuelas.",
    "nl": "De gemeenteraad heeft dinsdag de begroting voor het komende jaar goedgekeurd.",
    "ru": "Городской совет во вторник утвердил бюджет на следующий год для всех школ.",
    "ca": "L'ajuntament va aprovar dimarts el pressupost per a l'any vinent i nous programes per "
    "a les escoles.",
    "uk": "Міська рада у вівторок ухвалила бюджет на наступний рік для всіх шкіл.",
}
assert len(ALL_LANGUAGE_SAMPLES) == 19


@pytest.mark.parametrize("language", sorted(ALL_LANGUAGE_SAMPLES))
def test_classifier_detects_every_supported_language(
    classifier: LinguaClassifier, language: str
) -> None:
    detected, confidence = classifier.classify(ALL_LANGUAGE_SAMPLES[language])
    assert detected == language
    assert confidence >= 0.6


@pytest.mark.parametrize("language", sorted(ALL_LANGUAGE_SAMPLES))
def test_every_supported_language_article_gets_the_right_action(
    classifier: LinguaClassifier, language: str
) -> None:
    article = ArticleText(
        ContentAccess.FULL, None, body_html=f"<p>{ALL_LANGUAGE_SAMPLES[language]}</p>"
    )
    plan = plan_article_translation(article, classifier=classifier)
    assert plan.article_language.language == language
    assert plan.not_required is (language == "uk")
    expected = "keep" if language == "uk" else "translate"
    assert [p.action for p in plan.segments] == [expected]
