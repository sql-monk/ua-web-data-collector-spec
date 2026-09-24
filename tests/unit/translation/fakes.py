"""In-memory fakes портів перекладу для unit-тестів WP-04 (без мережі й провайдера)."""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from html.parser import HTMLParser

from collector.translation.memory import InMemoryTranslationMemory, MemoryEntry

UK_PREFIX = "УКР "


class FakeTranslator:
    """Детермінований «переклад»: префікс `УКР ` до masked-тексту; placeholder-и не чіпає.

    `mutate` дозволяє зіпсувати результат (для негативних тестів preservation).
    """

    def __init__(self, mutate: Callable[[str], str] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], str, str]] = []
        self._mutate = mutate

    async def __call__(
        self, texts: Sequence[str], source_language: str, target_language: str
    ) -> list[str]:
        self.calls.append((tuple(texts), source_language, target_language))
        results = [UK_PREFIX + text for text in texts]
        return [self._mutate(text) for text in results] if self._mutate else results

    @property
    def segments_sent(self) -> list[str]:
        return [text for texts, _, _ in self.calls for text in texts]


class RecordingMemory(InMemoryTranslationMemory):
    """InMemoryTranslationMemory з лічильниками викликів порту."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[tuple[str, ...]] = []
        self.put_calls: list[tuple[MemoryEntry, ...]] = []

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, MemoryEntry]:
        self.get_calls.append(tuple(keys))
        return await super().get_many(keys)

    async def put_many(self, entries: Sequence[MemoryEntry]) -> None:
        self.put_calls.append(tuple(entries))
        await super().put_many(entries)


class FixedClassifier:
    """Classifier за словником підрядок → мова (для детермінованих unit-кейсів)."""

    def __init__(self, rules: Mapping[str, tuple[str, float]], default: str | None = None) -> None:
        self.rules = rules
        self.default = default
        self.calls: list[str] = []

    def classify(self, text: str) -> tuple[str | None, float]:
        ranked = self.ranked(text)
        return ranked[0] if ranked else (None, 0.0)

    def ranked(self, text: str) -> list[tuple[str, float]]:
        self.calls.append(text)
        for needle, result in self.rules.items():
            if needle in text:
                return [result]
        return [(self.default, 0.99)] if self.default else []


def dom_events(markup: str) -> list[tuple[str, ...]]:
    """Нормалізоване дерево як послідовність подій: теги з відсортованими атрибутами і
    текст зі згорнутими пробілами (порожній текст відкидається). Порівняння DOM, не рядків."""
    parser = _EventParser()
    parser.feed(markup)
    parser.close()
    return parser.events


class _EventParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple[str, ...]] = []
        self._text: list[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._text)).strip()
        self._text = []
        if text:
            self.events.append(("text", text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._flush()
        self.events.append(("start", tag, *_attrs(attrs)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self._flush()
        self.events.append(("end", tag))

    def handle_data(self, data: str) -> None:
        self._text.append(data)

    def handle_comment(self, data: str) -> None:
        self._flush()
        self.events.append(("comment", data.strip()))

    def handle_decl(self, decl: str) -> None:
        self._flush()
        self.events.append(("decl", decl))

    def close(self) -> None:
        super().close()
        self._flush()


def _attrs(attrs: Iterable[tuple[str, str | None]]) -> list[str]:
    return sorted(f"{key}={html.unescape(value or '')}" for key, value in attrs)
