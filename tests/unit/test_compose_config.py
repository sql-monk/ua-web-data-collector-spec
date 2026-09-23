"""Unit-тести Compose/Docker-конфігурації як артефакту (WP-00 PR2/PR3; §7.5, §13, R-51/R-55).

Без Docker daemon і без docker CLI: читаємо `docker-compose.yml` через PyYAML (merge keys
`<<` резолвляться), `Dockerfile`, `.dockerignore`, `deploy/compose/dev.override.yml` і
`deploy/compose/secrets/`. Рендер через `docker compose config --format json` перевіряє
`tests/integration/test_compose_render.py` (потребує docker CLI).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
DEV_OVERRIDE_PATH = REPO_ROOT / "deploy" / "compose" / "dev.override.yml"
SECRETS_DIR = REPO_ROOT / "deploy" / "compose" / "secrets"

# §7.5 таблиця profiles → services (observability/tools ще без сервісів).
SPEC_7_5_PROFILES: dict[str, set[str]] = {
    "core": {"postgres", "mongo", "minio", "migrate-postgres", "ensure-mongo", "api", "scheduler"},
    "workers": {
        "discovery-worker",
        "fetch-worker",
        "parse-worker",
        "projector-worker",
        "translation-worker",
        "export-worker",
        "maintenance-worker",
    },
    "browser": {"browser-worker"},
    "gui": {"gui"},
}
WORKERS = SPEC_7_5_PROFILES["workers"] | SPEC_7_5_PROFILES["browser"]
STATEFUL = {"postgres", "mongo", "minio"}
ONE_SHOTS = {"migrate-postgres", "ensure-mongo"}
SPEC_7_5_NETWORKS = {
    "ingress",
    "frontend",
    "backend",
    "source-egress",
    "provider-egress",
    "telemetry",
}
# §7.6 default replicas.
SPEC_7_6_REPLICAS = {
    "discovery-worker": 1,
    "fetch-worker": 2,
    "browser-worker": 0,
    "parse-worker": 2,
    "projector-worker": 1,
    "translation-worker": 1,
    "export-worker": 1,
    "maintenance-worker": 1,
}
PINNED_IMAGE = re.compile(r"^[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}$")
SECRET_ENV_KEY = re.compile(r"(?i)(password|secret|token|api[_-]?key)$")


def _interpolate_defaults(value: str) -> str:
    """`${VAR:-default}` → `default`, як це зробить Compose без env."""
    return re.sub(r"\$\{[^:}]+:-([^}]*)\}", r"\1", value)


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return _load(COMPOSE_PATH)


@pytest.fixture(scope="module")
def services(compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = compose["services"]
    return result


def _seconds(value: str | int) -> float:
    if isinstance(value, int):
        return float(value)
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", value)
    assert match, value
    minutes, seconds = match.groups()
    return int(minutes or 0) * 60 + int(seconds or 0)


def _networks(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks", [])
    return set(networks) if isinstance(networks, list) else set(networks.keys())


# --- profiles / services --------------------------------------------------------------------


def test_services_match_spec_7_5_profile_table(services: dict[str, dict[str, Any]]) -> None:
    for profile, expected in SPEC_7_5_PROFILES.items():
        actual = {name for name, svc in services.items() if profile in svc.get("profiles", [])}
        assert actual == expected, profile
    all_declared = set().union(*SPEC_7_5_PROFILES.values())
    assert set(services) == all_declared, "сервіси поза таблицею §7.5"
    assert all(services[name].get("profiles") for name in services), "сервіс без profile"


def test_reserved_profiles_are_documented(compose: dict[str, Any]) -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    for profile in ("observability", "tools"):
        assert re.search(rf"^#.*\b{profile}\b", text, re.MULTILINE), profile


# --- workers (§7.5): без container_name / host ports / persistent state, масштабовані ----------


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_worker_is_scalable(services: dict[str, dict[str, Any]], name: str) -> None:
    svc = services[name]
    assert "container_name" not in svc
    assert "ports" not in svc
    assert "volumes" not in svc, "worker не має local persistent state"
    assert svc["deploy"]["replicas"] == SPEC_7_6_REPLICAS[name]
    assert _seconds(svc["stop_grace_period"]) >= 90
    assert svc["labels"]["collector.scalable"] == "true"
    assert svc["command"][:2] == ["collector", "worker"]
    assert svc["command"][2] == name.removesuffix("-worker")


def test_scheduler_is_singleton(services: dict[str, dict[str, Any]]) -> None:
    scheduler = services["scheduler"]
    assert scheduler["deploy"]["replicas"] == 1
    assert scheduler["labels"]["collector.scalable"] == "false"
    assert scheduler["command"] == ["collector", "scheduler"]


def test_no_container_name_anywhere(services: dict[str, dict[str, Any]]) -> None:
    assert not [name for name, svc in services.items() if "container_name" in svc]


# --- R-55 / §13: Docker socket, published ports, secrets ------------------------------------


def test_no_docker_socket_mount_anywhere() -> None:
    for path in (COMPOSE_PATH, DEV_OVERRIDE_PATH):
        assert "docker.sock" not in path.read_text(encoding="utf-8"), path.name


def test_only_gui_publishes_a_port(services: dict[str, dict[str, Any]]) -> None:
    """§7.5/§13: єдиний публічний ingress стека — gui; решта портів лише у dev.override.yml."""
    assert [name for name, svc in services.items() if "ports" in svc] == ["gui"]
    ports = services["gui"]["ports"]
    assert ports == ["${GUI_PORT:-80}:8080"], ports


def test_dev_override_binds_only_loopback() -> None:
    override = _load(DEV_OVERRIDE_PATH)
    assert set(override["services"]) <= STATEFUL | {"api"}
    for name, svc in override["services"].items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name}: {port}"


def test_secrets_are_files_with_examples_and_gitignored(compose: dict[str, Any]) -> None:
    declared = compose["secrets"]
    assert declared, "secrets мають бути оголошені"
    for name, spec in declared.items():
        file = Path(spec["file"])
        assert file.parts[:3] == ("deploy", "compose", "secrets"), name
        assert file.name == name
        assert (SECRETS_DIR / f"{name}.example").is_file(), f"немає {name}.example"
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", spec["file"]],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        )
        assert ignored.returncode == 0, f"{spec['file']} не в .gitignore"
    tracked = subprocess.run(
        ["git", "ls-files", "deploy/compose/secrets"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert all(f.endswith((".example", "init-secrets.sh")) for f in tracked), tracked


def test_services_reference_only_declared_secrets_and_no_secret_env(
    compose: dict[str, Any], services: dict[str, dict[str, Any]]
) -> None:
    declared = set(compose["secrets"])
    for name, svc in services.items():
        for secret in svc.get("secrets", []):
            key = secret if isinstance(secret, str) else secret["source"]
            assert key in declared, f"{name}: {key}"
        env = svc.get("environment", {})
        assert isinstance(env, dict), name
        for key in env:
            assert not SECRET_ENV_KEY.search(key), f"{name}: {key} має бути *_FILE (Docker secret)"


# --- application containers (§7.5, §13, FR-030) ----------------------------------------------


def _is_application(svc: dict[str, Any]) -> bool:
    return _interpolate_defaults(str(svc.get("image", ""))).startswith("collector:")


def test_application_services_are_read_only_non_root_with_tmpfs(
    services: dict[str, dict[str, Any]],
) -> None:
    app_services = {name for name, svc in services.items() if _is_application(svc)}
    # gui — окремий image (nginx), його інваріанти перевіряє test_gui_* нижче.
    assert app_services == set(services) - STATEFUL - {"gui"}
    for name in app_services:
        svc = services[name]
        assert svc["read_only"] is True, name
        assert svc["user"] == "10001:10001", name
        # S108: перевірка tmpfs-монтування контейнера, а не tmp-шлях у тесті.
        assert any(str(t).startswith("/tmp") for t in svc["tmpfs"]), name  # noqa: S108
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
        assert "volumes" not in svc, f"{name}: application image без volumes"
        assert svc["init"] is True, name


def test_every_service_has_resource_limits(services: dict[str, dict[str, Any]]) -> None:
    for name, svc in services.items():
        limits = svc["deploy"]["resources"]["limits"]
        assert limits.get("cpus") and limits.get("memory"), name


def test_long_running_services_have_healthcheck_and_grace_period(
    services: dict[str, dict[str, Any]],
) -> None:
    for name, svc in services.items():
        if name in ONE_SHOTS:
            assert svc["restart"] == "no", name
            assert "healthcheck" not in svc, name
            continue
        assert svc["healthcheck"]["test"], name
        assert "stop_grace_period" in svc, name


def test_readiness_waits_for_one_shots(services: dict[str, dict[str, Any]]) -> None:
    api = services["api"]["depends_on"]
    for one_shot in ONE_SHOTS:
        assert api[one_shot]["condition"] == "service_completed_successfully"
    for stateful in STATEFUL:
        assert api[stateful]["condition"] == "service_healthy"
    for name in SPEC_7_5_PROFILES["workers"] | {"scheduler"}:
        deps = services[name]["depends_on"]
        assert deps["migrate-postgres"]["condition"] == "service_completed_successfully", name
    assert services["migrate-postgres"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["ensure-mongo"]["depends_on"]["mongo"]["condition"] == "service_healthy"


def test_one_shot_commands_match_spec_16_2(services: dict[str, dict[str, Any]]) -> None:
    assert services["migrate-postgres"]["command"] == ["collector", "db", "migrate"]
    assert services["ensure-mongo"]["command"] == ["collector", "db", "ensure-mongo"]
    assert services["api"]["command"] == ["collector", "api"]


# --- мережі (§7.5) --------------------------------------------------------------------------


def test_networks_match_spec_7_5(compose: dict[str, Any]) -> None:
    networks = compose["networks"]
    assert set(networks) == SPEC_7_5_NETWORKS
    assert networks["backend"]["internal"] is True
    assert networks["telemetry"]["internal"] is True
    for egress in ("ingress", "source-egress", "provider-egress"):
        assert not networks[egress].get("internal"), egress


def test_service_network_placement(services: dict[str, dict[str, Any]]) -> None:
    for name in ("discovery-worker", "fetch-worker", "browser-worker"):
        assert _networks(services[name]) == {"backend", "source-egress"}, name
    assert _networks(services["translation-worker"]) == {"backend", "provider-egress"}
    for name in ("parse-worker", "projector-worker", "export-worker", "maintenance-worker"):
        assert _networks(services[name]) == {"backend"}, name
    # gate 3 CR-14/SEC L-2: api без ingress; gui↔api — окрема internal-мережа frontend.
    assert _networks(services["api"]) == {"backend", "frontend"}
    assert _networks(services["gui"]) == {"ingress", "frontend"}
    for name in STATEFUL | ONE_SHOTS | {"scheduler"}:
        assert _networks(services[name]) == {"backend"}, name
    on_ingress = {name for name, svc in services.items() if "ingress" in _networks(svc)}
    assert on_ingress == {"gui"}, "у ingress лише gui — єдиний публічний сервіс"
    assert "backend" not in _networks(services["gui"]), "gui не бачить БД/object store"


# --- stateful (§7.5, §8): pinned digests, named volumes ------------------------------------


@pytest.mark.parametrize("name", sorted(STATEFUL))
def test_stateful_image_pinned_by_digest_and_named_volumes(
    compose: dict[str, Any], services: dict[str, dict[str, Any]], name: str
) -> None:
    svc = services[name]
    assert PINNED_IMAGE.match(svc["image"]), svc["image"]
    volumes = svc["volumes"]
    assert volumes, f"{name}: stateful без named volume"
    named = [v for v in volumes if not str(v).startswith(".")]
    assert named, f"{name}: дані мають бути в named volume"
    for volume in named:
        source = str(volume).split(":", 1)[0]
        assert source in compose["volumes"], f"{name}: {source} не named volume"
    # Єдиний дозволений bind — read-only конфігурація з репозиторію (init-скрипти WP-01A).
    for volume in volumes:
        if str(volume).startswith("."):
            assert str(volume).endswith(":ro"), f"{name}: bind {volume} має бути read-only"
            assert str(volume).startswith("./deploy/"), volume
    assert "profiles" in svc and svc["profiles"] == ["core"]


def test_stateful_versions_match_spec_8(services: dict[str, dict[str, Any]]) -> None:
    assert services["postgres"]["image"].startswith("postgres:18@")
    assert services["mongo"]["image"].startswith("mongo:8.0@")
    assert "--replSet" in "".join(services["mongo"]["entrypoint"])
    assert "--keyFile" in "".join(services["mongo"]["entrypoint"])
    assert "minio" in services["minio"]["image"]


def test_named_volumes_only_for_stateful(compose: dict[str, Any]) -> None:
    used = {
        str(v).split(":", 1)[0]
        for svc in compose["services"].values()
        for v in svc.get("volumes", [])
        if not str(v).startswith(".")  # bind-и конфігів перевіряє тест вище
    }
    assert used == set(compose["volumes"])


def test_postgres_init_scripts_are_mounted_read_only_for_wp_01a(
    services: dict[str, dict[str, Any]],
) -> None:
    """Approved dependency WP-01A: NOLOGIN group-ролі §13 при першому старті кластера.

    Після merge WP-01A PR1 каталог містить `01-roles.sql` (створення ролей). Тест стежить, щоб
    у initdb не потрапило те, що туди не можна: GRANT (потребує таблиць, які зʼявляються лише
    після `collector db migrate`) і будь-які паролі/LOGIN-ролі.
    """
    mounts = [str(v) for v in services["postgres"]["volumes"]]
    assert "./deploy/compose/postgres/init:/docker-entrypoint-initdb.d:ro" in mounts
    init_dir = REPO_ROOT / "deploy" / "compose" / "postgres" / "init"
    assert init_dir.is_dir() and (init_dir / "README.md").is_file()
    scripts = sorted(path.name for path in init_dir.glob("*.sql"))
    assert scripts == ["01-roles.sql"], scripts
    body = (init_dir / "01-roles.sql").read_text(encoding="utf-8")
    assert "CREATE ROLE %I NOLOGIN" in body
    # Перевіряємо виконуваний SQL, не коментарі (вони пояснюють, де живе повний скрипт).
    statements = " ".join(
        line for line in body.splitlines() if not line.lstrip().startswith("--")
    ).replace("NOLOGIN", "")
    for forbidden in ("GRANT", "PASSWORD", "ALTER TABLE", "ALTER FUNCTION"):
        assert forbidden not in statements, forbidden


def test_postgres_dsn_secret_is_scoped_to_migration_and_queue_consumers(
    compose: dict[str, Any], services: dict[str, dict[str, Any]]
) -> None:
    """DSN отримують лише ті, хто справді ходить у PostgreSQL: one-shot міграцій і runtime.

    WP-01D PR1: `scheduler` (advisory lease + maintenance) і `*-worker` (claim/lease/heartbeat)
    читають чергу, тому DSN їм потрібен. `api`, stateful і Mongo-one-shot його не бачать.
    Окремі per-component DSN (§13) чекають на LOGIN-ролі —
    docs/plan/deps/WP-01D-to-WP-01A.md.
    """
    assert "postgres_dsn" in compose["secrets"]
    migrate = services["migrate-postgres"]
    assert migrate["secrets"] == ["postgres_dsn"]
    assert migrate["environment"]["COLLECTOR_POSTGRES_DSN_FILE"] == "/run/secrets/postgres_dsn"
    allowed = {"migrate-postgres", "scheduler"} | WORKERS
    for name, svc in services.items():
        holds_dsn = "postgres_dsn" in [
            s if isinstance(s, str) else s["source"] for s in svc.get("secrets", [])
        ]
        assert holds_dsn == (name in allowed), name
    # `collector db roles` ще немає в main — у compose лише коментар-нагадування.
    assert migrate["command"] == ["collector", "db", "migrate"]
    assert "collector db roles" in COMPOSE_PATH.read_text(encoding="utf-8")


def test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire() -> None:
    """§13 (знахідка F1 gate 2): runtime-процеси тимчасово ходять у PG тим самим DSN, що й
    міграції, — бо LOGIN-ролі per component ще не існують.

    Це навмисно **fail-loud** тест-вартовий, а не документація: щойно у `roles.sql` зʼявиться
    хоч одна LOGIN-роль (WP-01A PR2, dependency-запит `docs/plan/deps/WP-01D-to-WP-01A.md` §2),
    він упаде і змусить повернути §13-інваріант — per-role DSN для `scheduler` і `*-worker`
    замість спільного superuser-секрету `postgres_dsn`.
    """
    roles_sql = (
        REPO_ROOT / "src" / "collector" / "persistence" / "postgres" / "sql" / "roles.sql"
    ).read_text(encoding="utf-8")
    statements = " ".join(
        line for line in roles_sql.splitlines() if not line.lstrip().startswith("--")
    )
    # Lookbehind відсікає `NOLOGIN`: спрацювати має лише справжня LOGIN-роль.
    assert not re.search(r"(?<![A-Z])LOGIN", statements), (
        "у roles.sql зʼявилися LOGIN-ролі — поверніть §13-інваріант: worker/scheduler мають "
        "отримати власні per-role DSN, а не спільний secret postgres_dsn "
        "(docs/plan/deps/WP-01D-to-WP-01A.md §2)"
    )
    deps = REPO_ROOT / "docs" / "plan" / "deps" / "WP-01D-to-WP-01A.md"
    assert deps.is_file(), "тимчасове відхилення від §13 має лишатись оформленим запитом"
    assert "LOGIN" in deps.read_text(encoding="utf-8")


def test_dockerfile_optionally_copies_alembic_and_migrations() -> None:
    """Approved dependency WP-01A: файли з'являться після merge — COPY має бути опційним."""
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "alembic.in[i]" in text and "migration[s]/" in text
    assert "COLLECTOR_ALEMBIC_INI=/app/alembic.ini" in text
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!alembic.ini" in ignore and "!migrations/" in ignore


