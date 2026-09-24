"""Versioned glossary перекладу (§10 крок 12, FR-017): терміни/імена per language pair → `uk`.

Файл (`default.yaml` у цьому пакеті або явний шлях) описує пари `<source_language>` (або
`"*"` — будь-яка вихідна мова) зі списком записів `{term, target}` (українська форма) чи
`{term, keep: true}` (do-not-translate). `Glossary.version` — SHA-256 canonical JSON
канонізованого змісту (пари «термін → форма» у порядку файлу),
тож зміна одного терміна дає нову версію і, через TM key, новий переклад. Терміни
застосовуються маскуванням (`preservation.mask_segment`): до провайдера йде placeholder,
після — підстановка форми з glossary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Final

import yaml

from collector.contracts import canonical_sha256
from collector.translation.languages import TARGET_LANGUAGE, normalize_language_tag

GLOSSARY_SCHEMA: Final = 1
ANY_SOURCE_LANGUAGE: Final = "*"


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    """`target=None` — do-not-translate: термін доходить до перекладу незмінним."""

    term: str
    target: str | None

    @property
    def replacement(self) -> str:
        return self.term if self.target is None else self.target


@dataclass(frozen=True, slots=True)
class Glossary:
    version: str
    pairs: Mapping[str, tuple[GlossaryEntry, ...]]

    def entries_for(self, source_language: str) -> tuple[GlossaryEntry, ...]:
        """Записи для пари `source_language → uk`: спільні (`"*"`) + специфічні для мови."""
        return self.pairs.get(ANY_SOURCE_LANGUAGE, ()) + self.pairs.get(source_language, ())


def parse_glossary(data: object) -> Glossary:
    """Валідує вміст glossary-файлу; помилка формату — `ValueError` (не тихий пропуск)."""
    if not isinstance(data, Mapping):
        raise ValueError("glossary: очікується mapping")
    if data.get("schema") != GLOSSARY_SCHEMA:
        raise ValueError(f"glossary: підтримується лише schema {GLOSSARY_SCHEMA}")
    if data.get("target_language") != TARGET_LANGUAGE:
        raise ValueError(f"glossary: target_language має бути {TARGET_LANGUAGE!r}")
    raw_pairs = data.get("pairs")
    if not isinstance(raw_pairs, Mapping):
        raise ValueError("glossary: `pairs` має бути mapping")
    pairs: dict[str, tuple[GlossaryEntry, ...]] = {}
    for language, items in raw_pairs.items():
        if language != ANY_SOURCE_LANGUAGE and normalize_language_tag(language) != language:
            raise ValueError(f"glossary: невалідна мова пари {language!r}")
        if not isinstance(items, list):
            raise ValueError(f"glossary[{language}]: очікується список записів")
        entries = tuple(_entry(language, item) for item in items)
        terms = [entry.term for entry in entries]
        if len(terms) != len(set(terms)):
            raise ValueError(f"glossary[{language}]: термін повторюється")
        pairs[language] = entries
    # Версія — від канонізованого змісту (терміни після strip, target або do-not-translate), а
    # не від сирого YAML: `keep: false`, пробіли, порядок ключів і сторонні ключі її не
    # змінюють. Порядок записів у списку лишається значущим (консервативно, T-8).
    canonical = {
        "schema": GLOSSARY_SCHEMA,
        "target_language": TARGET_LANGUAGE,
        "pairs": {
            language: [[entry.term, entry.target] for entry in entries]
            for language, entries in pairs.items()
            if entries
        },
    }
    return Glossary(version=canonical_sha256(canonical), pairs=pairs)


def load_glossary(path: Path | None = None) -> Glossary:
    """Читає glossary-файл (default — `default.yaml` цього пакета); `yaml.safe_load`."""
    if path is None:
        text = resources.files(__package__).joinpath("default.yaml").read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return parse_glossary(yaml.safe_load(text))


def _entry(language: str, item: object) -> GlossaryEntry:
    if not isinstance(item, Mapping) or not isinstance(item.get("term"), str):
        raise ValueError(f"glossary[{language}]: запис без рядкового `term`")
    term: str = item["term"].strip()
    keep = item.get("keep", False)
    target = item.get("target")
    if not term or (keep is True) == (target is not None):
        raise ValueError(f"glossary[{language}]: {term!r} — рівно одне з `target` або `keep: true`")
    if target is not None and (not isinstance(target, str) or not target.strip()):
        raise ValueError(f"glossary[{language}]: {term!r} — `target` має бути непорожнім рядком")
    return GlossaryEntry(term, None if keep is True else target)
