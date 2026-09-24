"""Потокове читання body з лімітом **розпакованих** байтів і ratio-guard проти bomb (§13).

Декодування `Content-Encoding` робиться тут, а не в httpx: його декодери викликають
`zlib.decompress` без `max_length`, і один 64 КБ чанк gzip-bomb розгортається в ~64 МБ до
будь-якої перевірки. Тут кожен крок видає не більше `CHUNK_LIMIT` байтів (`max_length` у zlib,
`output_buffer_limit` у brotli>=1.2), лічильник перевіряється після кожного шматка, і читання
з мережі обривається на межі — пам'ять обмежена лімітом, а не розміром bomb.

Sitemap (`request_kind=sitemap`): якщо entity після `Content-Encoding` починається з gzip magic
(`.xml.gz` як `application/gzip`), зберігаються **стиснуті** bytes як отримано, а ліміт
100 МБ рахується після розпакування — окремим лічильним декодером.
"""

from __future__ import annotations

import zlib
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Protocol

import brotli  # type: ignore[import-untyped]  # brotli 1.2 не постачає stubs/py.typed

CHUNK_LIMIT = 64 * 1024
GZIP_MAGIC = b"\x1f\x8b"


class BodyLimitExceeded(Exception):  # noqa: N818 — рішення про карантин, а не збій
    """Body перевищив ліміт (`body_too_large`) або ratio-guard (`decompression_bomb`)."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class UnsupportedEncoding(Exception):  # noqa: N818
    """Невідомий `Content-Encoding` — body не можна безпечно декодувати."""


class Decoder(Protocol):
    def feed(self, data: bytes) -> Iterator[bytes]: ...

    def finish(self) -> Iterator[bytes]: ...


class ZlibDecoder:
    """gzip (у т.ч. multi-member) / zlib / raw deflate з обмеженим виходом на крок."""

    def __init__(self, wbits: int | None = None) -> None:
        self._wbits = wbits  # None — deflate: zlib чи raw визначається за першими байтами
        self._obj: zlib._Decompress | None = None
        self._obj_wbits = zlib.MAX_WBITS

    def feed(self, data: bytes) -> Iterator[bytes]:
        if self._obj is None:
            if not data:
                return
            wbits = self._wbits
            if wbits is None:
                has_header = (
                    len(data) >= 2
                    and (data[0] & 0x0F) == 8
                    and ((data[0] << 8) | data[1]) % 31 == 0
                )
                wbits = self._wbits = zlib.MAX_WBITS if has_header else -zlib.MAX_WBITS
            self._obj_wbits = wbits
            self._obj = zlib.decompressobj(wbits)
        while True:
            try:
                out = self._obj.decompress(data, CHUNK_LIMIT)
            except zlib.error as exc:
                raise BodyLimitExceeded("content_decoding_error", f"zlib: {exc}") from exc
            if out:
                yield out
            data = self._obj.unconsumed_tail
            if self._obj.eof and self._obj.unused_data:
                data = self._obj.unused_data  # наступний gzip member
                self._obj = zlib.decompressobj(self._obj_wbits)
                continue
            if not data and len(out) < CHUNK_LIMIT:
                return

    def finish(self) -> Iterator[bytes]:
        """Видати хвіст і відхилити усічений stream (gzip footer/zlib checksum включно)."""
        if self._obj is None:
            raise BodyLimitExceeded("content_decoding_error", "порожній compressed stream")
        tail = self._obj.flush()
        if tail:
            yield tail
        if not self._obj.eof:
            raise BodyLimitExceeded("content_decoding_error", "усічений zlib/gzip stream")


class BrotliDecoder:
    def __init__(self) -> None:
        self._obj = brotli.Decompressor()

    def feed(self, data: bytes) -> Iterator[bytes]:
        try:
            out = self._obj.process(data, output_buffer_limit=CHUNK_LIMIT)
            if out:
                yield out
            while not self._obj.can_accept_more_data():
                out = self._obj.process(b"", output_buffer_limit=CHUNK_LIMIT)
                if out:
                    yield out
        except brotli.error as exc:
            raise BodyLimitExceeded("content_decoding_error", f"brotli: {exc}") from exc

    def finish(self) -> Iterator[bytes]:
        """Brotli не має footer API, але `is_finished()` надійно відрізняє truncation."""
        if not self._obj.is_finished():
            raise BodyLimitExceeded("content_decoding_error", "усічений brotli stream")
        return iter(())


_DECODERS: dict[str, Callable[[], Decoder]] = {
    "gzip": lambda: ZlibDecoder(16 + zlib.MAX_WBITS),
    "x-gzip": lambda: ZlibDecoder(16 + zlib.MAX_WBITS),
    "deflate": ZlibDecoder,
    "br": BrotliDecoder,
}


def decoders_for(content_encoding: str | None) -> list[Decoder]:
    """Ланцюг декодерів у порядку застосування (зворотному до `Content-Encoding`)."""
    names = [n.strip().lower() for n in (content_encoding or "").split(",")]
    chain: list[Decoder] = []
    for name in reversed([n for n in names if n and n != "identity"]):
        factory = _DECODERS.get(name)
        if factory is None:
            raise UnsupportedEncoding(f"Content-Encoding {name!r} не підтримується")
        chain.append(factory())
    return chain


def _pipe(chain: list[Decoder], data: bytes) -> Iterator[bytes]:
    if not chain:
        yield data
        return
    head, rest = chain[0], chain[1:]
    for piece in head.feed(data):
        yield from _pipe(rest, piece)


def _finish(chain: list[Decoder]) -> Iterator[bytes]:
    """Завершити decoder chain зліва направо й перевірити EOF кожного шару."""
    if not chain:
        return
    head, rest = chain[0], chain[1:]
    for piece in head.finish():
        yield from _pipe(rest, piece)
    yield from _finish(rest)


@dataclass(slots=True)
class BodyLimits:
    max_bytes: int
    max_ratio: int
    ratio_min_bytes: int
    sitemap: bool = False


@dataclass(slots=True)
class Body:
    data: bytes
    received_bytes: int
    decoded_bytes: int
    stored_compressed: bool


async def read_body(
    raw: AsyncIterator[bytes],
    content_encoding: str | None,
    limits: BodyLimits,
    *,
    on_chunk: Callable[[], None] | None = None,
) -> Body:
    """Прочитати body чанками; перевищення → `BodyLimitExceeded` (викликач обриває з'єднання).

    `on_chunk` викликається перед обробкою кожного мережевого чанка (перевірка total deadline).
    """
    chain = decoders_for(content_encoding)
    # Sitemap: чи entity — gzip-файл, вирішується за накопиченим префіксом (≥ 2 байти), а не
    # за першим мережевим чанком — межі чанків контролює сервер (gate 2, F-1).
    decided = not limits.sitemap
    head = b""
    inner: list[Decoder] = []
    parts: list[bytes] = []
    received = decoded = 0

    def count(size: int) -> None:
        nonlocal decoded
        decoded += size
        if decoded > limits.max_bytes:
            msg = f"body перевищив {limits.max_bytes} байт після розпакування"
            raise BodyLimitExceeded("body_too_large", msg)
        if decoded > limits.ratio_min_bytes and decoded > limits.max_ratio * received:
            msg = f"ratio розпакування > {limits.max_ratio}"
            raise BodyLimitExceeded("decompression_bomb", msg)

    def consume(entity: bytes) -> None:
        parts.append(entity)
        if inner:
            for piece in _pipe(inner, entity):
                count(len(piece))
        else:
            count(len(entity))

    def consume_entity(entity: bytes) -> None:
        nonlocal decided, head
        if not decided:
            head += entity
            if len(head) < len(GZIP_MAGIC):
                return
            decided = True
            if head.startswith(GZIP_MAGIC):
                inner.append(ZlibDecoder(16 + zlib.MAX_WBITS))
            entity, head = head, b""
        consume(entity)

    async for chunk in raw:
        if on_chunk is not None:
            on_chunk()
        received += len(chunk)
        if received > limits.max_bytes:
            msg = f"отримано понад {limits.max_bytes} байт"
            raise BodyLimitExceeded("body_too_large", msg)
        for entity in _pipe(chain, chunk):
            consume_entity(entity)
    for entity in _finish(chain):
        consume_entity(entity)
    if head:  # entity коротший за magic — не gzip
        consume(head)
    for piece in _finish(inner):
        count(len(piece))
    return Body(
        data=b"".join(parts),
        received_bytes=received,
        decoded_bytes=decoded,
        stored_compressed=bool(inner),
    )
