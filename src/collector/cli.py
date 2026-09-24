"""CLI `collector` — контракт команд §16.2 ТЗ.

У WP-00 команди контракту є типізованими стабами: вони друкують
`not implemented: owned by WP-XX` у stderr і завершуються з кодом 2. Власник WP
замінює тіло відповідної команди, не змінюючи її назву та параметри.

Стан команд після WP-00 PR2 (Docker/Compose) і WP-01A PR1 (PostgreSQL foundation):

- `version` — реальна (WP-00);
- `contracts export [--check]` — реальна (WP-01C; foundation-розширення поза §16.2);
- `db migrate [--check] [--partitions-ahead N]` — реальна (WP-01A): `alembic upgrade head`
  і місячні партиції; DSN з `COLLECTOR_POSTGRES_DSN` або `COLLECTOR_POSTGRES_DSN_FILE`
  (Docker secret; у Compose це one-shot `migrate-postgres`). Замінила TCP-перевірку
  «no migrations yet; owner WP-01A» з WP-00 PR2;
- `db roles [--sql PATH] [--with-login [--secrets-dir DIR]]` — реальна (WP-01A): ролі БД §13 і
  GRANT, ідемпотентно; `--with-login` (PR2) вмикає LOGIN runtime-ролей з паролями з
  DSN-секретів `postgres_dsn_<component>`;
- `db ensure-mongo [--validators] [--indexes] [--users [--secrets-dir DIR]]` — реальна:
  ініціалізує single-member replica set (ідемпотентно; WP-00 PR2), далі (WP-01B PR1)
  forward-only Mongo-міграції (collections + `$jsonSchema` validators), indexes маніфесту §9.2 і
  Mongo-користувачі компонентів §13 з URI-секретів `mongo_uri_<component>`;
- `api` — запускає uvicorn зі стабом `GET /api/v1/health/components`
  (`collector.api.health`; owner WP-11A);
- `worker <role>` — реальна (WP-01D PR1): реєстрація instance, claim із черги §7.2,
  lease heartbeat, hot-change `desired_concurrency` з `worker_pools`, drain по SIGTERM
  (exit 0); доменний `handle(task)` додають WP-02/03/04/01B, до того працює `NoopHandler`;
- `scheduler` — реальна (WP-01D PR1): singleton через PostgreSQL advisory lease
  (другий процес чекає), maintenance tick `recover_expired_leases` + `mark_stale_instances`;
- обидві команди повертаються до placeholder-процесу WP-00 (`not implemented: owned by
  WP-01D` у stderr + живий процес) за `COLLECTOR_WORKER_PLACEHOLDER=1` — це rollback-прапорець
  картки WP-01D, а не режим за замовчуванням;
- решта (`e2e` — WP-14, `release build|verify` — WP-11A, `controller` — WP-01D) — стаби.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer

from collector.core.config import env_or_file, mongo_address
from collector.core.logging import configure_logging, get_logger
from collector.core.version import version_info
from collector.workers.config import placeholder_requested
from collector.workers.roles import WorkerRole

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from pymongo import MongoClient

    from collector.persistence.mongo.users import MongoUserCredential
    from collector.persistence.postgres.config import PostgresSettings
    from collector.workers.config import SchedulerRuntimeConfig, WorkerRuntimeConfig

# pymongo/fastapi/uvicorn/asyncio/sqlalchemy імпортуються лише в тілах команд, які їх потребують
# (gate 3, CR-12): `collector version`/`--help` — це image HEALTHCHECK і CI-контракт, вони мають
# бути дешевими. Те саме стосується `collector worker <role>`/`scheduler`: 11 контейнерів
# стартують одночасно, і зайвий важкий імпорт у кожному з'їдає CPU рівно у вікні `start_period`
# healthcheck-ів (CI PR #3: `collector db …` тягнув sqlalchemy+asyncio у ЦЕЙ модуль → fetch-worker
# unhealthy). Тест-вартовий: tests/unit/persistence/postgres/test_cli_db.py::
# test_importing_cli_does_not_pull_heavy_database_stack.

NOT_IMPLEMENTED_EXIT_CODE = 2
# Placeholder-процеси (scheduler/worker): період heartbeat-логу, с.
PLACEHOLDER_HEARTBEAT_SECONDS = 30.0
# ensure-mongo: скільки чекати, поки ініційований член стане primary, с.
MONGO_PRIMARY_WAIT_SECONDS = 60.0
MONGO_NOT_YET_INITIALIZED = 94  # код помилки replSetGetStatus до replSetInitiate
MONGO_ALREADY_INITIALIZED = 23  # replSetInitiate програв гонку паралельному ensure-mongo
# Мережеві таймаути клієнта ensure-mongo, мс: завислий replSetInitiate не тримає one-shot вічно.
MONGO_CLIENT_TIMEOUT_MS = 30_000

app = typer.Typer(
    name="collector",
    help="UA Web Data Collector — CLI для workers, API, міграцій, e2e і releases (§16.2).",
    add_completion=False,
    # Plain-text help (без rich): детермінований вивід у CI/Docker логах і тестах.
    rich_markup_mode=None,
)
db_app = typer.Typer(
    help="Схеми сховищ: PostgreSQL migrations (WP-01A), Mongo validators (WP-01B)."
)
release_app = typer.Typer(help="Immutable dataset releases (§9.9); owner — WP-11A.")
contracts_app = typer.Typer(
    help="Shared data contracts: JSON Schema snapshots (§9.4); owner — WP-01C."
)
app.add_typer(db_app, name="db")
app.add_typer(release_app, name="release")
# `contracts` — foundation-розширення поза §16.2 (approved dependency change,
# docs/plan/deps/WP-01C-to-WP-00.md).
app.add_typer(contracts_app, name="contracts")


def _help_when_no_subcommand(ctx: typer.Context) -> None:
    """Група без підкоманди друкує help і завершується кодом 0.

    Click `no_args_is_help` дає код 2 — той самий, що й стаби «not implemented»;
    тут виклик без підкоманди — не помилка і не стаб.
    """
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


for _group in (app, db_app, release_app, contracts_app):
    _group.callback(invoke_without_command=True)(_help_when_no_subcommand)


def not_implemented(owner: str) -> NoReturn:
    """Єдиний вихід для стабів: повідомлення у stderr і exit code 2."""
    typer.echo(f"not implemented: owned by {owner}", err=True)
    raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)


def placeholder_process(
    name: str,
    owner: str,
    *,
    stop: threading.Event | None = None,
    heartbeat_seconds: float = PLACEHOLDER_HEARTBEAT_SECONDS,
) -> None:
    """Довгоживучий placeholder для Compose: живий процес без доменної логіки.

    Друкує стаб-рядок у stderr (як інші стаби), далі логує heartbeat, доки не отримає
    SIGTERM/SIGINT (`stop_grace_period` у Compose) або поки не встановлено `stop`
    (тести). Завершується кодом 0 — це штатна зупинка, а не помилка.
    """
    typer.echo(f"not implemented: owned by {owner}", err=True)
    configure_logging(os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"))
    log = get_logger(f"collector.{name}")
    stop_event = threading.Event() if stop is None else stop

    def _request_stop(signum: int, _frame: object) -> None:
        log.info("placeholder.stop_requested", signal=signal.Signals(signum).name)
        stop_event.set()

    previous_handlers: dict[signal.Signals, object] = {}
    if threading.current_thread() is threading.main_thread():
        # PID 1 у контейнері ігнорує SIGTERM без явного handler — ставимо його самі;
        # попередні handlers відновлюються у finally (gate 3, CR-9: pytest/embedding).
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _request_stop)

    log.warning(
        "placeholder.started",
        component=name,
        owner=owner,
        detail="lease/queue logic not implemented yet; process stays alive for Compose",
    )
    try:
        while not stop_event.wait(heartbeat_seconds):
            log.info("placeholder.heartbeat", component=name)
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)  # type: ignore[arg-type]  # getsignal → Handlers | None
    log.info("placeholder.stopped", component=name)


def ensure_mongo_replica_set(
    client: MongoClient[dict[str, object]],
    *,
    replica_set: str,
    member_host: str,
    wait_seconds: float = MONGO_PRIMARY_WAIT_SECONDS,
) -> bool:
    """Ініціалізувати single-member replica set, якщо ще не ініціалізовано (ідемпотентно).

    Повертає True, якщо `replSetInitiate` виконано зараз, False — якщо RS уже існував
    (зокрема коли паралельний `ensure-mongo` встиг першим — код 23 `AlreadyInitialized`).
    В обох випадках чекає, поки член стане writable primary (`hello`); transient помилки
    драйвера під час election (`AutoReconnect`, `NotPrimaryError`) — повтор до deadline.
    """
    from pymongo.errors import AutoReconnect, NotPrimaryError, OperationFailure

    try:
        status = client.admin.command("replSetGetStatus")
    except OperationFailure as exc:
        if exc.code != MONGO_NOT_YET_INITIALIZED:
            raise
        try:
            client.admin.command(
                "replSetInitiate",
                {"_id": replica_set, "members": [{"_id": 0, "host": member_host}]},
            )
        except OperationFailure as race:
            if race.code != MONGO_ALREADY_INITIALIZED:
                raise
            initiated = False
        else:
            initiated = True
    else:
        if status.get("set") != replica_set:
            raise ValueError(
                f"replica set already initialised as {status.get('set')!r}, "
                f"expected {replica_set!r}; topology changes need a runbook/ADR (§7.5)"
            )
        initiated = False

    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            hello = client.admin.command("hello")
        except (AutoReconnect, NotPrimaryError):
            hello = {}
        if hello.get("isWritablePrimary"):
            return initiated
        if time.monotonic() >= deadline:
            raise TimeoutError(f"member {member_host} did not become primary in {wait_seconds}s")
        time.sleep(0.5)


@app.command()
def version() -> None:
    """Друкує версію пакета, Git SHA (env COLLECTOR_GIT_SHA) і версію схеми контрактів."""
    typer.echo(version_info().render())


@db_app.command("ensure-mongo")
def db_ensure_mongo(
    validators: Annotated[
        bool,
        typer.Option(
            "--validators", help="Застосувати Mongo-міграції: collections і $jsonSchema validators."
        ),
    ] = False,
    indexes: Annotated[
        bool,
        typer.Option(
            "--indexes", help="Створити відсутні indexes маніфесту §9.2, звітувати зайві."
        ),
    ] = False,
    users: Annotated[
        bool,
        typer.Option(
            "--users",
            help=(
                "Створити custom roles і користувачів компонентів §13; пароль кожного — з "
                "URI-секрету mongo_uri_<component> у --secrets-dir."
            ),
        ),
    ] = False,
    secrets_dir: Annotated[
        Path | None,
        typer.Option(
            "--secrets-dir",
            help=(
                "Каталог URI-секретів (типово $COLLECTOR_MONGO_USER_SECRETS_DIR або /run/secrets)."
            ),
        ),
    ] = None,
) -> None:
    """Ініціалізує MongoDB replica set; далі — validators, indexes і користувачі (WP-01B).

    Env: `COLLECTOR_MONGO_HOST`/`COLLECTOR_MONGO_PORT`, `COLLECTOR_MONGO_REPLICA_SET`
    (типово `rs0`), `COLLECTOR_MONGO_ROOT_USERNAME`, `COLLECTOR_MONGO_ROOT_PASSWORD[_FILE]`
    (Docker secret), `COLLECTOR_MONGO_DATABASE` (domain-БД, типово `collector`). Member host у
    конфігурації RS = `COLLECTOR_MONGO_HOST:PORT`. `--users` читає секрети **до** з'єднання:
    відсутній або чужий URI-секрет дає exit 1 без жодних змін. Drift міграцій, невалідні
    документи при `warn → error` і конфлікт indexes — exit 1 (повідомлення без секретів).
    """
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    from collector.persistence.mongo.users import (
        MongoUserError,
        load_user_credentials,
        user_secrets_dir,
    )

    configure_logging(os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"))
    log = get_logger("collector.db.ensure_mongo")
    try:
        credentials = (
            load_user_credentials(secrets_dir or user_secrets_dir(os.environ)) if users else None
        )
    except MongoUserError as exc:
        typer.echo(f"mongo users: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    host, port = mongo_address()
    replica_set = os.environ.get("COLLECTOR_MONGO_REPLICA_SET", "rs0")
    username = env_or_file("COLLECTOR_MONGO_ROOT_USERNAME")
    password = env_or_file("COLLECTOR_MONGO_ROOT_PASSWORD")
    client: MongoClient[dict[str, object]] = MongoClient(
        host=host,
        port=port,
        username=username,
        password=password,
        authSource="admin",
        directConnection=True,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=MONGO_CLIENT_TIMEOUT_MS,
        socketTimeoutMS=MONGO_CLIENT_TIMEOUT_MS,
        uuidRepresentation="standard",
        tz_aware=True,
    )
    try:
        try:
            initiated = ensure_mongo_replica_set(
                client, replica_set=replica_set, member_host=f"{host}:{port}"
            )
        except (PyMongoError, ValueError, TimeoutError) as exc:
            log.error("ensure_mongo.failed", error=f"{type(exc).__name__}: {exc}"[:300])
            raise typer.Exit(code=1) from exc
        log.info(
            "ensure_mongo.replica_set_ready",
            replica_set=replica_set,
            member=f"{host}:{port}",
            initiated_now=initiated,
        )
        if validators or indexes or users:
            _ensure_mongo_schema(
                client, validators=validators, indexes=indexes, credentials=credentials
            )
    finally:
        client.close()


def _ensure_mongo_schema(
    client: MongoClient[dict[str, object]],
    *,
    validators: bool,
    indexes: bool,
    credentials: list[MongoUserCredential] | None,
) -> None:
    """Validators/indexes/users після RS; будь-яка помилка → exit 1 без секретів у stderr."""
    from pymongo.errors import PyMongoError

    from collector.persistence.mongo.admin import IndexConflictError, apply_mongo_schema
    from collector.persistence.mongo.client import DEFAULT_DATABASE, MONGO_DATABASE_ENV
    from collector.persistence.mongo.migrations import (
        InvalidDocumentsError,
        MigrationDriftError,
        MigrationsNotFoundError,
    )
    from collector.persistence.mongo.users import MongoUserError

    database = os.environ.get(MONGO_DATABASE_ENV, "").strip() or DEFAULT_DATABASE
    try:
        result = apply_mongo_schema(
            client,
            database,
            validators=validators,
            indexes=indexes,
            credentials=credentials,
        )
    except (
        MigrationDriftError,
        InvalidDocumentsError,
        MigrationsNotFoundError,
        IndexConflictError,
        MongoUserError,
    ) as exc:
        typer.echo(f"ensure-mongo: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except PyMongoError as exc:
        typer.echo(f"ensure-mongo: {type(exc).__name__}: {exc}"[:300], err=True)
        raise typer.Exit(code=1) from exc
    if validators:
        applied = ", ".join(result.migrations_applied) or "none (up to date)"
        typer.echo(f"mongo migrations applied to {database}: {applied}")
    if result.indexes is not None:
        typer.echo(
            f"mongo indexes: created={len(result.indexes.created)} "
            f"present={len(result.indexes.present)}"
        )
        for name in result.indexes.created:
            typer.echo(f"index created: {name}")
        for name in result.indexes.extra:
            typer.echo(f"index not in manifest (review $indexStats): {name}", err=True)
    if result.users:
        typer.echo(f"mongo users applied: {', '.join(result.users)}")


@db_app.command("migrate")
def db_migrate(
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help=(
                "Не змінювати схему; exit 1, якщо вона відрізняється від моделей. "
                "Потребує тих самих прав, що й міграції (не read-only роль)."
            ),
        ),
    ] = False,
    partitions_ahead: Annotated[
        int,
        typer.Option(
            "--partitions-ahead", min=0, help="Скільки місяців партицій створити наперед."
        ),
    ] = 3,
) -> None:
    """Застосовує PostgreSQL migrations (`alembic upgrade head`) і створює місячні партиції.

    DSN — env `COLLECTOR_POSTGRES_DSN` або файл `COLLECTOR_POSTGRES_DSN_FILE`. Після міграцій
    виконайте `collector db roles`, щоб оновити GRANT для нових таблиць.
    """
    from collector.persistence.postgres.ops import migrate_database

    settings = _postgres_settings()
    result = _run_async(
        migrate_database(settings, check_only=check, partitions_months_ahead=partitions_ahead)
    )
    if check:
        for problem in result.drift:
            typer.echo(problem, err=True)
        if result.drift:
            typer.echo("schema drift: схема відрізняється від моделей", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"schema up to date: revision={result.revision_after}")
        return
    typer.echo(
        f"migrated {settings.redacted_dsn}: "
        f"{result.revision_before or 'empty'} -> {result.revision_after}"
    )
    for partition in result.partitions_created:
        typer.echo(f"partition created: {partition}")
    if result.drift:
        for problem in result.drift:
            typer.echo(problem, err=True)
        typer.echo("schema drift після upgrade: перевірте моделі/міграції", err=True)
        raise typer.Exit(code=1)


@db_app.command("roles")
def db_roles(
    sql: Annotated[
        Path | None,
        typer.Option(
            "--sql", help="Альтернативний SQL-файл ролей (типово — вбудований roles.sql)."
        ),
    ] = None,
    with_login: Annotated[
        bool,
        typer.Option(
            "--with-login",
            help=(
                "Увімкнути LOGIN runtime-ролей (§13); пароль кожної — з DSN-секрету "
                "postgres_dsn_<component> у --secrets-dir."
            ),
        ),
    ] = False,
    secrets_dir: Annotated[
        Path | None,
        typer.Option(
            "--secrets-dir",
            help=(
                "Каталог DSN-секретів ролей (типово $COLLECTOR_POSTGRES_ROLE_SECRETS_DIR "
                "або /run/secrets)."
            ),
        ),
    ] = None,
) -> None:
    """Створює ролі БД §13 і застосовує GRANT (ідемпотентно; після `db migrate`).

    `--with-login` додатково робить runtime-ролі LOGIN-ролями: секрети читаються **до**
    з'єднання з БД, тож відсутній або чужий DSN-секрет дає exit 1 без жодних змін.
    """
    from collector.persistence.postgres.ops import apply_database_roles
    from collector.persistence.postgres.roles import (
        ROLE_NAMES,
        RoleLoginError,
        default_roles_sql_path,
        load_role_logins,
        role_secrets_dir,
    )

    settings = _postgres_settings()
    path = sql or default_roles_sql_path()
    if not path.is_file():
        typer.echo(f"SQL-файл ролей не знайдено: {path}", err=True)
        raise typer.Exit(code=1)
    try:
        logins = (
            load_role_logins(secrets_dir or role_secrets_dir(os.environ)) if with_login else None
        )
        enabled = _run_async(apply_database_roles(settings, sql_path=path, logins=logins))
    except RoleLoginError as exc:
        # Повідомлення RoleLoginError не містять DSN/паролів — лише роль і причину.
        typer.echo(f"role logins: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"roles applied to {settings.redacted_dsn} from {path.name}: {', '.join(ROLE_NAMES)}"
    )
    if enabled:
        typer.echo(f"login enabled: {', '.join(enabled)}")


def _postgres_settings() -> PostgresSettings:
    from collector.persistence.postgres.config import PostgresConfigError, PostgresSettings

    try:
        return PostgresSettings.from_env()
    except PostgresConfigError as exc:
        typer.echo(f"postgres config: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _worker_config(role: WorkerRole) -> WorkerRuntimeConfig:
    from collector.workers.config import WorkerConfigError, WorkerRuntimeConfig

    try:
        return WorkerRuntimeConfig.from_env(role)
    except WorkerConfigError as exc:
        typer.echo(f"worker config: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _scheduler_config() -> SchedulerRuntimeConfig:
    from collector.workers.config import SchedulerRuntimeConfig, WorkerConfigError

    try:
        return SchedulerRuntimeConfig.from_env()
    except WorkerConfigError as exc:
        typer.echo(f"scheduler config: {exc}", err=True)
        raise typer.Exit(code=1) from exc


async def _run_worker(settings: PostgresSettings, config: WorkerRuntimeConfig) -> None:
    """Engine на процес + `WorkerRuntime.run()`; повертається після drain (exit code 0).

    Pool рахується **точно** від стелі слотів процесу (`COLLECTOR_WORKER_MAX_CONCURRENCY`,
    типово default ролі §7.6) плюс резерв на heartbeat/claim/зміну статусу, і без
    `max_overflow`: одночасних checkout-ів не може бути більше, ніж `max_slots + резерв`, тож
    черга за з'єднанням (і `pool_timeout`, який runtime побачив би як втрату lease — M-3
    код-рев'ю) не виникає за побудовою. Гарячий `desired_concurrency` понад стелю runtime
    обрізає з попередженням, а не мовчки впирається в pool.

    `command_timeout` дорівнює вікну self-fencing: зависання драйвера стає помилкою раніше,
    ніж instance встигне втратити lease (H-1).
    """
    from collector.persistence.postgres.engine import create_engine, create_session_factory
    from collector.workers.config import CONNECTION_RESERVE
    from collector.workers.runtime import WorkerRuntime

    engine = create_engine(
        settings,
        pool_size=config.max_slots + CONNECTION_RESERVE,
        max_overflow=0,
        application_name=f"collector-worker-{config.role.value}",
        command_timeout=config.command_timeout,
    )
    try:
        await WorkerRuntime(config, create_session_factory(engine)).run()
    finally:
        await engine.dispose()


async def _run_scheduler(settings: PostgresSettings, config: SchedulerRuntimeConfig) -> None:
    """Engine + `SchedulerRuntime.run()`; advisory lease тримає окреме з'єднання поза pool-ом."""
    from collector.persistence.postgres.engine import create_engine, create_session_factory
    from collector.workers.scheduler import SchedulerRuntime

    engine = create_engine(
        settings,
        # lease тримає окреме з'єднання поза pool-ом, тіку вистачає одного.
        pool_size=2,
        max_overflow=2,
        application_name="collector-sch",
        command_timeout=config.tick_seconds + config.lease_retry_seconds,
    )
    try:
        await SchedulerRuntime(config, engine, create_session_factory(engine)).run()
    finally:
        await engine.dispose()


