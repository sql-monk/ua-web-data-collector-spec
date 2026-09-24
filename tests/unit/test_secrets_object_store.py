"""Облікові дані за компонентами для MinIO, MongoDB і провайдера перекладу (WP-00 PR5, §13).

Контракт з трьох частин, які мають збігатися:

- `deploy/compose/secrets/init-secrets.sh` генерує `minio_<component>` (`access_key=` +
  `secret_key=`), `mongo_uri_<component>` (власний пароль, `replicaSet=rs0`,
  `authSource=admin`) і створює ПОРОЖНІЙ `google_translation_credentials` — скрипт запускається
  по-справжньому в tmp-каталозі (bash, без мережі);
- `deploy/compose/minio/ensure-minio.sh` + `policies/*.json` — buckets, користувачі, policies;
  запускається з stub-`mc` (журнал argv/stdin), тож перевіряється і те, що жоден секрет не
  потрапляє в argv;
- `docker-compose.yml`: хто що монтує (таблиця картки WP-00 PR5), `ensure-minio`,
  перемикач `ensure-mongo --validators --indexes --users` і його вартовий.

Реальні дозволи MinIO (fetcher не видаляє з `raw` і не пише в `normalized`, maintenance
видаляє) перевіряються на живому стеку — див. docs/plan/reports/WP-00/implementation-pr5.md.
Синтетичні значення в тестах будуються в рантаймі; жодного справжнього секрету у fixtures.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import typer
import typer.core
import yaml

from collector.cli import app

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
SECRETS_DIR = REPO_ROOT / "deploy" / "compose" / "secrets"
MINIO_DIR = REPO_ROOT / "deploy" / "compose" / "minio"
ENSURE_MINIO = MINIO_DIR / "ensure-minio.sh"
MINIO_DOCKERFILE = MINIO_DIR / "Dockerfile"

MINIO_COMPONENTS = ("fetcher", "parser", "projector", "translation", "maintenance", "readonly")
MINIO_SECRETS = tuple(f"minio_{c}" for c in MINIO_COMPONENTS)
MONGO_COMPONENTS = ("projector", "compactor", "api_ro", "export_ro")
MONGO_SECRETS = tuple(f"mongo_uri_{c}" for c in MONGO_COMPONENTS)
PROVIDER_SECRET = "google_translation_credentials"  # noqa: S105 — ім'я файла, не значення
BUCKETS = ("raw", "normalized", "archive", "translated", "events")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX48 = re.compile(r"^[0-9a-f]{48}$")

# Таблиця картки WP-00 PR5, п.1: компонент → {(bucket, дія)}. S3 не має окремої дії Head:
# HeadObject авторизується як s3:GetObject, тому «Head» у таблиці = GetObject.
GET, PUT, DELETE, LIST, LOCATION = (
    "s3:GetObject",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:ListBucket",
    "s3:GetBucketLocation",
)
EXPECTED_PERMISSIONS: dict[str, set[tuple[str, str]]] = {
    "fetcher": {("raw", PUT), ("raw", GET), ("raw", LOCATION)},
    "parser": {
        ("raw", GET),
        ("raw", LOCATION),
        ("normalized", PUT),
        ("normalized", GET),
        ("normalized", LOCATION),
    },
    "projector": {
        ("normalized", GET),
        ("normalized", LOCATION),
        ("events", PUT),
        ("events", GET),
        ("events", LOCATION),
        ("archive", PUT),
        ("archive", GET),
        ("archive", DELETE),
        ("archive", LOCATION),
    },
    "translation": {
        ("normalized", GET),
        ("normalized", LOCATION),
        ("translated", PUT),
        ("translated", GET),
        ("translated", LOCATION),
    },
    "maintenance": {
        (bucket, action)
        for bucket in ("raw", "normalized", "events", "translated")
        for action in (LIST, LOCATION, GET, DELETE)
    },
    "readonly": {(bucket, action) for bucket in BUCKETS for action in (GET, LOCATION)},
}


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = compose["services"]
    return result


def _secret_names(svc: dict[str, Any]) -> set[str]:
    return {s if isinstance(s, str) else s["source"] for s in svc.get("secrets", [])}


# --- compose: хто що монтує -------------------------------------------------------------------


def test_new_secrets_are_declared_as_files(compose: dict[str, Any]) -> None:
    for name in (*MINIO_SECRETS, *MONGO_SECRETS, PROVIDER_SECRET):
        assert compose["secrets"][name] == {"file": f"./deploy/compose/secrets/{name}"}, name


def test_root_credentials_only_in_their_server_and_init_one_shot(
    services: dict[str, dict[str, Any]],
) -> None:
    holders = {
        secret: {n for n, svc in services.items() if secret in _secret_names(svc)}
        for secret in ("minio_root_user", "minio_root_password", "mongo_root_password")
    }
    assert holders["minio_root_user"] == {"minio", "ensure-minio"}
    assert holders["minio_root_password"] == {"minio", "ensure-minio"}
    assert holders["mongo_root_password"] == {"mongo", "ensure-mongo"}


@pytest.mark.parametrize(
    "name", ["scheduler", "discovery-worker", "fetch-worker", "browser-worker", "parse-worker"]
)
def test_scheduler_fetcher_parser_have_no_mongo_credentials(
    services: dict[str, dict[str, Any]], name: str
) -> None:
    """§13: «scheduler/fetcher не має MongoDB credentials»; parser пише лише в PostgreSQL/S3."""
    svc = services[name]
    assert not {s for s in _secret_names(svc) if s.startswith("mongo")}, name
    assert not [k for k in svc.get("environment", {}) if "MONGO_URI" in k], name


def test_provider_credential_only_in_translation_worker_with_disabled_default(
    services: dict[str, dict[str, Any]],
) -> None:
    holders = {n for n, svc in services.items() if PROVIDER_SECRET in _secret_names(svc)}
    assert holders == {"translation-worker"}
    env = services["translation-worker"]["environment"]
    assert env["COLLECTOR_TRANSLATION_PROVIDER"] == "${COLLECTOR_TRANSLATION_PROVIDER:-disabled}"
    assert env["COLLECTOR_TRANSLATION_CREDENTIALS_FILE"] == f"/run/secrets/{PROVIDER_SECRET}"
    assert "COLLECTOR_TRANSLATION_PROJECT" in env and "COLLECTOR_TRANSLATION_LOCATION" in env


def test_every_minio_consumer_has_exactly_one_minio_credential(
    services: dict[str, dict[str, Any]],
) -> None:
    for name, svc in services.items():
        if name in {"minio", "ensure-minio"}:
            continue
        held = {s for s in _secret_names(svc) if s.startswith("minio_")}
        assert len(held) <= 1, f"{name}: {sorted(held)}"


def test_projector_waits_for_both_init_one_shots(services: dict[str, dict[str, Any]]) -> None:
    deps = services["projector-worker"]["depends_on"]
    assert deps["ensure-mongo"]["condition"] == "service_completed_successfully"
    assert deps["ensure-minio"]["condition"] == "service_completed_successfully"


def test_every_minio_consumer_waits_for_ensure_minio(services: dict[str, dict[str, Any]]) -> None:
    """Deps WP-00-to-WP-01D п.1 (resolved by orchestrator 2026-09-24): користувач MinIO існує
    до старту кожного сервісу, що монтує `minio_<component>`."""
    consumers = {
        name
        for name, svc in services.items()
        if name not in {"minio", "ensure-minio"}
        and any(s.startswith("minio_") for s in _secret_names(svc))
    }
    assert consumers >= {"api", "fetch-worker", "parse-worker", "maintenance-worker"}
    for name in consumers:
        condition = services[name]["depends_on"]["ensure-minio"]["condition"]
        assert condition == "service_completed_successfully", name


def test_new_credentials_never_inlined_into_environment(
    services: dict[str, dict[str, Any]],
) -> None:
    for name, svc in services.items():
        for key, value in svc.get("environment", {}).items():
            assert "mongodb://" not in str(value), f"{name}: {key}"
            assert "secret_key" not in str(value).lower(), f"{name}: {key}"
            if "MINIO_CREDENTIALS" in key or ("MONGO" in key and "URI" in key):
                assert key.endswith("_FILE"), f"{name}: {key}"


# --- ensure-minio: compose -----------------------------------------------------------------


def test_ensure_minio_is_a_hardened_one_shot_on_the_pinned_minio_image(
    services: dict[str, dict[str, Any]],
) -> None:
    svc = services["ensure-minio"]
    # Той самий source-built image, що й сервер; pinned `mc` збирається в ньому ж.
    assert svc["image"] == services["minio"]["image"]
    assert svc["profiles"] == ["core"]
    assert svc["restart"] == "no" and "healthcheck" not in svc
    assert svc["user"] == "10001:10001"
    assert svc["read_only"] is True and svc["init"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert any(str(t).startswith("/tmp") for t in svc["tmpfs"])  # noqa: S108 — tmpfs mount
    assert svc["environment"]["MC_CONFIG_DIR"].startswith("/tmp")  # noqa: S108
    assert svc["volumes"] == ["./deploy/compose/minio:/etc/collector/minio:ro"]
    assert svc["entrypoint"] == ["/bin/sh", "/etc/collector/minio/ensure-minio.sh"]
    assert svc["networks"] == ["backend"]
    assert "ports" not in svc
    assert svc["depends_on"] == {"minio": {"condition": "service_healthy"}}
    assert _secret_names(svc) == {"minio_root_user", "minio_root_password", *MINIO_SECRETS}
    assert svc["deploy"]["resources"]["limits"]["pids"]


def test_minio_image_builds_the_pinned_client_with_release_metadata() -> None:
    dockerfile = MINIO_DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z" in dockerfile
    assert "ARG MC_COMMIT=7394ce0dd2a80935aded936b09fa12cbb3cb8096" in dockerfile
    assert '"github.com/minio/mc@${MC_COMMIT}"' in dockerfile
    for name in ("Version", "ReleaseTag", "CommitID", "ShortCommitID", "CopyrightYear"):
        assert f"github.com/minio/mc/cmd.{name}=" in dockerfile
    assert "v0.0.0-20250813083541-7394ce0dd2a8" in dockerfile
    assert "COPY --from=builder /out/mc /usr/local/bin/mc" in dockerfile


def test_minio_names_agree_across_compose_script_policies_and_examples(
    compose: dict[str, Any],
) -> None:
    script = ENSURE_MINIO.read_text(encoding="utf-8")
    assert f'components="{" ".join(MINIO_COMPONENTS)}"' in script
    assert f'buckets="{" ".join(BUCKETS)}"' in script
    assert {p.stem for p in (MINIO_DIR / "policies").glob("*.json")} == set(MINIO_COMPONENTS)
    declared = {n for n in compose["secrets"] if n.startswith("minio_") and "root" not in n}
    assert declared == set(MINIO_SECRETS)
    for name in MINIO_SECRETS:
        assert (SECRETS_DIR / f"{name}.example").is_file(), name


# --- MinIO policies = таблиця картки --------------------------------------------------------


def _effective(policy: dict[str, Any]) -> set[tuple[str, str]]:
    granted: set[tuple[str, str]] = set()
    assert policy["Version"] == "2012-10-17"
    for statement in policy["Statement"]:
        assert statement["Effect"] == "Allow", statement
        assert set(statement) <= {"Sid", "Effect", "Action", "Resource"}, statement
        for action in statement["Action"]:
            assert "*" not in action, f"wildcard action {action}"
            for resource in statement["Resource"]:
                match = re.fullmatch(r"arn:aws:s3:::([a-z]+)(/\*)?", resource)
                assert match, resource
                bucket, objects = match.groups()
                # ListBucket/GetBucketLocation — дії над bucket-ом, решта — над об'єктами.
                assert bool(objects) == (action not in {LIST, LOCATION}), (action, resource)
                granted.add((bucket, action))
    return granted


@pytest.mark.parametrize("component", MINIO_COMPONENTS)
def test_minio_policy_grants_exactly_the_card_table(component: str) -> None:
    policy = json.loads((MINIO_DIR / "policies" / f"{component}.json").read_text("utf-8"))
    assert _effective(policy) == EXPECTED_PERMISSIONS[component]


def test_only_maintenance_and_projector_archive_may_delete() -> None:
    deleters = {
        (component, bucket)
        for component, perms in EXPECTED_PERMISSIONS.items()
        for bucket, action in perms
        if action == DELETE
    }
    assert {c for c, _ in deleters} == {"maintenance", "projector"}
    assert {b for c, b in deleters if c == "projector"} == {"archive"}
    assert ("archive", DELETE) not in EXPECTED_PERMISSIONS["maintenance"]


# --- ensure-mongo: перемикач і вартовий --------------------------------------------------------


def _ensure_mongo_script(services: dict[str, dict[str, Any]]) -> str:
    command = services["ensure-mongo"]["command"]
    assert command[:2] == ["sh", "-c"] and len(command) == 3, command
    # Compose перетворює `$$` на `$` перед запуском.
    return str(command[2]).replace("$$", "$")


def _ensure_mongo_options() -> set[str]:
    db = typer.main.get_group(app).commands["db"]
    assert isinstance(db, typer.core.TyperGroup)
    return {opt for param in db.commands["ensure-mongo"].params for opt in param.opts}


def test_ensure_mongo_mounts_root_and_all_component_uris(
    services: dict[str, dict[str, Any]],
) -> None:
    svc = services["ensure-mongo"]
    assert _secret_names(svc) == {"mongo_root_password", *MONGO_SECRETS}
    # WP-01B-to-WP-00 п.2: ролі `--users` дають права на COLLECTOR_MONGO_DATABASE, тож БД має
    # збігатися зі шляхом URI, який init-secrets.sh будує з того самого MONGO_DB.
    assert svc["environment"]["COLLECTOR_MONGO_DATABASE"] == "${MONGO_DB:-collector}"
    assert "COLLECTOR_MONGO_USER_SECRETS_DIR" not in svc["environment"]  # типовий /run/secrets
    assert svc["environment"]["COLLECTOR_MONGO_ROOT_PASSWORD_FILE"] == (
        "/run/secrets/mongo_root_password"  # noqa: S105 — шлях до secret, не значення
    )


def test_ensure_mongo_schema_switch_default_follows_cli_capability(
    services: dict[str, dict[str, Any]],
) -> None:
    """Вартовий «хто зливається другим»: щойно CLI WP-01B має `--users`, типово вмикаємо.

    До merge WP-01B PR1 `--validators/--indexes` — стаб (код 2), `--users` немає: типово 0,
    інакше стек не піднімається. Після merge лишити 0 означало б, що validators і
    Mongo-користувачі §13 мовчки не застосовуються — тест падає і вимагає перемкнути default.
    """
    default = services["ensure-mongo"]["environment"]["COLLECTOR_ENSURE_MONGO_SCHEMA"]
    options = _ensure_mongo_options()
    assert {"--validators", "--indexes"} <= options
    if "--users" in options:
        assert default == "${COLLECTOR_ENSURE_MONGO_SCHEMA:-1}", "CLI уже має --users"
    else:
        assert default == "${COLLECTOR_ENSURE_MONGO_SCHEMA:-0}", "CLI ще без --users"


# --- shell-інструменти ------------------------------------------------------------------------


def _git_bash_dir() -> Path | None:
    git = shutil.which("git")
    if not git:
        return None
    # `usr\bin` першим: `bin\bash.exe` — launcher, kill по timeout зупиняє його, а не bash.
    for root in Path(git).resolve().parents:
        for candidate in (root / "usr" / "bin", root / "bin"):
            if (candidate / "bash.exe").is_file() and (candidate / "sh.exe").is_file():
                return candidate
    return None


def _shell(name: str) -> str:
    """`bash`/`sh`: на POSIX обов'язкові (провал, не skip); на Windows — лише з Git Bash."""
    if sys.platform != "win32":
        found = shutil.which(name)
        assert found, f"{name} потрібен для init-secrets.sh / ensure-minio (§16.3)"
        return found
    directory = _git_bash_dir()
    if directory is None:
        pytest.skip("Git Bash не знайдено (лише Windows; у CI на Linux тест обов'язковий)")
    return str(directory / f"{name}.exe")


