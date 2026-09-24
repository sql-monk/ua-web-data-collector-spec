"""Integration-фікстури PostgreSQL 18 (WP-01A): чиста схема на кожен тест.

- сервер: `COLLECTOR_TEST_POSTGRES_ADMIN_DSN` (зовнішній PG, напр. compose) або
  `testcontainers` з pinned image `postgres:18@sha256:...`; Docker недоступний → skip з
  повідомленням (у CI `COLLECTOR_TEST_REQUIRE_DOCKER=1` перетворює skip на fail);
- мережа: лише loopback (маркер `integration` → `allow_hosts` у `tests/conftest.py`);
  testcontainers host примусово `127.0.0.1` (Ryuk/порти), Docker daemon — npipe/unix socket;
- template DB `collector_template` будується один раз на сесію (лениво, всередині першого
  тесту — коли політика сокетів уже застосована): `alembic upgrade head` + партиції +
  `roles.sql`; кожен тест отримує `CREATE DATABASE ... TEMPLATE collector_template` і
  `DROP DATABASE ... WITH (FORCE)` після себе — жодного спільного стану між тестами.
"""

from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from collector.contracts import (
    AppliedProjectionReceipt,
    DomainChangedEvent,
    NormalizedArtifactRef,
    encode_event,
    new_entity_id,
)
from collector.contracts.enums import DataDomain, EntityKind
from collector.persistence.postgres.config import PostgresSettings
from collector.persistence.postgres.engine import create_engine, create_session_factory
from collector.persistence.postgres.migrations import upgrade_to_head
from collector.persistence.postgres.models import EntityIndex
from collector.persistence.postgres.ops import apply_database_roles
from collector.persistence.postgres.partitions import ensure_month_partitions
from collector.persistence.postgres.repositories import entities, projection
from collector.persistence.postgres.roles import (
    RUNTIME_ROLES,
    apply_roles,
    dsn_secret_name,
    load_role_logins,
)

pytestmark = pytest.mark.integration

POSTGRES_IMAGE = (
    "postgres:18@sha256:86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae"
)
ADMIN_DSN_ENV = "COLLECTOR_TEST_POSTGRES_ADMIN_DSN"
REQUIRE_DOCKER_ENV = "COLLECTOR_TEST_REQUIRE_DOCKER"
TEMPLATE_DB = "collector_template"
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXED_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PostgresServer:
    """Адміністративне підключення до сервера (superuser) для CREATE/DROP DATABASE."""

    admin_url: URL

    def url_for(self, database: str) -> URL:
        return self.admin_url.set(database=database)


class TemplateState:
    """Кеш сесії: чи побудовано template DB (лениво, у першому тесті)."""

    def __init__(self) -> None:
        self.ready = False


def _skip_or_fail(reason: str) -> None:
    if os.environ.get(REQUIRE_DOCKER_ENV) == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _start_container() -> Iterator[PostgresServer]:
    try:
        from testcontainers.community.postgres import PostgresContainer
        from testcontainers.core.config import testcontainers_config
        from testcontainers.core.docker_client import DockerClient
    except ImportError as exc:  # pragma: no cover - dev-залежність
        _skip_or_fail(f"testcontainers недоступний: {exc}")
        raise
    try:
        DockerClient().client.ping()
    except Exception as exc:  # будь-яка помилка daemon = Docker недоступний
        _skip_or_fail(f"Docker недоступний ({type(exc).__name__}: {exc}) — integration skip")
        raise
    if not testcontainers_config.tc_host_override:
        # Ryuk і mapped ports мають іти на 127.0.0.1 (allow_hosts), а не на "localhost".
        testcontainers_config.tc_host_override = "127.0.0.1"
    container = PostgresContainer(
        POSTGRES_IMAGE,
        username="collector_test_admin",
        password=secrets.token_urlsafe(16),  # одноразовий, лише loopback-контейнер
        dbname="postgres",
        driver=None,
    ).with_command("postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off")
    container.start()
    try:
        url = make_url(container.get_connection_url(host="127.0.0.1", driver="asyncpg"))
        yield PostgresServer(admin_url=url)
    finally:
        container.stop()


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresServer]:
    external = os.environ.get(ADMIN_DSN_ENV)
    if external:
        yield PostgresServer(admin_url=PostgresSettings.from_dsn(external).url)
        return
    yield from _start_container()


@pytest.fixture(scope="session")
def _template_state() -> TemplateState:
    return TemplateState()


