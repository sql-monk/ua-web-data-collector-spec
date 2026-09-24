"""Тест-вартовий O-1 (картка WP-02): `PgOriginPermits` — тимчасовий адаптер до PG-лімітера.

Щойно WP-01D PR2 додасть `collector.core.limiter_runtime.OriginPermitClient`, цей тест стає
червоним: адаптер треба прибрати, а `SafeFetcher` підключити до `OriginPermitClient`.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

MODULE = "collector.core.limiter_runtime"
MESSAGE = (
    "прибрати `PgOriginPermits` після WP-01D PR2 і перейти на `OriginPermitClient` "
    "(collector.core.limiter_runtime) — рішення O-1 картки WP-02"
)


def _tripwire() -> str | None:
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != MODULE:
            raise
        return None
    return MESSAGE if hasattr(module, "OriginPermitClient") else None


def test_pg_origin_permits_adapter_is_still_needed() -> None:
    message = _tripwire()
    if message is not None:
        pytest.fail(message)


def test_tripwire_fires_when_origin_permit_client_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    """Самоперевірка вартового: фіктивний модуль з `OriginPermitClient` → повідомлення."""
    fake = types.ModuleType(MODULE)
    fake.OriginPermitClient = type("OriginPermitClient", (), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, MODULE, fake)
    assert _tripwire() == MESSAGE
