"""Ліміти body і timeout (§13, §10): потоковий обрив на межі розпакованих байтів, bombs,
sitemap `.xml.gz` 100 МБ, `Content-Length` без читання, Q-002 media, total 60 с.

Bombs генеруються тестом (synthetic): gzip — 16 однакових members по 64 MiB нулів
(~1 МБ стиснуто → 1 GiB; multi-member gzip валідний за RFC 1952), brotli — 1 GiB нулів.
"""

from __future__ import annotations

import asyncio
import gzip
import tracemalloc
import zlib
from collections.abc import Iterator

import brotli  # type: ignore[import-untyped]  # stubs відсутні
import httpx
import pytest
import respx
from fetch_fakes import PUBLIC_IP, FakeResolver, http_response

from collector.contracts.enums import ContentAccess, FetchOutcome
from collector.fetch.client import FetchRequest
from collector.fetch.config import MIB

URL = "http://example.org/data"
MEMBER = 64 * MIB


def _resolver() -> FakeResolver:
    return FakeResolver({"example.org": [PUBLIC_IP]})


def _chunked(data: bytes, size: int = 64 * 1024) -> Iterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


@pytest.fixture(scope="module")
def gzip_bomb() -> bytes:
    member = zlib.compressobj(9, zlib.DEFLATED, 31)
    zero = bytes(MIB)
    body = b"".join(member.compress(zero) for _ in range(MEMBER // MIB)) + member.flush()
    return body * 16


@pytest.fixture(scope="module")
def brotli_bomb() -> bytes:
    compressor = brotli.Compressor(quality=5)
    zero = bytes(MIB)
    return b"".join(compressor.process(zero) for _ in range(1024)) + compressor.finish()


class Counter:
    def __init__(self) -> None:
        self.served = 0

    def wrap(self, chunks: Iterator[bytes]) -> Iterator[bytes]:
        for chunk in chunks:
            self.served += 1
            yield chunk


async def test_stream_over_limit_without_content_length_is_cut_at_the_boundary(
    make_fetcher,
) -> None:
    """Нескінченний (100 МБ) потік без `Content-Length`: обрив одразу за 20 МБ, генератор
    не дочитано до кінця."""
    yielded = 0

    async def stream():
        nonlocal yielded
        for _ in range(100):
            yielded += 1
            yield bytes(MIB)

    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, content=stream()))
        result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.error_code == "body_too_large"
    assert result.decision.quarantine is True
    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.body is None
    assert yielded <= 21  # 20 МБ + чанк, що перевищив ліміт


async def test_exactly_limit_passes_and_limit_plus_one_byte_fails(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, None, chunks=iter([bytes(20 * MIB)])
    )
    ok = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert ok.decision.outcome is FetchOutcome.SUCCESS
    assert len(ok.body or b"") == 20 * MIB

    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, None, chunks=iter([bytes(20 * MIB), b"x"])
    )
    over = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert over.decision.error_code == "body_too_large"


async def test_declared_content_length_over_limit_is_refused_without_reading(
    make_fetcher, network
) -> None:
    counter = Counter()
    network.routes[(PUBLIC_IP, 80)] = lambda r: counter.wrap(
        http_response(200, None, {"Content-Length": "30000000"}, chunks=_chunked(bytes(MIB)))
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.error_code == "body_too_large"
    assert counter.served == 1  # лише заголовки


@pytest.mark.parametrize("ratio_guard", [True, False])
async def test_gzip_bomb_is_cut_within_limit_without_memory_growth(
    make_fetcher, network, gzip_bomb, ratio_guard
) -> None:
    counter = Counter()
    network.routes[(PUBLIC_IP, 80)] = lambda r: counter.wrap(
        http_response(200, None, {"Content-Encoding": "gzip"}, chunks=_chunked(gzip_bomb))
    )
    overrides = {} if ratio_guard else {"max_decompression_ratio": 10**9}

    tracemalloc.start()
    try:
        result = await make_fetcher(_resolver(), **overrides).fetch(FetchRequest(URL))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    expected = "decompression_bomb" if ratio_guard else "body_too_large"
    assert result.decision.error_code == expected
    assert result.decision.quarantine is True
    assert counter.served < len(gzip_bomb) // (64 * 1024)  # потік обірвано, не дочитано
    assert peak < 48 * MIB  # ≤ ліміт 20 МБ + службові буфери, а не 1 GiB


@pytest.mark.parametrize("ratio_guard", [True, False])
async def test_brotli_bomb_is_cut_within_limit(
    make_fetcher, network, brotli_bomb, ratio_guard
) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, None, {"Content-Encoding": "br"}, chunks=_chunked(brotli_bomb, 256)
    )
    overrides = {} if ratio_guard else {"max_decompression_ratio": 10**9}

    tracemalloc.start()
    try:
        result = await make_fetcher(_resolver(), **overrides).fetch(FetchRequest(URL))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.decision.error_code in {"decompression_bomb", "body_too_large"}
    if not ratio_guard:
        assert result.decision.error_code == "body_too_large"
    assert peak < 48 * MIB


async def test_double_gzip_content_encoding_is_decoded(make_fetcher, network) -> None:
    payload = b"<html>" + b"a" * 1000 + b"</html>"
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, gzip.compress(gzip.compress(payload)), {"Content-Encoding": "gzip, gzip"}
    )
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert result.body == payload


