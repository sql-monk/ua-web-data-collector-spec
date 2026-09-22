"""JSON-логування: одна JSON-подія на рядок, stdlib-записи теж JSON, секрети приховані (§13)."""

from __future__ import annotations

import io
import json
import logging

import pytest

from collector.core.logging import REDACTED_VALUE, configure_logging, get_logger, redact_secrets


def _json_lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().strip().splitlines()]


def test_configure_logging_emits_json_lines() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    get_logger("collector.test").info("подія", source_id="prom_ua", count=3)

    events = _json_lines(stream)
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "подія"
    assert event["level"] == "info"
    assert event["logger"] == "collector.test"
    assert event["source_id"] == "prom_ua"
    assert event["count"] == 3
    assert str(event["timestamp"]).endswith("Z")


def test_configure_logging_is_idempotent_and_filters_level() -> None:
    stream = io.StringIO()
    configure_logging("WARNING", stream=stream)
    configure_logging("WARNING", stream=stream)
    logger = get_logger("collector.test")
    logger.info("hidden")
    logger.warning("shown")

    events = _json_lines(stream)
    assert len(events) == 1
    assert events[0]["event"] == "shown"


def test_stdlib_records_from_third_party_loggers_are_json() -> None:
    """Записи через `logging` (httpx, pymongo, uvicorn) проходять той самий JSON-ланцюг."""
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)

    logging.getLogger("httpx").warning("HTTP Request: GET %s", "https://example.invalid/")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("pymongo").exception("x")

    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 2, lines
    first, second = (json.loads(line) for line in lines)
    assert first == {
        "event": "HTTP Request: GET https://example.invalid/",
        "logger": "httpx",
        "level": "warning",
        "timestamp": first["timestamp"],
    }
    assert str(first["timestamp"]).endswith("Z")
    assert second["logger"] == "pymongo"
    assert second["level"] == "error"
    assert "ValueError: boom" in str(second["exception"])


@pytest.mark.parametrize(
    "key",
    ["authorization", "Authorization", "PROXY-AUTHORIZATION", "cookie", "Set-Cookie", "api_key"],
)
def test_secret_keys_are_redacted_case_insensitively(key: str) -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    get_logger("collector.test").info("req", **{key: "Bearer abc"}, url="https://x.invalid/")

    (event,) = _json_lines(stream)
    assert event[key] == REDACTED_VALUE
    assert event["url"] == "https://x.invalid/"
    assert "Bearer abc" not in stream.getvalue()


def test_redaction_is_recursive_in_nested_mappings_and_lists() -> None:
    event = redact_secrets(
        None,
        "info",
        {
            "event": "req",
            "headers": {"Cookie": "sid=1", "Accept": "*/*", "nested": {"token": "t"}},
            "attempts": [{"password": "p", "status": 200}],
            "api-key": "k",
            "secret": {"inner": "value"},
        },
    )
    assert event == {
        "event": "req",
        "headers": {"Cookie": REDACTED_VALUE, "Accept": "*/*", "nested": {"token": REDACTED_VALUE}},
        "attempts": [{"password": REDACTED_VALUE, "status": 200}],
        "api-key": REDACTED_VALUE,
        "secret": REDACTED_VALUE,
    }


def test_redaction_applies_to_stdlib_extra_fields() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    logging.getLogger("uvicorn").info("auth", extra={"token": "t-123"})

    (event,) = _json_lines(stream)
    assert event["event"] == "auth"
    assert event["token"] == REDACTED_VALUE
    assert "t-123" not in stream.getvalue()


@pytest.mark.parametrize(
    "key", ["phone", "Phones", "email", "EMAILS", "contact", "contacts", "messenger", "seller_name"]
)
def test_contact_keys_are_redacted_in_logs(key: str) -> None:
    """§13/R-11: контакти продавців не дублюються в технічних логах."""
    stream = io.StringIO()
    configure_logging("INFO", stream=stream)
    get_logger("collector.test").info("seller", **{key: "+380501234567"}, source_id="olx_ua")

    (event,) = _json_lines(stream)
    assert event[key] == REDACTED_VALUE
    assert event["source_id"] == "olx_ua"
    assert "+380501234567" not in stream.getvalue()


def test_nested_contact_block_is_redacted_recursively() -> None:
    event = redact_secrets(
        None,
        "info",
        {
            "event": "listing",
            "seller": {"seller_name": "Іван", "phones": ["+380501234567"], "city": "Київ"},
            "observations": [{"email": "a@b.invalid", "price": 100}],
        },
    )
    assert event == {
        "event": "listing",
        "seller": {"seller_name": REDACTED_VALUE, "phones": REDACTED_VALUE, "city": "Київ"},
        "observations": [{"email": REDACTED_VALUE, "price": 100}],
    }