def _env(
    shell: str, extra: dict[str, str] | None = None, *, prepend: Path | None = None
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "MONGO_", "MC_"))}
    dirs: list[str] = [str(prepend)] if prepend else []
    if sys.platform == "win32":
        # `usr\bin\bash.exe` без launcher-а сам не додає утиліти MSYS/mingw у PATH.
        extra_dirs = [Path(shell).parent, Path(shell).parents[2] / "mingw64" / "bin"]
        dirs += [str(d) for d in extra_dirs if d.is_dir()]
    env["PATH"] = os.pathsep.join([*dirs, env.get("PATH", "")])
    env.update(extra or {})
    return env


def _prepare_secrets(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SECRETS_DIR / "init-secrets.sh", target / "init-secrets.sh")
    for example in SECRETS_DIR.glob("*.example"):
        shutil.copy2(example, target / example.name)


def _init_secrets_raw(
    target: Path, extra_env: dict[str, str] | None = None, *, prepare: bool = True
) -> subprocess.CompletedProcess[str]:
    if prepare:
        _prepare_secrets(target)
    bash = _shell("bash")
    return subprocess.run(  # noqa: S603 — фіксований argv, без shell
        [bash, (target / "init-secrets.sh").as_posix()],
        capture_output=True,
        encoding="utf-8",
        env=_env(bash, extra_env),
        check=False,
        timeout=180,  # Git Bash на завантаженому Windows-хості (WP-00 PR4 gate 3' low #1)
    )


