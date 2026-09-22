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
- `db roles [--sql PATH]` — реальна (WP-01A): ролі БД §13 і GRANT, ідемпотентно;
- `db ensure-mongo` — реально ініціалізує single-member replica set (ідемпотентно; WP-00 PR2);
  `--validators`/`--indexes` лишаються стабом WP-01B (після ініціалізації RS → код 2);
- `api` — запускає uvicorn зі стабом `GET /api/v1/health/components`
  (`collector.api.health`; owner WP-11A);
- `scheduler` і `worker <role>` — placeholder-процеси: тримають контейнер живим, логують,
  що lease/queue-логіка не реалізована (owner WP-01D), коректно зупиняються по SIGTERM
  (код 0). У stderr при старті друкується той самий рядок `not implemented: owned by
  WP-01D`, щоб скрипти могли відрізнити placeholder від реалізації;
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
from collector.workers.roles import WorkerRole

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from pymongo import MongoClient

    from collector.persistence.postgres.config import PostgresSettings

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
        bool, typer.Option("--validators", help="Застосувати $jsonSchema validators.")
    ] = False,
    indexes: Annotated[
        bool, typer.Option("--indexes", help="Створити/перевірити indexes.")
    ] = False,
) -> None:
    """Ініціалізує MongoDB replica set (реально); validators та індекси — стаб WP-01B.

    Env: `COLLECTOR_MONGO_HOST`/`COLLECTOR_MONGO_PORT`, `COLLECTOR_MONGO_REPLICA_SET`
    (типово `rs0`), `COLLECTOR_MONGO_ROOT_USERNAME`, `COLLECTOR_MONGO_ROOT_PASSWORD[_FILE]`
    (Docker secret). Member host у конфігурації RS = `COLLECTOR_MONGO_HOST:PORT`.
    """
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    configure_logging(os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"))
    log = get_logger("collector.db.ensure_mongo")
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
    )
    try:
        initiated = ensure_mongo_replica_set(
            client, replica_set=replica_set, member_host=f"{host}:{port}"
        )
    except (PyMongoError, ValueError, TimeoutError) as exc:
        log.error("ensure_mongo.failed", error=f"{type(exc).__name__}: {exc}"[:300])
        raise typer.Exit(code=1) from exc
    finally:
        client.close()
    log.info(
        "ensure_mongo.replica_set_ready",
        replica_set=replica_set,
        member=f"{host}:{port}",
        initiated_now=initiated,
    )
    if validators or indexes:
        # $jsonSchema validators та індекси (§8, §9.2) — owner WP-01B.
        not_implemented("WP-01B")


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
) -> None:
    """Створює ролі БД §13 і застосовує GRANT (ідемпотентно; після `db migrate`)."""
    from collector.persistence.postgres.ops import apply_database_roles
    from collector.persistence.postgres.roles import ROLE_NAMES, default_roles_sql_path

    settings = _postgres_settings()
    path = sql or default_roles_sql_path()
    if not path.is_file():
        typer.echo(f"SQL-файл ролей не знайдено: {path}", err=True)
        raise typer.Exit(code=1)
    _run_async(apply_database_roles(settings, sql_path=path))
    typer.echo(
        f"roles applied to {settings.redacted_dsn} from {path.name}: {', '.join(ROLE_NAMES)}"
    )


def _postgres_settings() -> PostgresSettings:
    from collector.persistence.postgres.config import PostgresConfigError, PostgresSettings

    try:
        return PostgresSettings.from_env()
    except PostgresConfigError as exc:
        typer.echo(f"postgres config: {exc}", err=True)
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
    """Запускає worker відповідної ролі (placeholder-процес; owner WP-01D)."""
    placeholder_process(f"worker.{role.value}", "WP-01D")


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
    """Запускає singleton scheduler (placeholder-процес; advisory lease — owner WP-01D)."""
    placeholder_process("scheduler", "WP-01D")


@app.command()
def controller() -> None:
    """Запускає desired-state controller worker pools (стаб; owner WP-01D)."""
    not_implemented("WP-01D")


def main() -> None:
    """Точка входу для `python -m collector.cli`."""
    app()


if __name__ == "__main__":
    main()
