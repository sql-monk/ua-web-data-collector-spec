"""Фабрики для тестів shared-контрактів: фіксовані часи, UUIDv7, valid payload-и.

Імпортується тестами як `factories` (каталог додано в sys.path у conftest, бо pytest працює в
`--import-mode=importlib` без пакета `tests`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
# Фіксовані UUIDv7 (version nibble 7, variant 10) для детермінованих fixtures/golden.
ENTITY_A = UUID("019997c0-0000-7000-8000-000000000001")
ENTITY_B = UUID("019997c0-0000-7000-8000-000000000002")
ENTITY_C = UUID("019997c0-0000-7000-8000-000000000003")
ENTITY_D = UUID("019997c0-0000-7000-8000-000000000004")
TASK_ID = UUID("11111111-1111-4111-8111-111111111111")
FETCH_ID = UUID("22222222-2222-4222-8222-222222222222")
EVENT_ID = UUID("33333333-3333-4333-8333-333333333333")
GROUP_1 = UUID("44444444-4444-4444-8444-444444444441")
GROUP_2 = UUID("44444444-4444-4444-8444-444444444442")


def at(minutes: int = 0, **kwargs: int) -> datetime:
    """`T0 + minutes` (+ довільні `timedelta` kwargs)."""
    return T0 + timedelta(minutes=minutes, **kwargs)