async def _admin_execute(server: PostgresServer, *statements: str) -> None:
    engine = create_async_engine(server.admin_url, isolation_level="AUTOCOMMIT", poolclass=None)
    try:
        async with engine.connect() as conn:
            for statement in statements:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()


async def _build_template(server: PostgresServer) -> None:
    await _admin_execute(
        server,
        f"DROP DATABASE IF EXISTS {TEMPLATE_DB} WITH (FORCE)",
        f"CREATE DATABASE {TEMPLATE_DB}",
    )
    engine = create_async_engine(server.url_for(TEMPLATE_DB), poolclass=None)
    try:
        async with engine.begin() as conn:
            await upgrade_to_head(conn, ini_path=REPO_ROOT / "alembic.ini")
            await ensure_month_partitions(conn, months_ahead=3, start=FIXED_NOW.date())
            await apply_roles(conn)
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_database(
    postgres_server: PostgresServer, _template_state: TemplateState
) -> AsyncIterator[PostgresSettings]:
    """Свіжа БД з template (міграції + ролі + партиції) на один тест."""
    if not _template_state.ready:
        await _build_template(postgres_server)
        _template_state.ready = True
    name = f"t_{uuid.uuid4().hex[:12]}"
    await _admin_execute(postgres_server, f"CREATE DATABASE {name} TEMPLATE {TEMPLATE_DB}")
    try:
        yield PostgresSettings(url=postgres_server.url_for(name))
    finally:
        await _admin_execute(postgres_server, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
async def pg_empty_database(postgres_server: PostgresServer) -> AsyncIterator[PostgresSettings]:
    """Порожня БД без міграцій — для тестів `upgrade head`/`downgrade`/CLI."""
    name = f"e_{uuid.uuid4().hex[:12]}"
    await _admin_execute(postgres_server, f"CREATE DATABASE {name}")
    try:
        yield PostgresSettings(url=postgres_server.url_for(name))
    finally:
        await _admin_execute(postgres_server, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
async def pg_engine(pg_database: PostgresSettings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(pg_database, pool_size=10, max_overflow=6, application_name="pytest")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def pg_sessions(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(pg_engine)


@pytest.fixture
async def pg_session(pg_sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with pg_sessions() as session:
        yield session


# --- PR2 builders (контракти → аргументи репозиторіїв projection/ack/outbox) -------------

COLLECTION = "catalog_items"
RAW_SHA = "0" * 64


def state_hash(n: int) -> str:
    return f"v1:{n:064x}"


def artifact_ref(entity_uuid: UUID, n: int, *, fetch: int | None = None) -> NormalizedArtifactRef:
    """Artifact із bytes `n`; `fetch` — номер fetch (lineage parse-кроку, CR-1), типово = `n`.

    Однакові `(n, fetch)` — **той самий parse** (повтор); той самий `n` з іншим `fetch` —
    новий parse byte-identical artifact-а (стан A→B→A).
    """
    sha = f"{n:064x}"
    return NormalizedArtifactRef(
        uri=f"s3://normalized/{sha}.json",
        sha256=sha,
        size_bytes=100 + n,
        media_type="application/json",
        schema_version="1.0",
        entity_uuid=entity_uuid,
        domain=DataDomain.CATALOG,
        parser_version="parser-1.0",
        fetch_id=UUID(int=n if fetch is None else fetch),
        raw_sha256=RAW_SHA,
        raw_uri="s3://raw/" + RAW_SHA,
        produced_at=FIXED_NOW,
    )


def attempt_record(ref: NormalizedArtifactRef | None = None) -> projection.ParseAttemptRecord:
    """Parse attempt; з `ref` — узгоджений з artifact lineage (SR-2: той самий fetch/raw/parser)."""
    if ref is None:
        return projection.ParseAttemptRecord(
            raw_sha256=RAW_SHA, parser_version="parser-1.0", outcome="succeeded", domain="catalog"
        )
    return projection.ParseAttemptRecord(
        raw_sha256=ref.raw_sha256,
        parser_version=ref.parser_version,
        outcome="succeeded",
        domain=ref.domain.value,
        fetch_id=ref.fetch_id,
    )


async def make_entity(session: AsyncSession, item: str = "item-1") -> EntityIndex:
    async with session.begin():
        return await entities.upsert_entity(
            session,
            entities.EntityIdentity(
                source_id="catalog_ua_example",
                source_item_id=item,
                domain=DataDomain.CATALOG,
                entity_kind=EntityKind.CATALOG_ITEM,
            ),
            now=FIXED_NOW,
        )


async def record(
    session: AsyncSession, entity_uuid: UUID, n: int, *, fetch: int | None = None
) -> projection.ParseResult:
    async with session.begin():
        return await projection.record_parse_result(
            session,
            attempt=attempt_record(artifact_ref(entity_uuid, n, fetch=fetch)),
            artifact_ref=artifact_ref(entity_uuid, n, fetch=fetch),
            object_key=f"normalized/{n:064x}.json",
            target_collection=COLLECTION,
            target_schema_version="1.0",
            now=FIXED_NOW,
        )


def receipt(
    task_id: UUID,
    entity_uuid: UUID,
    version: int,
    *,
    applied: bool,
    changed: bool,
    current_version: int | None = None,
    document_id: UUID | None = None,
) -> AppliedProjectionReceipt:
    """Receipt Mongo projector-а; для `applied AND changed` — з готовими event bytes."""
    result_version = version if applied else (current_version or version)
    fields: dict[str, object] = {}
    if applied and changed:
        event = DomainChangedEvent(
            event_id=new_entity_id(),
            aggregate_id=entity_uuid,
            aggregate_version=version,
            event_type="catalog.item.changed",
            payload_schema_version="1.0",
            occurred_at=FIXED_NOW,
            projection_task_id=task_id,
            previous_state_hash=state_hash(version - 1) if version > 1 else None,
            result_state_hash=state_hash(version),
            payload={"version": version, "note": "укр текст"},
        )
        encoded = encode_event(event)
        fields = {
            "event_id": encoded.event_id,
            "event_bytes": encoded.event_bytes,
            "event_media_type": encoded.event_media_type,
            "event_sha256": encoded.event_sha256,
        }
    return AppliedProjectionReceipt(
        projection_task_id=task_id,
        entity_uuid=entity_uuid,
        projection_version=version,
        target_collection=COLLECTION,
        document_id=document_id or entity_uuid,
        applied_to_current=applied,
        state_changed=changed,
        previous_hash=state_hash(version - 1) if changed and version > 1 else None,
        result_version=result_version,
        result_hash=state_hash(result_version),
        committed_at=FIXED_NOW,
        cluster_time=f"1790000000:{version}",
        **fields,
    )


# --- LOGIN-ролі per component (PR2 `test_role_logins.py`; PR3a — нові операції) -------
# Ролі кластерні (спільні для всіх тестових БД), тому `logins` після тесту повертає їх у
# NOLOGIN без пароля. Паролі — одноразові `secrets.token_hex` у рантаймі (жодного secret у
# fixtures).


@dataclass(frozen=True, slots=True)
class Logins:
    database: PostgresSettings
    secrets_dir: Path
    urls: dict[str, URL]


RoleEngine = Callable[[str], Awaitable[AsyncEngine]]


async def reset_role_logins(admin: AsyncEngine) -> None:
    async with admin.connect() as conn:
        for role in RUNTIME_ROLES:
            await conn.execute(text(f'ALTER ROLE "{role}" WITH NOLOGIN PASSWORD NULL'))


def write_role_secrets(database: PostgresSettings, directory: Path) -> dict[str, URL]:
    urls: dict[str, URL] = {}
    for role in RUNTIME_ROLES:
        url = database.url.set(username=role, password=secrets.token_hex(24))
        (directory / dsn_secret_name(role)).write_text(
            url.render_as_string(hide_password=False) + "\n", encoding="utf-8"
        )
        urls[role] = url
    return urls


@pytest.fixture
async def logins(pg_database: PostgresSettings, tmp_path: Path) -> AsyncIterator[Logins]:
    admin = create_async_engine(pg_database.url, isolation_level="AUTOCOMMIT", poolclass=None)
    urls = write_role_secrets(pg_database, tmp_path)
    try:
        await apply_database_roles(pg_database, logins=load_role_logins(tmp_path))
        yield Logins(database=pg_database, secrets_dir=tmp_path, urls=urls)
    finally:
        await reset_role_logins(admin)
        await admin.dispose()


@pytest.fixture
async def role_engine(logins: Logins) -> AsyncIterator[RoleEngine]:
    engines: list[AsyncEngine] = []

    async def make(role: str) -> AsyncEngine:
        engine = create_async_engine(logins.urls[role], poolclass=None)
        engines.append(engine)
        return engine

    try:
        yield make
    finally:
        for engine in engines:
            await engine.dispose()
