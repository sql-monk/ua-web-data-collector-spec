"""Adversarial-тести маскування і preservation (незалежний тестувальник WP-04 PR1).

§10 крок 12, §12.1: «placeholder/URL/числа в перекладі збережені на 100%». Перекладач, що
псує маску будь-яким способом, мусить бути виявлений валідатором, а коректний переклад
(включно з легітимною перестановкою слів) — проходити.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from collector.translation.glossary import load_glossary
from collector.translation.preservation import (
    MaskedSegment,
    mask_segment,
    validate_preservation,
)
from collector.translation.segmenter import segment_html

GLOSSARY = load_glossary()
NBSP = "\N{NO-BREAK SPACE}"
NNBSP = "\N{NARROW NO-BREAK SPACE}"


def _mask(markup: str, language: str = "de") -> MaskedSegment:
    segments = segment_html(markup).segments
    assert len(segments) == 1, segments
    return mask_segment(segments[0], GLOSSARY.entries_for(language))


# --- коректний переклад проходить і відновлює значення побайтово ------------------------------

PRESERVED: list[tuple[str, str, str, list[str]]] = [
    (
        "de_number",
        "de",
        "<p>Kosten 1.234,5 € und 12.300.000 Euro.</p>",
        ["1.234,5 €", "12.300.000"],
    ),
    ("fr_number_space", "fr", f"<p>Prix 1 234,5 € et 2{NBSP}500 habitants.</p>", ["1 234,5 €"]),
    ("fr_number_nnbsp", "fr", f"<p>Total 10{NNBSP}000 personnes.</p>", [f"10{NNBSP}000"]),
    ("en_number", "en", "<p>It cost 1,234.5 USD, or $3.2 per head.</p>", ["1,234.5 USD", "$3.2"]),
    ("ch_apostrophe", "de", "<p>Betrag 1'234.50 CHF heute.</p>", ["1'234.50 CHF"]),
    ("pl_currency", "pl", "<p>Koszt 12 500 zł, czyli 45 % budżetu.</p>", ["12 500 zł", "45 %"]),
    ("hu_currency", "hu", "<p>Az ár 3 000 Ft volt.</p>", ["3 000 Ft"]),
    ("cs_currency", "cs", "<p>Cena 250 Kč za kus.</p>", ["250 Kč"]),
    (
        "dates",
        "de",
        "<p>Am 12.03.2024 und am 2024-03-12 sowie 1/2/25.</p>",
        ["12.03.2024", "2024-03-12", "1/2/25"],
    ),
    ("percent_permille", "de", "<p>Plus 4,5% und 3 ‰.</p>", ["4,5%", "3 ‰"]),
    (
        "url_query_fragment",
        "de",
        "<p>Siehe https://example.org/a/b?x=1&amp;y=%C3%A4#abschnitt-2 bitte.</p>",
        ["https://example.org/a/b?x=1&amp;y=%C3%A4#abschnitt-2"],
    ),
    ("url_www", "de", "<p>Mehr auf www.example.org/info.</p>", ["www.example.org/info"]),
    (
        "email",
        "de",
        "<p>Kontakt: presse.stelle+news@stadt.example.co.uk heute.</p>",
        ["presse.stelle+news@stadt.example.co.uk"],
    ),
    ("glossary_target", "de", "<p>Der Bundestag stimmte zu.</p>", ["Бундестаг"]),
    (
        "glossary_keep",
        "de",
        "<p>Laut Tagesschau und Politico Europe.</p>",
        ["Tagesschau", "Politico Europe"],
    ),
    ("glossary_any", "fr", "<p>L'OTAN et NATO se réunissent.</p>", ["НАТО"]),
    (
        "link_with_number",
        "de",
        '<p>Die <a href="https://e.org/p?id=42&amp;l=de">Seite 42</a> nennt 7 Punkte.</p>',
        ['href="https://e.org/p?id=42&amp;l=de"'],
    ),
]


@pytest.mark.parametrize(
    ("name", "language", "markup", "expected"), PRESERVED, ids=[c[0] for c in PRESERVED]
)
def test_correct_translation_preserves_values_byte_for_byte(
    name: str, language: str, markup: str, expected: list[str]
) -> None:
    masked = _mask(markup, language)
    # Легітимний переклад: інший порядок слів, placeholder-и в іншому місці.
    ids = re.findall(r'<x id="\d+"/>', masked.text)
    translated = "УКР " + " ".join(reversed(ids)) + " кінець"
    result = validate_preservation(masked, translated)
    tag_ids = {p.id for p in masked.placeholders if p.kind == "tag"}
    if tag_ids:  # парні теги не можна переставляти — лишаємо вихідний порядок
        result = validate_preservation(masked, "УКР " + masked.text)
    assert result.ok, result.issues
    for value in expected:
        assert value in result.html


# --- перекладач псує маску: кожна мутація виявлена --------------------------------------------

SOURCE = (
    '<p>Am 12.03.2024 kostete es 1.234,5 € laut <a href="https://e.org/?q=1&amp;r=2">Bericht</a>, '
    "siehe https://example.org/x?y=1#z oder mail@example.org; der Bundestag tagt.</p>"
)


def _swap_first_two_ids(text: str) -> str:
    return (
        text.replace('<x id="1"/>', "@@")
        .replace('<x id="2"/>', '<x id="1"/>')
        .replace("@@", '<x id="2"/>')
    )


MASK_CORRUPTIONS: list[tuple[str, Callable[[str], str]]] = [
    ("drop_placeholder", lambda t: t.replace('<x id="2"/>', "", 1)),
    ("duplicate_placeholder", lambda t: t + ' <x id="2"/>'),
    ("unknown_placeholder", lambda t: t + ' <x id="99"/>'),
    ("single_quotes", lambda t: t.replace('<x id="1"/>', "<x id='1'/>")),
    ("space_after_lt", lambda t: t.replace('<x id="1"/>', '< x id="1"/>')),
    ("uppercase", lambda t: t.replace('<x id="1"/>', '<X ID="1"/>')),
    ("html_escaped", lambda t: t.replace('<x id="1"/>', '&lt;x id="1"/&gt;')),
    ("id_renumbered", lambda t: t.replace('<x id="3"/>', '<x id="30"/>')),
    ("placeholder_translated", lambda t: t.replace('<x id="2"/>', "1234,5 €")),
    ("placeholder_to_digits", lambda t: t.replace('<x id="1"/>', "13.03.2024")),
    ("extra_number", lambda t: t + " 7"),
    ("extra_url", lambda t: t + " https://evil.example/"),
    ("extra_email", lambda t: t + " x@evil.example"),
    ("extra_tag", lambda t: t + " <script>alert(1)</script>"),
    ("extra_closing_tag", lambda t: t + "</a>"),
    ("all_placeholders_removed", lambda t: re.sub(r'<x id="\d+"/>', "", t)),
    ("empty_translation", lambda t: ""),
    ("whitespace_translation", lambda t: "   "),
]


@pytest.mark.parametrize(
    ("name", "corrupt"), MASK_CORRUPTIONS, ids=[n for n, _ in MASK_CORRUPTIONS]
)
def test_translator_corrupting_mask_is_detected(name: str, corrupt: Callable[[str], str]) -> None:
    masked = _mask(SOURCE)
    assert validate_preservation(masked, "УКР " + masked.text).ok
    result = validate_preservation(masked, corrupt("УКР " + masked.text))
    assert not result.ok, name


def test_closing_tag_before_opening_tag_is_detected() -> None:
    masked = _mask(SOURCE)
    tag_ids = [p.id for p in masked.placeholders if p.kind == "tag"]
    assert len(tag_ids) == 2
    swapped = (
        masked.text.replace(f'<x id="{tag_ids[0]}"/>', "@@")
        .replace(f'<x id="{tag_ids[1]}"/>', f'<x id="{tag_ids[0]}"/>')
        .replace("@@", f'<x id="{tag_ids[1]}"/>')
    )
    assert "tag_order_broken" in validate_preservation(masked, swapped).issues


def test_placeholder_in_unmasked_number_text_changed_is_detected() -> None:
    # Число, яке маска не захопила (B2B), валідатор усе одно звіряє у видимому тексті.
    masked = _mask("<p>Das B2B-Geschäft wuchs.</p>")
    assert "number_mismatch" in validate_preservation(masked, "УКР B3B-бізнес").issues


def test_unmasked_number_kept_verbatim_passes() -> None:
    masked = _mask("<p>Das Modell B2B hat 3 Varianten.</p>")
    ok = validate_preservation(masked, masked.text.replace("Das", "Модель"))
    assert ok.ok


def test_masked_number_value_is_restored_not_retranslated() -> None:
    masked = _mask("<p>Es kostet 1.234,5 € pro Jahr.</p>")
    result = validate_preservation(masked, "Це коштує " + masked.text.split("kostet ")[1])
    assert result.ok
    assert "1.234,5 €" in result.html


def test_swapping_two_numeric_placeholders_is_accepted_as_reordering() -> None:
    """Факт для рев'ю: перестановку значень `5 von 10` → `10 von 5` валідатор не бачить —
    перестановка placeholder-ів легітимна для зміни порядку слів (межа методу)."""
    masked = _mask("<p>Es war 5 von 10.</p>")
    assert validate_preservation(masked, _swap_first_two_ids(masked.text)).ok


# --- знайдені дефекти (strict xfail) -----------------------------------------------------------


@pytest.mark.parametrize("minus", ["\N{MINUS SIGN}", "-"], ids=["U+2212", "hyphen"])
def test_dropped_minus_sign_is_detected(minus: str) -> None:
    masked = _mask(f"<p>Die Temperatur sank auf {minus}5 Grad.</p>")
    corrupted = ("УКР " + masked.text).replace(minus, "")
    assert not validate_preservation(masked, corrupted).ok


@pytest.mark.xfail(
    strict=True,
    reason="finding T-4: закривальна `)` URL (Wikipedia-стиль) відрізається від URL-маски; "
    "перекладач, що губить `)`, ламає URL непомітно",
)
def test_url_with_trailing_parenthesis_is_preserved() -> None:
    masked = _mask("<p>Siehe https://de.wikipedia.org/wiki/Bus_(Verkehr) jetzt.</p>")
    corrupted = ("УКР " + masked.text).replace(")", "")
    assert not validate_preservation(masked, corrupted).ok
