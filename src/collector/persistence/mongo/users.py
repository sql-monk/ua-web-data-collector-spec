"""Mongo-користувачі за компонентами §13 (CLI `db ensure-mongo --users`).

Custom roles (у БД `admin`, privileges — лише на collections domain-БД):

- `collector_projector` — `find/insert/update` на 11 domain collections і receipts +
  `listCollections` (перевірка validators у `check_ready`, PR3); **без** `remove`,
  `dropCollection`, `createIndex`, `collMod`;
- `collector_compactor` — те саме + `remove` лише на `entity_projection_versions` (§9.7, PR4);
- `collector_api_ro`, `collector_export_ro` — лише `find`.

Користувач `collector_<component>` має рівно одну однойменну роль. Пароль береться з Docker
secret `mongo_uri_<component>` (URI компонента, генерує WP-00 PR5): той самий файл монтується
сервісу як `COLLECTOR_MONGO_URI_FILE` і читається тут, тож секрет один. Root лише в
`ensure-mongo`; scheduler/fetcher/parser Mongo-користувачів не мають (§13). Повідомлення
помилок не містять URI/паролів — лише компонент і причину/код.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymongo import MongoClient
from pymongo.errors import ConfigurationError, InvalidURI, OperationFailure
from pymongo.uri_parser import parse_uri

from collector.persistence.mongo.schema import DOMAIN_COLLECTIONS, ENTITY_PROJECTION_VERSIONS

USER_COMPONENTS: tuple[str, ...] = ("projector", "compactor", "api_ro", "export_ro")
USER_SECRETS_DIR_ENV = "COLLECTOR_MONGO_USER_SECRETS_DIR"
DEFAULT_USER_SECRETS_DIR = Path("/run/secrets")
URI_SECRET_PREFIX = "mongo_uri_"  # noqa: S105 — префікс імені Docker secret, не пароль
AUTH_DATABASE = "admin"
_WRITE_ACTIONS: tuple[str, ...] = ("find", "insert", "update")
_READ_ACTIONS: tuple[str, ...] = ("find",)


class MongoUserError(ValueError):
    """Секрет компонента відсутній/невалідний або створення користувача не вдалося."""


def user_name(component: str) -> str:
    if component not in USER_COMPONENTS:
        msg = f"{component!r} не має Mongo-користувача (§13: лише {', '.join(USER_COMPONENTS)})"
        raise MongoUserError(msg)
    return f"collector_{component}"


@dataclass(frozen=True, slots=True)
class MongoUserCredential:
    """Компонент і пароль із його URI-секрету; `repr` пароль не показує."""

    component: str
    password: str

    @property
    def user(self) -> str:
        return user_name(self.component)

    def __repr__(self) -> str:
        return f"MongoUserCredential(component={self.component!r}, password=***)"


def credential_from_uri(component: str, uri: str) -> MongoUserCredential:
    """Розбирає URI компонента і перевіряє користувача та `authSource`."""
    expected = user_name(component)
    secret = URI_SECRET_PREFIX + component
    try:
        parsed = parse_uri(uri.strip(), validate=True, warn=False)
    except (InvalidURI, ConfigurationError, ValueError) as exc:
        msg = f"{component}: невалідний URI у секреті {secret}"
        raise MongoUserError(msg) from exc
    if parsed.get("username") != expected:
        msg = f"{component}: секрет {secret} має іншого користувача, очікувався {expected!r}"
        raise MongoUserError(msg)
    password = parsed.get("password")
    if not isinstance(password, str) or not password:
        msg = f"{component}: у секреті {secret} немає пароля"
        raise MongoUserError(msg)
    options: Mapping[str, Any] = parsed.get("options") or {}
    lowered = {str(key).lower(): value for key, value in options.items()}
    auth_source = lowered.get("authsource", AUTH_DATABASE)
    if auth_source != AUTH_DATABASE:
        msg = f"{component}: секрет {secret} має authSource={auth_source!r}, очікувався admin"
        raise MongoUserError(msg)
    return MongoUserCredential(component=component, password=password)


def load_user_credentials(
    secrets_dir: Path, components: Sequence[str] = USER_COMPONENTS
) -> list[MongoUserCredential]:
    """Читає `secrets_dir/mongo_uri_<component>` для кожного компонента; усі або жодного."""
    missing = [
        URI_SECRET_PREFIX + c
        for c in components
        if not (secrets_dir / (URI_SECRET_PREFIX + c)).is_file()
    ]
    if missing:
        msg = f"у {secrets_dir} бракує Mongo URI-секретів: {', '.join(missing)}"
        raise MongoUserError(msg)
    return [
        credential_from_uri(c, (secrets_dir / (URI_SECRET_PREFIX + c)).read_text(encoding="utf-8"))
        for c in components
    ]


def user_secrets_dir(environ: Mapping[str, str]) -> Path:
    """`COLLECTOR_MONGO_USER_SECRETS_DIR` або `/run/secrets` (Docker secrets)."""
    value = environ.get(USER_SECRETS_DIR_ENV, "").strip()
    return Path(value) if value else DEFAULT_USER_SECRETS_DIR


def role_privileges(database: str) -> dict[str, list[dict[str, Any]]]:
    """Privileges custom-ролей для domain-БД `database`."""

    def grant(collection: str, actions: Sequence[str]) -> dict[str, Any]:
        return {"resource": {"db": database, "collection": collection}, "actions": list(actions)}

    list_collections = grant("", ("listCollections",))
    compactor_actions = (*_WRITE_ACTIONS, "remove")
    return {
        "collector_projector": [
            *(grant(c, _WRITE_ACTIONS) for c in DOMAIN_COLLECTIONS),
            list_collections,
        ],
        "collector_compactor": [
            *(
                grant(c, compactor_actions if c == ENTITY_PROJECTION_VERSIONS else _WRITE_ACTIONS)
                for c in DOMAIN_COLLECTIONS
            ),
            list_collections,
        ],
        "collector_api_ro": [grant(c, _READ_ACTIONS) for c in DOMAIN_COLLECTIONS],
        "collector_export_ro": [grant(c, _READ_ACTIONS) for c in DOMAIN_COLLECTIONS],
    }


def apply_users(
    client: MongoClient[dict[str, Any]],
    database: str,
    credentials: Sequence[MongoUserCredential],
) -> list[str]:
    """Створює/оновлює custom roles і користувачів (ідемпотентно). Повертає імена користувачів.

    Роль отримує рівно privileges з `role_privileges` (`updateRole` замінює попередні — ручне
    розширення прав оператором не переживе наступний `ensure-mongo --users`). Помилка команди →
    `MongoUserError` лише з іменем і кодом: текст `OperationFailure` для createUser/updateUser
    може містити деталі команди.
    """
    admin = client[AUTH_DATABASE]
    privileges = role_privileges(database)
    for role, grants in privileges.items():
        exists = bool(admin.command("rolesInfo", role)["roles"])
        try:
            if exists:
                admin.command("updateRole", role, privileges=grants, roles=[])
            else:
                admin.command("createRole", role, privileges=grants, roles=[])
        except OperationFailure as exc:
            msg = f"{role}: createRole/updateRole не вдалося (code {exc.code})"
            raise MongoUserError(msg) from None
    done: list[str] = []
    for credential in credentials:
        user = credential.user
        roles = [{"role": user, "db": AUTH_DATABASE}]
        exists = bool(admin.command("usersInfo", user)["users"])
        try:
            if exists:
                admin.command("updateUser", user, pwd=credential.password, roles=roles)
            else:
                admin.command("createUser", user, pwd=credential.password, roles=roles)
        except OperationFailure as exc:
            msg = f"{user}: createUser/updateUser не вдалося (code {exc.code})"
            raise MongoUserError(msg) from None
        done.append(user)
    return done
