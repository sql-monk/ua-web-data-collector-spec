"""Redirects (§13 «перевіряє DNS/IP на кожному redirect»): guard + SSRF + pinning на кожному hop,
≤ 5 hop, downgrade і невідомий origin — permanent без запиту."""

from __future__ import annotations

import pytest
from fetch_fakes import PUBLIC_IP, PUBLIC_IP_2, FakeResolver, static

from collector.contracts.enums import FetchOutcome
from collector.fetch.client import FetchRequest

A_IP, B_IP = PUBLIC_IP, PUBLIC_IP_2


def _resolver() -> FakeResolver:
    return FakeResolver({"a.example": [A_IP], "b.example": [B_IP]})


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("http://127.0.0.1/", "ssrf_forbidden_address"),
        ("http://169.254.169.254/latest/meta-data/", "ssrf_forbidden_address"),
        ("http://[::1]/", "ssrf_forbidden_address"),
        ("//127.0.0.1/", "ssrf_forbidden_address"),
        ("http://2130706433/", "ssrf_forbidden_address"),
        ("file:///etc/passwd", "scheme_forbidden"),
        ("http://a.example:6379/", "port_forbidden"),
    ],
)
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirect_to_forbidden_target_is_blocked_on_that_hop(
    make_fetcher, network, location, reason, status
) -> None:
    network.routes[(A_IP, 80)] = static(status, b"", {"Location": location})
    network.routes[("127.0.0.1", 80)] = static(200, b"LEAK")

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.decision.error_code == "policy_blocked"
    assert result.decision.reason == reason
    assert network.connects == [(A_IP, 80)]
    assert [hop.status for hop in result.hops] == [status]


async def test_chain_public_public_private_blocks_on_third_hop(make_fetcher, network) -> None:
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": "http://b.example/x"})
    network.routes[(B_IP, 80)] = static(302, b"", {"Location": "http://10.0.0.1/"})
    network.routes[("10.0.0.1", 80)] = static(200, b"LEAK")

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.decision.error_code == "policy_blocked"
    assert network.connects == [(A_IP, 80), (B_IP, 80)]
    assert len(network.requests) == 2


async def test_relative_location_with_dot_segments_is_resolved(make_fetcher, network) -> None:
    def responder(request):
        if request.target == "/a/b/page":
            return static(302, b"", {"Location": "../../c?q=1#frag"})(request)
        return static(200, b"final")(request)

    network.routes[(A_IP, 80)] = responder

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/a/b/page"))

    assert result.body == b"final"
    assert [r.target for r in network.requests] == ["/a/b/page", "/c?q=1"]
    assert result.final_url == "http://a.example/c?q=1"


async def test_six_redirects_is_too_many(make_fetcher, network) -> None:
    counter = iter(range(100))
    network.routes[(A_IP, 80)] = lambda r: static(302, b"", {"Location": f"/r{next(counter)}"})(r)

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "too_many_redirects"
    assert len(network.requests) == 6  # початковий + 5 hop, сьомого запиту немає


async def test_five_redirects_are_followed(make_fetcher, network) -> None:
    def responder(request):
        n = int(request.target.strip("/r") or 0)
        if n < 5:
            return static(302, b"", {"Location": f"/r{n + 1}"})(request)
        return static(200, b"done")(request)

    network.routes[(A_IP, 80)] = responder

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.body == b"done"
    assert len(result.hops) == 6


async def test_https_to_http_downgrade_is_refused(make_fetcher, network) -> None:
    network.routes[(A_IP, 443)] = static(301, b"", {"Location": "http://a.example/"})
    network.routes[(A_IP, 80)] = static(200, b"plain")

    result = await make_fetcher(_resolver()).fetch(FetchRequest("https://a.example/"))

    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "redirect_downgrade"
    assert network.connects == [(A_IP, 443)]


async def test_cross_origin_redirect_without_bucket_is_refused_without_request(
    make_fetcher, network, permits
) -> None:
    permits.known = {"http://a.example"}
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": "http://b.example/"})
    network.routes[(B_IP, 80)] = static(200, b"other")

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "redirect_origin_unknown"
    assert network.connects == [(A_IP, 80)]
    assert len(permits.released) == 1


async def test_cross_origin_redirect_takes_permit_of_new_origin(
    make_fetcher, network, permits
) -> None:
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": "http://b.example/"})
    network.routes[(B_IP, 80)] = static(200, b"other")

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.body == b"other"
    assert permits.acquired == ["http://a.example", "http://b.example"]
    assert sorted(p.origin for p in permits.released) == ["http://a.example", "http://b.example"]
    assert permits.live == {}


async def test_same_origin_redirect_reuses_the_held_permit(make_fetcher, network, permits) -> None:
    network.routes[(A_IP, 80)] = lambda r: (
        static(302, b"", {"Location": "/b"})(r) if r.target == "/" else static(200, b"ok")(r)
    )

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.body == b"ok"
    assert permits.acquired == ["http://a.example"]


async def test_redirect_to_denied_origin_is_deferred_without_request(
    make_fetcher, network, permits
) -> None:
    from fetch_fakes import T0

    from collector.fetch.permits import Denied

    permits.deny["http://b.example"] = Denied("blocked", T0.replace(hour=13))
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": "http://b.example/"})
    network.routes[(B_IP, 80)] = static(200, b"other")

    result = await make_fetcher(_resolver()).fetch(FetchRequest("http://a.example/"))

    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == "permit_blocked"
    assert result.denied is not None
    assert network.connects == [(A_IP, 80)]
