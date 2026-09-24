"""SSRF (§13, картка WP-02 PR1 «Тести»): кожна заборонена адреса/форма → `policy_blocked`,
**0 connect** у fake backend; змішаний DNS; DNS rebinding і pinning (один резолв на hop)."""

from __future__ import annotations

from ipaddress import ip_address

import pytest
from fetch_fakes import PUBLIC_IP, PUBLIC_IP_2, FakeResolver, static

from collector.contracts.enums import FetchOutcome
from collector.fetch.client import FetchRequest
from collector.fetch.ssrf import PolicyBlocked, forbidden_reason, parse_ip_literal

ADDR = "ssrf_forbidden_address"
HOST = "ssrf_forbidden_host"
PUB = [PUBLIC_IP]

# (url, DNS-відповіді, додаткові дозволені порти, очікувана причина)
SSRF_CASES = [
    ("http://127.0.0.1/", {}, (), ADDR),
    ("http://[::1]/", {}, (), ADDR),
    ("http://2130706433/", {}, (), ADDR),
    ("http://0177.0.0.1/", {}, (), ADDR),
    ("http://0x7f.1/", {}, (), ADDR),
    ("http://127.1/", {}, (), ADDR),
    ("http://0.0.0.0/", {}, (), ADDR),
    ("http://[::ffff:127.0.0.1]/", {}, (), ADDR),
    ("http://[::ffff:7f00:1]/", {}, (), ADDR),
    ("http://[64:ff9b::a00:1]/", {}, (), ADDR),
    ("http://[2002:7f00:1::]/", {}, (), ADDR),
    ("http://169.254.169.254/latest/meta-data/", {}, (), ADDR),
    ("http://[fd00:ec2::254]/", {}, (), ADDR),
    ("http://100.64.0.1/", {}, (), ADDR),
    ("http://10.0.0.1/", {}, (), ADDR),
    ("http://[fe80::1%25eth0]/", {}, (), ADDR),
    ("http://[::7f00:1]/", {}, (), ADDR),
    ("http://224.0.0.1/", {}, (), ADDR),
    ("http://255.255.255.255/", {}, (), ADDR),
    ("http://localhost./", {"localhost.": PUB}, (), HOST),
    ("http://localhost/", {"localhost": PUB}, (), HOST),
    ("http://api.localhost/", {"api.localhost": PUB}, (), HOST),
    ("http://example.org./", {"example.org.": PUB}, (), HOST),
    ("http://minio:9000/", {"minio": ["172.18.0.5"]}, (9000,), ADDR),
    ("http://postgres:5432/", {"postgres": ["172.18.0.2"]}, (5432,), ADDR),
    ("file:///etc/passwd", {}, (), "scheme_forbidden"),
    ("gopher://example.org/", {"example.org": PUB}, (), "scheme_forbidden"),
    ("ftp://example.org/", {"example.org": PUB}, (), "scheme_forbidden"),
    ("http://u:p@example.org/", {"example.org": PUB}, (), "url_userinfo_forbidden"),
    ("http://example.org:6379/", {"example.org": PUB}, (), "port_forbidden"),
    ("http://1.2.3.4.5/", {}, (), "url_invalid"),
    ("http://0x5d.0xb8.0xd8.0x22/", {}, (), "host_noncanonical_ip"),
]


@pytest.mark.parametrize(("url", "answers", "ports", "reason"), SSRF_CASES)
async def test_forbidden_target_is_policy_blocked_without_connect(
    make_fetcher, network, permits, url, answers, ports, reason
) -> None:
    for ip in ("127.0.0.1", "172.18.0.5", PUBLIC_IP):
        for port in (80, 443, 5432, 6379, 9000):
            network.routes[(ip, port)] = static(200, b"LEAK")
    fetcher = make_fetcher(FakeResolver(answers), allowed_ports=frozenset({80, 443, *ports}))

    result = await fetcher.fetch(FetchRequest(url))

    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "policy_blocked"
    assert result.decision.reason == reason
    assert result.body is None
    assert network.connects == []
    assert permits.acquired == []


async def test_mixed_dns_answer_is_blocked_not_filtered(make_fetcher, network, permits) -> None:
    """Resolver дає публічну і приватну адресу — блок, а не «вибрати хорошу»."""
    network.routes[(PUBLIC_IP, 80)] = static(200, b"ok")
    fetcher = make_fetcher(FakeResolver({"example.org": [PUBLIC_IP, "10.0.0.1"]}))

    result = await fetcher.fetch(FetchRequest("http://example.org/"))

    assert result.decision.error_code == "policy_blocked"
    assert result.decision.reason == ADDR
    assert network.connects == []


