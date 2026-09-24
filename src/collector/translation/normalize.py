"""Нормалізація тексту для хешів і порогів (§12.2): оригінальний текст не змінюється."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
# ZWJ/ZWNJ змінюють зміст (emoji-послідовності, орфографія деяких писемностей) — лишаються.
_JOINERS = frozenset({"\N{ZERO WIDTH JOINER}", "\N{ZERO WIDTH NON-JOINER}"})


def normalize_text(text: str) -> str:
    """Unicode NFC, керівні символи → пробіл, format-символи (soft hyphen, ZWSP, bidi-мітки)
    прибрано, крім ZWJ/ZWNJ; пробіли згорнуто, trim."""
    nfc = unicodedata.normalize("NFC", text)
    cleaned = "".join(_replacement(ch) for ch in nfc)
    return _WHITESPACE.sub(" ", cleaned).strip()


def _replacement(ch: str) -> str:
    category = unicodedata.category(ch)
    if category == "Cc":
        return " "
    if category == "Cf" and ch not in _JOINERS:
        return ""
    return ch
