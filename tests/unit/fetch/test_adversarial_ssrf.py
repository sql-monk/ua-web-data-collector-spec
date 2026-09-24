"""Adversarial SSRF-тести незалежного тестувальника (WP-02 PR1, §13, §16.1 п.8).

Лише fakes: `FakeResolver` (DNS), `FakeNetwork` (TCP без сокетів, записує `(ip, port)`),
жодного live-запиту. Кожен кейс перевіряє не лише код рішення, а й **0 connect** на заборонену
адресу — саме це ламається, якщо guard пропускає hop або pinning бере не ту IP.
"""

from __future__ import annotations

import pytest
from fetch_fakes import PUBLIC_IP, PUBLIC_IP_2, FakeResolver, static

from collector.contracts.enums import FetchOutcome
from collector.fetch.client import FetchRequest
from collector.fetch.guard import RouteGuard

A_IP, B_IP = PUBLIC_IP, PUBLIC_IP_2
C_IP = "93.184.216.36"
ADDR = "ssrf_forbidden_address"
LEAK_TARGETS = ("127.0.0.1", "10.0.0.1", "0.0.0.0", "::", "::1", "fc00::1", "fe80::1")  # noqa: S104 — адреси-пастки, не bind


def _arm_leak_routes(network) -> None:
    """Будь-який connect на внутрішню адресу віддав би `LEAK` — тест бачить витік."""
    for ip in LEAK_TARGETS:
        for port in (80, 443, 22, 6379, 8080):
            network.routes[(ip, port)] = static(200, b"LEAK")


# --- літерали та числові форми ----------------------------------------------------------------

LITERAL_CASES = [
    ("http://[::ffff:127.0.0.1]/", ADDR),
    ("http://[::ffff:0.0.0.0]/", ADDR),
    ("http://[::ffff:10.0.0.1]/", ADDR),
    ("http://0.0.0.0/", ADDR),
    ("http://0/", ADDR),  # decimal 0 → 0.0.0.0
    ("http://[::]/", ADDR),
    ("http://[::0]/", ADDR),
    ("http://[fc00::1]/", ADDR),  # ULA
    ("http://[fd12:3456::1]/", ADDR),  # ULA
    ("http://[fe80::1%25eth0]/", ADDR),  # link-local зі scope id
    ("http://[fe80::1%251]/", ADDR),  # link-local з числовим scope id
    ("http://[FE80::1%25ETH0]/", ADDR),
    ("http://2130706433/", ADDR),  # decimal
    ("http://017700000001/", ADDR),  # octal одним числом
    ("http://0177.0.0.01/", ADDR),  # octal по октетах
    ("http://0x7f000001/", ADDR),  # hex одним числом
    ("http://0x7F.0x0.0x0.0x1/", ADDR),  # hex по октетах
    ("http://127.1/", ADDR),  # short form
    ("http://127.0.1/", ADDR),  # short form (3 частини)
    ("http://0177.1/", ADDR),  # octal short
    ("http://127.0.0.1./", ADDR),  # trailing dot у числовому host
    ("http://１２７.０.０.１/", ADDR),  # fullwidth цифри → 127.0.0.1
    ("http://127。0。0。1/", ADDR),  # ідеографічна крапка → 127.0.0.1
    ("http://evil@127.0.0.1/", "url_userinfo_forbidden"),
    ("http://evil.example@127.0.0.1/", "url_userinfo_forbidden"),
    ("http://example.org\\@127.0.0.1/", "url_userinfo_forbidden"),
    ("http://:@127.0.0.1/", "url_userinfo_forbidden"),
    ("file:///etc/passwd", "scheme_forbidden"),
    ("file://127.0.0.1/etc/passwd", "scheme_forbidden"),
    ("gopher://127.0.0.1:6379/_FLUSHALL", "scheme_forbidden"),
    ("dict://127.0.0.1:6379/info", "scheme_forbidden"),
    ("http://example.org:8080/", "port_forbidden"),
    ("http://example.org:22/", "port_forbidden"),
    ("https://example.org:6379/", "port_forbidden"),
]


@pytest.mark.parametrize(("url", "reason"), LITERAL_CASES)
async def test_forbidden_literal_forms_never_connect(
    make_fetcher, network, permits, url, reason
) -> None:
    _arm_leak_routes(network)
    network.routes[(A_IP, 80)] = static(200, b"public")
    resolver = FakeResolver({"example.org": [A_IP]})

    result = await make_fetcher(resolver).fetch(FetchRequest(url))

    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "policy_blocked"
    assert result.decision.reason == reason
    assert network.connects == []
    assert permits.acquired == []
    assert result.body is None


