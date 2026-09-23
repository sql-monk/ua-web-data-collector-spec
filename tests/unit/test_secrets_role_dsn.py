"""Per-component DSN-секрети §13 (WP-00 PR4; dependency `docs/plan/deps/WP-01A-to-WP-00.md` §4, §6).

Три частини одного контракту, які мають збігатися між собою:

- `deploy/compose/secrets/init-secrets.sh` генерує сім `postgres_dsn_<component>` (користувач =
  роль, власний пароль) — скрипт запускається по-справжньому в tmp-каталозі (bash, без мережі);
- `docker-compose.yml`: секрети оголошено, `migrate-postgres` монтує рівно їх + `postgres_dsn`
  і виконує `collector db roles --with-login`;
- `collector db roles --with-login` (WP-01A PR2) приймає саме такі файли — перевіряється його ж
  парсером `load_role_logins`, а не копією формату в тесті.

REVOKE PUBLIC у `postgres/init` перевіряє вартовий у `test_compose_config.py`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

from collector.persistence.postgres.roles import (
    DEFAULT_ROLE_SECRETS_DIR,
    RUNTIME_ROLES,
    dsn_secret_name,
    load_role_logins,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
SECRETS_DIR = REPO_ROOT / "deploy" / "compose" / "secrets"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

COMPONENTS = ("scheduler", "fetcher", "parser", "projector", "translation", "api_ro", "export_ro")
ROLE_DSN_SECRETS = tuple(f"postgres_dsn_{c}" for c in COMPONENTS)
HEX48 = re.compile(r"^[0-9a-f]{48}$")

# Дослівний блок з картки WP-00 PR4, вимога 2: паралельна гілка WP-01D PR1b вставляє той самий
# hunk одразу після `postgres_dsn`, тож будь-яка розбіжність дасть конфлікт злиття.
CARD_SECRETS_BLOCK = """\
  postgres_dsn:
    file: ./deploy/compose/secrets/postgres_dsn
  # Per-component LOGIN-ролі §13 (WP-01A PR2 `db roles --with-login`): кожен runtime-сервіс
  # монтує лише свій DSN; `migrate-postgres` монтує всі, щоб виставити паролі ролям.
  postgres_dsn_scheduler:
    file: ./deploy/compose/secrets/postgres_dsn_scheduler
  postgres_dsn_fetcher:
    file: ./deploy/compose/secrets/postgres_dsn_fetcher
  postgres_dsn_parser:
    file: ./deploy/compose/secrets/postgres_dsn_parser
  postgres_dsn_projector:
    file: ./deploy/compose/secrets/postgres_dsn_projector
  postgres_dsn_translation:
    file: ./deploy/compose/secrets/postgres_dsn_translation
  postgres_dsn_api_ro:
    file: ./deploy/compose/secrets/postgres_dsn_api_ro
  postgres_dsn_export_ro:
    file: ./deploy/compose/secrets/postgres_dsn_export_ro
