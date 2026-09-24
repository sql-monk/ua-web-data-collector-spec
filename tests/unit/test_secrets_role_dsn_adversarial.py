"""Adversarial-тести per-component DSN-секретів §13 (WP-00 PR4, незалежне тестування).

Доповнюють `test_secrets_role_dsn.py` сценаріями, які реалізатор не покривав:

- повторний `init-secrets.sh` не змінює жодного наявного секрету (не лише DSN), навіть
  «чужого» вмісту, який скрипт сам би не згенерував;
- частково наявний набір `postgres_dsn_*` → доповнюються лише відсутні файли;
- паролі всіх секретів попарно різні (у т. ч. відносно `postgres_password`/`postgres_dsn`,
  MongoDB і MinIO) і різні між двома незалежними ініціалізаціями;
- користувач у DSN = роль саме за мапою `dsn_secret_name(role)` з `db roles --with-login`;
- команда `migrate-postgres` дослівно з compose виконується в POSIX `sh` зі stub-`collector`:
  exit code першої невдалої команди зберігається, `db roles` після невдалих міграцій не
  стартує;
- жоден сервіс поза `migrate-postgres` (і жоден `x-*`-якір, через який секрети
  успадковуються) не отримує всі сім per-role DSN; DSN не потрапляє в `environment`;
- скрипт — LF у git-індексі й робочій копії, `.gitattributes` це фіксує, CRLF у `*.example`
  (Windows checkout) не просочується в секрети; працює і в `bash --posix`.

Жодних skip на Linux: відсутність `bash`/`sh`/`git` поза Windows — провал тесту.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

from collector.persistence.postgres.roles import RUNTIME_ROLES, dsn_secret_name, load_role_logins

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
SECRETS_DIR = REPO_ROOT / "deploy" / "compose" / "secrets"
SCRIPT_REL = "deploy/compose/secrets/init-secrets.sh"

COMPONENTS = ("scheduler", "fetcher", "parser", "projector", "translation", "api_ro", "export_ro")
ROLE_DSN_SECRETS = tuple(f"postgres_dsn_{c}" for c in COMPONENTS)
PASSWORD_FILES = ("postgres_password", "mongo_root_password", "minio_root_password")


# --- інструменти ----------------------------------------------------------------------------


def _git_bash_dir() -> Path | None:
    git = shutil.which("git")
    if not git:
        return None
    # Спершу `usr\bin` (справжній bash/sh): `bin\bash.exe` — launcher, kill по timeout його
    # зупиняє, а bash — ні (WP-00 PR4 gate 3' low #1); `bin` лишається fallback-ом.
    for root in Path(git).resolve().parents:
        for candidate in (root / "usr" / "bin", root / "bin"):
            if (candidate / "bash.exe").is_file() and (candidate / "sh.exe").is_file():
                return candidate
    return None


def _shell(name: str) -> str:
    """`bash`/`sh`: на POSIX обов'язкові (провал, не skip); на Windows — лише з Git Bash."""
    if sys.platform != "win32":
        found = shutil.which(name)
        assert found, f"{name} потрібен для init-secrets.sh / migrate-postgres (§16.3)"
        return found
    directory = _git_bash_dir()
    if directory is None:
        pytest.skip("Git Bash не знайдено (лише Windows; у CI на Linux тест обов'язковий)")
    return str(directory / f"{name}.exe")


def _prepare(target: Path, *, crlf_examples: bool = False) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SECRETS_DIR / "init-secrets.sh", target / "init-secrets.sh")
    for example in SECRETS_DIR.glob("*.example"):
        data = example.read_bytes().replace(b"\r\n", b"\n")
        if crlf_examples:
            data = data.replace(b"\n", b"\r\n")
        (target / example.name).write_bytes(data)


