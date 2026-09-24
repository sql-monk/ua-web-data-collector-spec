"""Route Guard (§10 п.2, §3 п.10, O-4): scheme/port/userinfo, allow/deny patterns, denylist.

Guard перевіряє URL **до кожного запиту і на кожному redirect hop**; DNS/IP — окремо в
`collector.fetch.ssrf`. Patterns — regex з manifest (`allowed_url_patterns`,
`denied_url_patterns`, Додаток B), що застосовуються до нормалізованого URL; порожній allow-список
означає «будь-який URL, що пройшов решту перевірок» (handler PR2 передає patterns за
`request_kind`, бо robots/sitemap не підпадають під patterns сторінок).

Глобальний denylist (§3 п.10, O-4) — файл, змонтований як Docker config/secret, перечитується
при зміні mtime без перевипуску коду. Формат рядка: `# коментар`, `example.org` (host і всі
subdomains) або `re:<regex>` (search у нормалізованому URL). Налаштований, але відсутній чи
нечитабельний файл → fail closed (`global_denylist_unavailable`): термінова зупинка важливіша
за доступність.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from collector.fetch.ssrf import PolicyBlocked
from collector.fetch.urls import DEFAULT_PORTS, UrlError, normalize_url

ALLOWED_SCHEMES = frozenset(DEFAULT_PORTS)


@dataclass(frozen=True, slots=True)
class GuardedUrl:
    """URL, що пройшов Route Guard: нормалізований рядок і ASCII host/port для резолву."""

    url: str
    scheme: str
    host: str
    port: int


class GlobalDenylist:
    """Denylist із файлу з перечитуванням за mtime (потокобезпечно, дешево на кожен запит)."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._mtime_ns: int | None = None
        self._hosts: frozenset[str] = frozenset()
        self._patterns: tuple[re.Pattern[str], ...] = ()

    def _load(self, path: Path) -> tuple[frozenset[str], tuple[re.Pattern[str], ...]]:
        try:
            mtime = path.stat().st_mtime_ns
            with self._lock:
                if mtime != self._mtime_ns:
                    hosts, patterns = set(), []
                    for raw in path.read_text(encoding="utf-8").splitlines():
                        line = raw.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("re:"):
                            patterns.append(re.compile(line[3:]))
                        else:
                            hosts.add(line.lower().rstrip("."))
                    self._hosts, self._patterns = frozenset(hosts), tuple(patterns)
                    self._mtime_ns = mtime
                return self._hosts, self._patterns
        except (OSError, UnicodeDecodeError, re.error) as exc:
            raise PolicyBlocked(
                "global_denylist_unavailable", f"denylist недоступний: {type(exc).__name__}"
            ) from exc

    def check(self, url: str, host: str) -> None:
        if self._path is None:
            return
        hosts, patterns = self._load(self._path)
        labels = host.split(".")
        if any(".".join(labels[i:]) in hosts for i in range(len(labels))):
            raise PolicyBlocked("global_denylist", f"host {host!r} у глобальному denylist")
        if any(p.search(url) for p in patterns):
            raise PolicyBlocked("global_denylist", "URL у глобальному denylist")


class RouteGuard:
    """Політика URL одного route: викликається для кожного запиту і кожного redirect hop."""

    def __init__(
        self,
        *,
        allowed_patterns: Iterable[str] = (),
        denied_patterns: Iterable[str] = (),
        allowed_ports: frozenset[int] = frozenset({80, 443}),
        denylist: GlobalDenylist | None = None,
    ) -> None:
        self._allowed = tuple(re.compile(p) for p in allowed_patterns)
        self._denied = tuple(re.compile(p) for p in denied_patterns)
        self._ports = allowed_ports
        self._denylist = denylist or GlobalDenylist(None)

    def check(self, url: str) -> GuardedUrl:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise PolicyBlocked("url_invalid", f"некоректний URL: {exc}") from exc
        scheme = parts.scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise PolicyBlocked("scheme_forbidden", f"scheme {scheme!r} заборонено")
        try:
            normalized = normalize_url(url).normalized
        except UrlError as exc:
            raise PolicyBlocked(exc.error_code, str(exc)) from exc
        effective_port = port if port is not None else DEFAULT_PORTS[scheme]
        if effective_port not in self._ports:
            raise PolicyBlocked("port_forbidden", f"порт {effective_port} заборонено")
        host = urlsplit(normalized).hostname or ""
        self._denylist.check(normalized, host)
        if any(p.search(normalized) for p in self._denied):
            raise PolicyBlocked("route_denied", "URL підпадає під denied_url_patterns")
        if self._allowed and not any(p.search(normalized) for p in self._allowed):
            raise PolicyBlocked("route_not_allowed", "URL поза allowed_url_patterns")
        return GuardedUrl(url=normalized, scheme=scheme, host=host, port=effective_port)