def _run_runtime(coro: Coroutine[Any, Any, None]) -> None:
    """`_run_async` для worker/scheduler: чужа LOGIN-роль БД → exit 1 зі зрозумілим stderr (§13).

    `RoleLoginError` піднімає `collector.workers.login` при старті, ще до першого claim; її
    повідомлення містять лише імена ролей, без DSN і пароля. Rollback без перебудови image —
    `COLLECTOR_WORKER_PLACEHOLDER=1` (перевірка до БД не доходить).
    """
    from collector.persistence.postgres.roles import RoleLoginError

    try:
        _run_async(coro)
    except RoleLoginError as exc:
        typer.echo(f"role login: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _is_postgres_error(exc: BaseException) -> bool:
    """Помилка з'єднання/запиту PostgreSQL.

    Крім `sqlalchemy.exc.*` і `OSError`, сюди входять помилки самого asyncpg: драйвер кидає їх
    напряму під час connect/auth (`InvalidPasswordError`) і при виконанні скрипта ролей через
    simple query protocol, а SQLAlchemy їх не обгортає. Клас визначаємо за модулем — у asyncpg
    немає `py.typed`, тож імпортувати його в типізований код не можна (L-3 код-рев'ю).

    `sqlalchemy.exc` імпортується тут, а не в модулі: виклик відбувається лише після того, як
    команда `db …` уже підтягнула SQLAlchemy (див. коментар про lazy-імпорти вище).
    """
    from sqlalchemy.exc import DBAPIError, SQLAlchemyError

    return (
        isinstance(exc, OSError | DBAPIError | SQLAlchemyError)
        or type(exc).__module__.split(".")[0] == "asyncpg"
    )


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """`asyncio.run` з перекладом помилок БД у exit code 1 без traceback у stderr."""
    import asyncio

    try:
        return asyncio.run(coro)
    except Exception as exc:
        if not _is_postgres_error(exc):
            raise
        typer.echo(f"postgres error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def e2e(
    source: Annotated[str, typer.Option("--source", help="source_id або `fixtures`.")],
    offline: Annotated[
        bool, typer.Option("--offline", help="Лише offline fixtures, без мережі.")
    ] = False,
) -> None:
    """Наскрізний прогін збору для одного джерела (стаб; owner WP-14)."""
    not_implemented("WP-14")


@release_app.command("build")
def release_build(
    watermark: Annotated[str, typer.Option("--watermark", help="Watermark release (§9.9).")],
    output: Annotated[Path, typer.Option("--output", help="Каталог для parts і manifest.")],
) -> None:
    """Збирає immutable dataset release (стаб; owner WP-11A)."""
    not_implemented("WP-11A")


@release_app.command("verify")
def release_verify(
    manifest: Annotated[Path, typer.Option("--manifest", help="Шлях до manifest.json.")],
) -> None:
    """Перевіряє manifest і checksums release (стаб; owner WP-11A)."""
    not_implemented("WP-11A")


@contracts_app.command("export")
def contracts_export(
    output: Annotated[
        Path, typer.Option("--output", help="Каталог snapshot-ів (`schemas/`).")
    ] = Path("schemas"),
    check: Annotated[
        bool,
        typer.Option("--check", help="Не писати; exit 1, якщо snapshot-и відрізняються."),
    ] = False,
) -> None:
    """Генерує JSON Schema snapshots shared-контрактів у `schemas/<group>/<name>.v<major>.json`."""
    from collector.contracts.schema_export import check_schemas, export_schemas

    if check:
        problems = check_schemas(output)
        for problem in problems:
            typer.echo(problem, err=True)
        if problems:
            typer.echo(
                f"schema drift: {len(problems)} проблем(и); виконайте `collector contracts export`",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"schemas up to date: {output}")
        return
    written = export_schemas(output)
    for path in written:
        typer.echo(path.as_posix())
    typer.echo(f"exported {len(written)} schemas to {output}")


@app.command()
def worker(
    role: Annotated[WorkerRole, typer.Argument(help="Роль worker pool за §7.6.")],
) -> None:
    """Запускає stateless worker ролі: claim із черги, lease heartbeat, graceful drain (§7.6).

    Env: `COLLECTOR_POSTGRES_DSN[_FILE]`, `COLLECTOR_WORKER_*` (див.
    `collector.workers.config`). `desired_concurrency` береться з `worker_pools` і
    змінюється без рестарту. SIGTERM → drain у межах `COLLECTOR_WORKER_STOP_GRACE_SECONDS`
    і exit 0. `COLLECTOR_WORKER_PLACEHOLDER=1` повертає placeholder-процес WP-00 (rollback).
    """
    if placeholder_requested():
        placeholder_process(f"worker.{role.value}", "WP-01D")
        return
    configure_logging(os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"))
    settings = _postgres_settings()
    config = _worker_config(role)
    _run_runtime(_run_worker(settings, config))


@app.command()
def api() -> None:
    """Запускає operator/read API: у WP-00 лише стаб health (owner WP-11A).

    Env: `COLLECTOR_API_HOST` (типово `0.0.0.0` — контейнер без published port),
    `COLLECTOR_API_PORT` (типово `8000`).
    """
    import uvicorn

    configure_logging(os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"))
    host = os.environ.get("COLLECTOR_API_HOST", "0.0.0.0")  # noqa: S104 — bind у контейнері
    port = int(os.environ.get("COLLECTOR_API_PORT", "8000"))
    uvicorn.run(
        "collector.api.health:create_app",
        factory=True,
        host=host,
        port=port,
        # Логи uvicorn ідуть через structlog ProcessorFormatter (collector.core.logging).
        log_config=None,
        access_log=False,
        # Без заголовка `server: uvicorn` (SEC L-4).
        server_header=False,
    )


@app.command()
def scheduler() -> None:
    """Запускає singleton scheduler: PostgreSQL advisory lease + maintenance tick (§7.5).

    Другий процес не стає активним, а чекає на звільнення lease; при втраті lease активний
    процес припиняє планування. Env: `COLLECTOR_POSTGRES_DSN[_FILE]`,
    `COLLECTOR_SCHEDULER_*`; SIGTERM → exit 0; `COLLECTOR_WORKER_PLACEHOLDER=1` — rollback
    до placeholder-процесу WP-00.
    """
    if placeholder_requested():
        placeholder_process("scheduler", "WP-01D")
        return
    configure_logging(os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"))
    settings = _postgres_settings()
    config = _scheduler_config()
    _run_runtime(_run_scheduler(settings, config))


@app.command()
def controller() -> None:
    """Запускає desired-state controller worker pools (стаб; owner WP-01D)."""
    not_implemented("WP-01D")


def main() -> None:
    """Точка входу для `python -m collector.cli`."""
    app()


if __name__ == "__main__":
    main()