def _run(target: Path, *, bash_args: tuple[str, ...] = ()) -> str:
    bash = _shell("bash")
    env = {k: v for k, v in os.environ.items() if not k.startswith("POSTGRES_")}
    if sys.platform == "win32":
        # `usr\bin\bash.exe` без launcher-а: утиліти MSYS/mingw у PATH додаємо самі.
        extra = [Path(bash).parent, Path(bash).parents[2] / "mingw64" / "bin"]
        dirs = [str(d) for d in extra if d.is_dir()]
        env["PATH"] = os.pathsep.join([*dirs, env.get("PATH", "")])
    proc = subprocess.run(  # noqa: S603 — фіксований argv, без shell
        [bash, *bash_args, (target / "init-secrets.sh").as_posix()],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=180,  # Git Bash на завантаженому Windows-хості (gate 3' low #1)
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _secret_files(directory: Path) -> dict[str, bytes]:
    return {
        p.name: p.read_bytes()
        for p in directory.iterdir()
        if p.is_file() and p.suffix != ".example" and p.name != "init-secrets.sh"
    }


def _password(raw: bytes) -> str:
    url = urlsplit(raw.decode("ascii").strip())
    assert url.password, raw
    return url.password


# --- ідемпотентність і часткові набори --------------------------------------------------------


def test_rerun_leaves_every_existing_secret_byte_identical(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _run(tmp_path)
    before = _secret_files(tmp_path)
    # 5 секретів Compose + 8 DSN + WP-00 PR5 (6 MinIO, 4 Mongo URI, порожній credential
    # перекладу); жодного «зайвого» файла.
    expected = {
        "postgres_password",
        "postgres_dsn",
        "mongo_root_password",
        "mongo_keyfile",
        "minio_root_user",
        "minio_root_password",
        *ROLE_DSN_SECRETS,
        *(
            f"minio_{c}"
            for c in ("fetcher", "parser", "projector", "translation", "maintenance", "readonly")
        ),
        *(f"mongo_uri_{c}" for c in ("projector", "compactor", "api_ro", "export_ro")),
        "google_translation_credentials",
    }
    assert set(before) == expected
    stdout = _run(tmp_path)
    assert _secret_files(tmp_path) == before
    assert "gen " not in stdout and "copy " not in stdout, stdout


def test_rerun_keeps_operator_supplied_role_dsn_verbatim(tmp_path: Path) -> None:
    """Файл, який скрипт сам би так не згенерував (ротований пароль, інший хост), не чіпається."""
    _prepare(tmp_path)
    custom = b"postgresql://collector_fetcher:rotated-by-operator@db.example:6000/other\n"
    (tmp_path / "postgres_dsn_fetcher").write_bytes(custom)
    stdout = _run(tmp_path)
    assert (tmp_path / "postgres_dsn_fetcher").read_bytes() == custom
    assert "skip  postgres_dsn_fetcher (exists)" in stdout


def test_partial_role_dsn_set_is_completed_without_touching_present_files(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _run(tmp_path)
    present = ("postgres_dsn_scheduler", "postgres_dsn_fetcher", "postgres_dsn_api_ro")
    kept = {name: (tmp_path / name).read_bytes() for name in present}
    other_before = {
        k: v for k, v in _secret_files(tmp_path).items() if not k.startswith("postgres_dsn_")
    }
    missing = [name for name in ROLE_DSN_SECRETS if name not in present]
    for name in missing:
        (tmp_path / name).unlink()

    stdout = _run(tmp_path)

    after = _secret_files(tmp_path)
    for name in present:
        assert after[name] == kept[name], name
        assert f"skip  {name} (exists)" in stdout, name
    for name in missing:
        assert f"gen   {name} " in stdout, name
        assert name in after, name
    assert {k: v for k, v in after.items() if not k.startswith("postgres_dsn_")} == other_before
    # Нові паролі не збігаються з наявними (кожен компонент — власний).
    new_passwords = {_password(after[name]) for name in missing}
    assert len(new_passwords) == len(missing)
    assert new_passwords.isdisjoint({_password(v) for v in kept.values()})
    # Доповнений набір приймає `db roles --with-login`.
    assert [login.role for login in load_role_logins(tmp_path)] == list(RUNTIME_ROLES)


# --- паролі і користувачі ----------------------------------------------------------------------


def test_all_passwords_pairwise_distinct_across_secrets_and_across_hosts(tmp_path: Path) -> None:
    hosts = (tmp_path / "a", tmp_path / "b")
    seen: dict[str, str] = {}
    for host in hosts:
        _prepare(host)
        _run(host)
        files = _secret_files(host)
        values = {f"{host.name}/{n}": files[n].decode("ascii").strip() for n in PASSWORD_FILES}
        values.update(
            {f"{host.name}/{n}": _password(files[n]) for n in ("postgres_dsn", *ROLE_DSN_SECRETS)}
        )
        # postgres_dsn навмисно = postgres_password (його читає сам Postgres) — одна пара.
        assert values[f"{host.name}/postgres_dsn"] == values[f"{host.name}/postgres_password"]
        del values[f"{host.name}/postgres_dsn"]
        for name, value in values.items():
            assert value.isascii() and value.isprintable() and len(value) == 48, name
        seen.update(values)
    duplicates = {v for v in seen.values() if list(seen.values()).count(v) > 1}
    assert not duplicates, "повторюваний пароль між секретами/хостами"


def test_dsn_user_equals_role_by_db_roles_mapping(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _run(tmp_path)
    for role in RUNTIME_ROLES:
        raw = (tmp_path / dsn_secret_name(role)).read_bytes().decode("ascii")
        url = urlsplit(raw.strip())
        assert url.username == role, role
        # Роль не суперкористувач міграцій і не `collector_migrate`.
        assert url.username not in ("collector", "collector_migrate", "postgres")


# --- migrate-postgres: exit code у POSIX sh -------------------------------------------------


def _migrate_script() -> str:
    compose: dict[str, Any] = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    command = compose["services"]["migrate-postgres"]["command"]
    assert command[:2] == ["sh", "-c"], command
    return str(command[2])


def _run_migrate_with_stub(tmp_path: Path, *, migrate_rc: int, roles_rc: int) -> tuple[int, str]:
    """Виконує дослівну команду з compose у `sh`, де `collector` — stub, що пише журнал."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    journal = tmp_path / "journal.txt"
    stub = bindir / "collector"
    stub.write_bytes(
        (
            "#!/bin/sh\n"
            f'echo "$*" >> "{journal.as_posix()}"\n'
            'case "$*" in\n'
            f'  "db migrate") exit {migrate_rc} ;;\n'
            f'  "db roles --with-login") exit {roles_rc} ;;\n'
            "  *) exit 97 ;;\n"
            "esac\n"
        ).encode()
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    sh = _shell("sh")
    # PATH у нативному форматі ОС (MSYS sh на Windows конвертує його сам).
    path = os.pathsep.join([str(bindir), str(Path(sh).parent), os.environ.get("PATH", "")])
    env = {**os.environ, "PATH": path}
    proc = subprocess.run(  # noqa: S603 — фіксований argv
        [sh, "-c", _migrate_script()],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=180,
    )
    return proc.returncode, journal.read_text() if journal.exists() else ""


def test_migrate_failure_propagates_and_roles_is_not_run(tmp_path: Path) -> None:
    rc, journal = _run_migrate_with_stub(tmp_path, migrate_rc=3, roles_rc=0)
    assert rc == 3, journal
    assert journal.splitlines() == ["db migrate"], "db roles не має виконуватися після збою"


def test_roles_failure_is_the_exit_code_of_the_one_shot(tmp_path: Path) -> None:
    rc, journal = _run_migrate_with_stub(tmp_path, migrate_rc=0, roles_rc=5)
    assert rc == 5
    assert journal.splitlines() == ["db migrate", "db roles --with-login"]


def test_migrate_then_roles_success_is_zero(tmp_path: Path) -> None:
    rc, journal = _run_migrate_with_stub(tmp_path, migrate_rc=0, roles_rc=0)
    assert rc == 0
    assert journal.splitlines() == ["db migrate", "db roles --with-login"]


# --- compose: хто бачить per-role DSN ------------------------------------------------------


def _compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return data


def _names(block: dict[str, Any]) -> set[str]:
    return {s if isinstance(s, str) else s["source"] for s in block.get("secrets", []) or []}


def test_no_service_other_than_migrate_postgres_holds_all_role_dsns() -> None:
    compose = _compose()
    for name, svc in compose["services"].items():
        held = _names(svc) & set(ROLE_DSN_SECRETS)
        if name == "migrate-postgres":
            continue
        assert held != set(ROLE_DSN_SECRETS), name
        assert len(held) <= 1, f"{name}: {sorted(held)}"


def test_no_extension_anchor_distributes_role_dsns() -> None:
    """`x-*`-якорі (`x-worker`, `x-collector-runtime`) успадковуються багатьма сервісами."""
    compose = _compose()
    for key, block in compose.items():
        if key.startswith("x-") and isinstance(block, dict):
            assert not (_names(block) & set(ROLE_DSN_SECRETS)), key


def test_role_dsn_never_inlined_into_environment() -> None:
    compose = _compose()
    for name, svc in compose["services"].items():
        env = svc.get("environment") or {}
        items = env.items() if isinstance(env, dict) else (e.partition("=")[::2] for e in env)
        for key, value in items:
            assert "postgresql://" not in str(value), f"{name}: {key}"
            if "DSN" in str(key):
                assert str(key).endswith("_FILE"), f"{name}: {key}"


# --- LF / POSIX -----------------------------------------------------------------------------


def _git(*args: str) -> bytes:
    git = shutil.which("git")
    assert git, "git потрібен для перевірки .gitattributes"
    return subprocess.run(  # noqa: S603 — фіксований argv
        [git, "-C", str(REPO_ROOT), *args], capture_output=True, check=True, timeout=30
    ).stdout


def test_init_secrets_is_lf_in_index_and_worktree_and_pinned_by_gitattributes() -> None:
    attrs = _git("check-attr", "text", "eol", "--", SCRIPT_REL).decode()
    assert f"{SCRIPT_REL}: eol: lf" in attrs, attrs
    assert f"{SCRIPT_REL}: text: set" in attrs, attrs
    indexed = _git("show", f":{SCRIPT_REL}")
    assert b"\r" not in indexed
    worktree = (REPO_ROOT / SCRIPT_REL).read_bytes()
    assert b"\r" not in worktree
    assert worktree.startswith(b"#!/usr/bin/env bash\n")
    mode = _git("ls-files", "-s", "--", SCRIPT_REL).split()[0]
    assert mode == b"100755", "скрипт має бути виконуваним у git"


def test_crlf_examples_do_not_leak_carriage_returns_into_secrets(tmp_path: Path) -> None:
    _prepare(tmp_path, crlf_examples=True)
    _run(tmp_path)
    for name, data in _secret_files(tmp_path).items():
        assert b"\r" not in data, name
    load_role_logins(tmp_path)


def test_script_runs_in_bash_posix_mode(tmp_path: Path) -> None:
    _prepare(tmp_path)
    _run(tmp_path, bash_args=("--posix",))
    assert [login.role for login in load_role_logins(tmp_path)] == list(RUNTIME_ROLES)
