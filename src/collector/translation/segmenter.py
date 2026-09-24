"""HTML-безпечний segmenter і reassembly (§10 крок 11, §16.1 п.1).

Cleaned HTML → впорядковані сегменти по блочних елементах (`p`, `li`, `h1–h6`,
`blockquote`, `figcaption`, `td/th`, `dt/dd`, …). Inline-розмітка (`a`, `em`, `strong`, `br`,
`span`, `sup/sub`, …) лишається всередині сегмента як токени, які `preservation.mask_segment`
замінює захищеними placeholder-ами. `code/kbd/samp/var` і елементи з `translate="no"` усередині
тексту — непрозорий захищений токен; `pre`, `script`, `style` тощо не стають сегментами.

Документ зберігається як послідовність незмінних шматків вихідного HTML і слотів сегментів,
тож `reassemble(doc, [s.html for s in doc.segments])` відтворює вхідне дерево. Парсер —
stdlib `html.parser` (без мережі; `<!ENTITY>` і DTD не обробляються, невідомі `&name;`
лишаються буквально); обхід ітеративний, тож глибока вкладеність не вичерпує стек.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal

INLINE_TAGS = frozenset(
    {
        "a", "abbr", "acronym", "b", "bdi", "bdo", "big", "br", "button", "cite", "data", "del",
        "dfn", "em", "font", "i", "img", "ins", "label", "mark", "nobr", "output", "q", "rp",
        "rt", "ruby", "s", "small", "span", "strike", "strong", "sub", "sup", "time", "tt", "u",
        "wbr",
    }
)  # fmt: skip
PROTECTED_INLINE_TAGS = frozenset({"code", "kbd", "samp", "var"})
SKIPPED_TAGS = frozenset(
    {"head", "math", "noscript", "pre", "script", "style", "svg", "template", "textarea"}
)
VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr",
    }
)  # fmt: skip
# Неявне закриття (HTML5 «optional end tags»): тег → (що закриває, межа пошуку в стеку).
_IMPLIED_END: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "li": (frozenset({"li"}), frozenset({"ul", "ol", "menu"})),
    "dt": (frozenset({"dt", "dd"}), frozenset({"dl"})),
    "dd": (frozenset({"dt", "dd"}), frozenset({"dl"})),
    "td": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "th": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "tr": (frozenset({"tr", "td", "th"}), frozenset({"table", "thead", "tbody", "tfoot"})),
}
# Слот перекладного атрибута (`alt`/`title`) усередині сирого тегу; U+FDD0/U+FDD1 —
# Unicode noncharacters, у вхідному HTML замінюються на U+FFFD.
_IMPLIED_END_SCAN = 256  # межа пошуку неявного закриття: hostile-вкладеність не дає O(n²)
_ATTR_SLOT = "\ufdd0{}\ufdd1"
_ATTR_SLOT_RE = re.compile("\ufdd0(\\d+)\ufdd1")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_SENTENCE_TAIL = re.compile(r"[.!?…][\"'»”’)]*$")

TokenKind = Literal["text", "tag", "protected"]
SegmentKind = Literal["text", "attribute"]


@dataclass(frozen=True, slots=True)
class Token:
    """Сирий шматок HTML усередині сегмента: текст (escaped), inline-тег або захищений блок."""

    kind: TokenKind
    raw: str
    name: str = ""
    closing: bool = False


@dataclass(frozen=True, slots=True)
class Segment:
    """Одиниця перекладу: inline-вміст одного блоку або значення атрибута `alt`/`title`."""

    index: int
    kind: SegmentKind
    block: str
    lang: str | None
    tokens: tuple[Token, ...]

    @property
    def html(self) -> str:
        """Вихідний HTML сегмента (для `attribute` — escaped значення атрибута)."""
        return "".join(token.raw for token in self.tokens)

    @property
    def text(self) -> str:
        """Видимий текст без розмітки і захищених токенів — для детекції мови й порогів."""
        pieces: list[str] = []
        for token in self.tokens:
            if token.kind == "text":
                pieces.append(html.unescape(token.raw))
            elif token.kind == "protected" or token.name == "br":
                pieces.append(" ")
        return "".join(pieces)


@dataclass(frozen=True, slots=True)
class SegmentedDocument:
    """Незмінні шматки вихідного HTML (`str`) і слоти текстових сегментів (`int`)."""

    parts: tuple[str | int, ...]
    segments: tuple[Segment, ...]
    root_lang: str | None
    # Незакритий захищений/пропущений елемент тягнувся до кінця документа: його текст не
    # перекладається (як і в DOM браузера), тож план ставить quality flag.
    unterminated_protected: bool = False


def matched_tag_pairs(tokens: Sequence[Token]) -> list[tuple[int, int]]:
    """Пари (open, close) inline-тегів зі строгим стековим зіставленням; решта — непарні."""
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for index, token in enumerate(tokens):
        if token.kind != "tag" or token.name in VOID_TAGS:
            continue
        if not token.closing:
            stack.append(index)
        elif stack and tokens[stack[-1]].name == token.name:
            pairs.append((stack.pop(), index))
    return pairs


def segment_html(
    source: str,
    *,
    max_segment_chars: int | None = None,
    translate_attributes: Iterable[str] = (),
) -> SegmentedDocument:
    """Розбиває cleaned HTML на сегменти.

    `max_segment_chars` — ліміт запиту провайдера: довший блок ділиться на речення поза
    inline-тегами (речення, довше за ліміт, лишається цілим). `translate_attributes` —
    атрибути (`alt`, `title`), значення яких стають окремими сегментами (`kind="attribute"`).
    """
    attributes = frozenset(name.lower() for name in translate_attributes)
    if attributes:
        source = source.replace("\ufdd0", "\ufffd").replace("\ufdd1", "\ufffd")
    parser = _Segmenter(attributes, max_segment_chars)
    parser.feed(source)
    parser.close()
    return parser.document()


def reassemble(document: SegmentedDocument, translations: Sequence[str]) -> str:
    """Збирає HTML: текстові сегменти — HTML-переклад, атрибутні — plain text (escape)."""
    if len(translations) != len(document.segments):
        msg = f"reassemble: {len(translations)} перекладів на {len(document.segments)} сегментів"
        raise ValueError(msg)
    joined = "".join(
        part if isinstance(part, str) else translations[part] for part in document.parts
    )
    if not any(segment.kind == "attribute" for segment in document.segments):
        return joined

    def attribute(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(document.segments) or document.segments[index].kind != "attribute":
            return "\ufffd"
        return html.escape(translations[index], quote=True)

    return _ATTR_SLOT_RE.sub(attribute, joined)


@dataclass(slots=True)
class _Skip:
    """Захищений/пропущений елемент: куди піде (`to_run` — токен у сегменті, інакше parts),
    глибина однойменних тегів і лічильник інших тегів, відкритих усередині."""

    name: str
    to_run: bool
    buffer: list[str]
    depth: int = 1
    inner: Counter[str] = field(default_factory=Counter)
    closers: Counter[str] = field(default_factory=Counter)


class _Segmenter(HTMLParser):
    def __init__(self, attributes: frozenset[str], max_chars: int | None) -> None:
        super().__init__(convert_charrefs=False)
        self._attributes = attributes
        self._max_chars = max_chars
        self._parts: list[str | int] = []
        self._segments: list[Segment] = []
        self._stack: list[tuple[str, str | None]] = []  # (блочний тег, успадкована мова)
        self._open: Counter[str] = Counter()
        self._run: list[Token] = []
        self._run_context: tuple[str, str | None] = ("", None)
        # Захищений/пропущений елемент: (тег, глибина, куди: run чи parts, буфер).
        self._skip: _Skip | None = None
        self._root_lang: str | None = None
        self._top_level = 0
        self._unterminated = False
        self._pending_text: list[str] = []  # сусідні text/entity-шматки до склеювання (O(n))

    def document(self) -> SegmentedDocument:
        return SegmentedDocument(
            tuple(self._parts), tuple(self._segments), self._root_lang, self._unterminated
        )

    # --- події парсера -------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self.get_starttag_text() or f"<{tag}>", void=tag in VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self.get_starttag_text() or f"<{tag}/>", void=True)

    def handle_endtag(self, tag: str) -> None:
        raw = f"</{tag}>"
        skip = self._skip
        if skip is not None and skip.inner[tag] > 0:
            skip.inner[tag] -= 1
            skip.buffer.append(raw)
            return
        if skip is not None and tag == skip.name:
            skip.buffer.append(raw)
            skip.depth -= 1
            if skip.depth == 0:
                self._end_skip()
            return
        if skip is not None and not skip.closers[tag]:
            skip.buffer.append(raw)
            return
        if skip is not None:
            # Закривається будь-який предок (блочний чи inline), відкритий до незакритого
            # захищеного елемента (`<p><code>ls</p>`, `<a><code>x</a>`): як і браузер,
            # завершуємо захищену область — далі звичайний текст.
            self._end_skip()
        if tag in INLINE_TAGS:
            self._append(Token("tag", raw, tag, closing=True))
            return
        self._flush()
        self._parts.append(raw)
        if self._open[tag]:
            for position in range(len(self._stack) - 1, -1, -1):
                if self._stack[position][0] == tag:
                    self._pop_to(position)
                    break

    def handle_data(self, data: str) -> None:
        self._text(data)

    def handle_entityref(self, name: str) -> None:
        self._text(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._text(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._opaque(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._opaque(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        # stdlib віддає `CDATA[x` без завершального `]]`; інші марковані секції — без `]`.
        self._opaque(f"<![{data}]]>" if data.startswith("CDATA[") else f"<![{data}]>")

    def handle_pi(self, data: str) -> None:
        self._opaque(f"<?{data}>")

    def close(self) -> None:
        super().close()
        if self._skip is not None:
            self._unterminated = True
            self._end_skip()
        self._flush()

    # --- внутрішнє -----------------------------------------------------------------------

    def _start(
        self, tag: str, attrs: list[tuple[str, str | None]], raw: str, *, void: bool
    ) -> None:
        skip = self._skip
        if skip is not None and skip.to_run and tag not in INLINE_TAGS | PROTECTED_INLINE_TAGS:
            # Блочний тег не може бути всередині inline `code`/`translate="no"`: область закрита.
            self._end_skip()
        elif skip is not None:
            skip.buffer.append(raw)
            if tag == skip.name and not void:
                skip.depth += 1
            elif not void:
                skip.inner[tag] += 1
            return
        attr_map = {key.lower(): value for key, value in attrs}
        lang = attr_map.get("lang")
        if not self._stack:
            # Мова документа — `lang` єдиного елемента верхнього рівня (`html`/обгортка).
            self._top_level += 1
            self._root_lang = lang if self._top_level == 1 else None
        no_translate = (attr_map.get("translate") or "").lower() == "no"
        inline = tag in INLINE_TAGS or tag in PROTECTED_INLINE_TAGS
        if tag in SKIPPED_TAGS or tag in PROTECTED_INLINE_TAGS or no_translate:
            if void and inline:
                self._opaque(raw)
            elif void:
                self._block_start(tag, raw, lang, void=True)
            if void:
                return
            if not inline:
                self._flush()
            self._skip = _Skip(tag, inline, [raw], closers=self._open_ancestors())
            return
        raw = self._attribute_slots(raw, tag, attrs, lang)
        if tag in INLINE_TAGS:
            self._append(Token("tag", raw, tag))
        else:
            self._block_start(tag, raw, lang, void=void)

    def _block_start(self, tag: str, raw: str, lang: str | None, *, void: bool) -> None:
        self._flush()
        if self._stack and self._stack[-1][0] == "p":
            self._pop_to(len(self._stack) - 1)
        closes, boundary = _IMPLIED_END.get(tag, (frozenset(), frozenset()))
        if any(self._open[name] for name in closes):
            lowest = max(len(self._stack) - _IMPLIED_END_SCAN, 0)
            for position in range(len(self._stack) - 1, lowest - 1, -1):
                name = self._stack[position][0]
                if name in boundary:
                    break
                if name in closes:
                    self._pop_to(position)
                    break
        self._parts.append(raw)
        if not void:
            # У стеку — успадкована мова: контекст сегмента береться за O(1).
            self._stack.append((tag, lang or (self._stack[-1][1] if self._stack else None)))
            self._open[tag] += 1

    def _pop_to(self, position: int) -> None:
        for name, _ in self._stack[position:]:
            self._open[name] -= 1
        del self._stack[position:]

    def _attribute_slots(
        self, raw: str, tag: str, attrs: list[tuple[str, str | None]], lang: str | None
    ) -> str:
        del attrs  # позиції значень — з токенайзера сирого тегу, не з regex-пошуку по тегу
        seen: set[str] = set()
        slots: list[tuple[int, int, int]] = []
        for name, value_start, value_end in _attribute_values(raw):
            if name in seen:  # HTML: діє перше входження атрибута, дублікати ігноруються
                continue
            seen.add(name)
            value_raw = raw[value_start:value_end]
            if value_raw[:1] in {'"', "'"}:
                value_raw = value_raw[1:-1]
            if name not in self._attributes or not html.unescape(value_raw).strip():
                continue
            index = len(self._segments)
            context_lang = lang or self._context()[1]
            token = Token("text", value_raw)
            self._segments.append(Segment(index, "attribute", tag, context_lang, (token,)))
            slots.append((value_start, value_end, index))
        for value_start, value_end, index in reversed(slots):
            raw = f'{raw[:value_start]}"{_ATTR_SLOT.format(index)}"{raw[value_end:]}'
        return raw

    def _open_ancestors(self) -> Counter[str]:
        """Усі відкриті предки: блочний стек + непарні inline-теги поточного run."""
        self._materialize_text()
        ancestors = Counter(name for name, _ in self._stack)
        inline: list[str] = []
        for token in self._run:
            if token.kind != "tag" or token.name in VOID_TAGS:
                continue
            if not token.closing:
                inline.append(token.name)
            elif inline and inline[-1] == token.name:
                inline.pop()
        ancestors.update(inline)
        return ancestors

    def _end_skip(self) -> None:
        skip, self._skip = self._skip, None
        if skip is None:
            return
        raw = "".join(skip.buffer)
        if skip.to_run:
            self._append(Token("protected", raw))
        else:
            self._parts.append(raw)

    def _context(self) -> tuple[str, str | None]:
        return self._stack[-1] if self._stack else ("", None)

    def _append(self, token: Token) -> None:
        if token.kind != "text":
            self._materialize_text()
        if not self._run:
            self._run_context = self._context()
        self._run.append(token)

    def _materialize_text(self) -> None:
        if self._pending_text:
            raw = "".join(self._pending_text)
            self._pending_text = []
            self._append(Token("text", raw))

    def _text(self, raw: str) -> None:
        if self._skip is not None:
            self._skip.buffer.append(raw)
        elif self._pending_text:
            self._pending_text.append(raw)
        elif self._run and self._run[-1].kind == "text":
            self._pending_text = [self._run.pop().raw, raw]
        else:
            self._pending_text = [raw]

    def _opaque(self, raw: str) -> None:
        if self._skip is not None:
            self._skip.buffer.append(raw)
        elif self._run or self._pending_text:
            self._append(Token("protected", raw))
        else:
            self._parts.append(raw)

    def _flush(self) -> None:
        self._materialize_text()
        run, self._run = self._run, []
        if not run:
            return
        matched = {index for pair in matched_tag_pairs(run) for index in pair}
        start, end = 0, len(run)
        while start < end and run[start].kind == "tag" and start not in matched:
            start += 1
        while end > start and run[end - 1].kind == "tag" and end - 1 not in matched:
            end -= 1
        self._parts.extend(token.raw for token in run[:start])
        for chunk in self._chunks(run[start:end]):
            self._emit(chunk)
        self._parts.extend(token.raw for token in run[end:])

    def _chunks(self, tokens: list[Token]) -> list[list[Token]]:
        """Ділить run на речення лише коли текст довший за ліміт запиту провайдера."""
        if self._max_chars is None or _plain_length(tokens) <= self._max_chars:
            return [tokens]
        pairs = matched_tag_pairs(tokens)
        depth_change = {open_: 1 for open_, _ in pairs} | {close: -1 for _, close in pairs}
        units: list[list[Token]] = [[]]  # шматки, після яких можна різати (кінець речення)
        depth = 0
        for index, token in enumerate(tokens):
            depth += depth_change.get(index, 0)
            if token.kind != "text" or depth != 0:
                units[-1].append(token)
                continue
            for sentence in _split_keeping_whitespace(token.raw):
                units[-1].append(Token("text", sentence))
                if _SENTENCE_TAIL.search(sentence):
                    units.append([])
        chunks: list[list[Token]] = []
        for unit in units:
            if chunks and _plain_length(chunks[-1]) + _plain_length(unit) <= self._max_chars:
                chunks[-1].extend(unit)
            elif unit:
                chunks.append(list(unit))
        return chunks

    def _emit(self, tokens: list[Token]) -> None:
        tokens = [token for token in tokens if token.raw]
        if not any(token.kind == "text" and html.unescape(token.raw).strip() for token in tokens):
            self._parts.extend(token.raw for token in tokens)
            return
        if tokens[0].kind == "text":
            head = tokens[0].raw
            stripped = head.lstrip()
            self._parts.append(head[: len(head) - len(stripped)])
            tokens[0] = Token("text", stripped)
        tail = ""
        if tokens[-1].kind == "text":
            last = tokens[-1].raw
            stripped = last.rstrip()
            tail = last[len(stripped) :]
            tokens[-1] = Token("text", stripped)
        tokens = [token for token in tokens if token.raw]
        index = len(self._segments)
        block, lang = self._run_context
        self._segments.append(Segment(index, "text", block, lang, tuple(tokens)))
        self._parts.append(index)
        if tail:
            self._parts.append(tail)


_TAG_NAME_RE = re.compile(r"<[^\s/>]+")
_ATTR_NAME_RE = re.compile(r"[\s/]*([^\s/>=][^\s/>=]*)")
_ATTR_VALUE_RE = re.compile(r"""\s*=\s*("[^"]*"|'[^']*'|[^\s>]*)""")


