"""Adversarial-тести лімітів body, timeout і conditional GET (WP-02 PR1, §13, §10, FR-004).

Незалежний тестувальник. HTTP віддає `FakeNetwork` сирими HTTP/1.1 bytes (справжній h11 у
httpcore), тому chunked framing, брехливий `Content-Length` і розбиття на TCP-сегменти
перевіряються так, як їх побачить продуктивний стек. Bombs — synthetic, генеруються тестом.
"""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Iterator

import pytest
from fetch_fakes import PUBLIC_IP, FakeResolver, http_response, static

from collector.contracts.enums import ContentAccess, FetchOutcome
from collector.fetch.client import FetchRequest
from collector.fetch.config import MIB

URL = "http://example.org/data"
SITEMAP = "http://example.org/sitemap.xml.gz"
LIMIT = 20 * MIB


def _resolver() -> FakeResolver:
    return FakeResolver({"example.org": [PUBLIC_IP]})


def _chunks(data: bytes, size: int = 64 * 1024) -> Iterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def _gzip_zeros(size: int) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
    step = 8 * MIB
    parts = [compressor.compress(bytes(min(step, size - s))) for s in range(0, size, step)]
    return b"".join(parts) + compressor.flush()


def _chunked_encoding(pieces: Iterator[bytes]) -> Iterator[bytes]:
    """HTTP/1.1 `Transfer-Encoding: chunked` framing."""
    for piece in pieces:
        yield f"{len(piece):x}\r\n".encode() + piece + b"\r\n"
    yield b"0\r\n\r\n"


class Served:
    def __init__(self) -> None:
        self.bytes = 0

    def wrap(self, chunks: Iterator[bytes]) -> Iterator[bytes]:
        for chunk in chunks:
            self.bytes += len(chunk)
            yield chunk


# --- 20 МБ: рівно / +1, chunked, брехливий Content-Length ---------------------------------------


