"""Segmenter/reassembly (картка WP-04 PR1, вимоги 1–2; §10 крок 11, §16.1 п.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.translation.fakes import dom_events

from collector.translation.normalize import normalize_text
from collector.translation.segmenter import (
    SegmentedDocument,
    reassemble,
    segment_html,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "translation" / "html"
HTML_FIXTURES = sorted(FIXTURES.glob("*.html"))
assert HTML_FIXTURES, "tests/fixtures/translation/html порожня — параметризація не має бути пустою"


def _identity(document: SegmentedDocument) -> list[str]:
    return [s.html if s.kind == "text" else s.text for s in document.segments]


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("path", HTML_FIXTURES, ids=lambda p: p.name)
@pytest.mark.parametrize("attributes", [(), ("alt", "title")], ids=["no-attrs", "alt-title"])
def test_identity_reassembly_restores_equivalent_dom(
    path: Path, attributes: tuple[str, ...]
) -> None:
    source = path.read_text(encoding="utf-8")
    document = segment_html(source, translate_attributes=attributes)
    assert dom_events(reassemble(document, _identity(document))) == dom_events(source)


def test_nested_inline_markup_stays_inside_one_segment() -> None:
    document = segment_html(_read("inline_nested.html"))
    link = next(s for s in document.segments if "Projektseite" in s.text)
    assert '<a href="https://example.org/plan?id=42&amp;lang=de"><strong>' in link.html
    assert link.html.endswith("</strong></a>.")
    assert link.block == "p"
    quote = next(s for s in document.segments if "sicher" in s.text)
    assert quote.block == "p"  # blockquote>p → сегмент по p, тег i всередині
    assert quote.html == "Wir wollen, dass mehr Menschen <i>sicher</i> mit dem Rad fahren."
    costs = next(s for s in document.segments if "Kosten" in s.text)
    assert "<br>" in costs.html and "Frühjahr" in costs.html  # br не рве сегмент


def test_lists_and_table_cells_are_separate_segments() -> None:
    document = segment_html(_read("lists_table.html"))
    blocks = [(s.block, s.text) for s in document.segments]
    assert ("li", "The committee approved the draft after a long discussion.") in blocks
    assert ("li", "Two amendments were rejected:") in blocks  # текст li до вкладеного ol
    assert ("li", "the transport levy;") in blocks
    assert ("th", "Region") in blocks and ("td", "12,5 %") in blocks
    assert ("dt", "Levy") in blocks and ("dd", "A fee paid by every visitor staying overnight.")
    fees = next(s for s in document.segments if "parking" in s.text)
    assert fees.html == 'the <a href="https://example.org/fees">new parking fees</a>.'


def test_code_pre_script_style_and_translate_no_are_not_segments() -> None:
    source = _read("figure_code.html")
    document = segment_html(source)
    texts = " ".join(s.html for s in document.segments)
    assert "range(3)" not in texts
    assert "tracking" not in texts
    assert "color" not in texts
    assert "Nazwa programu" not in texts
    script = next(s for s in document.segments if "skrypt" in s.text)
    # Inline code — захищений токен усередині сегмента, не окремий сегмент.
    assert [t.kind for t in script.tokens] == ["text", "protected", "text"]
    assert "2024" not in script.text
    assert all("make report" not in s.text for s in document.segments)
    translated = reassemble(document, ["X" for _ in document.segments])
    for untouched in ('print("nie tłumaczyć")', 'var tracking = "nie tłumaczyć";', "color: #333"):
        assert untouched in translated


def test_attributes_are_segments_only_when_configured() -> None:
    source = _read("figure_code.html")
    assert all(s.kind == "text" for s in segment_html(source).segments)
    document = segment_html(source, translate_attributes=("alt",))
    alt = [s for s in document.segments if s.kind == "attribute"]
    assert [s.text for s in alt] == ["Nowy most nad rzeką o zachodzie słońca"]
    translations = _identity(document)
    translations[alt[0].index] = 'Новий міст "на заході"'
    out = reassemble(document, translations)
    assert 'alt="Новий міст &quot;на заході&quot;"' in out
    assert 'src="https://example.org/img/bridge.jpg"' in out  # src не перекладається


def test_href_is_unchanged_after_translation() -> None:
    document = segment_html(_read("inline_nested.html"))
    out = reassemble(document, ["Т" + s.html for s in document.segments])
    assert 'href="https://example.org/plan?id=42&amp;lang=de"' in out


def test_long_block_is_split_on_sentences_only_above_limit() -> None:
    sentence = "Das ist ein ziemlich langer Satz über den Verkehr in der Stadt."
    source = "<p>" + " ".join([sentence] * 6) + " <b>Ende</b> des Absatzes.</p>"
    assert len(segment_html(source).segments) == 1
    document = segment_html(source, max_segment_chars=150)
    assert len(document.segments) > 1
    assert all(len(s.text) <= 150 for s in document.segments)
    assert document.segments[-1].html.endswith("<b>Ende</b> des Absatzes.")
    assert dom_events(reassemble(document, _identity(document))) == dom_events(source)


def test_sentence_split_never_breaks_inside_inline_tag() -> None:
    inner = "Erster Satz. Zweiter Satz. Dritter Satz."
    source = f"<p><a href='https://example.org'>{inner}</a> Rest.</p>"
    document = segment_html(source, max_segment_chars=15)
    link = next(s for s in document.segments if "Erster" in s.text)
    assert link.html.startswith("<a href='https://example.org'>") and "</a>" in link.html


def test_hostile_html_does_not_crash_or_resolve_entities() -> None:
    source = _read("hostile.html")
    document = segment_html(source)
    joined = " ".join(s.text for s in document.segments)
    assert "&xxe;" in joined and "&boom;" in joined  # буквально, без resolve
    assert "passwd" not in joined.replace("file:///etc/passwd", "")
    assert dom_events(reassemble(document, _identity(document))) == dom_events(source)


def test_very_deep_nesting_does_not_exhaust_stack() -> None:
    depth = 20_000
    source = "<div>" * depth + "<p>Tief <span>" * 200 + "innen" + "</span>" * 200 + "</p>"
    source += "</div>" * depth
    document = segment_html(source)
    assert len(document.segments) >= 1
    assert "innen" in document.segments[-1].text
    assert reassemble(document, _identity(document)) == source


def test_unbalanced_inline_tags_at_segment_edges_stay_outside_segment() -> None:
    source = "<p><a href='https://example.org'>Text<div>Block</div>mehr</a></p>"
    document = segment_html(source)
    assert [s.html for s in document.segments] == ["Text", "Block", "mehr"]
    assert dom_events(reassemble(document, _identity(document))) == dom_events(source)


def test_empty_and_whitespace_only_input_has_no_segments() -> None:
    assert segment_html(_read("empty.html")).segments == ()
    assert segment_html("").segments == ()
    assert segment_html("<p> <br> </p><div>\n</div>").segments == ()


def test_lang_attribute_is_captured_per_segment_and_root() -> None:
    document = segment_html(
        '<article lang="de"><p>Hallo Welt</p><blockquote lang="en"><p>Quote</p></blockquote>'
        "</article>"
    )
    assert document.root_lang == "de"
    assert [s.lang for s in document.segments] == ["de", "en"]


def test_reassemble_rejects_wrong_number_of_translations() -> None:
    document = segment_html("<p>Eins</p><p>Zwei</p>")
    with pytest.raises(ValueError, match="2 сегментів"):
        reassemble(document, ["Один"])


def test_normalization_for_hash_does_not_touch_original() -> None:
    original = "Cafe\u0301\u00a0 \t Bundes\u00adtag\n"
    assert normalize_text(original) == "Café Bundestag"
    assert original == "Cafe\u0301\u00a0 \t Bundes\u00adtag\n"


def test_root_lang_requires_single_top_level_element() -> None:
    assert segment_html('<p lang="en">One</p><p>Two</p>').root_lang is None
    assert (
        segment_html('<!DOCTYPE html>\n<html lang="lt"><body><p>A</p></body></html>').root_lang
        == "lt"
    )