def _init_secrets(target: Path, extra_env: dict[str, str] | None = None) -> str:
    proc = _init_secrets_raw(target, extra_env)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _generated(directory: Path) -> dict[str, bytes]:
    return {
        p.name: p.read_bytes()
        for p in directory.iterdir()
        if p.is_file() and p.suffix != ".example" and p.name != "init-secrets.sh"
    }


def _minio_credential(raw: bytes) -> tuple[str, str]:
    text = raw.decode("ascii")
    assert "\r" not in text and text.endswith("\n"), text[:20]
    lines = text.splitlines()
    assert len(lines) == 2, "рівно два рядки: access_key і secret_key"
    assert lines[0].startswith("access_key=") and lines[1].startswith("secret_key=")
    return lines[0].removeprefix("access_key="), lines[1].removeprefix("secret_key=")


# --- init-secrets.sh по-справжньому ----------------------------------------------------------


def test_init_secrets_generates_minio_credentials_per_component(tmp_path: Path) -> None:
    stdout = _init_secrets(tmp_path)
    keys: set[str] = set()
    for component in MINIO_COMPONENTS:
        access_key, secret_key = _minio_credential((tmp_path / f"minio_{component}").read_bytes())
        assert access_key == f"collector-{component}", component
        assert HEX40.match(secret_key), component
        assert secret_key not in stdout, f"{component}: ключ у stdout"
        keys.add(secret_key)
    assert len(keys) == len(MINIO_COMPONENTS), "secret key-і попарно різні"


