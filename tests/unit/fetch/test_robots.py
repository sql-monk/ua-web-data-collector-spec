from datetime import UTC, datetime, timedelta

import pytest

from collector.fetch.robots import RobotsSnapshot, robots_allows, robots_url


def test_robots_url_is_origin_scoped() -> None:
    assert robots_url("https://example.test/a?x=1") == "https://example.test/robots.txt"


def test_respect_blocks_disallowed_page_but_diagnostic_does_not() -> None:
    body = b"User-agent: *\nDisallow: /private\n"
    url = "https://example.test/private/item"

    assert robots_allows("respect", body, url, "Collector/1") is False
    assert robots_allows("diagnostic", body, url, "Collector/1") is True


def test_unknown_robots_policy_fails_closed_at_configuration_boundary() -> None:
    with pytest.raises(ValueError, match="unknown robots policy"):
        robots_allows("surprise", b"", "https://example.test/", "Collector/1")


def test_snapshot_ttl_is_strict() -> None:
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    snapshot = RobotsSnapshot(now - timedelta(hours=1), 200, "a" * 64, "raw/key")

    assert snapshot.fresh(now, timedelta(hours=2))
    assert not snapshot.fresh(now, timedelta(hours=1))
