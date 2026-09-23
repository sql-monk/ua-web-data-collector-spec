"""LOGIN-ролі без БД: імена секретів, розбір DSN компонента, SCRAM verifier (§13)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from pathlib import Path

import pytest

from collector.persistence.postgres.roles import (
    DEFAULT_ROLE_SECRETS_DIR,
    ROLE_SECRETS_DIR_ENV,
    RUNTIME_ROLES,
    RoleLogin,
    RoleLoginError,
    component_of,
    dsn_secret_name,
    load_role_logins,
    login_from_dsn,
    role_secrets_dir,
    scram_sha256_verifier,
)

SCRAM = re.compile(r"^SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")


def test_secret_names_follow_the_agreed_postgres_dsn_component_format() -> None:
    assert [dsn_secret_name(role) for role in RUNTIME_ROLES] == [
        "postgres_dsn_scheduler",
        "postgres_dsn_fetcher",
        "postgres_dsn_parser",
        "postgres_dsn_projector",
        "postgres_dsn_translation",
        "postgres_dsn_api_ro",
        "postgres_dsn_export_ro",
    ]
    with pytest.raises(RoleLoginError):
        component_of("collector_migrate")


def test_login_from_dsn_accepts_only_the_role_itself_and_never_echoes_the_dsn() -> None:
    login = login_from_dsn("collector_fetcher", "postgresql://collector_fetcher:abc123@pg/db\n")
    assert (login.role, login.password) == ("collector_fetcher", "abc123")
    assert "abc123" not in repr(login)
    for dsn in (
        "postgresql://collector:abc123@pg/db",  # міграційний superuser із dev
        "postgresql://collector_migrate:abc123@pg/db",
        "postgresql://collector_parser:abc123@pg/db",  # чужа роль
        "postgresql://collector_fetcher@pg/db",  # без пароля
        "postgresql://collector_fetcher:%D0%BF%D0%B0%D1%80@pg/db",  # не ASCII
        "not a dsn",
    ):
        with pytest.raises(RoleLoginError) as info:
            login_from_dsn("collector_fetcher", dsn)
        assert "abc123" not in str(info.value)


def test_load_role_logins_is_all_or_nothing(tmp_path: Path) -> None:
    for role in RUNTIME_ROLES[:-1]:
        (tmp_path / dsn_secret_name(role)).write_text(
            f"postgresql://{role}:pw-{role}@pg/db\n", encoding="utf-8"
        )
    with pytest.raises(RoleLoginError, match="postgres_dsn_export_ro"):
        load_role_logins(tmp_path)
    last = RUNTIME_ROLES[-1]
    (tmp_path / dsn_secret_name(last)).write_text(f"postgresql://{last}:pw@pg/db", "utf-8")
    logins = load_role_logins(tmp_path)
    assert [login.role for login in logins] == list(RUNTIME_ROLES)
    assert all(isinstance(login, RoleLogin) for login in logins)


def test_role_secrets_dir_defaults_to_docker_secrets() -> None:
    assert role_secrets_dir({}) == DEFAULT_ROLE_SECRETS_DIR == Path("/run/secrets")
    assert role_secrets_dir({ROLE_SECRETS_DIR_ENV: "/tmp/x"}) == Path("/tmp/x")  # noqa: S108


def test_scram_verifier_matches_rfc_7677_derivation() -> None:
    """Незалежне перерахування RFC 5802 §3: StoredKey = H(HMAC(SaltedPassword, "Client Key"))."""
    salt = bytes(range(16))
    verifier = scram_sha256_verifier("pencil", salt=salt)
    assert SCRAM.match(verifier)
    _, iterations_salt, keys = verifier.split("$")
    iterations, salt_b64 = iterations_salt.split(":")
    stored_b64, server_b64 = keys.split(":")
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", base64.b64decode(salt_b64), int(iterations))
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    assert base64.b64decode(stored_b64) == hashlib.sha256(client_key).digest()
    assert base64.b64decode(server_b64) == hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    # Випадкова сіль за замовчуванням: той самий пароль — різні verifier-и.
    assert scram_sha256_verifier("pencil") != scram_sha256_verifier("pencil")
    assert "'" not in scram_sha256_verifier("it's")