# `Location` передається сирими latin-1 bytes (fake-сервер): не-latin-1 форми — лише в URL job-а.
REDIRECT_CASES = [(u, r) for u, r in LITERAL_CASES if u.isascii()]


@pytest.mark.parametrize(("location", "reason"), REDIRECT_CASES)
async def test_forbidden_literal_forms_on_redirect_hop_never_connect(
    make_fetcher, network, permits, location, reason
) -> None:
    """Ті самі форми як `Location` першого hop-а: другого connect немає, permit звільнено,
    рішення — permanent `policy_blocked` (§10 п.9), а не retryable, який повторив би hop 1."""
    _arm_leak_routes(network)
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": location})
    resolver = FakeResolver({"a.example": [A_IP], "example.org": [A_IP]})

    result = await make_fetcher(resolver).fetch(FetchRequest("http://a.example/"))

    assert network.connects == [(A_IP, 80)]
    assert permits.live == {}
    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "policy_blocked"
    assert result.decision.reason == reason


# --- redirect-ланцюг: блок саме на 3-му hop ---------------------------------------------------


@pytest.mark.parametrize(
    ("third", "answers"),
    [
        ("http://c.example/", {"c.example": ["10.0.0.1"]}),  # ім'я → private (DNS)
        ("http://c.example/", {"c.example": [C_IP, "127.0.0.1"]}),  # змішаний DNS
        ("http://c.example/", {"c.example": ["::ffff:127.0.0.1"]}),  # AAAA mapped loopback
        ("http://c.example/", {"c.example": ["fe80::1%eth0"]}),  # AAAA link-local зі scope
        ("http://[::ffff:127.0.0.1]/", {}),
        ("http://0x7f.1/", {}),
    ],
)
async def test_redirect_chain_public_public_private_blocks_on_third_hop(
    make_fetcher, network, permits, third, answers
) -> None:
    _arm_leak_routes(network)
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": "http://b.example/1"})
    network.routes[(B_IP, 80)] = static(301, b"", {"Location": third})
    resolver = FakeResolver({"a.example": [A_IP], "b.example": [B_IP], **answers})

    result = await make_fetcher(resolver).fetch(FetchRequest("http://a.example/"))

    assert result.decision.outcome is FetchOutcome.PERMANENT_FAILURE
    assert result.decision.error_code == "policy_blocked"
    assert result.decision.reason == ADDR
    assert network.connects == [(A_IP, 80), (B_IP, 80)]  # перші два hop виконані
    assert [h.status for h in result.hops] == [302, 301]
    assert permits.acquired == ["http://a.example", "http://b.example"]  # c — без permit
    assert permits.live == {}


# --- DNS rebinding і pinning -------------------------------------------------------------------


async def test_rebinding_resolver_is_asked_once_and_connect_uses_checked_ip(
    make_fetcher, network
) -> None:
    """Резолвер «перемикається» на 127.0.0.1 після першої відповіді: якби бібліотека резолвила
    ще раз (connect за іменем), fake backend отримав би ім'я або 127.0.0.1."""
    _arm_leak_routes(network)
    network.routes[(A_IP, 443)] = static(200, b"public")
    resolver = FakeResolver({"a.example": [[A_IP]] + [["127.0.0.1"]] * 10})

    result = await make_fetcher(resolver).fetch(FetchRequest("https://a.example/x"))

    assert result.body == b"public"
    assert resolver.calls == ["a.example"]
    assert network.connects == [(A_IP, 443)]
    assert all(host != "a.example" for host, _ in network.connects)
    assert network.sni == ["a.example"]
    assert network.requests[0].headers["host"] == "a.example"


async def test_rebinding_on_third_hop_to_new_host_is_rechecked(make_fetcher, network) -> None:
    """a → b → c: c резолвиться публічно лише в «чужому» запиті; на hop-і — уже loopback."""
    _arm_leak_routes(network)
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": "http://b.example/"})
    network.routes[(B_IP, 80)] = static(302, b"", {"Location": "http://c.example/"})
    network.routes[(C_IP, 80)] = static(200, b"c")
    resolver = FakeResolver(
        {"a.example": [A_IP], "b.example": [B_IP], "c.example": [["127.0.0.1"], [C_IP]]}
    )

    result = await make_fetcher(resolver).fetch(FetchRequest("http://a.example/"))

    assert result.decision.error_code == "policy_blocked"
    assert resolver.calls == ["a.example", "b.example", "c.example"]
    assert network.connects == [(A_IP, 80), (B_IP, 80)]


