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
        "a", "abbr", "b", "bdi", "bdo", "br", "cite", "data", "del", "dfn", "em", "font", "i",
        "img", "ins", "mark", "q", "s", "small", "span", "strong", "sub", "sup", "time", "u",
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

    def document(self) -> SegmentedDocument:
        return SegmentedDocument(tuple(self._parts), tuple(self._segments), self._root_lang)

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
        if skip is not None and not self._open[tag]:
            skip.buffer.append(raw)
            return
        if skip is not None:
            # Закривається предок незакритого захищеного елемента (`<p><code>ls</p>`): як і
            # браузер, обмежуємо захищену область батьківським блоком — далі звичайний текст.
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
            self._skip = _Skip(tag, inline, [raw])
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
        for name, value in attrs:
            if name.lower() not in self._attributes or not value or not value.strip():
                continue
            pattern = re.compile(
                rf"(\s{re.escape(name)}\s*=\s*)(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))",
                re.IGNORECASE,
            )
            match = pattern.search(raw)
            if match is None:
                continue
            value_raw = next(group for group in match.groups()[1:] if group is not None)
            index = len(self._segments)
            context_lang = lang or self._context()[1]
            token = Token("text", value_raw)
            self._segments.append(Segment(index, "attribute", tag, context_lang, (token,)))
            slot = f'{match.group(1)}"{_ATTR_SLOT.format(index)}"'
            raw = raw[: match.start()] + slot + raw[match.end() :]
        return raw

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
        if not self._run:
            self._run_context = self._context()
        if token.kind == "text" and self._run and self._run[-1].kind == "text":
            token = Token("text", self._run[-1].raw + token.raw)
            self._run[-1] = token
            return
        self._run.append(token)

    def _text(self, raw: str) -> None:
        if self._skip is not None:
            self._skip.buffer.append(raw)
        else:
            self._append(Token("text", raw))

    def _opaque(self, raw: str) -> None:
        if self._skip is not None:
            self._skip.buffer.append(raw)
        elif self._run:
            self._append(Token("protected", raw))
        else:
            self._parts.append(raw)

    def _flush(self) -> None:
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