"""


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return data


def _secret_names(svc: dict[str, Any]) -> list[str]:
    return [s if isinstance(s, str) else s["source"] for s in svc.get("secrets", [])]


# --- контракт імен: compose ↔ init-secrets ↔ `db roles --with-login` -------------------------


def test_component_list_matches_runtime_roles_of_db_roles() -> None:
    """Сім секретів — рівно ті, яких шукає `load_role_logins` для `RUNTIME_ROLES`."""
    assert {dsn_secret_name(role) for role in RUNTIME_ROLES} == set(ROLE_DSN_SECRETS)


def test_every_role_dsn_secret_is_declared_verbatim_after_postgres_dsn(
    compose: dict[str, Any],
) -> None:
    declared = compose["secrets"]
    for name in ROLE_DSN_SECRETS:
        assert declared[name] == {"file": f"./deploy/compose/secrets/{name}"}, name
    text = COMPOSE_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert CARD_SECRETS_BLOCK in text, "блок secrets має збігатися з карткою дослівно"


def test_migrate_postgres_mounts_exactly_migration_and_role_dsns(
    compose: dict[str, Any],
) -> None:
    migrate = compose["services"]["migrate-postgres"]
    assert sorted(_secret_names(migrate)) == sorted(["postgres_dsn", *ROLE_DSN_SECRETS])
    env = migrate["environment"]
    assert env["COLLECTOR_POSTGRES_DSN_FILE"] == "/run/secrets/postgres_dsn"
    # Каталог секретів ролей — типовий /run/secrets; перевизначення вказувало б не туди,
    # куди Compose монтує file-secrets.
    assert "COLLECTOR_POSTGRES_ROLE_SECRETS_DIR" not in env
    assert Path("/run/secrets") == DEFAULT_ROLE_SECRETS_DIR


def test_migrate_postgres_runs_roles_with_login_after_migrate_keeping_exit_code(
    compose: dict[str, Any],
) -> None:
    command = compose["services"]["migrate-postgres"]["command"]
    assert command[:2] == ["sh", "-c"] and len(command) == 3, command
    script = command[2]
    # `&&`: roles не виконується після невдалих міграцій; `exec`: exit code roles — exit code
    # контейнера. Жодних `;`/`||`, які ковтнули б помилку.
    assert script == "collector db migrate && exec collector db roles --with-login"
    assert ";" not in script and "||" not in script


def test_role_dsn_secrets_are_not_mounted_into_services_outside_the_plan(
    compose: dict[str, Any],
) -> None:
    """Поки WP-01D PR1b не перевів runtime: per-role DSN бачить лише `migrate-postgres`.

    Після злиття PR1b кожен runtime-сервіс додасть рівно свій DSN — тоді цей список
    розширюється (власник мапи споживачів — `test_compose_config_adversarial.py`).
    """
    for name, svc in compose["services"].items():
        held = set(_secret_names(svc)) & set(ROLE_DSN_SECRETS)
        if name == "migrate-postgres":
            assert held == set(ROLE_DSN_SECRETS)
        else:
            assert len(held) <= 1, f"{name}: більше одного per-role DSN ({sorted(held)})"


# --- .example без секретів -------------------------------------------------------------------


@pytest.mark.parametrize("name", ROLE_DSN_SECRETS)
def test_role_dsn_example_is_a_placeholder_without_secret(name: str) -> None:
    body = (SECRETS_DIR / f"{name}.example").read_text(encoding="utf-8")
    assert body.startswith("GENERATED"), name
    role = "collector_" + name.removeprefix("postgres_dsn_")
    assert f"postgresql://{role}:<random_hex_48>@" in body, name
    # Жодного значення, схожого на реальний пароль (hex від 16 символів) чи DSN з паролем.
    assert not re.search(r"[0-9a-f]{16,}", body), name
    assert not re.search(r"://[^:\s]+:[^<\s][^@\s]*@", body), name


# --- init-secrets.sh по-справжньому ----------------------------------------------------------


def _bash() -> str:
    """bash для запуску скрипту. На Windows — лише Git Bash (System32\\bash.exe — це WSL).

    На POSIX (CI) відсутність bash — провал, а не skip: інакше тест мовчки не виконувався б.
    """
    if sys.platform != "win32":
        found = shutil.which("bash")
        assert found, "bash потрібен для init-secrets.sh (clean-host start, §16.3)"
        return found
    git = shutil.which("git")
    if git:
        # git.exe лежить у `<Git>\cmd`, `<Git>\bin` або `<Git>\mingw64\bin` — шукаємо вгору.
        for root in Path(git).resolve().parents:
            for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
                if candidate.is_file():
                    return str(candidate)
    pytest.skip("Git Bash не знайдено (лише Windows; у CI на Linux тест обов'язковий)")


def _run_init_secrets(target: Path, extra_env: dict[str, str] | None = None) -> str:
    """Копія скрипту + *.example у `target` і запуск; повертає stdout (без секретів)."""
    proc = _run_init_secrets_raw(target, extra_env)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _run_init_secrets_raw(
    target: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SECRETS_DIR / "init-secrets.sh", target / "init-secrets.sh")
    for example in SECRETS_DIR.glob("*.example"):
        shutil.copy2(example, target / example.name)
    env = {k: v for k, v in os.environ.items() if not k.startswith("POSTGRES_")}
    env.update(extra_env or {})
    proc = subprocess.run(  # noqa: S603 — фіксований argv, без shell
        [_bash(), (target / "init-secrets.sh").as_posix()],
        capture_output=True,
        encoding="utf-8",  # скрипт пише UTF-8; locale-кодування Windows його зіпсувало б
        env=env,
        check=False,
        timeout=60,
    )
    return proc


def _dsn_files(directory: Path) -> dict[str, str]:
    return {
        name: (directory / name).read_bytes().decode("ascii")
        for name in ("postgres_dsn", *ROLE_DSN_SECRETS)
    }


def test_init_secrets_generates_eight_dsns_with_distinct_passwords_and_role_users(
    tmp_path: Path,
) -> None:
    stdout = _run_init_secrets(tmp_path)
    dsns = _dsn_files(tmp_path)
    passwords: dict[str, str] = {}
    for name, raw in dsns.items():
        assert raw.endswith("\n") and raw.count("\n") == 1 and "\r" not in raw, name
        url = urlsplit(raw.strip())
        assert url.scheme == "postgresql", name
        assert (url.hostname, url.port, url.path) == ("postgres", 5432, "/collector"), name
        expected_user = (
            "collector" if name == "postgres_dsn" else "collector_" + name.split("_", 2)[2]
        )
        assert url.username == expected_user, name
        assert url.password is not None and HEX48.match(url.password), name
        passwords[name] = url.password
        assert url.password not in stdout, f"{name}: пароль у stdout скрипту"
    assert len(set(passwords.values())) == len(passwords), "паролі мають бути попарно різні"
    # Міграційний DSN — з postgres_password (його читає сам Postgres).
    assert (tmp_path / "postgres_password").read_text(encoding="ascii").strip() == passwords[
        "postgres_dsn"
    ]


def test_generated_role_dsns_are_accepted_by_db_roles_with_login(tmp_path: Path) -> None:
    """Формат збігається з тим, що читає `collector db roles --with-login` (WP-01A PR2)."""
    _run_init_secrets(tmp_path)
    logins = load_role_logins(tmp_path)
    assert [login.role for login in logins] == list(RUNTIME_ROLES)
    assert len({login.password for login in logins}) == len(RUNTIME_ROLES)


def test_init_secrets_is_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    _run_init_secrets(tmp_path)
    before = _dsn_files(tmp_path)
    stdout = _run_init_secrets(tmp_path)
    assert _dsn_files(tmp_path) == before
    for name in ROLE_DSN_SECRETS:
        assert f"skip  {name} (exists)" in stdout, name


def test_init_secrets_adds_only_missing_role_dsns_on_pre_pr4_host(tmp_path: Path) -> None:
    """Хост, ініціалізований до PR4: наявні секрети лишаються, додаються лише нові DSN."""
    _run_init_secrets(tmp_path)
    kept = (tmp_path / "postgres_dsn").read_bytes()
    for name in ROLE_DSN_SECRETS:
        (tmp_path / name).unlink()
    _run_init_secrets(tmp_path)
    assert (tmp_path / "postgres_dsn").read_bytes() == kept
    load_role_logins(tmp_path)


def test_init_secrets_honours_postgres_host_port_db_overrides(tmp_path: Path) -> None:
    _run_init_secrets(
        tmp_path, {"POSTGRES_HOST": "db.internal", "POSTGRES_PORT": "6543", "POSTGRES_DB": "c2"}
    )
    for name, raw in _dsn_files(tmp_path).items():
        url = urlsplit(raw.strip())
        assert (url.hostname, url.port, url.path) == ("db.internal", 6543, "/c2"), name


# --- CI: той самий шлях генерації + перевірка на живому стеку ---------------------------------


def test_ci_docker_job_checks_role_logins_revoke_public_and_leaks() -> None:
    ci: dict[str, Any] = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = ci["jobs"]["docker"]["steps"]
    runs = [str(step.get("run", "")) for step in steps]
    secrets_step = next(i for i, r in enumerate(runs) if "init-secrets.sh" in r)
    up = next(i for i, r in enumerate(runs) if "up -d --wait" in r)
    check = next(i for i, r in enumerate(runs) if "collector_fetcher" in r)
    down = next(i for i, r in enumerate(runs) if "down -v" in r)
    assert secrets_step < up < check < down
    script = runs[check]
    assert "rolcanlogin" in script and "collector_translation" in script
    assert 'permission denied for database "postgres"' in script
    assert "aclexplode" in script and "grantee = 0" in script
    assert "docker inspect" in script and "docker compose logs" in script
    # DSN іде через env без значення в argv (`-e PGDSN`), не через `-e PGDSN=...`.
    assert "-e PGDSN " in script and "-e PGDSN=" not in script


# --- gate 2 F-1: каталог / порожній файл на місці секрету ------------------------------------


def test_init_secrets_replaces_empty_directory_left_by_docker(tmp_path: Path) -> None:
    """Docker Desktop створює порожній каталог на місці відсутнього file-secret (F-1).

    Скрипт не має вважати його «наявним секретом»: каталог прибирається, секрет генерується.
    """
    (tmp_path / "postgres_dsn_fetcher").mkdir(parents=True)
    (tmp_path / "postgres_password").mkdir()
    stdout = _run_init_secrets(tmp_path)
    assert "skip  postgres_dsn_fetcher" not in stdout
    assert "fix   postgres_dsn_fetcher" in stdout
    assert (tmp_path / "postgres_dsn_fetcher").is_file()
    assert (tmp_path / "postgres_password").is_file()
    load_role_logins(tmp_path)
    dsn = urlsplit((tmp_path / "postgres_dsn").read_text(encoding="ascii").strip())
    assert dsn.password == (tmp_path / "postgres_password").read_text(encoding="ascii").strip()


def test_init_secrets_stops_on_non_empty_directory_with_hint(tmp_path: Path) -> None:
    """Непорожній каталог — не вгадуємо, що в ньому: exit ≠ 0, підказка, вміст не зачеплено."""
    blocker = tmp_path / "postgres_dsn_parser"
    blocker.mkdir(parents=True)
    (blocker / "keep.txt").write_text("user data", encoding="utf-8")
    proc = _run_init_secrets_raw(tmp_path)
    assert proc.returncode != 0
    assert "postgres_dsn_parser" in proc.stderr and "rm -r" in proc.stderr
    assert (blocker / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_init_secrets_regenerates_empty_file(tmp_path: Path) -> None:
    """Порожній файл секрету не містить (нема чого губити) — генерується заново, а не skip."""
    _run_init_secrets(tmp_path)
    (tmp_path / "postgres_dsn_translation").write_bytes(b"")
    stdout = _run_init_secrets(tmp_path)
    assert "gen   postgres_dsn_translation" in stdout
    load_role_logins(tmp_path)


# --- gate 3: збій генератора, атомарний запис, lock, узгодженість postgres_dsn ----------------


def _failing_openssl_env(tmp_path: Path) -> dict[str, str]:
    """`openssl`, що падає без виводу (CR-1/L-1), — функцією через `BASH_ENV`.

    Не PATH-shim: Git Bash (`bin/bash.exe`) на Windows сам ставить `/mingw64/bin` першим у PATH
    і знайшов би справжній openssl. Функція має пріоритет над PATH на всіх платформах, а
    `command -v openssl` у скрипті її бачить.
    """
    rc = tmp_path / "failing-openssl.bash"
    rc.write_bytes(b"openssl() { return 1; }\n")
    return {"BASH_ENV": rc.as_posix()}


def _leftovers(directory: Path) -> list[str]:
    """Тимчасові файли `write_secret` і lock-каталог, що могли лишитися після запуску."""
    return sorted(p.name for p in directory.iterdir() if p.name.startswith("."))


def test_init_secrets_fails_loudly_when_generator_fails(tmp_path: Path) -> None:
    """Збій `openssl rand` → exit ≠ 0 і жодного файла з порожнім паролем (раніше exit 0)."""
    secrets = tmp_path / "secrets"
    proc = _run_init_secrets_raw(secrets, _failing_openssl_env(tmp_path))
    assert proc.returncode != 0
    assert "генератор" in proc.stderr
    generated = [p.name for p in secrets.iterdir() if p.is_file()]
    for name in ("postgres_password", "postgres_dsn", *ROLE_DSN_SECRETS, "mongo_keyfile"):
        assert name not in generated, f"{name} записано попри збій генератора"
    assert _leftovers(secrets) == [], "tmp-файли/lock не прибрано"


def test_init_secrets_fails_on_generator_failure_for_role_dsn_only(tmp_path: Path) -> None:
    """Хост до PR4: наявні секрети є, бракує лише per-role DSN — збій не дає DSN без пароля."""
    secrets = tmp_path / "secrets"
    _run_init_secrets(secrets)
    for name in ROLE_DSN_SECRETS:
        (secrets / name).unlink()
    proc = _run_init_secrets_raw(secrets, _failing_openssl_env(tmp_path))
    assert proc.returncode != 0
    for name in ROLE_DSN_SECRETS:
        assert not (secrets / name).exists(), name
    # Після відновлення генератора звичайний запуск лікує стан.
    _run_init_secrets(secrets)
    load_role_logins(secrets)


def test_init_secrets_leaves_no_temp_files_or_lock(tmp_path: Path) -> None:
    _run_init_secrets(tmp_path)
    assert _leftovers(tmp_path) == []
    script = (SECRETS_DIR / "init-secrets.sh").read_text(encoding="utf-8")
    # Атомарність: tmp у тому ж каталозі + mv (перейменування в межах однієї FS).
    assert 'mktemp "$here/.' in script and 'mv -f "$tmp" "$1"' in script


def test_init_secrets_waits_for_lock_and_gives_up_with_hint(tmp_path: Path) -> None:
    """Зайнятий lock (паралельний запуск) → чекає, потім exit ≠ 0 без жодних записів."""
    _run_init_secrets_raw(tmp_path)  # створює каталог і копіює скрипт
    for name in ("postgres_dsn", *ROLE_DSN_SECRETS):
        (tmp_path / name).unlink()
    (tmp_path / ".init-secrets.lock").mkdir()
    proc = _run_init_secrets_raw(tmp_path, {"INIT_SECRETS_LOCK_TIMEOUT": "1"})
    assert proc.returncode != 0
    assert ".init-secrets.lock" in proc.stderr and "rmdir" in proc.stderr
    assert not (tmp_path / "postgres_dsn").exists()
    # Чужий lock не видаляється.
    assert (tmp_path / ".init-secrets.lock").is_dir()


def test_init_secrets_refuses_new_postgres_password_when_dsn_exists(tmp_path: Path) -> None:
    """CR-2: DSN уже несе пароль Postgres — мовчки генерувати інший не можна."""
    _run_init_secrets(tmp_path)
    dsn_before = (tmp_path / "postgres_dsn").read_bytes()
    (tmp_path / "postgres_password").write_bytes(b"")
    proc = _run_init_secrets_raw(tmp_path)
    assert proc.returncode != 0
    assert "postgres_dsn" in proc.stderr and "down -v" in proc.stderr
    assert (tmp_path / "postgres_password").read_bytes() == b""
    assert (tmp_path / "postgres_dsn").read_bytes() == dsn_before


def test_init_secrets_warns_when_existing_dsn_and_password_diverge(tmp_path: Path) -> None:
    _run_init_secrets(tmp_path)
    (tmp_path / "postgres_password").write_bytes(b"0" * 48 + b"\n")
    proc = _run_init_secrets_raw(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "warn:" in proc.stderr and "postgres_dsn" in proc.stderr
    clean = _run_init_secrets_raw(tmp_path / "fresh")
    assert "warn:" not in clean.stderr
