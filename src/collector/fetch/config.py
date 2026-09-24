"""Конфігурація fetch core з env (§10 timeout, §13 ліміти body, §3 п.5 User-Agent, Q-002)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from collector.core.version import package_version

MIB = 1024 * 1024


def _default_user_agent() -> str:
    return f"UAWebDataCollector/{package_version()}"


@dataclass(frozen=True, slots=True)
class FetchConfig:
    """Параметри одного `SafeFetcher`; значення за замовчуванням — з §10/§13.

    `max_redirects` — кількість redirect-hop-ів після початкового запиту (5 → шостий redirect
    дає `too_many_redirects`). `max_decompression_ratio` — ratio-guard проти bomb: розпаковано /
    отримано понад це значення після `ratio_guard_min_bytes` розпакованих байтів → обрив.
    """

    user_agent: str
    max_body_bytes: int = 20 * MIB
    max_sitemap_bytes: int = 100 * MIB
    max_decompression_ratio: int = 200
    ratio_guard_min_bytes: int = 1 * MIB
    media_binaries: bool = False
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    total_timeout: float = 60.0
    max_redirects: int = 5
    allowed_ports: frozenset[int] = frozenset({80, 443})
    denylist_file: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> FetchConfig:
        env: Mapping[str, str] = os.environ if environ is None else environ
        denylist = env.get("COLLECTOR_FETCH_DENYLIST_FILE")
        return cls(
            user_agent=env.get("COLLECTOR_FETCH_USER_AGENT") or _default_user_agent(),
            max_body_bytes=int(env.get("COLLECTOR_FETCH_MAX_BODY_BYTES", 20 * MIB)),
            max_sitemap_bytes=int(env.get("COLLECTOR_FETCH_MAX_SITEMAP_BYTES", 100 * MIB)),
            media_binaries=env.get("COLLECTOR_FETCH_MEDIA_BINARIES", "0") == "1",
            denylist_file=Path(denylist) if denylist else None,
        )