def test_init_secrets_generates_mongo_uris_per_component(tmp_path: Path) -> None:
    stdout = _init_secrets(tmp_path)
    passwords: set[str] = set()
    for component in MONGO_COMPONENTS:
        raw = (tmp_path / f"mongo_uri_{component}").read_bytes().decode("ascii")
        assert raw.endswith("\n") and raw.count("\n") == 1 and "\r" not in raw, component
        url = urlsplit(raw.strip())
        assert url.scheme == "mongodb", component
        assert (url.hostname, url.port, url.path) == ("mongo", 27017, "/collector"), component
        assert url.username == f"collector_{component}", component
        assert url.password is not None and HEX48.match(url.password), component
        assert parse_qs(url.query) == {"replicaSet": ["rs0"], "authSource": ["admin"]}
        assert url.password not in stdout, component
        passwords.add(url.password)
    assert len(passwords) == len(MONGO_COMPONENTS)


def test_init_secrets_honours_mongo_host_port_db_overrides(tmp_path: Path) -> None:
    _init_secrets(tmp_path, {"MONGO_HOST": "m.internal", "MONGO_PORT": "27018", "MONGO_DB": "c2"})
    for name in MONGO_SECRETS:
        url = urlsplit((tmp_path / name).read_text(encoding="ascii").strip())
        assert (url.hostname, url.port, url.path) == ("m.internal", 27018, "/c2"), name