# --- Dockerfile / .dockerignore (§7.5, §13) -------------------------------------------------


def test_dockerfile_is_multistage_pinned_non_root_without_secrets() -> None:
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    image_args = dict(re.findall(r"^ARG (\w+_IMAGE)=(\S+)$", text, re.MULTILINE))
    assert set(image_args) == {"PYTHON_IMAGE", "UV_IMAGE"}
    for value in image_args.values():
        assert PINNED_IMAGE.match(value), value
    assert image_args["PYTHON_IMAGE"].startswith("python:3.13-slim@")
    assert re.search(r"^FROM \$\{PYTHON_IMAGE\} AS builder$", text, re.MULTILINE)
    assert re.search(r"^FROM \$\{PYTHON_IMAGE\} AS runtime$", text, re.MULTILINE)
    assert "uv sync --frozen --no-dev" in text
    assert re.search(r"^USER 10001:10001$", text, re.MULTILINE)
    assert re.search(r"^HEALTHCHECK ", text, re.MULTILINE)
    assert "org.opencontainers.image.revision" in text
    assert "ua.collector.schema-version" in text
    for line in text.splitlines():
        if line.startswith(("ARG ", "ENV ")):
            assert not SECRET_ENV_KEY.search(line.split("=", 1)[0]), line


def test_dockerignore_excludes_everything_but_build_inputs() -> None:
    lines = [
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines[0] == "*"
    allowed = {line[1:] for line in lines if line.startswith("!")}
    assert allowed == {
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "README.md",
        "src/",
        # WP-01C (approved dependency): реєстр джерел у image для валідації source_id.
        "docs/research/source-registry.yaml",
        # WP-01A (approved dependency): Alembic-конфіг і міграції; з'являться після merge,
        # COPY у Dockerfile опційний.
        "alembic.ini",
        "migrations/",
    }


def test_dockerfile_ships_source_registry_for_wp_01c() -> None:
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"^COPY .*docs/research/source-registry\.yaml /app/config/source-registry\.yaml$",
        text,
        re.MULTILINE,
    )
    assert "COLLECTOR_SOURCE_REGISTRY=/app/config/source-registry.yaml" in text
    assert (REPO_ROOT / "docs" / "research" / "source-registry.yaml").is_file()


