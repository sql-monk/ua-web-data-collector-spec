"""Unit-тести Compose/Docker-конфігурації як артефакту (WP-00 PR2; §7.5, §13, R-51/R-55, §16.1).

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

# §7.5 таблиця profiles → services (gui/observability/tools ще без сервісів у PR2).
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
}
WORKERS = SPEC_7_5_PROFILES["workers"] | SPEC_7_5_PROFILES["browser"]
STATEFUL = {"postgres", "mongo", "minio"}
ONE_SHOTS = {"migrate-postgres", "ensure-mongo"}
SPEC_7_5_NETWORKS = {"ingress", "backend", "source-egress", "provider-egress", "telemetry"}
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
    for profile in ("gui", "observability", "tools"):
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


def test_base_compose_publishes_no_ports(services: dict[str, dict[str, Any]]) -> None:
    assert not [name for name, svc in services.items() if "ports" in svc]


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
    assert app_services == set(services) - STATEFUL
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
    assert _networks(services["api"]) == {"backend", "ingress"}
    for name in STATEFUL | ONE_SHOTS | {"scheduler"}:
        assert _networks(services[name]) == {"backend"}, name
    on_ingress = {name for name, svc in services.items() if "ingress" in _networks(svc)}
    assert on_ingress == {"api"}, "у ingress лише api (gui — PR3)"


# --- stateful (§7.5, §8): pinned digests, named volumes ------------------------------------


@pytest.mark.parametrize("name", sorted(STATEFUL))
def test_stateful_image_pinned_by_digest_and_named_volumes(
    compose: dict[str, Any], services: dict[str, dict[str, Any]], name: str
) -> None:
    svc = services[name]
    assert PINNED_IMAGE.match(svc["image"]), svc["image"]
    volumes = svc["volumes"]
    assert volumes, f"{name}: stateful без named volume"
    for volume in volumes:
        source = str(volume).split(":", 1)[0]
        assert source in compose["volumes"], f"{name}: {source} не named volume"
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
    }
    assert used == set(compose["volumes"])


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
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    scans = [s["with"] for s in ci["jobs"]["docker"]["steps"] if "trivy" in (s.get("uses") or "")]
    by_severity = {s["severity"]: s for s in scans}
    assert set(by_severity) == {"CRITICAL", "HIGH"}
    assert "ignore-unfixed" not in by_severity["CRITICAL"], "§13: unfixed CRITICAL не пропускати"
    assert str(by_severity["CRITICAL"]["exit-code"]) == "1"
    assert by_severity["HIGH"]["ignore-unfixed"] is True


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


def test_ci_python_job_shows_skips() -> None:
    """CR-3: render-тести виконуються у job python; skip видимий (-rs)."""
    ci = _load(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    runs = [s.get("run", "") for s in ci["jobs"]["python"]["steps"]]
    assert any('pytest -m "not live" -rs' in r for r in runs)
