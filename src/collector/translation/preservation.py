"""Маскування і перевірка preservation (§10 крок 12, §12.1).

`mask_segment` замінює inline-теги, захищені блоки (`code`), URL, e-mail, терміни glossary,
дати і числа (з відсотками/валютами, у форматах `1.234,5` / `1 234,5` / `1,234.5`) на
placeholder-и `<x id="N"/>` до запиту провайдеру. `validate_preservation` після перекладу
перевіряє: кожен placeholder рівно один раз, парні inline-теги не переставлені й не
перехрещені, мультимножина чисел збігається після нормалізації формату, URL і e-mail
побайтово ті самі, inline-теги збігаються за мультимножиною. Провал — список issue-кодів,
а не «тихий» пропуск: сегмент отримує quality flag `preservation_failed`.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from collector.translation.glossary import GlossaryEntry
from collector.translation.segmenter import Segment, matched_tag_pairs

PlaceholderKind = Literal["tag", "protected", "url", "email", "glossary", "date", "number"]
PreservationIssue = Literal[
    "placeholder_missing",
    "placeholder_duplicated",
    "placeholder_unknown",
    "tag_order_broken",
    "tag_mismatch",
    "number_mismatch",
    "url_mismatch",
    "email_mismatch",
]

PLACEHOLDER_TEMPLATE = '<x id="{}"/>'
# Провайдер у режимі text/html може повернути `<x id="1"></x>` — це той самий placeholder.
PLACEHOLDER_RE = re.compile(r'<x\s+id="(\d+)"\s*/?>(?:\s*</x>)?')

_GROUP_SEPARATORS = " \N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}'\N{RIGHT SINGLE QUOTATION MARK}"
_CURRENCY = r"(?:€|\$|£|zł|Ft|Kč|lei|kn|CHF|EUR|USD|GBP|PLN|HUF|CZK|RON|SEK|DKK|NOK|BGN|UAH|грн)"
_NUMBER = rf"\d{{1,3}}(?:[{_GROUP_SEPARATORS}.,]\d{{3}})+(?:[.,]\d+)?(?!\d)|\d+(?:[.,]\d+)?"
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'«»]+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?)]}»”’\"'"
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DATE_RE = re.compile(r"(?<!\d)(?:\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{4}-\d{2}-\d{2})(?![\d])")
_AMOUNT_RE = re.compile(
    rf"(?<![\w.,])(?:{_CURRENCY}\s?)?(?:{_NUMBER})(?:\s?(?:%|‰|{_CURRENCY}(?!\w)))?(?!\d)"
)
# Знак перед числом (дефіс-мінус, U+2212, en dash — типографський мінус у de/fr/pl): лише
# впритул до цифри і не після літери/цифри, тож `COVID-19` і діапазон `10\N{EN DASH}15` знаком не є.
# Знак лишається текстом для провайдера (може локалізувати форму мінуса), але валідатор
# порівнює числа зі знаком: втрата мінуса (інверсія значення) — `number_mismatch`.
_SIGNS = "-\N{MINUS SIGN}\N{EN DASH}"
_BARE_NUMBER_RE = re.compile(rf"(?<![\d.,])(?:(?<![\w.,])[{_SIGNS}](?=\d))?(?:{_NUMBER})")
_NUMERIC_PATTERNS: tuple[tuple[re.Pattern[str], PlaceholderKind], ...] = (
    (_DATE_RE, "date"),
    (_AMOUNT_RE, "number"),
)
_TAG_RE = re.compile(r"<!--.*?-->|<[^>]*>", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Placeholder:
    """`source` — вихідний фрагмент; `replacement` — HTML, що підставляється після перекладу."""

    id: int
    kind: PlaceholderKind
    source: str
    replacement: str


@dataclass(frozen=True, slots=True)
class MaskedSegment:
    """Текст для провайдера і все, що потрібно для зворотної підстановки й перевірки."""

    text: str
    source_html: str
    placeholders: tuple[Placeholder, ...]
    tag_pairs: tuple[tuple[int, int], ...]

    def unmask(self, translated: str) -> str:
        """Підставляє placeholder-и; невідомі id лишаються як є (їх ловить валідатор)."""
        by_id = {placeholder.id: placeholder.replacement for placeholder in self.placeholders}

        def replace(match: re.Match[str]) -> str:
            return by_id.get(int(match.group(1)), match.group(0))

        return PLACEHOLDER_RE.sub(replace, translated)


@dataclass(frozen=True, slots=True)
class PreservationResult:
    """`html` — переклад після підстановки placeholder-ів; валідний лише при `ok`."""

    issues: tuple[PreservationIssue, ...]
    html: str

    @property
    def ok(self) -> bool:
        return not self.issues


def mask_segment(segment: Segment, glossary: Sequence[GlossaryEntry] = ()) -> MaskedSegment:
    """Сегмент → masked HTML. Текст між placeholder-ами лишається HTML-escaped."""
    placeholders: list[Placeholder] = []

    def add(kind: PlaceholderKind, source: str, replacement: str) -> str:
        placeholder = Placeholder(len(placeholders) + 1, kind, source, replacement)
        placeholders.append(placeholder)
        return PLACEHOLDER_TEMPLATE.format(placeholder.id)

    token_ids: dict[int, int] = {}
    pieces: list[str] = []
    for index, token in enumerate(segment.tokens):
        if token.kind == "text":
            pieces.append(_mask_text(html.unescape(token.raw), glossary, add))
            continue
        pieces.append(add("tag" if token.kind == "tag" else "protected", token.raw, token.raw))
        token_ids[index] = len(placeholders)
    pairs = tuple(
        (token_ids[open_], token_ids[close]) for open_, close in matched_tag_pairs(segment.tokens)
    )
    return MaskedSegment("".join(pieces), segment.html, tuple(placeholders), pairs)


def validate_preservation(masked: MaskedSegment, translated: str) -> PreservationResult:
    """Перевіряє переклад masked-тексту (до підстановки) проти джерела; див. docstring модуля."""
    issues: list[PreservationIssue] = []
    found = [int(match.group(1)) for match in PLACEHOLDER_RE.finditer(translated)]
    counts = Counter(found)
    expected = {placeholder.id for placeholder in masked.placeholders}
    if expected - counts.keys():
        issues.append("placeholder_missing")
    if any(count > 1 for count in counts.values()):
        issues.append("placeholder_duplicated")
    if counts.keys() - expected:
        issues.append("placeholder_unknown")
    if _tags_reordered(masked.tag_pairs, found):
        issues.append("tag_order_broken")
    restored = masked.unmask(translated)
    if Counter(_tags(restored)) != Counter(_tags(masked.source_html)):
        issues.append("tag_mismatch")
    source_text, restored_text = _visible_text(masked.source_html), _visible_text(restored)
    if _numbers(source_text) != _numbers(restored_text):
        issues.append("number_mismatch")
    if Counter(_urls(source_text)) != Counter(_urls(restored_text)):
        issues.append("url_mismatch")
    if Counter(_EMAIL_RE.findall(source_text)) != Counter(_EMAIL_RE.findall(restored_text)):
        issues.append("email_mismatch")
    return PreservationResult(tuple(dict.fromkeys(issues)), restored)


def normalize_number(raw: str) -> str:
    """`1.234,5`, `1 234,5`, `1,234.5` → `1234.5`; `3,5` і `3.5` → `3.5`.

    Останній із двох різних роздільників — десятковий; одиночний роздільник із рівно трьома
    цифрами після нього — групування (у джерелі й перекладі трактується однаково).
    """
    sign = "-" if raw[:1] in _SIGNS else ""
    digits = "".join(ch for ch in raw.lstrip(_SIGNS) if ch not in _GROUP_SEPARATORS)
    return sign + _normalize_unsigned(digits)


def _normalize_unsigned(digits: str) -> str:
    marks = [ch for ch in digits if ch in ".,"]
    if not marks:
        return digits
    if len(set(marks)) == 2:
        decimal = marks[-1]
        integer, _, fraction = digits.rpartition(decimal)
        return integer.replace(".", "").replace(",", "") + "." + fraction
    mark = marks[0]
    head, _, tail = digits.rpartition(mark)
    if len(marks) > 1 or len(tail) == 3:
        return digits.replace(mark, "")
    return head + "." + tail


def _mask_text(
    plain: str,
    glossary: Sequence[GlossaryEntry],
    add: Callable[[PlaceholderKind, str, str], str],
) -> str:
    spans: list[tuple[int, int, PlaceholderKind, str]] = []

    def claim(start: int, end: int, kind: PlaceholderKind, replacement: str) -> None:
        if start < end and all(end <= s or start >= e for s, e, _, _ in spans):
            spans.append((start, end, kind, replacement))

    for start, end in _url_spans(plain):
        claim(start, end, "url", plain[start:end])
    for match in _EMAIL_RE.finditer(plain):
        claim(match.start(), match.end(), "email", match.group(0))
    for entry in sorted(glossary, key=lambda item: len(item.term), reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(entry.term)}(?!\w)", plain):
            claim(match.start(), match.end(), "glossary", entry.replacement)
    for regex, kind in _NUMERIC_PATTERNS:
        for match in regex.finditer(plain):
            claim(match.start(), match.end(), kind, match.group(0))
    pieces: list[str] = []
    position = 0
    for start, end, kind, replacement in sorted(spans):
        pieces.append(html.escape(plain[position:start], quote=False))
        pieces.append(add(kind, plain[start:end], html.escape(replacement, quote=False)))
        position = end
    pieces.append(html.escape(plain[position:], quote=False))
    return "".join(pieces)


def _url_spans(plain: str) -> Iterable[tuple[int, int]]:
    for match in _URL_RE.finditer(plain):
        url = match.group(0)
        # Кінцева пунктуація — не частина URL, окрім збалансованої `)` (`/wiki/Bus_(Verkehr)`).
        while url and url[-1] in _URL_TRAILING:
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            url = url[:-1]
        yield match.start(), match.start() + len(url)


def _urls(text: str) -> list[str]:
    return [text[start:end] for start, end in _url_spans(text)]


def _numbers(text: str) -> Counter[str]:
    """Числа видимого тексту поза URL/e-mail, нормалізовані за форматом."""
    for start, end in sorted(
        [*_url_spans(text), *((m.start(), m.end()) for m in _EMAIL_RE.finditer(text))],
        reverse=True,
    ):
        text = text[:start] + " " + text[end:]
    return Counter(normalize_number(match.group(0)) for match in _BARE_NUMBER_RE.finditer(text))


def _tags(fragment: str) -> list[str]:
    return [match.group(0) for match in _TAG_RE.finditer(fragment)]


def _visible_text(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", fragment))


def _tags_reordered(pairs: Sequence[tuple[int, int]], found: Sequence[int]) -> bool:
    position = {placeholder_id: index for index, placeholder_id in enumerate(found)}
    spans = [
        (position[open_], position[close])
        for open_, close in pairs
        if open_ in position and close in position
    ]
    if any(start > end for start, end in spans):
        return True
    return any(a[0] < b[0] < a[1] < b[1] for a in spans for b in spans)
