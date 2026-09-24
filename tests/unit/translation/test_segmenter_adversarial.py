"""Adversarial-тести segmenter/reassembly (незалежний тестувальник WP-04 PR1).

Властивості:
- identity: `reassemble(segment(x), [s.html ...]) == x` байт-у-байт для well-formed HTML;
- «переклад»: розмітка поза сегментами (теги, атрибути, коментарі, `script/style/pre/code`)
  лишається байт-у-байт, а повторна сегментація результату дає той самий скелет.

`hypothesis` у залежностях відсутній, тому property-based набір — детермінований генератор
на `random.Random(seed)` з фіксованими seed-ами (відтворюваний на будь-якій ОС).
"""

from __future__ import annotations

import html
import random
import re
from collections import Counter

import pytest
from tests.unit.translation.fakes import dom_events

from collector.translation.preservation import mask_segment, validate_preservation
from collector.translation.segmenter import SegmentedDocument, reassemble, segment_html

# --- байт-у-байт identity на ручних edge-кейсах ----------------------------------------------

BYTE_EXACT_CASES: list[tuple[str, str]] = [
    (
        "nested_inline",
        '<p>Die <a href="https://e.org/?a=1&amp;b=2#x"><strong>Seite</strong></a>.</p>',
    ),
    ("unclosed_inline", "<p>offen <b>fett <i>kursiv</p><p>weiter</p>"),
    ("implied_li", "<ul><li>eins<li>zwei<li>drei</ul>"),
    ("implied_td", "<table><tr><td>a<td>b<tr><td>c</table>"),
    ("stray_end_tags", "<p>x</p></div></span></section>"),
    ("entities", "<p>A &amp; B &lt;tag&gt; &quot;q&quot; &#39;s &#x27;t &euro; &copy;</p>"),
    ("nbsp", "<p>10&nbsp;km&nbsp;&nbsp;weit\u00a0und\u202fschmal</p>"),
    ("unknown_entity", "<p>b &unknown; c &xxe; d</p>"),
    ("escaped_script_text", "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"),
    ("script_mid", "<p>vor</p><script>if (a < b && c > d) { x = '</p>'; }</script><p>nach</p>"),
    ("script_in_p", "<p>a <script>var s = '<b>';</script> c</p>"),
    ("style", "<style>p > a { content: '<p>'; }</style><p>Text</p>"),
    ("pre_nested", "<pre><pre>x</pre>y <b>z</b></pre><p>b</p>"),
    ("pre_with_code", "<pre><code>for i in range(3):\n    print(i)</code></pre><p>Ende</p>"),
    ("inline_code", "<p>Befehl <code>rm -rf &lt;dir&gt;</code> nicht ausführen.</p>"),
    ("kbd_samp_var", "<p>Drücke <kbd>Ctrl</kbd>, <samp>ok</samp>, <var>x</var>.</p>"),
    ("attr_text", "<p title=\"Ein Titel &amp; mehr\" data-x='a > b'>Inhalt</p>"),
    ("img_alt", '<p>Bild <img src="/a.jpg" alt="Brücke bei Nacht"> hier</p>'),
    ("comments", "<!-- top --><p>a <!-- mitte --> b</p><!----><p>c</p>"),
    ("comment_with_tags", "<p>a <!-- <p>kein Block</p> --> b</p>"),
    ("processing_instr", "<p>a <?php echo 1 ?> b</p>"),
    ("doctype", '<!DOCTYPE html>\n<html lang="de"><body><p>Hallo</p></body></html>'),
    ("rtl", '<p dir="rtl">שלום <b>עולם</b> \u200fمرحبا \u202bembedded\u202c</p>'),
    ("emoji_zwj", "<p>👨\u200d👩\u200d👧 Familie 🏳️\u200d🌈 und 👍🏽</p>"),
    ("br_variants", "<p>Zeile<br>zwei<br/>drei<br />vier</p>"),
    ("translate_no", '<p>a <span translate="no">Markenname</span> b</p>'),
    ("translate_no_block", '<div translate="no"><p>Nicht übersetzen</p></div><p>Ja</p>'),
    ("crlf", "<p>a\r\nb</p>\r\n<p>c</p>"),
    ("bare_text", "nur Text ohne Tags"),
    ("whitespace_edges", "<p>   innen   </p>\n\t<p>\u00a0x\u00a0</p>"),
    ("lang_mix", '<article lang="de"><p>Hallo</p><p lang="fr">Bonjour</p></article>'),
    ("textarea", "<p>a <textarea><b>t</b></textarea> b</p>"),
    ("self_closing_code", "<p>a <code/> b</p>"),
    ("svg_math", "<p>x</p><svg><text>kein Text</text></svg><math><mi>y</mi></math><p>z</p>"),
    ("deep_inline", "<p>" + "<span>" * 50 + "tief" + "</span>" * 50 + "</p>"),
]