def test_every_generated_password_is_unique_across_all_secrets(tmp_path: Path) -> None:
    _init_secrets(tmp_path)
    files = _generated(tmp_path)
    values: dict[str, str] = {}
    for name in ("postgres_password", "mongo_root_password", "minio_root_password"):
        values[name] = files[name].decode("ascii").strip()
    for name, raw in files.items():
        if name.startswith(("postgres_dsn_", "mongo_uri_")):
            password = urlsplit(raw.decode("ascii").strip()).password
            assert password, name
            values[name] = password
    for name in MINIO_SECRETS:
        values[name] = _minio_credential(files[name])[1]
    assert len(values) == 3 + 7 + len(MONGO_SECRETS) + len(MINIO_SECRETS)
    assert len(set(values.values())) == len(values), "повторюваний пароль/ключ"


def test_init_secrets_creates_empty_provider_credential_and_never_fills_it(
    tmp_path: Path,
) -> None:
    stdout = _init_secrets(tmp_path)
    assert (tmp_path / PROVIDER_SECRET).read_bytes() == b""
    assert f"empty {PROVIDER_SECRET}" in stdout
    again = _init_secrets(tmp_path)
    assert (tmp_path / PROVIDER_SECRET).read_bytes() == b""
    assert f"keep  {PROVIDER_SECRET}" in again


def test_init_secrets_keeps_operator_provider_credential_verbatim(tmp_path: Path) -> None:
    _prepare_secrets(tmp_path)
    # Синтетичне значення в рантаймі (не секрет і не шаблон ключа).
    supplied = json.dumps({"type": "synthetic", "id": secrets.token_hex(4)}).encode() + b"\n"
    (tmp_path / PROVIDER_SECRET).write_bytes(supplied)
    stdout = _init_secrets(tmp_path)
    assert (tmp_path / PROVIDER_SECRET).read_bytes() == supplied
    assert f"skip  {PROVIDER_SECRET} (exists)" in stdout