@pytest.mark.parametrize(("size", "ok"), [(LIMIT, True), (LIMIT + 1, False)])
async def test_body_with_exact_content_length_at_limit_and_plus_one(
    make_fetcher, network, size, ok
) -> None:
    """Той самий кейс, що й без `Content-Length`, але з точним заголовком: `+1` відхиляється
    ще до читання body."""
    served = Served()
    body = bytes(size)
    network.routes[(PUBLIC_IP, 80)] = lambda r: served.wrap(
        http_response(200, None, {"Content-Length": str(size)}, chunks=_chunks(body))
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    if ok:
        assert result.decision.outcome is FetchOutcome.SUCCESS
        assert len(result.body or b"") == LIMIT
    else:
        assert result.decision.error_code == "body_too_large"
        assert result.decision.quarantine is True
        assert result.body is None
        assert served.bytes < 1024  # лише заголовки


@pytest.mark.parametrize(("size", "ok"), [(LIMIT, True), (LIMIT + 1, False)])
async def test_transfer_encoding_chunked_at_limit_and_plus_one(
    make_fetcher, network, size, ok
) -> None:
    served = Served()
    network.routes[(PUBLIC_IP, 80)] = lambda r: served.wrap(
        http_response(
            200,
            None,
            {"Transfer-Encoding": "chunked"},
            chunks=_chunked_encoding(_chunks(bytes(size) + bytes(50 * MIB) * (not ok))),
        )
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    if ok:
        assert result.decision.outcome is FetchOutcome.SUCCESS
        assert len(result.body or b"") == LIMIT
    else:
        assert result.decision.error_code == "body_too_large"
        assert served.bytes < LIMIT + 2 * MIB  # обрив на межі, 70 МБ не дочитано


async def test_lying_small_content_length_does_not_smuggle_extra_bytes(
    make_fetcher, network
) -> None:
    """`Content-Length: 5`, а сервер шле 30 МБ: береться рівно 5 байтів, решта не читається."""
    served = Served()
    network.routes[(PUBLIC_IP, 80)] = lambda r: served.wrap(
        http_response(200, None, {"Content-Length": "5"}, chunks=_chunks(bytes(30 * MIB)))
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.body == bytes(5)
    assert served.bytes < 2 * MIB


async def test_lying_large_content_length_truncated_body_is_not_success(
    make_fetcher, network
) -> None:
    """`Content-Length: 1000`, а з'єднання закрито після 10 байтів: обрізаний body не можна
    зберегти як успішний artifact — мережева помилка, retryable."""
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, None, {"Content-Length": "1000"}, chunks=iter([b"0123456789"])
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == "network_error"
    assert result.body is None


async def test_compressed_content_length_below_limit_does_not_bypass_decoded_limit(
    make_fetcher, network
) -> None:
    """`Content-Length` — стиснутий розмір (≈ 20 КБ): ліміт рахується після розпакування."""
    compressed = _gzip_zeros(LIMIT + 1)
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, compressed, {"Content-Encoding": "gzip"}
    )

    result = await make_fetcher(_resolver(), max_decompression_ratio=10**9).fetch(FetchRequest(URL))

    assert result.decision.error_code == "body_too_large"
    assert result.body is None


# --- gzip-бомби -------------------------------------------------------------------------------


async def test_gzip_bomb_in_tiny_tcp_segments_is_cut(make_fetcher, network) -> None:
    """Малі сегменти (1 КБ) — ratio-guard і ліміт не мають залежати від розміру чанка."""
    bomb = _gzip_zeros(200 * MIB)
    served = Served()
    network.routes[(PUBLIC_IP, 80)] = lambda r: served.wrap(
        http_response(200, None, {"Content-Encoding": "gzip"}, chunks=_chunks(bomb, 1024))
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.error_code in {"decompression_bomb", "body_too_large"}
    assert result.decision.quarantine is True
    assert result.body is None
    assert served.bytes < len(bomb)


async def test_gzip_bomb_in_one_big_segment_is_cut_without_full_expansion(
    make_fetcher, network
) -> None:
    """Уся бомба одним сегментом: декодер має видавати обмеженими шматками, а не
    `zlib.decompress` усього чанка (200 МБ) перед перевіркою."""
    import tracemalloc

    bomb = _gzip_zeros(200 * MIB)
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, None, {"Content-Encoding": "gzip"}, chunks=iter([bomb])
    )

    tracemalloc.start()
    try:
        result = await make_fetcher(_resolver(), max_decompression_ratio=10**9).fetch(
            FetchRequest(URL)
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.decision.error_code == "body_too_large"
    assert peak < 48 * MIB


async def test_sitemap_gzip_bomb_behind_content_encoding_is_cut(make_fetcher, network) -> None:
    """`.xml.gz`, додатково загорнутий у `Content-Encoding: gzip`: внутрішній gzip теж
    рахується до 100 МБ."""
    inner = _gzip_zeros(100 * MIB + 1)
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, None, {"Content-Encoding": "gzip"}, chunks=_chunks(gzip.compress(inner))
    )

    result = await make_fetcher(_resolver(), max_decompression_ratio=10**9).fetch(
        FetchRequest(SITEMAP, "sitemap")
    )

    assert result.decision.error_code == "body_too_large"


async def test_sitemap_gzip_bomb_with_split_magic_bytes_is_still_counted(
    make_fetcher, network
) -> None:
    """Сервер розбиває gzip magic `1f 8b` між двома TCP-сегментами. Розпізнавання
    `.xml.gz` не має залежати від меж чанків, інакше 100 МБ-ліміт після розпакування
    обходиться, а в raw artifact потрапляє бомба під виглядом звичайного body."""
    bomb = _gzip_zeros(300 * MIB)  # ~300 КБ стиснуто
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200,
        None,
        {"Content-Type": "application/gzip"},
        chunks=iter([bomb[:1], *_chunks(bomb[1:])]),
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(SITEMAP, "sitemap"))

    assert result.decision.outcome is not FetchOutcome.SUCCESS
    assert result.decision.error_code in {"body_too_large", "decompression_bomb"}
    assert result.body is None


async def test_sitemap_split_magic_legit_gz_is_stored_compressed(make_fetcher, network) -> None:
    """Той самий split для легітимного `.xml.gz`: зберігається стиснутим і `decoded_bytes`
    рахує розпаковане, а не отримане."""
    payload = b"<urlset>" + b"<url><loc>http://example.org/a</loc></url>" * 1000 + b"</urlset>"
    compressed = gzip.compress(payload)
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200,
        None,
        {"Content-Type": "application/gzip"},
        chunks=iter([compressed[:1], compressed[1:]]),
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(SITEMAP, "sitemap"))

    assert result.body == compressed
    assert result.body_compressed is True
    assert result.decoded_bytes == len(payload)