async def test_each_hop_pins_its_own_host_and_sends_its_own_host_header(
    make_fetcher, network
) -> None:
    network.routes[(A_IP, 443)] = static(302, b"", {"Location": "https://b.example/p"})
    network.routes[(B_IP, 443)] = static(200, b"b")
    resolver = FakeResolver({"a.example": [A_IP], "b.example": [B_IP]})

    result = await make_fetcher(resolver).fetch(FetchRequest("https://a.example/"))

    assert result.body == b"b"
    assert network.connects == [(A_IP, 443), (B_IP, 443)]
    assert network.sni == ["a.example", "b.example"]
    assert [r.headers["host"] for r in network.requests] == ["a.example", "b.example"]


# --- Host header / header injection ------------------------------------------------------------


async def test_host_header_cannot_be_overridden_via_url_crlf(make_fetcher, network) -> None:
    """CR/LF в URL не додає заголовків: `Host` — рівно ім'я pinned host-а, другого `Host` немає."""
    network.routes[(A_IP, 80)] = static(200, b"ok")
    resolver = FakeResolver({"example.org": [A_IP]})
    url = "http://example.org/a\r\nHost: 127.0.0.1\r\nX-Injected: 1"

    await make_fetcher(resolver).fetch(FetchRequest(url))

    assert network.connects == [(A_IP, 80)]
    request = network.requests[0]
    assert request.headers["host"] == "example.org"
    assert "x-injected" not in request.headers
    assert "\r" not in request.target and "\n" not in request.target


def test_fetch_request_has_no_caller_headers() -> None:
    """Контракт PR1: викликач не передає довільних заголовків (`Host`, `Authorization`,
    `Cookie`) — лише validators; override `Host` неможливий за побудовою."""
    fields = set(FetchRequest.__dataclass_fields__)
    assert fields == {
        "url",
        "request_kind",
        "job_id",
        "if_none_match",
        "if_modified_since",
        "total_timeout",
    }


# --- IDN / punycode homograph -----------------------------------------------------------------

HOMOGRAPH = "http://еxample.org/"  # кирилична «е» замість латинської
HOMOGRAPH_ACE = "xn--xample-2of.org"


async def test_idn_homograph_does_not_match_ascii_allow_pattern(make_fetcher, network) -> None:
    guard = RouteGuard(allowed_patterns=[r"^https?://example\.org/"])
    resolver = FakeResolver({HOMOGRAPH_ACE: [A_IP], "example.org": [A_IP]})
    network.routes[(A_IP, 80)] = static(200, b"spoof")

    result = await make_fetcher(resolver, guard=guard).fetch(FetchRequest(HOMOGRAPH))

    assert result.decision.error_code == "policy_blocked"
    assert result.decision.reason == "route_not_allowed"
    assert resolver.calls == []
    assert network.connects == []


async def test_idn_host_is_resolved_as_punycode_and_ssrf_checked(make_fetcher, network) -> None:
    """Resolver отримує лише ACE-форму; її приватна адреса блокується як будь-яка інша."""
    _arm_leak_routes(network)
    resolver = FakeResolver({HOMOGRAPH_ACE: ["10.0.0.1"]})

    result = await make_fetcher(resolver).fetch(FetchRequest(HOMOGRAPH))

    assert result.decision.reason == ADDR
    assert resolver.calls == [HOMOGRAPH_ACE]
    assert network.connects == []


async def test_idn_redirect_to_homograph_of_denylisted_host_is_not_confused(
    make_fetcher, network, tmp_path
) -> None:
    """Denylist `example.org` не має збігатися з гомографом, а сам `example.org` — блокується
    і після IDNA-нормалізації (`EXAMPLE.org.` → `example.org`)."""
    from collector.fetch.guard import GlobalDenylist

    denylist = tmp_path / "deny.txt"
    denylist.write_text("example.org\n", encoding="utf-8")
    guard = RouteGuard(denylist=GlobalDenylist(denylist))
    network.routes[(A_IP, 80)] = static(302, b"", {"Location": "http://EXAMPLE.org/"})
    resolver = FakeResolver({"a.example": [A_IP], "example.org": [B_IP]})

    result = await make_fetcher(resolver, guard=guard).fetch(FetchRequest("http://a.example/"))

    assert result.decision.reason == "global_denylist"
    assert network.connects == [(A_IP, 80)]