def test_init_secrets_replaces_docker_placeholder_dir_with_empty_provider_file(
    tmp_path: Path,
) -> None:
    """Docker Desktop створює каталог на місці відсутнього file-secret (WP-00 PR4, F-1)."""
    (tmp_path / PROVIDER_SECRET).mkdir(parents=True)
    (tmp_path / "minio_fetcher").mkdir()
    stdout = _init_secrets(tmp_path)
    assert (tmp_path / PROVIDER_SECRET).is_file()
    assert (tmp_path / PROVIDER_SECRET).read_bytes() == b""
    assert f"fix   {PROVIDER_SECRET}" in stdout and "fix   minio_fetcher" in stdout
    _minio_credential((tmp_path / "minio_fetcher").read_bytes())


def test_init_secrets_is_idempotent_for_new_secrets(tmp_path: Path) -> None:
    _init_secrets(tmp_path)
    before = _generated(tmp_path)
    stdout = _init_secrets(tmp_path)
    assert _generated(tmp_path) == before
    for name in (*MINIO_SECRETS, *MONGO_SECRETS):
        assert f"skip  {name} (exists)" in stdout, name
    assert "gen " not in stdout


def test_init_secrets_adds_only_pr5_secrets_on_pre_pr5_host(tmp_path: Path) -> None:
    """Хост до PR5: наявні секрети не змінюються, додаються лише нові файли."""
    _init_secrets(tmp_path)
    for name in (*MINIO_SECRETS, *MONGO_SECRETS, PROVIDER_SECRET):
        (tmp_path / name).unlink()
    old = _generated(tmp_path)
    stdout = _init_secrets(tmp_path)
    after = _generated(tmp_path)
    assert {k: v for k, v in after.items() if k in old} == old
    assert set(after) - set(old) == {*MINIO_SECRETS, *MONGO_SECRETS, PROVIDER_SECRET}
    for name in (*MINIO_SECRETS, *MONGO_SECRETS):
        assert f"gen   {name} " in stdout, name


def test_init_secrets_rejects_unknown_example_before_writing_anything(tmp_path: Path) -> None:
    """Невідомий приклад більше не копіюється як «секрет» (fail-closed), і нічого не пишеться."""
    _prepare_secrets(tmp_path)
    (tmp_path / "surprise_token.example").write_text("placeholder\n", encoding="utf-8")
    proc = _init_secrets_raw(tmp_path, prepare=False)
    assert proc.returncode != 0
    assert "surprise_token" in proc.stderr
    assert _generated(tmp_path) == {}
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith(".")], "lock/tmp лишились"


def test_init_secrets_fails_loudly_when_generator_fails_for_minio_key(tmp_path: Path) -> None:
    """Збій генератора → exit ≠ 0, файла з порожнім/коротким ключем немає (як CR-1 PR4)."""
    secrets_dir = tmp_path / "secrets"
    _init_secrets(secrets_dir)
    for name in (*MINIO_SECRETS, *MONGO_SECRETS):
        (secrets_dir / name).unlink()
    rc = tmp_path / "failing-openssl.bash"
    rc.write_bytes(b"openssl() { return 1; }\n")
    proc = _init_secrets_raw(secrets_dir, {"BASH_ENV": rc.as_posix()}, prepare=False)
    assert proc.returncode != 0
    assert "генератор" in proc.stderr
    for name in (*MINIO_SECRETS, *MONGO_SECRETS):
        assert not (secrets_dir / name).exists(), name


# --- .example без секретів ---------------------------------------------------------------------


@pytest.mark.parametrize("name", [*MINIO_SECRETS, *MONGO_SECRETS, PROVIDER_SECRET])
def test_new_examples_are_placeholders_without_secrets(name: str) -> None:
    body = (SECRETS_DIR / f"{name}.example").read_text(encoding="utf-8")
    assert body.startswith(("GENERATED", "OPERATOR-SUPPLIED")), name
    assert not re.search(r"[0-9a-f]{16,}", body), name
    assert not re.search(r"://[^:\s]+:[^<\s][^@\s]*@", body), name
    assert "PRIVATE KEY" not in body and "private_key" not in body, name
    if name.startswith("minio_"):
        assert "secret_key=<random_hex_40>" in body, name
    if name.startswith("mongo_uri_"):
        assert ":<random_hex_48>@" in body, name


