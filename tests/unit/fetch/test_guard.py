"""Route Guard (§10 п.2, §3 п.10, O-4): patterns, порти, глобальний denylist з mtime-reload."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fetch_fakes import PUBLIC_IP, FakeResolver, static

from collector.fetch.client import FetchRequest
from collector.fetch.guard import GlobalDenylist, RouteGuard
from collector.fetch.ssrf import PolicyBlocked

PATTERN = r"^https://auto\.ria\.com/uk/auto_[^/]+_[0-9]+\.html$"


def _reason(guard: RouteGuard, url: str) -> str | None:
    try:
        guard.check(url)
    except PolicyBlocked as exc:
        return exc.error_code
    return None


def test_allowed_patterns_apply_to_normalized_url() -> None:
    guard = RouteGuard(allowed_patterns=[PATTERN])
    assert _reason(guard, "HTTPS://Auto.RIA.com:443/uk/auto_bmw_x5_123.html?utm_source=a") is None
    assert _reason(guard, "https://auto.ria.com/uk/news/1") == "route_not_allowed"


def test_denied_patterns_win() -> None:
    guard = RouteGuard(allowed_patterns=[r"^https://e\.org/"], denied_patterns=[r"/admin"])
    assert _reason(guard, "https://e.org/admin/x") == "route_denied"


def test_ports_default_and_override() -> None:
    assert _reason(RouteGuard(), "http://e.org:8080/") == "port_forbidden"
    assert _reason(RouteGuard(allowed_ports=frozenset({8080})), "http://e.org:8080/") is None
    assert _reason(RouteGuard(allowed_ports=frozenset({8080})), "http://e.org/") == "port_forbidden"


def test_guarded_url_has_ascii_host_and_port() -> None:
    guarded = RouteGuard().check("https://Пример.рф/путь")
    assert guarded.host == "xn--e1afmkfd.xn--p1ai"
    assert guarded.port == 443


def test_global_denylist_hosts_subdomains_and_regex(tmp_path: Path) -> None:
    path = tmp_path / "denylist.txt"
    path.write_text("# emergency\nbad.example\nre:/private/\n", encoding="utf-8")
    guard = RouteGuard(denylist=GlobalDenylist(path))
    assert _reason(guard, "https://bad.example/") == "global_denylist"
    assert _reason(guard, "https://www.bad.example/x") == "global_denylist"
    assert _reason(guard, "https://notbad.example/") is None
    assert _reason(guard, "https://good.example/private/1") == "global_denylist"


def test_global_denylist_is_reloaded_on_mtime_change(tmp_path: Path) -> None:
    path = tmp_path / "denylist.txt"
    path.write_text("", encoding="utf-8")
    guard = RouteGuard(denylist=GlobalDenylist(path))
    assert _reason(guard, "https://stop.example/") is None

    path.write_text("stop.example\n", encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert _reason(guard, "https://stop.example/") == "global_denylist"


def test_configured_but_missing_denylist_fails_closed(tmp_path: Path) -> None:
    guard = RouteGuard(denylist=GlobalDenylist(tmp_path / "missing.txt"))
    assert _reason(guard, "https://any.example/") == "global_denylist_unavailable"


async def test_denylisted_redirect_target_is_blocked_on_hop(
    make_fetcher, network, tmp_path
) -> None:
    path = tmp_path / "denylist.txt"
    path.write_text("b.example\n", encoding="utf-8")
    network.routes[(PUBLIC_IP, 80)] = static(302, b"", {"Location": "http://b.example/"})
    resolver = FakeResolver({"a.example": [PUBLIC_IP], "b.example": ["93.184.216.99"]})
    fetcher = make_fetcher(resolver, guard=RouteGuard(denylist=GlobalDenylist(path)))

    result = await fetcher.fetch(FetchRequest("http://a.example/"))

    assert result.decision.reason == "global_denylist"
    assert network.connects == [(PUBLIC_IP, 80)]
    assert resolver.calls == ["a.example"]  # denylist — до DNS


@pytest.mark.parametrize("scheme", ["ws", "wss", "data", "javascript", "chrome", "ftp", "file"])
def test_only_http_and_https(scheme: str) -> None:
    assert _reason(RouteGuard(), f"{scheme}://e.org/") == "scheme_forbidden"
