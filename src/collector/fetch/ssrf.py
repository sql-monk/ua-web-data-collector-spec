"""SSRF-класифікатор (§13): числові форми host-а, заборонені імена і адреси, резолв з pinning.

Модель: host → (IP-літерал | ім'я) → **один** резолв усіх A/AAAA → блок, якщо **хоча б одна**
адреса не глобальна → повертається перша перевірена IP, і лише на неї відкривається TCP
(`collector.fetch.transport`). Повторного резолву бібліотекою немає, тому DNS rebinding між
перевіркою і connect неможливий.

Числові форми IPv4 канонізуються за семантикою `inet_aton`/WHATWG (decimal `2130706433`,
octal `0177.0.0.1`, hex `0x7f.1`, скорочена `127.1`): host вважається числовим, якщо його
останній label — число. Некоректний числовий host і **неканонічна** форма навіть публічної IP
блокуються — легітимні джерела так не посилаються, а різночитання парсерів = обхід guard-а.
"""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import Sequence
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from typing import Protocol

IPAddress = IPv4Address | IPv6Address

METADATA_ADDRESSES: frozenset[IPAddress] = frozenset(
    {ip_address("169.254.169.254"), ip_address("fd00:ec2::254")}
)
NAT64_PREFIX = ip_network("64:ff9b::/96")
IPV4_COMPATIBLE_PREFIX = ip_network("::/96")
_NUMERIC_LABEL = re.compile(r"^(0[xX][0-9a-fA-F]*|[0-9]+)$")


class PolicyBlocked(Exception):  # noqa: N818 — доменна назва рішення, а не збою
    """Запит заборонено політикою (guard/SSRF/redirect) — permanent, без мережевого запиту."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class DnsResolutionError(Exception):
    """Резолв не дав адрес або впав — мережева (retryable) помилка, не порушення політики."""


class Resolver(Protocol):
    """DNS-резолвер: усі адреси host-а (рядки IPv4/IPv6). У тестах — fake."""

    async def resolve(self, host: str, port: int) -> Sequence[str]: ...


class SystemResolver:
    """Резолв через `loop.getaddrinfo` (A і AAAA, TCP)."""

    async def resolve(self, host: str, port: int) -> Sequence[str]:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _parse_ipv4_number(host: str) -> IPv4Address:
    parts = host.split(".")
    if parts[-1] == "":  # `1.2.3.4.` — trailing dot у числовому host
        parts.pop()
    if not 1 <= len(parts) <= 4 or not all(_NUMERIC_LABEL.match(p) for p in parts):
        raise PolicyBlocked("url_invalid", f"некоректний числовий host {host!r}")
    values = []
    for part in parts:
        if part[:2].lower() == "0x":
            values.append(int(part[2:] or "0", 16))
        elif len(part) > 1 and part.startswith("0"):
            if not set(part) <= set("01234567"):
                raise PolicyBlocked("url_invalid", f"некоректний octal у host {host!r}")
            values.append(int(part, 8))
        else:
            values.append(int(part))
    head, last = values[:-1], values[-1]
    if any(v > 255 for v in head) or last >= 256 ** (5 - len(values)):
        raise PolicyBlocked("url_invalid", f"числовий host поза діапазоном IPv4: {host!r}")
    number = last
    for index, value in enumerate(head):
        number += value << (8 * (3 - index))
    return IPv4Address(number)


def parse_ip_literal(host: str) -> IPAddress | None:
    """IP-літерал host-а (з канонізацією числових IPv4) або `None`, якщо host — ім'я."""
    bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    if ":" in bare:
        try:
            return ip_address(bare.replace("%25", "%"))
        except ValueError as exc:
            raise PolicyBlocked("url_invalid", f"некоректний IPv6 host {host!r}") from exc
    labels = bare.rstrip(".").split(".")
    if not _NUMERIC_LABEL.match(labels[-1]):
        return None
    ip = _parse_ipv4_number(bare)
    if forbidden_reason(ip) is None and str(ip) != bare:
        raise PolicyBlocked("host_noncanonical_ip", f"неканонічна форма IP {host!r}")
    return ip


def forbidden_reason(ip: IPAddress) -> str | None:
    """Чому адреса заборонена для вихідного запиту, або `None` для глобальної unicast."""
    if ip in METADATA_ADDRESSES:
        return "cloud metadata"
    if isinstance(ip, IPv6Address):
        embedded: IPv4Address | None = ip.ipv4_mapped or ip.sixtofour
        if ip in NAT64_PREFIX:
            embedded = IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None and (reason := forbidden_reason(embedded)) is not None:
            return f"embedded IPv4 {embedded}: {reason}"
        if ip in IPV4_COMPATIBLE_PREFIX:
            return "IPv4-compatible IPv6"
        if ip.scope_id:
            return "scoped IPv6"
    checks = (
        (ip.is_unspecified, "unspecified"),
        (ip.is_loopback, "loopback"),
        (ip.is_link_local, "link-local"),
        (ip.is_multicast, "multicast"),
        (ip.is_private, "private"),
        (ip.is_reserved, "reserved"),
        (not ip.is_global, "not global"),  # CGNAT 100.64/10, 0.0.0.0/8, broadcast, ...
    )
    return next((reason for hit, reason in checks if hit), None)


def check_host_name(host: str) -> None:
    """Заборонені за ім'ям: `localhost`, `*.localhost` і будь-який host із trailing dot."""
    lowered = host.lower()
    if lowered.endswith("."):
        raise PolicyBlocked("ssrf_forbidden_host", f"host із trailing dot: {host!r}")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise PolicyBlocked("ssrf_forbidden_host", f"заборонений host {host!r}")


def _require_allowed(ip: IPAddress, host: str) -> None:
    reason = forbidden_reason(ip)
    if reason is not None:
        raise PolicyBlocked("ssrf_forbidden_address", f"{host!r} → {ip}: {reason}")


async def resolve_pinned(host: str, port: int, resolver: Resolver) -> IPAddress:
    """Рівно один резолв: усі адреси мають бути дозволені; повертає IP для pinning."""
    literal = parse_ip_literal(host)
    if literal is not None:
        _require_allowed(literal, host)
        return literal
    check_host_name(host)
    try:
        answers = await resolver.resolve(host, port)
    except OSError as exc:
        raise DnsResolutionError(f"DNS {host!r}: {exc}") from exc
    if not answers:
        raise DnsResolutionError(f"DNS {host!r}: порожня відповідь")
    try:
        addresses = [ip_address(answer) for answer in answers]
    except ValueError as exc:
        raise PolicyBlocked("ssrf_forbidden_address", f"некоректна адреса з DNS: {exc}") from exc
    for address in addresses:
        _require_allowed(address, host)
    return addresses[0]