async def test_corrupt_gzip_is_permanent_quarantine_not_success(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(
        200, b"\x1f\x8b\x08\x00garbage-not-deflate", {"Content-Encoding": "gzip"}
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "content_decoding_error"
    assert result.body is None


# --- total timeout на весь fetch (усі hop + body) -------------------------------------------


async def test_total_timeout_spans_redirect_hops(make_fetcher, network, clock) -> None:
    """Кожен hop «триває» 25 с: третій hop уже поза 60 с — timeout, а не свіжі 60 с на hop."""

    def slow_redirect(request):
        clock.advance(25.0)
        n = int(request.target.strip("/r") or 0)
        return static(302, b"", {"Location": f"/r{n + 1}"})(request)

    network.routes[(PUBLIC_IP, 80)] = slow_redirect

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://example.org/"))

    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == "timeout"
    assert len(network.requests) == 3


async def test_slow_drip_timeout_releases_permit(make_fetcher, network, clock, permits) -> None:
    def drip() -> Iterator[bytes]:
        for _ in range(1000):
            clock.advance(2.0)
            yield b"x"

    network.routes[(PUBLIC_IP, 80)] = lambda r: http_response(200, None, chunks=drip())

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.error_code == "timeout"
    assert result.body is None
    assert permits.live == {}
    assert network.chunks_served <= 33


# --- conditional GET -----------------------------------------------------------------------------


async def test_304_with_last_modified_only(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(
        304, b"", {"Last-Modified": "Tue, 22 Sep 2026 10:00:00 GMT"}
    )
    result = await make_fetcher(_resolver()).fetch(
        FetchRequest(URL, if_modified_since="Tue, 22 Sep 2026 10:00:00 GMT")
    )
    assert network.requests[0].headers["if-modified-since"] == "Tue, 22 Sep 2026 10:00:00 GMT"
    assert "if-none-match" not in network.requests[0].headers
    assert result.not_modified is True
    assert result.decision.content_access is not ContentAccess.GONE
    assert result.body is None


async def test_validators_are_not_forwarded_to_cross_origin_redirect(make_fetcher, network) -> None:
    """ETag одного origin-а не відправляється іншому (redirect на інший host)."""
    network.routes[(PUBLIC_IP, 80)] = static(302, b"", {"Location": "http://other.example/"})
    network.routes[("93.184.216.99", 80)] = static(200, b"x")
    resolver = FakeResolver({"example.org": [PUBLIC_IP], "other.example": ["93.184.216.99"]})

    await make_fetcher(resolver).fetch(
        FetchRequest(URL, if_none_match='"secret-etag"', if_modified_since="x")
    )

    assert "if-none-match" not in network.requests[1].headers
    assert "if-modified-since" not in network.requests[1].headers


# --- порожня відповідь ≠ видалення -----------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (b"", {}),
        (gzip.compress(b""), {"Content-Encoding": "gzip"}),  # стиснуте порожнє
    ],
)
async def test_empty_200_is_retryable_not_gone(make_fetcher, network, body, headers) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(200, body, headers)

    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))

    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == "empty_body"
    assert result.decision.content_access is not ContentAccess.GONE
    assert result.decision.quarantine is False


@pytest.mark.parametrize("status", [404, 410])
async def test_404_410_are_gone_without_any_lifecycle_field(make_fetcher, network, status) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(status, b"")
    result = await make_fetcher(_resolver()).fetch(FetchRequest(URL))
    assert result.decision.content_access is ContentAccess.GONE
    fields = set(type(result.decision).__dataclass_fields__) | set(
        type(result).__dataclass_fields__
    )
    assert not any("lifecycle" in f or "delet" in f for f in fields)