def test_ci_runs_compose_config_and_image_build() -> None:
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    run_steps = [
        step["run"] for job in ci["jobs"].values() for step in job["steps"] if "run" in step
    ]
    assert any("docker compose config --quiet" in run for run in run_steps)
    assert any("docker compose build" in run or "docker build" in run for run in run_steps)
    assert any("up -d --wait" in run for run in run_steps)
    uses = [step.get("uses", "") for job in ci["jobs"].values() for step in job["steps"]]
    assert any("sbom" in u for u in uses), "SBOM step (syft) відсутній"
    assert any("trivy" in u for u in uses), "vulnerability scan (trivy) відсутній"


def test_mongo_healthcheck_is_robust_to_entrypoint_init_phase(
    services: dict[str, dict[str, Any]],
) -> None:
    """Gate 2, H-1: init-mongod слухає лише loopback і без --replSet — healthcheck його омине."""
    healthcheck = services["mongo"]["healthcheck"]
    test = " ".join(map(str, healthcheck["test"]))
    # Gate 3 CR-1: перша IPv4 з `hostname -I` явно (dual-stack: `hostname -i` віддає й IPv6).
    assert "hostname -I" in test and "hostname -i" not in test, "явно IPv4, не hostname -i"
    assert "grep -m1 -E '^[0-9]+(\.[0-9]+){3}$$'" in test, test  # `$$` — escape Compose
    assert 'test -n "$' in test, "порожня адреса → healthcheck fail, не mongosh без --host"
    assert "127.0.0.1" not in test and "localhost" not in test
    assert "isreplicaset" in test and "setName" in test, "ознака члена RS, не лише ping"
    # Не вимагати primary: до `ensure-mongo` (depends_on service_healthy) член ще не primary.
    assert "isWritablePrimary" not in test
    assert _seconds(healthcheck["start_period"]) >= 40
    assert _seconds(healthcheck["timeout"]) >= 10
    assert healthcheck["retries"] >= 10


