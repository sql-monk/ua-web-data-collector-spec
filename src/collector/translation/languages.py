"""Мовна модель перекладу (§1 п.2, §8; рішення користувача U-2 / О-5 картки WP-04).

- `CORE_SOURCE_LANGUAGES` — 16 основних вихідних мов із golden corpus;
- `EXTRA_SOURCE_LANGUAGES` — default додаткових мов (`ru`, `ca`), які перекладаються з тими
  самими gates; фактичний набір — env `COLLECTOR_TRANSLATION_EXTRA_SOURCE_LANGUAGES`;
- `TARGET_LANGUAGE` — `uk`: цільова мова і мова UA-джерел (`not_required`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

TARGET_LANGUAGE: Final = "uk"
CORE_SOURCE_LANGUAGES: Final = frozenset(
    {"de", "fr", "en", "lt", "lv", "et", "pl", "hu", "ro", "cs", "sk", "sl", "hr", "it", "es", "nl"}
)
EXTRA_SOURCE_LANGUAGES: Final = frozenset({"ru", "ca"})
EXTRA_SOURCE_LANGUAGES_ENV: Final = "COLLECTOR_TRANSLATION_EXTRA_SOURCE_LANGUAGES"

_ISO_639_1 = re.compile(r"^[a-z]{2}$")


def normalize_language_tag(tag: str | None) -> str | None:
    """BCP 47 / locale (`de-AT`, `pt_BR`, `DE`) → ISO 639-1 lowercase; невалідне → `None`."""
    if tag is None:
        return None
    primary = re.split(r"[-_]", tag.strip(), maxsplit=1)[0].lower()
    return primary if _ISO_639_1.match(primary) else None


def parse_extra_source_languages(raw: str | None) -> frozenset[str]:
    """Значення env `COLLECTOR_TRANSLATION_EXTRA_SOURCE_LANGUAGES` (коми) → набір мов.

    `None` (змінну не задано) → default `EXTRA_SOURCE_LANGUAGES`; порожній рядок → жодної
    додаткової мови. `uk` і невалідні коди відхиляються — конфіг не може «тихо» звузитися.
    """
    if raw is None:
        return EXTRA_SOURCE_LANGUAGES
    result: set[str] = set()
    for item in raw.split(","):
        if not item.strip():
            continue
        code = normalize_language_tag(item)
        if code is None or code != item.strip().lower():
            msg = f"{EXTRA_SOURCE_LANGUAGES_ENV}: невалідний ISO 639-1 код {item.strip()!r}"
            raise ValueError(msg)
        if code == TARGET_LANGUAGE:
            msg = f"{EXTRA_SOURCE_LANGUAGES_ENV}: {TARGET_LANGUAGE!r} — цільова мова, не вихідна"
            raise ValueError(msg)
        result.add(code)
    return frozenset(result)


def supported_source_languages(extra: Iterable[str] = EXTRA_SOURCE_LANGUAGES) -> frozenset[str]:
    """Мови, сегменти яких надсилаються провайдеру: 16 основних + додаткові (U-2)."""
    return CORE_SOURCE_LANGUAGES | frozenset(extra)
