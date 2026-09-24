"""Нормалізація URL/origin (FR-014): `normalize_origin` — єдиний ключ bucket-а limiter-а."""

from __future__ import annotations

import pytest

from collector.contracts.identity import tracking_query_keys
from collector.fetch.urls import UrlError, normalize_origin, normalize_url


def test_tracking_params_removed_original_kept() -> None:
    raw = "HTTPS://News.Example.ORG:443/a/b?utm_source=x&id=7&fbclid=abc&UTM_Medium=y&q=1#top"
    url = normalize_url(raw)
    assert url.original == raw
    assert url.normalized == "https://news.example.org/a/b?id=7&q=1"
    assert tracking_query_keys(url.normalized) == frozenset()


def test_idna_and_default_port() -> None:
    assert normalize_url("http://Пример.РФ:80/x").normalized == "http://xn--e1afmkfd.xn--p1ai/x"
    assert normalize_url("https://example.org:8443").normalized == "https://example.org:8443/"


def test_query_order_and_encoding_preserved() -> None:
    assert (
        normalize_url("http://e.org/p?b=2&a=%2F&a=1").normalized == "http://e.org/p?b=2&a=%2F&a=1"
    )


def test_only_tracking_query_leaves_no_question_mark() -> None:
    assert normalize_url("http://e.org/p?utm_campaign=1").normalized == "http://e.org/p"


@pytest.mark.parametrize(
    ("raw", "origin"),
    [
        ("HTTP://Example.ORG:80/x", "http://example.org"),
        ("https://example.org:443/", "https://example.org"),
        ("https://example.org:8443/a?b", "https://example.org:8443"),
        ("http://[2606:2800::1]:80/", "http://[2606:2800::1]"),
        ("http://Пример.рф/", "http://xn--e1afmkfd.xn--p1ai"),
    ],
)
def test_normalize_origin(raw: str, origin: str) -> None:
    assert normalize_origin(raw) == origin


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("http://u:p@example.org/", "url_userinfo_forbidden"),
        ("http:///nohost", "url_invalid"),
        ("http://example.org:99999/", "url_invalid"),
        ("http://a..b/", "url_invalid"),
    ],
)
def test_invalid_urls(raw: str, code: str) -> None:
    with pytest.raises(UrlError) as info:
        normalize_url(raw)
    assert info.value.error_code == code


def test_underscore_host_is_accepted_lowercased() -> None:
    assert normalize_origin("http://My_Site.Example.org/") == "http://my_site.example.org"