async def test_double_gzip_bomb_is_cut(make_fetcher, network) -> None:
    inner = gzip.compress(bytes(40 * MIB), compresslevel=9)
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, gzip.compress(inner), {"Content-Encoding": "gzip, gzip"}
    )
    result = await make_fetcher(_resolver(), max_decompression_ratio=10**9).fetch(FetchRequest(URL))
    assert result.decision.error_code == "body_too_large"


@pytest.mark.parametrize(
    ("encoding", "encode"),
    [
        ("deflate", zlib.compress),
        ("deflate", lambda d: zlib.compress(d)[2:-4]),  # raw deflate без zlib-заголовка
        ("br", brotli.compress),
        ("identity", lambda d: d),
    ],
)
async def test_supported_encodings_are_decoded(make_fetcher, network, encoding, encode) -> None:
    payload = b"hello " * 500
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, encode(payload), {"Content-Encoding": encoding}
    )
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert result.body == payload


async def test_unknown_content_encoding_is_permanent(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, b"xx", {"Content-Encoding": "zstd"}
    )
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert result.decision.error_code == "content_encoding_unsupported"
    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE


def _gzip_of_zeros(size: int) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
    step = 4 * MIB
    parts = [compressor.compress(bytes(min(step, size - start))) for start in range(0, size, step)]
    return b"".join(parts) + compressor.flush()


@pytest.mark.parametrize(("size", "ok"), [(100 * MIB, True), (100 * MIB + 1, False)])
async def test_sitemap_xml_gz_limit_counts_decompressed_bytes(
    make_fetcher, network, size, ok
) -> None:
    """`.xml.gz` як `application/gzip` без `Content-Encoding`: ліміт 100 МБ після розпакування,
    raw bytes зберігаються стиснутими як отримано."""
    compressed = _gzip_of_zeros(size)
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, None, {"Content-Type": "application/gzip"}, chunks=_chunked(compressed)
    )
    fetcher = make_fetcher(_resolver(), max_decompression_ratio=10**9)

    result = await fetcher.fetch(FetchRequest("http://example.org/sitemap.xml.gz", "sitemap"))

    if ok:
        assert result.decision.outcome is FetchOutcome.SUCCESS
        assert result.body == compressed
        assert result.body_compressed is True
        assert result.decoded_bytes == size
    else:
        assert result.decision.error_code == "body_too_large"


async def test_page_gzip_file_is_not_expanded_but_sitemap_is(make_fetcher, network) -> None:
    """Для `page` `application/gzip` — звичайні bytes (ліміт на отримане), без розпакування."""
    compressed = gzip.compress(b"<urlset/>")
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, compressed, {"Content-Type": "application/gzip"}
    )
    page = await make_fetcher(_resolver()).fetch(FetchRequest("http://example.org/x.gz"))
    assert page.body == compressed
    assert page.body_compressed is False


async def test_media_binary_is_skipped_after_headers(make_fetcher, network) -> None:
    counter = Counter()
    network.routes[(PUBLIC_IP, 80)] = lambda r: counter.wrap(
        http_response(200, None, {"Content-Type": "image/jpeg"}, chunks=_chunked(bytes(MIB)))
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.outcome is FetchOutcome.SUCCESS
    assert result.decision.content_access is ContentAccess.METADATA_ONLY
    assert result.decision.error_code == "media_binary_skipped"
    assert result.body is None
    assert result.media_type == "image/jpeg"
    assert counter.served == 1


async def test_media_binary_is_read_when_enabled(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, b"\xff\xd8jpeg", {"Content-Type": "image/jpeg"}
    )
    result = await make_fetcher(_resolver(), media_binaries=True).fetch(FetchRequest(URL))
    assert result.body == b"\xff\xd8jpeg"


async def test_slow_drip_hits_total_timeout_with_simulated_clock(
    make_fetcher, network, clock
) -> None:
    """1 байт/с (годинник тесту): total 60 с на весь fetch спрацьовує без реального очікування."""

    def drip() -> Iterator[bytes]:
        for _ in range(10_000):
            clock.advance(1.0)
            yield b"x"

    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(200, None, chunks=drip())

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == "timeout"
    assert network.chunks_served <= 62


async def test_hanging_stream_hits_asyncio_total_timeout(make_fetcher) -> None:
    async def hang():
        yield b"x"
        await asyncio.Event().wait()

    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, content=hang()))
        result = await make_fetcher(_resolver(), total_timeout=0.05).fetch(FetchRequest(URL))

    assert result.decision.error_code == "timeout"


async def test_sitemap_total_timeout_override_from_manifest(
    make_fetcher, network, clock, permits
) -> None:
    # Manifest override дозволений лише коли runtime просить permit, що покриває це вікно.
    permits.lease_seconds = 310

    def drip() -> Iterator[bytes]:
        for _ in range(200):
            clock.advance(1.0)
            yield b"x"

    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(200, None, chunks=drip())

    result = await make_fetcher(_resolver()).fetch(
        FetchRequest(URL, "sitemap", total_timeout=300.0)
    )

    assert result.decision.outcome is FetchOutcome.SUCCESS
    assert result.body == b"x" * 200