def test_dockerfile_copies_registry_with_explicit_mode() -> None:
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert re.search(r"^COPY .*--chmod=0644 .*source-registry\.yaml", text, re.MULTILINE)


def test_ci_trivy_critical_without_ignore_unfixed_and_high_with() -> None:
    """Обидва образи (`collector` і `collector-gui`) скануються за однаковою політикою §13."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    scans = [s["with"] for s in ci["jobs"]["docker"]["steps"] if "trivy" in (s.get("uses") or "")]
    by_image: dict[str, dict[str, Any]] = {}
    for scan in scans:
        by_image.setdefault(scan["image-ref"], {})[scan["severity"]] = scan
    assert set(by_image) == {"collector:ci", "collector-gui:ci"}, by_image.keys()
    for image, by_severity in by_image.items():
        assert set(by_severity) == {"CRITICAL", "HIGH"}, image
        assert "ignore-unfixed" not in by_severity["CRITICAL"], (
            f"{image}: §13 — unfixed CRITICAL не пропускати"
        )
        assert str(by_severity["CRITICAL"]["exit-code"]) == "1", image
        assert by_severity["HIGH"]["ignore-unfixed"] is True, image
        assert str(by_severity["HIGH"]["exit-code"]) == "1", image


def test_ci_generates_sbom_for_both_images() -> None:
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    sboms = [s["with"] for s in ci["jobs"]["docker"]["steps"] if "sbom" in (s.get("uses") or "")]
    assert {s["image"] for s in sboms} == {"collector:ci", "collector-gui:ci"}
    assert all(s["upload-artifact"] for s in sboms)


def test_ci_web_job_audits_npm_dependencies() -> None:
    """Gate 2, H-1 / §13: залежності GUI скануються на кожен PR, HIGH+ блокує."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    steps = ci["jobs"]["web"]["steps"]
    names = [s.get("name", "") for s in steps]
    runs = [s.get("run", "") for s in steps]
    audit = [r for r in runs if "npm audit" in r]
    assert audit, "немає кроку npm audit"
    joined = " ".join(audit)
    assert "--audit-level=high" in joined, "HIGH/CRITICAL мають блокувати"
    assert "--omit=dev" in joined, "runtime-залежності (bundle у браузері) — окремо"
    # Аудит іде після `npm ci` (по lockfile), але до lint/test/build — щоб PR падав швидко.
    audit_at = next(i for i, r in enumerate(runs) if "npm audit" in r)
    ci_at = next(i for i, r in enumerate(runs) if r.strip() == "npm ci")
    lint_at = next(i for i, r in enumerate(runs) if "npm run lint" in r)
    assert ci_at < audit_at < lint_at, names