def _identity(document: SegmentedDocument) -> list[str]:
    return [s.html if s.kind == "text" else s.text for s in document.segments]


def _translate_masked(document: SegmentedDocument) -> list[str]:
    """Повний шлях: mask → «перекладач» (префікс) → validate → unmask."""
    results: list[str] = []
    for segment in document.segments:
        masked = mask_segment(segment)
        outcome = validate_preservation(masked, "УКР " + masked.text)
        assert outcome.ok, outcome.issues
        results.append(outcome.html if segment.kind == "text" else html.unescape(outcome.html))
    return results


def _skeleton(parts: tuple[str | int, ...]) -> list[str | None]:
    """Скелет документа: злиті сусідні сирі шматки (без порожніх) і `None` на місці слоту."""
    result: list[str | None] = []
    for part in parts:
        if isinstance(part, int):
            result.append(None)
        elif part:
            if result and isinstance(result[-1], str):
                result[-1] += part
            else:
                result.append(part)
    return result


def _markup_events(
    markup: str, translated_attributes: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    """Події DOM без тексту; значення перекладних атрибутів (`alt`/`title`) приховано."""
    hidden = tuple(f"{name}=" for name in translated_attributes)
    return [
        tuple(
            f"{item.split('=', 1)[0]}=*" if hidden and item.startswith(hidden) else item
            for item in event
        )
        for event in dom_events(markup)
        if event[0] != "text"
    ]


@pytest.mark.parametrize(
    ("name", "source"), BYTE_EXACT_CASES, ids=[name for name, _ in BYTE_EXACT_CASES]
)
@pytest.mark.parametrize("attributes", [(), ("alt", "title")], ids=["no-attrs", "alt-title"])
def test_identity_reassembly_is_byte_exact(
    name: str, source: str, attributes: tuple[str, ...]
) -> None:
    document = segment_html(source, translate_attributes=attributes)
    assert reassemble(document, _identity(document)) == source


PROTECTED_CONTENT: list[tuple[str, str]] = [
    ("<p>vor</p><script>var t = 'Hallo Welt, bitte nicht';</script>", "Hallo Welt, bitte nicht"),
    ("<style>.hinweis::after { content: 'Achtung'; }</style><p>x</p>", "Achtung"),
    ("<pre>Formatierter Text bleibt</pre><p>x</p>", "Formatierter Text bleibt"),
    ("<p>Nutze <code>git commit</code> jetzt.</p>", "git commit"),
    ("<noscript>Bitte JavaScript aktivieren</noscript><p>x</p>", "Bitte JavaScript aktivieren"),
    ('<p>a <span translate="no">Tagesschau Plus</span> b</p>', "Tagesschau Plus"),
    ("<p>a <!-- redaktioneller Kommentar --> b</p>", "redaktioneller Kommentar"),
    ("<template><p>Vorlage</p></template><p>x</p>", "Vorlage"),
]


@pytest.mark.parametrize(
    ("source", "needle"), PROTECTED_CONTENT, ids=[n for _, n in PROTECTED_CONTENT]
)
def test_protected_content_never_reaches_segment_text(source: str, needle: str) -> None:
    document = segment_html(source)
    assert all(needle not in segment.text for segment in document.segments)
    out = reassemble(document, _translate_masked(document))
    assert needle in out


def test_attribute_values_are_never_segment_text_unless_configured() -> None:
    source = (
        '<p title="Titeltext" data-info="Datentext"><a href="https://e.org/x?q=Suche">Link</a></p>'
    )
    document = segment_html(source)
    assert [s.text for s in document.segments] == ["Link"]
    out = reassemble(document, [document.segments[0].html.replace("Link", "Посилання")])
    assert out == source.replace(">Link<", ">Посилання<")


def test_attribute_segment_escapes_hostile_translation() -> None:
    source = '<p><img src="/a.jpg" alt="Brücke"> Text</p>'
    document = segment_html(source, translate_attributes=("alt",))
    translations = _identity(document)
    alt = next(s for s in document.segments if s.kind == "attribute")
    translations[alt.index] = '"><script>alert(1)</script>'
    out = reassemble(document, translations)
    assert "<script>" not in out
    assert 'alt="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in out


def test_noncharacter_slot_markers_in_input_cannot_forge_attribute_slots() -> None:
    source = '<p><img alt="Bild"> \ufdd00\ufdd1 Text</p>'
    document = segment_html(source, translate_attributes=("alt",))
    out = reassemble(document, ["ALT", "TEXT"][: len(document.segments)])
    assert out.count("ALT") == 1


# --- DOM-еквівалентні (але не байт-у-байт) нормалізації stdlib-парсера ------------------------
# Зафіксовано у звіті тестувальника як low: DOM не змінюється, байти — так.

DOM_ONLY_CASES: list[tuple[str, str]] = [
    ("uppercase_end_tag", "<P>Upper</P>"),
    ("end_tag_whitespace", "<p>ws</p >"),
    ("amp_without_semicolon", "<p>AT&amp T</p>"),
    ("nbsp_without_semicolon", "<p>x &nbsp y</p>"),
    ("charref_without_semicolon", "<p>&#39 r</p>"),
    ("comment_bang_close", "<p>a<!--x--!>b</p>"),
]


@pytest.mark.parametrize(("name", "source"), DOM_ONLY_CASES, ids=[n for n, _ in DOM_ONLY_CASES])
def test_parser_normalizations_keep_dom_equivalent(name: str, source: str) -> None:
    document = segment_html(source)
    assert dom_events(reassemble(document, _identity(document))) == dom_events(source)


# --- знайдені дефекти (strict xfail: червоніє, щойно дефект виправлено) -----------------------


def test_cdata_section_is_byte_exact() -> None:
    source = "<p>a <![CDATA[x < y]]> b</p>"
    document = segment_html(source)
    assert reassemble(document, _identity(document)) == source


def test_unclosed_inline_code_does_not_swallow_following_paragraphs() -> None:
    source = "<p>Befehl <code>ls</p><p>Dieser Absatz muss übersetzt werden.</p>"
    document = segment_html(source)
    assert any("Dieser Absatz" in segment.text for segment in document.segments)


# --- property-based набір: детермінований генератор well-formed HTML --------------------------

_TEXTS = [
    "Der Rat hat beschlossen",
    "A &amp; B",
    "Preis 1.234,5&nbsp;€",
    "&lt;kein Tag&gt;",
    "&#39;zitiert&#x27;",
    "שלום עולם",
    "مرحبا\u200f",
    "👨\u200d👩\u200d👧 Familie",
    "Café\u0301",
    "   Leerraum   ",
    "Satz eins. Satz zwei! Satz drei?",
    "https://example.org/a?b=1&amp;c=2#frag",
    "mail@example.org",
    "\u00a0",
    "Ünïcödé ß",
]
_INLINE = ["a", "em", "strong", "b", "i", "span", "sup", "sub", "small", "q"]
_PROTECTED = [
    "<code>x &lt; y</code>",
    "<kbd>Ctrl</kbd>",
    '<span translate="no">Brand</span>',
    "<!-- c -->",
    "<br>",
    "<br/>",
    '<img src="/i.png" alt="Bildtext">',
]
_BLOCKS = ["p", "h2", "h3", "figcaption", "dd", "dt"]
_OPAQUE_BLOCKS = [
    "<script>var s = '<p>nicht</p>';</script>",
    "<style>p{color:red}</style>",
    "<pre>  roh\n  text </pre>",
    "<!-- Block-Kommentar -->",
]


def _inline(rng: random.Random, depth: int) -> str:
    pieces: list[str] = []
    for _ in range(rng.randint(1, 4)):
        roll = rng.random()
        if roll < 0.5 or depth > 3:
            pieces.append(rng.choice(_TEXTS))
        elif roll < 0.8:
            tag = rng.choice(_INLINE)
            attrs = ' href="https://e.org/?x=1&amp;y=2"' if tag == "a" else ""
            if tag == "span" and rng.random() < 0.3:
                attrs = ' lang="fr" title="Titel"'
            pieces.append(f"<{tag}{attrs}>{_inline(rng, depth + 1)}</{tag}>")
        else:
            pieces.append(rng.choice(_PROTECTED))
        if rng.random() < 0.4:
            pieces.append(rng.choice([" ", "\n", "  "]))
    return "".join(pieces)


def _block(rng: random.Random, depth: int) -> str:
    roll = rng.random()
    if roll < 0.45 or depth > 2:
        tag = rng.choice(_BLOCKS)
        return f"<{tag}>{_inline(rng, 0)}</{tag}>"
    if roll < 0.6:
        items = "".join(f"<li>{_inline(rng, 0)}</li>" for _ in range(rng.randint(1, 3)))
        tag = rng.choice(["ul", "ol"])
        return f"<{tag}>{items}</{tag}>"
    if roll < 0.7:
        return f"<blockquote>{_block(rng, depth + 1)}</blockquote>"
    if roll < 0.8:
        cells = "".join(f"<td>{_inline(rng, 0)}</td>" for _ in range(rng.randint(1, 3)))
        return f"<table><tr><th>Kopf</th></tr><tr>{cells}</tr></table>"
    if roll < 0.9:
        return rng.choice(_OPAQUE_BLOCKS)
    return f"<div>{_block(rng, depth + 1)}{_block(rng, depth + 1)}</div>"


def _document(seed: int) -> str:
    rng = random.Random(seed)  # noqa: S311 - детермінований генератор тест-даних
    body = "\n".join(_block(rng, 0) for _ in range(rng.randint(1, 6)))
    return f'<article lang="de">{body}</article>' if rng.random() < 0.5 else body


SEEDS = list(range(300))
_TAG_RE = re.compile(r"<[^>]+>")


@pytest.mark.parametrize("seed", SEEDS)
def test_property_identity_roundtrip_is_byte_exact(seed: int) -> None:
    source = _document(seed)
    for attributes in ((), ("alt", "title")):
        document = segment_html(source, translate_attributes=attributes)
        assert reassemble(document, _identity(document)) == source


@pytest.mark.parametrize("seed", SEEDS)
def test_property_markup_outside_segments_survives_translation(seed: int) -> None:
    source = _document(seed)
    document = segment_html(source)
    translations = [f"ПЕРЕКЛАД{index}" for index in range(len(document.segments))]
    out = reassemble(document, translations)
    # Кожен текстовий сегмент замінено маркером; решта (теги, коментарі, script) — ті самі байти.
    expected = "".join(
        part if isinstance(part, str) else translations[part] for part in document.parts
    )
    assert out == expected
    # Мультимножина тегів поза сегментами й усередині (inline-теги сегментів замінено) —
    # теги, що були в `parts`, усі на місці; жоден тег не з'явився нізвідки.
    outside = Counter(
        tag for part in document.parts if isinstance(part, str) for tag in _TAG_RE.findall(part)
    )
    assert Counter(_TAG_RE.findall(out)) == outside
    # Повторна сегментація перекладу: той самий скелет і рівно наші маркери як сегменти.
    again = segment_html(out)
    assert [s.html for s in again.segments] == translations
    assert _skeleton(again.parts) == _skeleton(document.parts)


@pytest.mark.parametrize("seed", SEEDS)
def test_property_masked_translation_keeps_every_tag_and_attribute(seed: int) -> None:
    source = _document(seed)
    for attributes in ((), ("alt", "title")):
        document = segment_html(source, translate_attributes=attributes)
        out = reassemble(document, _translate_masked(document))
        # Послідовність тегів (з атрибутами), коментарів і декларацій ідентична — змінився
        # лише текст; «УКР » з'явився рівно стільки разів, скільки текстових сегментів.
        assert _markup_events(out, attributes) == _markup_events(source, attributes)
        assert out.count("УКР ") == len(document.segments)


@pytest.mark.parametrize("seed", SEEDS[:100])
def test_property_segments_are_deterministic_and_nonempty(seed: int) -> None:
    source = _document(seed)
    first, second = segment_html(source), segment_html(source)
    assert first == second
    for segment in first.segments:
        assert segment.text.strip(), f"порожній сегмент: {segment!r}"
        assert segment.html == segment.html.strip() or segment.kind == "attribute"


@pytest.mark.parametrize("seed", SEEDS[:100])
def test_property_sentence_split_preserves_bytes(seed: int) -> None:
    source = _document(seed)
    for limit in (5, 20, 60):
        document = segment_html(source, max_segment_chars=limit)
        assert reassemble(document, _identity(document)) == source
