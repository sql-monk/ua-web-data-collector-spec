"""Нормалізація URL і origin (FR-014, §9.3 п.3).

`normalize_origin` — **єдиний** ключ `origin_rate_buckets.origin`: scheduler (`ensure_bucket`),
discovery і fetch мають імпортувати саме цю функцію, інакше `HTTP://Example.ORG:80` і
`http://example.org` стали б двома bucket-ами й подвоїли б rate до одного origin (R-53).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

import idna

from collector.contracts.identity import (
    TRACKING_QUERY_KEYS,
    TRACKING_QUERY_PREFIXES,
    NormalizedUrl,
)

DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}
_ASCII_HOST = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*\.?$")


class UrlError(ValueError):
    """URL не можна нормалізувати; `error_code` — стабільний код для `fetches.error_code`."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _host(hostname: str) -> str:
    if ":" in hostname:  # IPv6 literal (urlsplit уже прибрав дужки)
        return f"[{hostname.lower()}]"
    try:
        return idna.encode(hostname, uts46=True).decode("ascii")
    except idna.IDNAError as exc:
        if _ASCII_HOST.match(hostname):  # `_` у DNS-іменах трапляється, IDNA його не пускає
            return hostname.lower()
        raise UrlError("url_invalid", f"host не проходить IDNA: {exc}") from exc


def _split(raw: str) -> tuple[str, str, int | None, str, str]:
    """(scheme, host, port або None для default, path, query) з валідацією."""
    try:
        parts = urlsplit(raw.strip())
        port = parts.port
    except ValueError as exc:
        raise UrlError("url_invalid", f"некоректний URL: {exc}") from exc
    scheme = parts.scheme.lower()
    if not parts.hostname:
        raise UrlError("url_invalid", "URL без host")
    if parts.username is not None or parts.password is not None:
        raise UrlError("url_userinfo_forbidden", "URL з userinfo заборонено (anonymous-only)")
    if port == DEFAULT_PORTS.get(scheme):
        port = None
    return scheme, _host(parts.hostname), port, parts.path or "/", parts.query


def _netloc(host: str, port: int | None) -> str:
    return host if port is None else f"{host}:{port}"


def _strip_tracking(query: str) -> str:
    kept = []
    for part in query.split("&"):
        key = part.split("=", 1)[0].lower()
        if not part or key in TRACKING_QUERY_KEYS or key.startswith(TRACKING_QUERY_PREFIXES):
            continue
        kept.append(part)
    return "&".join(kept)


def normalize_url(raw: str) -> NormalizedUrl:
    """Lowercase scheme/host, IDNA, без default port, fragment і tracking-параметрів.

    Порядок і кодування решти query-параметрів не змінюються (сервер може бути чутливим до
    них); `original` зберігається дослівно.
    """
    scheme, host, port, path, query = _split(raw)
    normalized = urlunsplit((scheme, _netloc(host, port), path, _strip_tracking(query), ""))
    return NormalizedUrl(original=raw, normalized=normalized)


def normalize_origin(url: str) -> str:
    """`scheme://host[:port]` — ключ origin limiter-а (default port прибирається)."""
    scheme, host, port, _, _ = _split(url)
    return f"{scheme}://{_netloc(host, port)}"
