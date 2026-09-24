"""Нормалізація тексту для хешів і порогів (§12.2): оригінальний текст не змінюється."""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Unicode NFC, керівні символи → пробіл, format-символи (soft hyphen, ZWSP) прибрано,
    пробіли згорнуто, trim."""
    nfc = unicodedata.normalize("NFC", text)
    cleaned = "".join(_replacement(ch) for ch in nfc)
    return _WHITESPACE.sub(" ", cleaned).strip()


def _replacement(ch: str) -> str:
    category = unicodedata.category(ch)
    if category == "Cc":
        return " "
    return "" if category == "Cf" else ch