def _attribute_values(raw: str) -> list[tuple[str, int, int]]:
    """Послідовний токенайзер атрибутів сирого стартового тегу (як у `html.parser`):
    (ім'я lowercase, початок, кінець значення разом із лапками). Вміст значень ніколи не
    читається як ім'я атрибута — на відміну від regex-пошуку по всьому тегу."""
    match = _TAG_NAME_RE.match(raw)
    position = match.end() if match else len(raw)
    result: list[tuple[str, int, int]] = []
    while position < len(raw):
        name = _ATTR_NAME_RE.match(raw, position)
        if name is None:
            break
        position = name.end()
        value = _ATTR_VALUE_RE.match(raw, position)
        if value is not None:
            result.append((name.group(1).lower(), value.start(1), value.end(1)))
            position = value.end()
    return result


def _plain_length(tokens: Iterable[Token]) -> int:
    return sum(len(html.unescape(token.raw)) for token in tokens if token.kind == "text")


def _split_keeping_whitespace(raw: str) -> list[str]:
    """`"A. B. C"` → `["A.", " B.", " C"]`: пробіл між реченнями — на початку наступного."""
    pieces: list[str] = []
    position = 0
    for match in _SENTENCE_END.finditer(raw):
        pieces.append(raw[position : match.start()])
        position = match.start()
    pieces.append(raw[position:])
    return [piece for piece in pieces if piece]