async def test_dns_rebinding_one_resolution_per_hop_and_connect_to_checked_ip(
    make_fetcher, network
) -> None:
    """1-й резолв — публічна IP, 2-й — 127.0.0.1: запит іде рівно на перевірену IP, SNI і
    `Host` — оригінальне ім'я; бібліотека не резолвить повторно."""
    network.routes[(PUBLIC_IP, 443)] = static(200, b"page")
    network.routes[("127.0.0.1", 443)] = static(200, b"LEAK")
    resolver = FakeResolver({"example.org": [[PUBLIC_IP], ["127.0.0.1"]]})

    result = await make_fetcher(resolver).fetch(FetchRequest("https://example.org/"))

    assert result.body == b"page"
    assert resolver.calls == ["example.org"]
    assert network.connects == [(PUBLIC_IP, 443)]
    assert network.sni == ["example.org"]
    assert network.requests[0].headers["host"] == "example.org"


async def test_dns_rebinding_on_same_host_redirect_is_rechecked(make_fetcher, network) -> None:
    """Redirect на той самий host: новий hop — новий резолв, rebinding на 127.0.0.1 — блок."""
    network.routes[(PUBLIC_IP, 443)] = static(302, b"", {"Location": "/next"})
    network.routes[("127.0.0.1", 443)] = static(200, b"LEAK")
    resolver = FakeResolver({"example.org": [[PUBLIC_IP], ["127.0.0.1"]]})

    result = await make_fetcher(resolver).fetch(FetchRequest("https://example.org/"))

    assert result.decision.error_code == "policy_blocked"
    assert resolver.calls == ["example.org", "example.org"]
    assert network.connects == [(PUBLIC_IP, 443)]


async def test_first_checked_address_is_pinned(make_fetcher, network) -> None:
    network.routes[(PUBLIC_IP, 80)] = static(200, b"a")
    network.routes[(PUBLIC_IP_2, 80)] = static(200, b"b")
    resolver = FakeResolver({"example.org": [PUBLIC_IP, PUBLIC_IP_2]})

    result = await make_fetcher(resolver).fetch(FetchRequest("http://example.org/"))

    assert result.body == b"a"
    assert network.connects == [(PUBLIC_IP, 80)]
    assert result.hops[0].address == PUBLIC_IP


async def test_dns_failure_is_retryable_not_policy(make_fetcher, network) -> None:
    result = await make_fetcher(FakeResolver({})).fetch(FetchRequest("http://nx.example.org/"))
    assert result.decision.outcome is FetchOutcome.RETRYABLE
    assert result.decision.error_code == "dns_error"
    assert network.connects == []


async def test_dns_garbage_answer_is_blocked(make_fetcher, network) -> None:
    result = await make_fetcher(FakeResolver({"example.org": ["not-an-ip"]})).fetch(
        FetchRequest("http://example.org/")
    )
    assert result.decision.error_code == "policy_blocked"
    assert network.connects == []


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("2130706433", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("0x7f.1", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("10.1", "10.0.0.1"),
        ("::ffff:127.0.0.1", "::ffff:7f00:1"),
    ],
)
def test_numeric_host_forms_are_canonicalized(host: str, expected: str) -> None:
    assert parse_ip_literal(host) == ip_address(expected)


@pytest.mark.parametrize("host", ["example.org", "1e100.net", "xn--e1afmkfd.xn--p1ai"])
def test_names_are_not_ip_literals(host: str) -> None:
    assert parse_ip_literal(host) is None


@pytest.mark.parametrize("host", ["08.0.0.1", "256.1.1.1", "1.2.3.4.5", "0x1g.1", "1.2.3.256"])
def test_invalid_numeric_hosts_are_rejected(host: str) -> None:
    with pytest.raises(PolicyBlocked):
        parse_ip_literal(host)


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_global_unicast_is_allowed(address: str) -> None:
    assert forbidden_reason(ip_address(address)) is None


@pytest.mark.parametrize(
    "address",
    [
        "172.16.0.1",
        "192.168.1.1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "ff0e::1",
        "::",
        "64:ff9b::7f00:1",
        "2002:a00:1::",
        "::ffff:10.0.0.1",
        "0.1.2.3",
        "198.18.0.1",
        "240.0.0.1",
        "224.0.1.1",
    ],
)
def test_non_global_addresses_are_forbidden(address: str) -> None:
    assert forbidden_reason(ip_address(address)) is not None


async def test_system_resolver_is_blocked_in_unit_tests() -> None:
    """Страховка conftest: без fake resolver-а тест падає, а не резолвить реальний DNS."""
    from collector.fetch.ssrf import SystemResolver

    with pytest.raises(AssertionError, match="реальний DNS"):
        await SystemResolver().resolve("example.org", 443)
