"""JSON-логування: одна JSON-подія на рядок, без ASCII-escape українських символів."""

from __future__ import annotations

import io
import json

from collector.core.logging import configure_logging, get_logger


def test_configure_logging_emits_json_lines() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    get_logger("collector.test").info("подія", source_id="prom_ua", count=3)

    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "подія"
    assert event["level"] == "info"
    assert event["logger"] == "collector.test"
    assert event["source_id"] == "prom_ua"
    assert event["count"] == 3
    assert event["timestamp"].endswith("Z")


def test_configure_logging_is_idempotent_and_filters_level() -> None:
    stream = io.StringIO()
    configure_logging("WARNING", stream=stream)
    configure_logging("WARNING", stream=stream)
    logger = get_logger("collector.test")
    logger.info("hidden")
    logger.warning("shown")

    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "shown"
