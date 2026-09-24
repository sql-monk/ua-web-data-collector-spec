"""Маскування і preservation 100% (картка WP-04 PR1, вимога 7; §10 крок 12, §12.1)."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from collector.translation.glossary import GlossaryEntry
from collector.translation.preservation import (
    PLACEHOLDER_RE,
    MaskedSegment,
    mask_segment,
    normalize_number,
    validate_preservation,
)
from collector.translation.segmenter import segment_html

GLOSSARY = (
    GlossaryEntry("Bundestag", "Бундестаг"),
    GlossaryEntry("Politico Europe", None),
)
# (опис, HTML-сегмент, фрагменти, які мають стати placeholder-ами)
CASES: list[tuple[str, str, list[str]]] = [
    ("de-number", "Die Kosten betragen 1.234,5 Euro.", ["1.234,5"]),
    ("fr-number", "Le budget atteint 1 234,5 millions.", ["1 234,5"]),
    ("en-number", "The fund holds 1,234.5 million.", ["1,234.5"]),
    ("date-dotted", "Die Sitzung ist am 05.03.2024 geplant.", ["05.03.2024"]),
    ("date-iso", "Deadline: 2024-03-05.", ["2024-03-05"]),
    ("percent", "Die Inflation stieg auf 2,4 %.", ["2,4 %"]),
    ("euro", "Ein Ticket kostet € 49 im Monat.", ["€ 49"]),
    ("zloty", "Bilet kosztuje 120 zł miesięcznie.", ["120 zł"]),
    ("forint", "A jegy ára 9 500 Ft havonta.", ["9 500 Ft"]),
    ("url-query", "Mehr unter https://example.org/a?b=1&amp;c=2 heute.", ["https://example.org/a?b=1&c=2"]),
    ("email", "Fragen an presse@example.org senden.", ["presse@example.org"]),
    ("glossary-name", "Der Bundestag stimmte zu, berichtet Politico Europe.",
     ["Bundestag", "Politico Europe"]),
]  # fmt: skip
assert CASES


def _masked(markup: str) -> MaskedSegment:
    (segment,) = segment_html(f"<p>{markup}</p>").segments
    return mask_segment(segment, GLOSSARY)


def _translate(masked: MaskedSegment) -> str:
    """Коректний fake-переклад: інший текст, ті самі placeholder-и в тому самому порядку."""
    return "Переклад: " + masked.text


@pytest.mark.parametrize(("name", "markup", "protected"), CASES, ids=[c[0] for c in CASES])
def test_correct_translation_passes_and_protected_values_are_restored(
    name: str, markup: str, protected: list[str]
) -> None:
    masked = _masked(markup)
    sources = [placeholder.source for placeholder in masked.placeholders]
    for fragment in protected:
        assert fragment in sources, f"{name}: {fragment!r} не замасковано"
        assert fragment not in masked.text
    result = validate_preservation(masked, _translate(masked))
    assert result.ok, result.issues
    assert result.html.startswith("Переклад: ")


def test_glossary_target_and_do_not_translate_are_substituted() -> None:
    masked = _masked("Der Bundestag stimmte zu, berichtet Politico Europe.")
    result = validate_preservation(masked, _translate(masked))
    assert "Бундестаг" in result.html and "Politico Europe" in result.html
    assert "Bundestag" not in result.html


def test_inline_tags_become_placeholders_and_href_is_byte_identical() -> None:
    masked = _masked('Siehe <a href="https://example.org/x?y=1&amp;z=2"><strong>hier</strong></a>.')
    assert "<a" not in masked.text and "href" not in masked.text
    assert len(masked.tag_pairs) == 2
    result = validate_preservation(masked, _translate(masked))
    assert result.ok
    assert '<a href="https://example.org/x?y=1&amp;z=2"><strong>' in result.html


def test_placeholder_in_open_close_form_is_accepted() -> None:
    masked = _masked("Es kamen 5 Gäste.")
    translated = re.sub(r'<x id="(\d+)"/>', r'<x id="\1"></x>', _translate(masked))
    assert validate_preservation(masked, translated).ok


# --- мутації: кожна ловиться окремо ---------------------------------------------------------

MUTATION_SOURCE = (
    'Am 05.03.2024 kostete es 1.234,5 € laut <a href="https://example.org/q?a=1">Bericht</a> '
    "von presse@example.org und https://example.org/data?x=2."
)


def _swap_digit(text: str) -> str:
    """Замість placeholder-а числа — саме число зі зміненою цифрою."""
    return PLACEHOLDER_RE.sub(lambda m: "1.234,6 €" if m.group(1) == "2" else m.group(0), text)


def _drop_url(text: str) -> str:
    return PLACEHOLDER_RE.sub(lambda m: "" if m.group(1) == "6" else m.group(0), text)


def _rewrite_url(text: str) -> str:
    return PLACEHOLDER_RE.sub(
        lambda m: "https://example.org/data?x=3" if m.group(1) == "6" else m.group(0), text
    )


def _swap_tag_order(text: str) -> str:
    """Переставляє placeholder-и `<a>` і `</a>` місцями (закриття перед відкриттям)."""
    return (
        text.replace('<x id="3"/>', "\0")
        .replace('<x id="4"/>', '<x id="3"/>')
        .replace("\0", '<x id="4"/>')
    )


def _drop_closing_a(text: str) -> str:
    return text.replace('<x id="4"/>', "")


def _extra_digit(text: str) -> str:
    return text + " 7"


def _duplicate(text: str) -> str:
    return text + ' <x id="1"/>'


def _unknown(text: str) -> str:
    return text + ' <x id="99"/>'


def _inject_tag(text: str) -> str:
    return "<b>" + text + "</b>"


MUTATIONS: list[tuple[Callable[[str], str], set[str]]] = [
    (_swap_digit, {"placeholder_missing", "number_mismatch"}),
    (_drop_url, {"placeholder_missing", "url_mismatch"}),
    (_rewrite_url, {"placeholder_missing", "url_mismatch"}),
    (_swap_tag_order, {"tag_order_broken"}),
    (_drop_closing_a, {"placeholder_missing", "tag_mismatch"}),
    (_extra_digit, {"number_mismatch"}),
    (_duplicate, {"placeholder_duplicated"}),
    (_unknown, {"placeholder_unknown"}),
    (_inject_tag, {"tag_mismatch"}),
]
assert MUTATIONS


def test_mutation_source_placeholder_layout() -> None:
    masked = _masked(MUTATION_SOURCE)
    kinds = [(p.id, p.kind, p.source) for p in masked.placeholders]
    assert kinds == [
        (1, "date", "05.03.2024"),
        (2, "number", "1.234,5 €"),
        (3, "tag", '<a href="https://example.org/q?a=1">'),
        (4, "tag", "</a>"),
        (5, "email", "presse@example.org"),
        (6, "url", "https://example.org/data?x=2"),
    ]


@pytest.mark.parametrize(("mutate", "expected"), MUTATIONS, ids=[m.__name__ for m, _ in MUTATIONS])
def test_each_mutation_is_caught(mutate: Callable[[str], str], expected: set[str]) -> None:
    masked = _masked(MUTATION_SOURCE)
    assert validate_preservation(masked, _translate(masked)).ok
    result = validate_preservation(masked, mutate(_translate(masked)))
    assert not result.ok
    assert expected <= set(result.issues), result.issues


def test_crossed_tags_are_caught() -> None:
    masked = _masked("A <b>fett <i>kursiv</i></b> Ende.")
    # <b> <i> </b> </i> — перехрещення пар.
    translated = 'А <x id="1"/>жирний <x id="2"/>курсив<x id="4"/><x id="3"/> кінець.'
    assert "tag_order_broken" in validate_preservation(masked, translated).issues


def test_legitimate_word_reordering_is_not_a_failure() -> None:
    masked = _masked("Am 05.03.2024 kamen 5 Gäste.")
    # Порядок двох незалежних placeholder-ів змінився — це нормальний переклад.
    translated = '<x id="2"/> гостей прийшли <x id="1"/>.'
    assert validate_preservation(masked, translated).ok


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("1.234,5", "1234.5"),
        ("1 234,5", "1234.5"),
        ("1,234.5", "1234.5"),
        ("1\u202f234,5", "1234.5"),
        ("3,5", "3.5"),
        ("3.5", "3.5"),
        ("1.234.567", "1234567"),
        ("2024", "2024"),
    ],
)
def test_number_normalization_across_locales(raw: str, normalized: str) -> None:
    assert normalize_number(raw) == normalized


def test_locale_reformatting_of_unmasked_number_is_accepted() -> None:
    # Номер у protected-токені лишається тим самим; провайдер переформатував вільне число.
    masked = _masked("Es waren <code>v2</code> und 1.234,5 Tonnen.")
    translated = masked.text.replace("Es waren", "Було")
    translated = PLACEHOLDER_RE.sub(
        lambda m: (
            "1 234,5" if masked.placeholders[int(m.group(1)) - 1].kind == "number" else m.group(0)
        ),
        translated,
    )
    result = validate_preservation(masked, translated)
    assert "number_mismatch" not in result.issues
    assert result.issues == ("placeholder_missing",)  # placeholder усе одно обов'язковий
