"""Схема в **живій БД** проти контрактних enum і констант коду (M-3 код-рев'ю).

`alembic check` цієї прогалини не закриває: autogenerate не порівнює ні CHECK-констрейнти, ні
`WHERE` partial index-ів, а unit-тест `test_metadata.py` звіряє enum лише з метаданими моделі
(тобто сам із собою). Тут значення читаються з `pg_get_constraintdef`/`pg_indexes.indexdef`
після `alembic upgrade head` — тож розходження «контракт змінився, міграція ні» або підміна
констрейнта руками ламає CI, а не production.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from collector.contracts.enums import (
    ContentAccess,
    DataDomain,
    FetchOutcome,
    RouteState,
    SourceState,
    UploadClaimStatus,
)
from collector.persistence.postgres.models.artifacts import PARSE_OUTCOMES
from collector.persistence.postgres.models.limiter import PERMIT_RELEASE_REASONS
from collector.persistence.postgres.models.outbox import OUTBOX_TOPICS
from collector.persistence.postgres.models.pools import (
    INSTANCE_STATUSES,
    POOL_MODES,
    SCALE_COMMAND_STATUSES,
)
from collector.persistence.postgres.models.projection import (
    CLAIMABLE_PROJECTION_STATUSES,
    PROJECTION_TASK_STATUSES,
)
from collector.persistence.postgres.models.queue import (
    CLAIMABLE_JOB_STATUSES,
    CRAWL_RUN_KINDS,
    CRAWL_RUN_STATUSES,
    DEAD_LETTER_REASONS,
    JOB_STATUSES,
)
from collector.workers.roles import WorkerRole

pytestmark = pytest.mark.integration

_QUOTED = re.compile(r"'([^']*)'")

# (constraint, очікувані значення з контрактів/констант коду)
CHECK_CONTRACTS: list[tuple[str, str, frozenset[str]]] = [
    ("sources", "ck_sources_state", frozenset(s.value for s in SourceState)),
    ("sources", "ck_sources_domain", frozenset(d.value for d in DataDomain)),
    ("source_routes", "ck_source_routes_state", frozenset(s.value for s in RouteState)),
    ("worker_pools", "ck_worker_pools_role", frozenset(r.value for r in WorkerRole)),
    ("worker_pools", "ck_worker_pools_mode", frozenset(POOL_MODES)),
    ("worker_instances", "ck_worker_instances_status", frozenset(INSTANCE_STATUSES)),
    ("scale_commands", "ck_scale_commands_status", frozenset(SCALE_COMMAND_STATUSES)),
    ("crawl_jobs", "ck_crawl_jobs_status", frozenset(JOB_STATUSES)),
    ("crawl_runs", "ck_crawl_runs_kind", frozenset(CRAWL_RUN_KINDS)),
    ("crawl_runs", "ck_crawl_runs_status", frozenset(CRAWL_RUN_STATUSES)),
    ("dead_letters", "ck_dead_letters_reason", frozenset(DEAD_LETTER_REASONS)),
    (
        "origin_rate_permits",
        "ck_origin_rate_permits_release_reason",
        frozenset(PERMIT_RELEASE_REASONS),
    ),
    # PR2
    ("fetches", "ck_fetches_outcome", frozenset(o.value for o in FetchOutcome)),
    ("fetches", "ck_fetches_content_access", frozenset(a.value for a in ContentAccess)),
    ("parse_attempts", "ck_parse_attempts_outcome", frozenset(PARSE_OUTCOMES)),
    ("parse_attempts", "ck_parse_attempts_domain", frozenset(d.value for d in DataDomain)),
    (
        "artifact_upload_claims",
        "ck_artifact_upload_claims_status",
        frozenset(s.value for s in UploadClaimStatus),
    ),
    (
        "normalized_artifacts",
        "ck_normalized_artifacts_domain",
        frozenset(d.value for d in DataDomain),
    ),
    ("projection_tasks", "ck_projection_tasks_status", frozenset(PROJECTION_TASK_STATUSES)),
    ("entity_index", "ck_entity_index_domain", frozenset(d.value for d in DataDomain)),
    ("outbox_events", "ck_outbox_events_topic", frozenset(OUTBOX_TOPICS)),
]


async def _constraint_def(engine: AsyncEngine, table: str, name: str) -> str:
    async with engine.connect() as conn:
        definition = await conn.scalar(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = :table AND c.conname = :name"
            ),
            {"table": table, "name": name},
        )
    assert definition is not None, f"{table}.{name} немає у БД"
    return str(definition)


@pytest.mark.parametrize(
    ("table", "constraint", "expected"),
    CHECK_CONTRACTS,
    ids=[name for _, name, _ in CHECK_CONTRACTS],
)
async def test_check_constraint_in_database_matches_contract_values(
    pg_engine: AsyncEngine, table: str, constraint: str, expected: frozenset[str]
) -> None:
    """Множина значень у CHECK живої БД дорівнює множині з контракту — не підмножина."""
    definition = await _constraint_def(pg_engine, table, constraint)
    assert set(_QUOTED.findall(definition)) == set(expected), definition


async def test_claim_index_predicate_in_database_matches_claimable_statuses(
    pg_engine: AsyncEngine,
) -> None:
    """Предикат partial index-у hot path (`claim`) у БД = `CLAIMABLE_JOB_STATUSES`."""
    async with pg_engine.connect() as conn:
        indexdef = await conn.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": "ix_crawl_jobs_claimable_order"},
        )
    assert indexdef is not None
    where = str(indexdef).split(" WHERE ", 1)[1]
    assert set(_QUOTED.findall(where)) == set(CLAIMABLE_JOB_STATUSES), indexdef
    assert "priority DESC" in str(indexdef)


async def test_projection_claim_index_predicate_matches_claimable_statuses(
    pg_engine: AsyncEngine,
) -> None:
    """Те саме для `ix_projection_tasks_claimable_order` (hot path `claim_projection_tasks`)."""
    async with pg_engine.connect() as conn:
        indexdef = await conn.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": "ix_projection_tasks_claimable_order"},
        )
    assert indexdef is not None
    where = str(indexdef).split(" WHERE ", 1)[1]
    assert set(_QUOTED.findall(where)) == set(CLAIMABLE_PROJECTION_STATUSES), indexdef
    assert "priority DESC" in str(indexdef)


async def test_every_contract_value_is_actually_accepted_by_the_database(
    pg_engine: AsyncEngine,
) -> None:
    """Кожне значення `SourceState` справді проходить INSERT.

    Рев'юер відтворив саме цей розрив: CHECK у БД урізали до двох станів, `alembic check`
    лишився зеленим, а перше джерело у `blocked_anonymous` впало в runtime.
    """
    async with pg_engine.begin() as conn:
        for index, state in enumerate(SourceState):
            await conn.execute(
                text(
                    "INSERT INTO sources (id, source_id, domain, country, state, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), :sid, 'news', 'UA', "
                    ":state, now(), now())"
                ),
                {"sid": f"news_ua_probe_{index}", "state": state.value},
            )
        count = await conn.scalar(text("SELECT count(*) FROM sources"))
    assert count == len(list(SourceState))


async def test_drifted_check_constraint_is_detected(pg_engine: AsyncEngine) -> None:
    """Мутаційний контроль самого тесту: підміна CHECK у БД має червоніти.

    Відтворює сценарій рев'ю — CHECK урізано руками (або міграцією, що відстала від контракту),
    `alembic check` мовчить. Транзакція відкочується, схема лишається незмінною.
    """
    async with pg_engine.connect() as conn:
        transaction = await conn.begin()
        await conn.execute(text("ALTER TABLE sources DROP CONSTRAINT ck_sources_state"))
        await conn.execute(
            text("ALTER TABLE sources ADD CONSTRAINT ck_sources_state CHECK (state IN ('enabled'))")
        )
        definition = await conn.scalar(
            text(
                "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = 'sources' AND c.conname = 'ck_sources_state'"
            )
        )
        assert set(_QUOTED.findall(str(definition))) != {s.value for s in SourceState}
        await transaction.rollback()

    assert set(
        _QUOTED.findall(await _constraint_def(pg_engine, "sources", "ck_sources_state"))
    ) == {s.value for s in SourceState}
