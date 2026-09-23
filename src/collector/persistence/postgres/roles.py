"""Ролі БД §13: `sql/roles.sql` (GRANT) і LOGIN per component (CLI `collector db roles`).

Дві частини, обидві ідемпотентні:

- `apply_roles` — SQL-скрипт (group-ролі, ownership, GRANT) через simple query protocol
  asyncpg (DO-блоки з крапками з комою не діляться на statements). Скрипт **не чіпає**
  LOGIN/паролі: повторний `db roles` без `--with-login` не вимикає вже видані логіни;
- `apply_logins` — `ALTER ROLE <runtime-роль> LOGIN PASSWORD '<SCRAM verifier>'` для кожної
  runtime-ролі (§13 «облікові дані БД розділені за компонентами»; dependency WP-01D F1).
  Пароль береться з DSN-секрету компонента (`postgres_dsn_<component>`), тобто секрет один:
  той самий файл монтується сервісу як `COLLECTOR_POSTGRES_DSN_FILE` і читається тут, щоб
  виставити пароль ролі. У БД іде лише SCRAM-SHA-256 verifier (RFC 5802/7677), тож
  відкритий пароль не потрапляє ні в `log_statement`, ні в `pg_stat_statements`.
  `collector_migrate` LOGIN не отримує ніколи: міграції виконує окремий login-користувач
  (у dev — superuser `POSTGRES_USER`), а runtime ним не користується.

Transaction boundary: викликач (`async with engine.begin()` — скрипт і логіни атомарно).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

ROLE_NAMES: tuple[str, ...] = (
    "collector_migrate",
    "collector_scheduler",
    "collector_fetcher",
    "collector_parser",
    "collector_projector",
    "collector_translation",
    "collector_api_ro",
    "collector_export_ro",
)
MIGRATE_ROLE = "collector_migrate"
RUNTIME_ROLES: tuple[str, ...] = tuple(r for r in ROLE_NAMES if r != MIGRATE_ROLE)
ROLE_SECRETS_DIR_ENV = "COLLECTOR_POSTGRES_ROLE_SECRETS_DIR"
DEFAULT_ROLE_SECRETS_DIR = Path("/run/secrets")
DSN_SECRET_PREFIX = "postgres_dsn_"  # noqa: S105 — префікс імені Docker secret, не пароль
SCRAM_ITERATIONS = 4096
"""Типове значення PostgreSQL `scram_iterations`; сервер приймає verifier з будь-яким >= 1."""


class RoleLoginError(ValueError):
    """DSN-секрет runtime-ролі відсутній, не відповідає ролі або роль має зайві права."""


def component_of(role: str) -> str:
    """`collector_fetcher` → `fetcher`: суфікс імені Docker secret `postgres_dsn_<component>`."""
    if role not in RUNTIME_ROLES:
        msg = f"{role!r} не є runtime-роллю §13"
        raise RoleLoginError(msg)
    return role.removeprefix("collector_")


def dsn_secret_name(role: str) -> str:
    """Ім'я Docker secret з DSN runtime-ролі, напр. `postgres_dsn_fetcher`."""
    return DSN_SECRET_PREFIX + component_of(role)


@dataclass(frozen=True, slots=True)
class RoleLogin:
    """Роль і пароль із її DSN-секрету; `repr` пароль не показує."""

    role: str
    password: str

    def __repr__(self) -> str:
        return f"RoleLogin(role={self.role!r}, password=***)"


def login_from_dsn(role: str, dsn: str) -> RoleLogin:
    """Розбирає DSN компонента і перевіряє, що він справді для `role` (а не для міграцій).

    Повідомлення помилок не містять DSN — лише назву ролі й причину: секрет не має потрапити
    у stderr CLI.
    """
    secret = dsn_secret_name(role)
    try:
        url = make_url(dsn.strip())
    except (ArgumentError, ValueError) as exc:
        msg = f"{role}: невалідний DSN у секреті {secret}"
        raise RoleLoginError(msg) from exc
    if url.username != role:
        msg = (
            f"{role}: секрет {secret} має користувача {url.username!r}, очікувався {role!r} "
            "(runtime не може ходити чужою або міграційною роллю, §13)"
        )
        raise RoleLoginError(msg)
    password = url.password
    if not isinstance(password, str) or not password:
        msg = f"{role}: у секреті {secret} немає пароля"
        raise RoleLoginError(msg)
    if not (password.isascii() and password.isprintable()):
        # Verifier рахується без SASLprep: для printable ASCII він тотожний серверному.
        msg = f"{role}: пароль у секреті {secret} має бути printable ASCII"
        raise RoleLoginError(msg)
    return RoleLogin(role=role, password=password)