# --- gate 3 (код-рев'ю / security-рев'ю) -----------------------------------------------------


def test_every_service_has_pids_limit_and_log_rotation(services: dict[str, dict[str, Any]]) -> None:
    """SEC L-1/L-5, CR-7: fork-exhaustion і log flood обмежені для всіх, включно зі stateful."""
    for name, svc in services.items():
        pids = svc["deploy"]["resources"]["limits"]["pids"]
        assert pids == (1024 if name in STATEFUL else 256), name
        logging = svc["logging"]
        assert logging["driver"] == "json-file" and logging["options"]["max-size"], name


def test_minio_is_capability_dropped_and_read_only(services: dict[str, dict[str, Any]]) -> None:
    """SEC M-1: root лишається (vendor image), але без capabilities і з read-only rootfs."""
    minio = services["minio"]
    assert minio["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in minio["security_opt"]
    assert minio["read_only"] is True
    assert any(str(t).startswith("/tmp") for t in minio["tmpfs"])  # noqa: S108 — tmpfs mount
    assert minio["environment"]["MC_CONFIG_DIR"].startswith("/tmp")  # noqa: S108
    for name in STATEFUL:
        assert services[name]["cap_drop"] == ["ALL"], name


def test_worker_depends_on_covers_its_healthcheck_dependencies(
    services: dict[str, dict[str, Any]],
) -> None:
    """CR-6: усе, що перевіряє healthcheck worker-а, є в depends_on як service_healthy."""
    for name in SPEC_7_5_PROFILES["workers"]:
        svc = services[name]
        checked = {c for c in ("postgres", "mongo", "minio") if c in svc["healthcheck"]["test"]}
        deps = svc["depends_on"]
        for component in checked:
            assert deps[component]["condition"] == "service_healthy", f"{name}: {component}"


def test_init_secrets_generates_random_passwords_not_examples() -> None:
    """SEC L-3: жодних default credentials — паролі генеруються, приклади не копіюються."""
    script = (SECRETS_DIR / "init-secrets.sh").read_text(encoding="utf-8")
    assert "openssl rand -hex" in script and "*_password)" in script
    assert "random_hex > " in script
    for example in SECRETS_DIR.glob("*_password.example"):
        assert "GENERATED" in example.read_text(encoding="utf-8"), example.name
    # DSN будується з того самого згенерованого пароля, не копіюється з прикладу.
    assert "postgres_dsn)" in script and 'cat "$here/postgres_password"' in script
    dsn_example = (SECRETS_DIR / "postgres_dsn.example").read_text(encoding="utf-8")
    assert "GENERATED" in dsn_example
    assert (
        (SECRETS_DIR / "mongo_keyfile.example")
        .read_text(encoding="utf-8")
        .startswith("GENERATE-ME")
    )


def test_ci_health_assert_parses_ps_json_not_grep() -> None:
    """CR-2: `grep -vc healthy` рахував unhealthy як healthy — тепер явний парсер."""
    ci_text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "grep -vc healthy" not in ci_text
    assert "--format json | python3 deploy/compose/check-healthy.py" in ci_text
    assert (REPO_ROOT / "deploy" / "compose" / "check-healthy.py").is_file()


def _pytest_invocations(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Кроки job-а, які запускають pytest, разом з їхнім env."""
    return [s for s in job["steps"] if "pytest" in s.get("run", "")]


def _marker_selector(run: str) -> str:
    """Вираз після `-m` у команді pytest."""
    match = re.search(r'-m\s+"([^"]+)"', run)
    assert match, run
    return match.group(1)


def test_ci_python_job_shows_skips() -> None:
    """CR-3: render-тести виконуються у job python; skip видимий (-rs)."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    runs = [s.get("run", "") for s in ci["jobs"]["python"]["steps"]]
    assert any("pytest" in r and "-rs" in r for r in runs), "skip має бути видимим (-rs)"


# Модулі, які фізично потребують піднятого стека / зібраного образу gui.
GUI_RUNTIME_TEST_MODULES = (
    "tests/e2e/test_gui_runtime_contract.py",
    "tests/e2e/test_gui_api_down_branch.py",
    "tests/e2e/test_runtime_suite_is_enforced.py",
)


def test_ci_python_job_does_not_collect_gui_runtime_tests() -> None:
    """Пострев'ю S-1: у job `python` немає стека, тому runtime-тести gui там не збираються.

    Перевіряється інваріант, а не дослівний рядок команди. Виключення — по шляху, а не
    селектором `-m "not e2e"`: §16.2 фіксує `uv run pytest -m "not live"` дослівно, а маркер
    `e2e` носитимуть і offline-тести WP-14, які мають виконуватись саме тут.

    Друга половина інваріанта: прапорця заборони skip у цьому job немає. Саме прив'язка до
    універсальної `CI` (її GitHub Actions ставить в усіх job-ах) валила job `python`.
    """
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    job = ci["jobs"]["python"]
    invocations = _pytest_invocations(job)
    assert invocations, "job python не запускає pytest"
    for step in invocations:
        run = step["run"]
        selector = _marker_selector(run)
        assert "not live" in selector, selector
        for module in GUI_RUNTIME_TEST_MODULES:
            assert f"--ignore={module}" in run, f"{module} збирається у job python"
        assert "COLLECTOR_E2E_REQUIRED" not in str(step.get("env", {}))
    assert "COLLECTOR_E2E_REQUIRED" not in str(job.get("env", {}))


def test_ci_python_job_keeps_spec_16_2_command_verbatim() -> None:
    """§16.2: CI має виконувати саме задокументовану команду, а не її звужений варіант."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    runs = [s.get("run", "") for s in ci["jobs"]["python"]["steps"]]
    assert any('uv run pytest -m "not live"' in r for r in runs)


def test_ci_docker_job_forbids_skipping_e2e_after_stack_is_up() -> None:
    """Пострев'ю S-1: заборону skip вмикає власний прапорець, і лише там, де є стек."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    job = ci["jobs"]["docker"]
    e2e_steps = [s for s in _pytest_invocations(job) if "e2e" in s["run"]]
    assert len(e2e_steps) == 1, "рівно один крок з runtime-тестами gui"
    step = e2e_steps[0]
    assert step["env"]["COLLECTOR_E2E_REQUIRED"] in {"1", 1}, step.get("env")
    # Прапорець — крокового рівня: на рівні job-а він увімкнув би заборону ще до `up --wait`.
    assert "COLLECTOR_E2E_REQUIRED" not in str(job.get("env", {}))
    assert "COLLECTOR_E2E_REQUIRED" not in str(ci.get("env", {})), "не глобально"
    # Той самий прапорець читають самі модулі — інакше заборона нічого не вмикає.
    for name in ("test_gui_runtime_contract.py", "test_gui_api_down_branch.py"):
        source = (REPO_ROOT / "tests" / "e2e" / name).read_text(encoding="utf-8")
        assert "COLLECTOR_E2E_REQUIRED" in source, name
        assert 'os.environ.get("CI"' not in source, f"{name}: універсальна CI знову у гейті"


def test_build_contract_gate_does_not_depend_on_universal_ci() -> None:
    """Пострев'ю S-1, той самий клас помилки на боці web: `npm run test` іде до `build`."""
    package_json = (REPO_ROOT / "web" / "package.json").read_text(encoding="utf-8")
    assert "--mode build-contract" in package_json, "скрипт test:build має задавати режим"
    source = (REPO_ROOT / "web" / "tests" / "unit" / "build-contract.test.ts").read_text(
        encoding="utf-8"
    )
    assert "import.meta.env.MODE === 'build-contract'" in source
    assert "process.env.CI" not in source, "CI=true у job web валив би звичайний `npm run test`"


# --- GUI (WP-00 PR3; §7.7, §8, §13) ----------------------------------------------------------

GUI_DOCKERFILE_PATH = REPO_ROOT / "web" / "Dockerfile"
GUI_NGINX_CONF_PATH = REPO_ROOT / "deploy" / "compose" / "gui" / "nginx.conf"


def test_gui_container_is_non_root_read_only_without_secrets(
    compose: dict[str, Any], services: dict[str, dict[str, Any]]
) -> None:
    """§13: єдиний публічний сервіс має найжорсткіший runtime-профіль."""
    gui = services["gui"]
    assert gui["profiles"] == ["gui"]
    assert gui["user"] == "101:101", "uid nginx-unprivileged, не root"
    assert gui["read_only"] is True
    assert gui["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in gui["security_opt"]
    assert gui["init"] is True
    assert "volumes" not in gui, "конфіг nginx зашитий в image, не bind-mount"
    assert "secrets" not in gui, "gui не отримує жодного secret"
    assert "environment" not in gui
    # read-only rootfs: усе, що пише nginx, — у tmpfs з uid worker-а.
    tmpfs = {str(entry).split(":", 1)[0] for entry in gui["tmpfs"]}
    assert tmpfs == {"/tmp", "/var/cache/nginx"}  # noqa: S108 — tmpfs mounts
    assert any("uid=101,gid=101" in str(entry) for entry in gui["tmpfs"])


def test_gui_is_the_only_ingress_and_depends_on_api(
    services: dict[str, dict[str, Any]],
) -> None:
    gui = services["gui"]
    assert gui["depends_on"]["api"]["condition"] == "service_healthy"
    assert set(gui["depends_on"]) == {"api"}, "gui залежить лише від api"
    assert gui["deploy"]["resources"]["limits"]["pids"] == 256
    assert _seconds(gui["stop_grace_period"]) >= 30
    # Healthcheck: процес (nginx) + критична dependency (api через proxy) (§7.5).
    assert gui["healthcheck"]["test"][0] == "CMD", "exec-форма, без shell"
    test = " ".join(map(str, gui["healthcheck"]["test"]))
    assert "http://127.0.0.1:8080/api/v1/health/components" in test


def test_gui_image_is_multistage_pinned_non_root() -> None:
    text = GUI_DOCKERFILE_PATH.read_text(encoding="utf-8")
    image_args = dict(re.findall(r"^ARG (\w+_IMAGE)=(\S+)$", text, re.MULTILINE))
    assert set(image_args) == {"NODE_IMAGE", "NGINX_IMAGE"}
    for value in image_args.values():
        assert PINNED_IMAGE.match(value), value
    assert image_args["NODE_IMAGE"].startswith("node:24."), "Node 24 LTS (.nvmrc)"
    assert image_args["NGINX_IMAGE"].startswith("nginxinc/nginx-unprivileged:")
    assert re.search(r"^FROM \$\{NODE_IMAGE\} AS builder$", text, re.MULTILINE)
    assert re.search(r"^FROM \$\{NGINX_IMAGE\} AS runtime$", text, re.MULTILINE)
    assert "npm ci" in text, "збірка строго за package-lock.json"
    assert re.search(r"^USER 101:101$", text, re.MULTILINE)
    assert re.search(r"^HEALTHCHECK ", text, re.MULTILINE)
    assert "org.opencontainers.image.revision" in text
    for line in text.splitlines():
        if line.startswith(("ARG ", "ENV ")):
            assert not SECRET_ENV_KEY.search(line.split("=", 1)[0]), line
    # Node у runtime-шарі не лишається: копіюється лише зібрана статика.
    assert "COPY --from=builder" in text and "/build/dist /usr/share/nginx/html" in text


def test_gui_build_uses_web_context_and_deploy_conf(services: dict[str, dict[str, Any]]) -> None:
    build = services["gui"]["build"]
    assert build["context"] == "./web"
    assert build["dockerfile"] == "Dockerfile"
    assert build["additional_contexts"]["gui-conf"] == "./deploy/compose/gui"
    assert GUI_NGINX_CONF_PATH.is_file()
    assert (REPO_ROOT / "web" / ".nvmrc").read_text(encoding="utf-8").strip() == "24"


def test_gui_nginx_has_restrictive_csp_and_security_headers() -> None:
    """§13: restrictive CSP і security headers; same-origin proxy /api → api:8000."""
    conf = GUI_NGINX_CONF_PATH.read_text(encoding="utf-8")
    csp = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', conf)
    assert csp, "немає CSP"
    directives = dict((part.split(" ", 1) + [""])[:2] for part in csp.group(1).split("; ") if part)
    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == "'self'", "жодного inline/CDN-скрипта"
    assert directives["connect-src"] == "'self'", "fetch/SSE лише same-origin"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert directives["object-src"] == "'none'"
    for header, value in (
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Cross-Origin-Resource-Policy", "same-origin"),
    ):
        assert f'add_header {header} "{value}" always;' in conf, header
    assert "Permissions-Policy" in conf and "Strict-Transport-Security" in conf
    assert "server_tokens off;" in conf


def test_gui_nginx_proxies_api_same_origin_and_hides_health_detail() -> None:
    conf = GUI_NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert "listen 8080;" in conf and "listen 80;" not in conf, "non-root не слухає <1024"
    assert "set $api_host api:8000;" in conf
    assert "set $api_upstream http://$api_host;" in conf
    assert "resolver 127.0.0.11" in conf, "docker DNS: перестворення api не ламає proxy"
    assert "proxy_pass $api_upstream$request_uri;" in conf
    # SPA fallback: маршрути React Router віддають index.html, а не 404.
    assert "try_files $uri $uri/ /index.html;" in conf
    # §13/CR-14: детальний звіт компонентів назовні не віддається до OIDC (WP-11A).
    assert "auth_request /internal-api-health;" in conf
    assert '{"status":"ready"}' in conf and '{"status":"not_ready"}' in conf
    health_block = conf.split("location = /api/v1/health/components {", 1)[1].split("}", 1)[0]
    assert "proxy_pass" not in health_block, "публічний health не проксіює тіло звіту"


def test_ci_runs_web_pipeline_from_spec_16_2() -> None:
    """§16.2: `npm ci && npm run lint && npm run test && npm run build && npm run test:e2e`."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    web = ci["jobs"]["web"]
    runs = " ".join(step.get("run", "") for step in web["steps"])
    for command in ("npm ci", "npm run lint", "npm run test", "npm run build", "npm run test:e2e"):
        assert command in runs, command
    setup_node = [s for s in web["steps"] if "setup-node" in (s.get("uses") or "")]
    assert setup_node, "немає actions/setup-node"
    assert setup_node[0]["with"]["cache"] == "npm", "кеш npm обов'язковий"
    assert setup_node[0]["with"]["node-version-file"] == "web/.nvmrc"
    # GUI image збирається і перевіряється на non-root у job docker.
    docker_runs = " ".join(step.get("run", "") for step in ci["jobs"]["docker"]["steps"])
    assert "--profile gui" in docker_runs, "clean-host acceptance §16.2 включає gui"


def _nginx_block(conf: str, header: str) -> str:
    """Тіло location-блока nginx.conf (до рядка з закриваючою дужкою того ж рівня)."""
    body = conf.split(header, 1)[1]
    return body.split(chr(10) + "    }", 1)[0]


def _nginx_directives(text: str) -> str:
    """Лише директиви, без коментарів: коментарі пояснюють, чому чогось НЕ робимо, і
    містять ті самі рядки, наявність яких перевіряють assert-и нижче."""
    return chr(10).join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_gui_nginx_does_not_forward_client_host_to_api() -> None:
    """SEC M-1: `server_name _` приймає будь-який Host; пересилати його в api не можна.

    Підтверджений наслідок до фіксу: `curl -H 'Host: evil.example.com'` давав
    `location: http://evil.example.com/...` від FastAPI `redirect_slashes`. У WP-11A це був би
    OIDC `redirect_uri` на чужий домен, у WP-13 — cache poisoning.
    """
    conf = GUI_NGINX_CONF_PATH.read_text(encoding="utf-8")
    api_block = _nginx_block(conf, "location /api/ {")
    assert "proxy_set_header Host $api_host;" in api_block, "Host має бути фіксований"
    directives = _nginx_directives(conf)
    assert "proxy_set_header Host $host;" not in directives, "клієнтський Host не пересилається"
    assert "X-Forwarded-Host" not in directives, "той самий підроблюваний Host під іншим іменем"
    # absolute-Location від api (побудований з внутрішнього Host) стає відносним, інакше
    # штатний редирект повів би браузер на `http://api:8000/…`.
    assert "proxy_redirect http://$api_host/ /;" in api_block


def test_gui_nginx_does_not_forward_spoofable_client_ip() -> None:
    """Код-рев'ю M-3 / SEC L-4: gui — перший hop, тому XFF замінюється, а не доклеюється."""
    conf = GUI_NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    assert "$proxy_add_x_forwarded_for" not in _nginx_directives(conf), (
        "доклеювання дозволяє клієнту підробити перший елемент ланцюга"
    )


def test_gui_nginx_access_log_drops_query_string() -> None:
    """SEC M-2 (§13): `?access_token=…` / OIDC `?code=…` не мають осідати в логах хоста."""
    conf = GUI_NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert "log_format gui_no_query" in conf
    log_format = conf.split("log_format gui_no_query", 1)[1].split(";", 1)[0]
    assert "$uri" in log_format, "шлях без query"
    assert "$query_string" not in log_format and "$args" not in log_format
    assert "$request " not in log_format
    assert "access_log /var/log/nginx/access.log gui_no_query;" in conf


def test_gui_nginx_does_not_serve_source_maps() -> None:
    """Код-рев'ю M-2 / SEC L-1: `.map` несе повний оригінальний TS і віддавався б анонімно."""
    conf = GUI_NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert re.search(r"location ~ \\.map\$ \{", conf), conf[:0] or "немає location для .map"
    map_block = conf.split("location ~", 1)[1].split("}", 1)[0]
    assert "return 404;" in map_block
    # Друга лінія оборони — образ узагалі не містить мап (`vite build --mode image`).
    vite_config = (REPO_ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert "sourcemap: mode !== 'image'" in vite_config
    package_json = (REPO_ROOT / "web" / "package.json").read_text(encoding="utf-8")
    assert "vite build --mode image" in package_json


def test_gui_nginx_resolver_has_timeout_and_api_without_slash_is_not_spa() -> None:
    """Код-рев'ю L-3 і L-8."""
    conf = GUI_NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert "resolver_timeout" in conf, "фаза DNS не покрита proxy_connect_timeout"
    assert "location = /api {" in conf, "`/api` без слеша не має падати у SPA-fallback"
    api_exact = conf.split("location = /api {", 1)[1].split("}", 1)[0]
    assert "return 404" in api_exact
    api_block = _nginx_block(conf, "location /api/ {")
    assert "expires -1;" in api_block, "§7.7: відповіді API не кешуються браузером"


def test_ci_runs_gui_runtime_tests_against_live_stack() -> None:
    """Код-рев'ю H-1: без цього кроку 19 runtime-тестів gui у CI мовчки пропускались."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    docker_steps = ci["jobs"]["docker"]["steps"]
    runs = [s.get("run", "") for s in docker_steps]
    pytest_at = next((i for i, r in enumerate(runs) if "pytest -m e2e" in r), None)
    assert pytest_at is not None, "job docker не запускає runtime-тести gui"
    up_at = next(i for i, r in enumerate(runs) if "up -d --wait" in r)
    down_at = next(i for i, r in enumerate(runs) if "down -v" in r)
    assert up_at < pytest_at < down_at, "e2e мають іти між `up --wait` і `down -v`"
    # Контракт збірки — окремим прогоном ПІСЛЯ build (у ланцюжку §16.2 test іде до build).
    web_runs = [s.get("run", "") for s in ci["jobs"]["web"]["steps"]]
    build_at = next(i for i, r in enumerate(web_runs) if "npm run build" in r)
    test_build_at = next(i for i, r in enumerate(web_runs) if "npm run test:build" in r)
    assert build_at < test_build_at


# --- бюджет healthcheck (CI PR #4) -----------------------------------------------------------

# Мінімуми, виведені з виміру вартості проби на CPU-обмеженому runner-і (див. коментар до
# anchor `x-healthcheck-budget` у docker-compose.yml): проба піднімає повний Python і йде в
# БД, на 0.25 CPU це ~6 с. Значення нижчі за ці знову зроблять job `docker` флакі.
MIN_HEALTHCHECK_TIMEOUT_S = 15
MIN_HEALTHCHECK_START_PERIOD_S = 60
MIN_HEALTHCHECK_RETRIES = 5
MAX_HEALTHCHECK_START_INTERVAL_S = 10


def test_application_healthcheck_budget_survives_parallel_start(
    services: dict[str, dict[str, Any]],
) -> None:
    """CI PR #4: job `docker` падав відтворювано, щоразу на іншому контейнері.

    Причина — не конкретний сервіс, а спільний бюджет: `timeout: 5s` на пробу, яка коштує
    ~6 с при 0.25 CPU, і `start_period: 15s` при `interval: 30s` (перша проба виконувалась
    уже ПІСЛЯ grace-періоду, тож її невдача одразу йшла в залік `retries`).
    """
    application = {name for name, svc in services.items() if name not in STATEFUL} - ONE_SHOTS
    assert application, "немає application-сервісів"
    for name in sorted(application):
        healthcheck = services[name]["healthcheck"]
        assert _seconds(healthcheck["timeout"]) >= MIN_HEALTHCHECK_TIMEOUT_S, name
        assert _seconds(healthcheck["start_period"]) >= MIN_HEALTHCHECK_START_PERIOD_S, name
        assert healthcheck["retries"] >= MIN_HEALTHCHECK_RETRIES, name
        # `start_interval` (Docker 25+/API 1.44+): без нього перша проба чекала б цілий
        # `interval`, і `start_period` не давав би нічого.
        assert "start_interval" in healthcheck, f"{name}: немає start_interval"
        assert _seconds(healthcheck["start_interval"]) <= MAX_HEALTHCHECK_START_INTERVAL_S, name
        assert _seconds(healthcheck["start_interval"]) < _seconds(healthcheck["interval"]), name


def test_healthcheck_budget_is_defined_once(compose: dict[str, Any]) -> None:
    """Бюджет задається одним anchor — інакше він розійдеться між сервісами при правці."""
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "x-healthcheck-budget: &healthcheck-budget" in text
    # Реальна властивість «задано один раз» — це не кількість входжень `<<:` (частина
    # worker-ів успадковує бюджет транзитивно через anchor `x-worker`), а те, що ЖОДЕН
    # application-сервіс не має власних чисел: усі таймінги збігаються побайтово.
    services = compose["services"]
    application = {n for n in services if n not in STATEFUL} - ONE_SHOTS
    budgets = {
        name: tuple(
            str(services[name]["healthcheck"].get(key))
            for key in ("interval", "timeout", "start_period", "start_interval", "retries")
        )
        for name in application
    }
    assert len(set(budgets.values())) == 1, f"бюджет розійшовся між сервісами: {budgets}"
    # Вимір, на якому тримаються числа, лишається у файлі: без нього наступний рев'юер
    # побачить «магічні» 15/90 і поверне їх назад.
    assert "0.25 CPU" in text and "start_interval" in text