# --- ensure-minio.sh зі stub-`mc` -------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_secrets(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Один справжній запуск `init-secrets.sh` на модуль; тести отримують копію (Git Bash
    на Windows — секунди на запуск, а ensure-minio-тестам потрібен лише валідний набір)."""
    directory = tmp_path_factory.mktemp("generated") / "secrets"
    _init_secrets(directory)
    return directory


STUB_MC = """#!/usr/bin/env bash
# Stub `mc`: журнал argv, stdin `admin user add` і наявності MC_HOST_collector.
log="$MC_STUB_LOG"
printf 'argv %s\\n' "$*" >> "$log"
printf 'host %s\\n' "${MC_HOST_collector-}" >> "$log"
case "$1 $2 $3" in
  "admin user add")
    IFS= read -r ak; IFS= read -r sk
    printf 'stdin %s %s\\n' "$ak" "$sk" >> "$log" ;;
  "admin user info")
    printf '{"status":"success","accessKey":"%s","policyName":"%s%s","userStatus":"enabled"}\\n' \\
      "$5" "$5" "${MC_STUB_EXTRA_POLICY-}" ;;
esac
exit "${MC_STUB_RC:-0}"
"""


def _run_ensure_minio(
    tmp_path: Path, secrets_dir: Path, extra_env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "mc"
    stub.write_bytes(STUB_MC.encode())
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "mc.log"
    log.write_bytes(b"")
    bash = _shell("bash")
    env = _env(
        bash,
        {
            "MC_STUB_LOG": log.as_posix(),
            "COLLECTOR_MINIO_SECRETS_DIR": secrets_dir.as_posix(),
            "COLLECTOR_MINIO_POLICIES_DIR": (MINIO_DIR / "policies").as_posix(),
            "COLLECTOR_MINIO_URL": "http://minio:9000",
            **(extra_env or {}),
        },
        prepend=bindir,
    )
    proc = subprocess.run(  # noqa: S603 — фіксований argv, без shell
        [bash, ENSURE_MINIO.as_posix()],
        capture_output=True,
        encoding="utf-8",
        env=env,
        check=False,
        timeout=180,
    )
    return proc, log.read_text(encoding="utf-8").splitlines()


def test_ensure_minio_creates_buckets_users_and_policies_idempotently(
    tmp_path: Path, generated_secrets: Path
) -> None:
    secrets_dir = tmp_path / "secrets"
    shutil.copytree(generated_secrets, secrets_dir)
    proc, log = _run_ensure_minio(tmp_path, secrets_dir)
    assert proc.returncode == 0, proc.stderr
    argv = [line.removeprefix("argv ") for line in log if line.startswith("argv ")]
    assert argv[0] == "ready collector"
    for bucket in BUCKETS:
        assert f"mb --ignore-existing collector/{bucket}" in argv, bucket
    stdin = [line.split() for line in log if line.startswith("stdin ")]
    for component in MINIO_COMPONENTS:
        policy_file = (MINIO_DIR / "policies" / f"{component}.json").as_posix()
        user = f"collector-{component}"
        assert f"admin policy create collector {user} {policy_file}" in argv, component
        assert f"admin policy attach collector {user} --user {user}" in argv, component
        _, secret_key = _minio_credential((secrets_dir / f"minio_{component}").read_bytes())
        assert ["stdin", user, secret_key] in stdin, component
    assert "ensure-minio: done" in proc.stdout
    # Повторний запуск — ті самі команди, rc 0 (ідемпотентність на боці mc перевірено на стеку).
    proc2, _ = _run_ensure_minio(tmp_path, secrets_dir)
    assert proc2.returncode == 0, proc2.stderr


def test_ensure_minio_never_puts_secrets_in_argv_or_output(
    tmp_path: Path, generated_secrets: Path
) -> None:
    secrets_dir = tmp_path / "secrets"
    shutil.copytree(generated_secrets, secrets_dir)
    proc, log = _run_ensure_minio(tmp_path, secrets_dir)
    assert proc.returncode == 0, proc.stderr
    root_password = (secrets_dir / "minio_root_password").read_text(encoding="ascii").strip()
    user_keys = [_minio_credential((secrets_dir / name).read_bytes())[1] for name in MINIO_SECRETS]
    argv = "\n".join(line for line in log if line.startswith("argv "))
    for value in (root_password, *user_keys):
        assert value not in argv, "секрет в argv mc"
        assert value not in proc.stdout and value not in proc.stderr, "секрет у виводі"
    # Root — лише через MC_HOST_collector у середовищі процесу mc.
    hosts = {line.removeprefix("host ") for line in log if line.startswith("host ")}
    assert hosts == {f"http://collector-minio:{root_password}@minio:9000"}


def test_ensure_minio_fails_when_user_has_extra_policy(
    tmp_path: Path, generated_secrets: Path
) -> None:
    secrets_dir = tmp_path / "secrets"
    shutil.copytree(generated_secrets, secrets_dir)
    proc, _ = _run_ensure_minio(tmp_path, secrets_dir, {"MC_STUB_EXTRA_POLICY": ",consoleAdmin"})
    assert proc.returncode != 0
    assert "collector-fetcher" in proc.stderr and "detach" in proc.stderr


@pytest.mark.parametrize(
    ("content", "hint"),
    [
        (b"", "access_key"),
        (b"access_key=collector-fetcher\nsecret_key=short\n", "secret_key"),
        (b"access_key=someone-else\nsecret_key=" + b"a" * 40 + b"\n", "access_key"),
        (b"access_key=collector-fetcher\npassword=x\n", "невідомий рядок"),
    ],
)
def test_ensure_minio_rejects_malformed_component_secret(
    tmp_path: Path, generated_secrets: Path, content: bytes, hint: str
) -> None:
    secrets_dir = tmp_path / "secrets"
    shutil.copytree(generated_secrets, secrets_dir)
    (secrets_dir / "minio_fetcher").write_bytes(content)
    proc, log = _run_ensure_minio(tmp_path, secrets_dir)
    assert proc.returncode != 0
    assert hint in proc.stderr
    assert not [line for line in log if "user add" in line], "користувача не створено"


def test_ensure_minio_fails_on_missing_root_secret(tmp_path: Path, generated_secrets: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    shutil.copytree(generated_secrets, secrets_dir)
    (secrets_dir / "minio_root_password").unlink()
    proc, log = _run_ensure_minio(tmp_path, secrets_dir)
    assert proc.returncode != 0
    assert "minio_root_password" in proc.stderr
    assert log == [], "mc не викликається без root"


def test_ensure_minio_propagates_mc_failure(tmp_path: Path, generated_secrets: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    shutil.copytree(generated_secrets, secrets_dir)
    proc, _ = _run_ensure_minio(tmp_path, secrets_dir, {"MC_STUB_RC": "7"})
    assert proc.returncode != 0


def test_ensure_minio_script_is_lf_and_strict() -> None:
    raw = ENSURE_MINIO.read_bytes()
    assert b"\r" not in raw
    assert raw.startswith(b"#!/bin/sh\n")
    assert b"set -eu" in raw
    # `mc alias set` приймає секрет в argv — заборонено.
    assert b"alias set" not in raw


def test_ensure_minio_script_is_valid_posix_shell() -> None:
    proc = subprocess.run(  # noqa: S603 — фіксований локальний shell і шлях до скрипту
        [_shell("sh"), "-n", ENSURE_MINIO.as_posix()],
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


# --- ensure-mongo: дослівна команда з compose у POSIX sh ------------------------------------


def _run_ensure_mongo(
    tmp_path: Path, services: dict[str, dict[str, Any]], schema: str
) -> tuple[int, str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    journal = tmp_path / "journal.txt"
    stub = bindir / "collector"
    stub.write_bytes(f'#!/bin/sh\necho "$*" >> "{journal.as_posix()}"\nexit 0\n'.encode())
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    sh = _shell("sh")
    env = _env(sh, {"COLLECTOR_ENSURE_MONGO_SCHEMA": schema}, prepend=bindir)
    proc = subprocess.run(  # noqa: S603 — фіксований argv
        [sh, "-c", _ensure_mongo_script(services)],
        capture_output=True,
        encoding="utf-8",
        env=env,
        check=False,
        timeout=180,
    )
    text = journal.read_text(encoding="utf-8") if journal.exists() else ""
    return proc.returncode, text, proc.stderr


def test_ensure_mongo_switch_off_runs_replica_set_only(
    tmp_path: Path, services: dict[str, dict[str, Any]]
) -> None:
    rc, journal, _ = _run_ensure_mongo(tmp_path, services, "0")
    assert rc == 0
    assert journal.splitlines() == ["db ensure-mongo"]


def test_ensure_mongo_switch_on_adds_validators_indexes_users(
    tmp_path: Path, services: dict[str, dict[str, Any]]
) -> None:
    rc, journal, _ = _run_ensure_mongo(tmp_path, services, "1")
    assert rc == 0
    assert journal.splitlines() == ["db ensure-mongo --validators --indexes --users"]


@pytest.mark.parametrize("value", ["yes", "", "1 --drop"])
def test_ensure_mongo_switch_rejects_unknown_values(
    tmp_path: Path, services: dict[str, dict[str, Any]], value: str
) -> None:
    rc, journal, stderr = _run_ensure_mongo(tmp_path, services, value)
    assert rc == 2
    assert journal == "", "collector не запускається з невідомим перемикачем"
    assert "COLLECTOR_ENSURE_MONGO_SCHEMA" in stderr