def load_role_logins(secrets_dir: Path, roles: tuple[str, ...] = RUNTIME_ROLES) -> list[RoleLogin]:
    """Читає `secrets_dir/postgres_dsn_<component>` для кожної ролі; бракує хоч одного → помилка.

    Усі або жодного: частково увімкнені логіни лишили б частину сервісів на спільному
    міграційному DSN, і це було б видно лише в runtime.
    """
    missing = [
        dsn_secret_name(role)
        for role in roles
        if not (secrets_dir / dsn_secret_name(role)).is_file()
    ]
    if missing:
        msg = f"у {secrets_dir} бракує DSN-секретів: {', '.join(missing)}"
        raise RoleLoginError(msg)
    return [
        login_from_dsn(role, (secrets_dir / dsn_secret_name(role)).read_text(encoding="utf-8"))
        for role in roles
    ]


def role_secrets_dir(environ: Mapping[str, str]) -> Path:
    """`COLLECTOR_POSTGRES_ROLE_SECRETS_DIR` або `/run/secrets` (Docker secrets)."""
    value = environ.get(ROLE_SECRETS_DIR_ENV, "").strip()
    return Path(value) if value else DEFAULT_ROLE_SECRETS_DIR


def scram_sha256_verifier(
    password: str, *, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """SCRAM-SHA-256 verifier у форматі `pg_authid.rolpassword` (RFC 5802/7677)."""
    salt_bytes = secrets.token_bytes(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, iterations)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")

    def b64(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{b64(salt_bytes)}${b64(stored_key)}:{b64(server_key)}"


def default_roles_sql_path() -> Path:
    """`collector/persistence/postgres/sql/roles.sql` усередині пакета."""
    return Path(str(resources.files("collector.persistence.postgres").joinpath("sql/roles.sql")))


def load_roles_sql(path: Path | None = None) -> str:
    return (path or default_roles_sql_path()).read_text(encoding="utf-8")


async def apply_roles(conn: AsyncConnection, *, sql_path: Path | None = None) -> None:
    """Виконує скрипт ролей/GRANT; повторний виклик безпечний.

    Скрипт іде через сирий `asyncpg.Connection.execute` (simple query protocol — інакше DO-блоки
    з `;` довелося б ділити на statements). Помилки asyncpg при цьому не є `SQLAlchemyError`,
    тому транслюються у `DBAPIError` — щоб викликачі (CLI `_run_async`) ловили їх так само, як
    помилки будь-якого іншого запиту, а не показували traceback (L-3 код-рев'ю).
    """
    script = load_roles_sql(sql_path)
    raw = await conn.get_raw_connection()
    driver: Any = raw.driver_connection  # asyncpg.Connection без типізації
    try:
        await driver.execute(script)
    except Exception as exc:
        # asyncpg не має py.typed, тому клас помилки визначаємо за модулем, а не імпортом;
        # усе, що не з asyncpg, пробрасуємо як є.
        if type(exc).__module__.split(".")[0] != "asyncpg":
            raise
        raise DBAPIError(statement=None, params=None, orig=exc) from exc


PRIVILEGED_BUILTIN_ROLES: tuple[str, ...] = (
    "pg_write_all_data",
    "pg_read_server_files",
    "pg_execute_server_program",
    "pg_signal_backend",
)
"""Вбудовані ролі, членство в яких дає runtime більше, ніж GRANT-и `roles.sql` (gate 3, S-4)."""

_PRIVILEGED_MEMBERSHIPS = text(
    "SELECT r.rolname FROM pg_roles r "
    "WHERE r.rolname <> :role AND pg_has_role(:role, r.oid, 'MEMBER') "
    "AND (r.rolsuper OR r.rolcreaterole OR r.rolbypassrls OR r.rolname = ANY(:privileged)) "
    "ORDER BY r.rolname"
)


async def privileged_memberships(conn: AsyncConnection, role: str) -> list[str]:
    """Ролі (транзитивно), членом яких є `role` і які дають права понад runtime (S-4).

    Привілейована — роль з `rolsuper`/`rolcreaterole`/`rolbypassrls`, `collector_migrate` або
    одна з `PRIVILEGED_BUILTIN_ROLES`. Для superuser `pg_has_role` істинний для всіх ролей,
    тож superuser-логін завжди має непорожній результат.
    """
    rows = await conn.execute(
        _PRIVILEGED_MEMBERSHIPS,
        {"role": role, "privileged": [MIGRATE_ROLE, *PRIVILEGED_BUILTIN_ROLES]},
    )
    return [str(name) for name in rows.scalars()]


async def apply_logins(conn: AsyncConnection, logins: list[RoleLogin]) -> list[str]:
    """`ALTER ROLE … LOGIN PASSWORD '<verifier>'` для runtime-ролей. Ідемпотентно.

    Явні `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS` — щоб ручна помилка
    оператора в минулому не лишила runtime-логін із зайвими атрибутами. Роль має бути з
    allowlist `RUNTIME_ROLES` і **не** бути (транзитивно) членом привілейованих ролей
    (`privileged_memberships`, gate 3 S-4): мовчки `REVOKE` тут не робиться — операція падає,
    бо хтось свідомо видав runtime зайві права.

    Помилка самого `ALTER ROLE` → `RoleLoginError` лише з назвою ролі та SQLSTATE (S-5): текст
    `DBAPIError` містить SQL разом із SCRAM verifier і не має потрапити у stderr/CI-логи.

    Transaction boundary: викликач; зазвичай та сама транзакція, що й `apply_roles`
    (ролі мають уже існувати).
    """
    applied: list[str] = []
    for login in logins:
        component_of(login.role)
        extra = await privileged_memberships(conn, login.role)
        if extra:
            msg = (
                f"{login.role} є членом привілейованих ролей {', '.join(extra)}: "
                "runtime-роль не може мати прав понад GRANT-и roles.sql (§13)"
            )
            raise RoleLoginError(msg)
        verifier = scram_sha256_verifier(login.password)
        # ALTER ROLE не приймає bind-параметрів. Ім'я ролі — з фіксованого RUNTIME_ROLES
        # (перевірено вище), verifier складається лише з [A-Za-z0-9+/=:$-], лапок у ньому
        # бути не може. `exec_driver_sql`, а не `text()`: `:<base64>` у verifier `text()`
        # сприйняв би як bind-параметр.
        try:
            await conn.exec_driver_sql(
                f'ALTER ROLE "{login.role}" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                f"NOREPLICATION NOBYPASSRLS INHERIT PASSWORD '{verifier}'"
            )
        except DBAPIError as exc:
            sqlstate = _sqlstate(exc)
            msg = f"{login.role}: ALTER ROLE … LOGIN не вдалося (SQLSTATE {sqlstate})"
            raise RoleLoginError(msg) from None
        applied.append(login.role)
    return applied


def _sqlstate(exc: DBAPIError) -> str:
    """SQLSTATE з помилки драйвера без її тексту (текст може містити SQL із verifier)."""
    orig: Any = exc.orig
    for source in (orig, getattr(orig, "__cause__", None)):
        code = getattr(source, "sqlstate", None) or getattr(source, "pgcode", None)
        if isinstance(code, str) and code:
            return code
    return "unknown"


async def verify_runtime_login(conn: AsyncConnection) -> str:
    """Перевірка для runtime-процесів (WP-01D): поточний логін — runtime-роль без зайвих прав.

    Вимоги (gate 3, S-4): `current_user` з allowlist `RUNTIME_ROLES`; без `rolsuper`/
    `rolcreaterole`/`rolbypassrls`; без (транзитивного) членства в привілейованих ролях
    (`privileged_memberships`, включно з `collector_migrate`). Повертає `current_user`, інакше
    `RoleLoginError`. Призначена для одного виклику при старті процесу: спільний міграційний
    DSN (знахідка F1 WP-01D) має падати одразу, а не тихо працювати з правами власника схеми.
    """
    row = (
        await conn.execute(
            text(
                "SELECT current_user AS name, r.rolsuper AS superuser, "
                "r.rolcreaterole AS createrole, r.rolbypassrls AS bypassrls "
                "FROM pg_roles r WHERE r.rolname = current_user"
            )
        )
    ).one()
    name = str(row.name)
    hint = "використайте DSN компонента (postgres_dsn_<component>), §13"
    if row.superuser or row.createrole or row.bypassrls:
        msg = (
            f"runtime-підключення під {name!r} має атрибути superuser/createrole/bypassrls: {hint}"
        )
        raise RoleLoginError(msg)
    if name not in RUNTIME_ROLES:
        msg = f"runtime-підключення під {name!r}: це не runtime-роль {RUNTIME_ROLES}; {hint}"
        raise RoleLoginError(msg)
    extra = await privileged_memberships(conn, name)
    if extra:
        msg = f"runtime-підключення під {name!r} є членом {', '.join(extra)}: {hint}"
        raise RoleLoginError(msg)
    return name
