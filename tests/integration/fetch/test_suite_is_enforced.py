"""Захист від мовчазного skip integration-suite fetch (картка WP-02, «Спільні вимоги»).

Зразок — `tests/e2e/test_runtime_suite_is_enforced.py`. Під `COLLECTOR_TEST_REQUIRE_DOCKER=1`:

- кожен модуль із `ENFORCED_MODULES` існує і реально виконується: будь-який skip у каталозі
  `conftest.py` перетворює на fail (`pytest_runtest_makereport`);
- PostgreSQL справді доступний (цей тест бере фікстуру БД — без Docker він падає, а не skip).

Локально без прапорця модуль сам пропускається — Docker не обов'язковий для розробника.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

REQUIRED = os.environ.get("COLLECTOR_TEST_REQUIRE_DOCKER") == "1"
ENFORCED_MODULES = (
    "test_claimed_upload.py",
    "test_handler.py",
    "test_permits_pg.py",
    "test_storage_minio.py",
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not REQUIRED,
        reason="перевірка діє лише там, де Docker обіцяний (COLLECTOR_TEST_REQUIRE_DOCKER=1)",
    ),
]


def test_enforced_modules_exist() -> None:
    here = Path(__file__).parent
    for name in ENFORCED_MODULES:
        assert (here / name).is_file(), name


async def test_postgres_is_reachable_so_suite_runs(
    pg_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_sessions() as session:
        assert await session.scalar(text("SELECT 1")) == 1
