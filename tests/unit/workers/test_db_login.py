"""LOGIN-роль БД runtime (§13; картка WP-01D PR1b п.1–3) без PostgreSQL.

- мапінг `WorkerRole` → роль БД покриває всі ролі й збігається з DSN-секретами в Compose;
- `verify_component_login` відхиляє роль чужого компонента;
- CLI `worker`/`scheduler`: `RoleLoginError` → exit 1 зі зрозумілим stderr без DSN/пароля.

Реальні відмови (superuser, член `collector_migrate`) — integration-тест
`tests/integration/scaling/test_runtime_login.py`.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from typer.testing import CliRunner

from collector.cli import app
from collector.contracts import new_entity_id
from collector.persistence.postgres.clock import utcnow
from collector.persistence.postgres.roles import (
    MIGRATE_ROLE,
    RUNTIME_ROLES,
    RoleLoginError,
    dsn_secret_name,
)
from collector.workers import login
from collector.workers.handlers import HANDLER_FACTORIES, HandlerContext, NoopHandler
from collector.workers.registry import ROLE_HANDLER_MODULES, load_role_bindings
from collector.workers.roles import (
    DB_ROLE_BY_WORKER_ROLE,
    SCHEDULER_DB_ROLE,
    WorkerRole,
    db_role_for,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def test_every_worker_role_maps_to_a_writable_runtime_login_role() -> None:
    assert set(DB_ROLE_BY_WORKER_ROLE) == set(WorkerRole)
    for role in WorkerRole:
        db_role = db_role_for(role)
        assert db_role in RUNTIME_ROLES, role
        # Read-only ролі не можуть писати heartbeat/claim, міграційна — заборонена §13.
        assert db_role not in {MIGRATE_ROLE, "collector_api_ro", "collector_export_ro"}, role
    assert SCHEDULER_DB_ROLE in RUNTIME_ROLES


def test_mapping_matches_the_card() -> None:
    """Мапінг картки WP-01D PR1b п.1–2 (export → scheduler — рішення оркестратора, варіант (а))."""
    assert {role.value: db_role_for(role) for role in WorkerRole} == {
        "discovery": "collector_fetcher",
        "fetch": "collector_fetcher",
        "browser": "collector_fetcher",
        "parse": "collector_parser",
        "projector": "collector_projector",
        "translation": "collector_translation",
        "export": "collector_scheduler",
        "maintenance": "collector_scheduler",
    }


def test_compose_mounts_the_dsn_of_the_role_the_process_verifies() -> None:
    """Compose і runtime узгоджені: сервіс монтує DSN саме тієї ролі, яку перевіряє процес."""
    services: dict[str, Any] = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )["services"]
    expected = {f"{role.value}-worker": db_role_for(role) for role in WorkerRole}
    expected["scheduler"] = SCHEDULER_DB_ROLE
    for name, db_role in expected.items():
        secret = dsn_secret_name(db_role)
        assert services[name]["secrets"] == [secret], name
        assert services[name]["environment"]["COLLECTOR_POSTGRES_DSN_FILE"] == (
            f"/run/secrets/{secret}"
        ), name


class _Session:
    async def execute(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def connection(self) -> object:
        return object()

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _sessions() -> Any:
    return cast("Any", _Session)


async def test_component_login_rejects_the_role_of_another_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_verify(_conn: object) -> str:
        return "collector_parser"

    monkeypatch.setattr(login, "verify_runtime_login", fake_verify)
    with pytest.raises(RoleLoginError, match="'collector_parser'.*'collector_fetcher'"):
        await login.verify_component_login(
            _sessions(), "collector_fetcher", statement_timeout_ms=1000
        )
    assert (
        await login.verify_component_login(_sessions(), "collector_parser", statement_timeout_ms=1)
        == "collector_parser"
    )


async def test_component_login_propagates_privileged_login_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def superuser(_conn: object) -> str:
        msg = "runtime-підключення під 'collector' має атрибути superuser"
        raise RoleLoginError(msg)

    monkeypatch.setattr(login, "verify_runtime_login", superuser)
    with pytest.raises(RoleLoginError, match="superuser"):
        await login.verify_component_login(
            _sessions(), "collector_fetcher", statement_timeout_ms=1000
        )


@pytest.mark.parametrize(
    ("argv", "target"), [(["worker", "fetch"], "_run_worker"), (["scheduler"], "_run_scheduler")]
)
def test_cli_exits_1_on_foreign_login_without_leaking_the_dsn(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], target: str
) -> None:
    password = secrets.token_hex(12)  # синтетичний, будується в рантаймі (gitleaks)
    dsn = f"postgresql://collector:{password}@127.0.0.1:5432/collector"
    monkeypatch.setenv("COLLECTOR_POSTGRES_DSN", dsn)
    monkeypatch.delenv("COLLECTOR_POSTGRES_DSN_FILE", raising=False)
    monkeypatch.delenv("COLLECTOR_WORKER_PLACEHOLDER", raising=False)

    async def refuse(*_args: object) -> None:
        msg = "runtime-підключення під 'collector' має атрибути superuser: використайте DSN"
        raise RoleLoginError(msg)

    monkeypatch.setattr(f"collector.cli.{target}", refuse)
    result = runner.invoke(app, argv)
    assert result.exit_code == 1, result.output
    assert "role login:" in result.output
    assert "superuser" in result.output
    assert "Traceback" not in result.output
    assert password not in result.output
    assert dsn not in result.output


def test_export_worker_keeps_scheduler_role_only_while_its_handler_is_noop() -> None:
    """Тест-вартовий S-1 (security-pr1b): `export-worker` під `collector_scheduler` — тимчасово.

    Ризик у картці WP-01D закривається до першого реального export handler (WP-11A) або до pilot.
    Щойно export отримує handler, відмінний від `NoopHandler`, поки монтує
    `postgres_dsn_scheduler` / мапиться на `collector_scheduler`, цей тест падає: спершу окрема
    роль (`collector_exporter` / `collector_export_ro`-з'єднання), потім handler.
    """
    services: dict[str, Any] = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )["services"]
    on_scheduler_role = (
        db_role_for(WorkerRole.EXPORT) == SCHEDULER_DB_ROLE
        or "postgres_dsn_scheduler" in services["export-worker"]["secrets"]
    )
    if not on_scheduler_role:
        return
    reason = (
        "export-worker досі під collector_scheduler (ризик S-1 картки WP-01D): спершу окрема "
        "роль для експорту, потім реальний handler"
    )
    # PR1c: реєстр із lazy import — export не має ні модуля в мапі, ні фабрики, і прив'язка
    # ролі — рівно одна `NoopHandler` (сесії не потрібні: Noop-шлях у БД не ходить).
    assert WorkerRole.EXPORT not in ROLE_HANDLER_MODULES, reason
    assert WorkerRole.EXPORT not in HANDLER_FACTORIES, reason
    context = HandlerContext(
        role=WorkerRole.EXPORT,
        sessions=cast("Any", None),
        worker_instance_id=new_entity_id(),
        clock=utcnow,
        env={},
    )
    bindings = load_role_bindings(context)
    assert [type(binding.handler) for binding in bindings] == [NoopHandler], reason
    # Реєстр наповнюється при імпорті доменного модуля, тож перевіряємо й джерела: жоден модуль
    # не реєструє фабрику для EXPORT.
    registrations = re.compile(r"HANDLER_FACTORIES\s*(\[|\.update|\.setdefault)[^\n]*EXPORT")
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "src").rglob("*.py")
        if registrations.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"{reason}: {offenders}"
