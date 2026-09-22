"""Adversarial-інваріанти Compose/Dockerfile/CI (wp-tester, WP-00 PR2; §7.5, §13, R-55, §16.1 п.14).

Доповнює tests/unit/test_compose_config.py тим, що там не пінилося:

- least-privilege secrets: кожен secret має рівно тих споживачів, що документовано
  (`deploy/compose/README.md`); `api`/workers без DB credentials у WP-00;
- healthcheck довгоживучих application-сервісів = process + критична dependency (§7.5),
  а не лише «процес живий»;
- drain-контракт workers: `restart: unless-stopped`, `stop_signal: SIGTERM`, `init: true`;
- dev-override не послаблює безпеку (лише loopback `ports`/`environment`, без volumes/
  user/privileged/read_only/cap_add);
- Dockerfile: останній `USER` — non-root, без `USER root` після нього, `ENTRYPOINT []`,
  без `COPY . .`, без `--mount=type=secret`, `HEALTHCHECK` у exec-формі;
- CI job `docker`: порядок build → SBOM → scan → up; `down -v` з `if: always()`; без
  Docker socket; без hardcoded credentials.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
DEV_OVERRIDE_PATH = REPO_ROOT / "deploy" / "compose" / "dev.override.yml"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Документована мапа споживачів секретів (deploy/compose/README.md, §13). Нові споживачі
# додають own-WP свідомо (оновлюючи README і цю мапу), а не через copy-paste anchor.
SECRET_CONSUMERS: dict[str, set[str]] = {
    "postgres_password": {"postgres"},
    "mongo_root_password": {"mongo", "ensure-mongo"},
    "mongo_keyfile": {"mongo"},
    "minio_root_user": {"minio"},
    "minio_root_password": {"minio"},
    # DSN міграційної ролі — лише one-shot `migrate-postgres` (approved dependency WP-01A;
    # §13: migration role не використовується runtime-процесами).
    "postgres_dsn": {"migrate-postgres"},
}
COMPONENT_NAMES = ("postgres", "mongo", "minio")
ONE_SHOTS = {"migrate-postgres", "ensure-mongo"}
STATEFUL = {"postgres", "mongo", "minio"}


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def services() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = _load(COMPOSE_PATH)["services"]
    return result


def _secret_names(svc: dict[str, Any]) -> set[str]:
    return {s if isinstance(s, str) else s["source"] for s in svc.get("secrets", [])}


# --- secrets: least privilege ---------------------------------------------------------------


def test_each_secret_has_exactly_documented_consumers(services: dict[str, dict[str, Any]]) -> None:
    consumers: dict[str, set[str]] = {name: set() for name in SECRET_CONSUMERS}
    for name, svc in services.items():
        for secret in _secret_names(svc):
            assert secret in consumers, f"{name}: недокументований secret {secret}"
            consumers[secret].add(name)
    assert consumers == SECRET_CONSUMERS


def test_api_and_workers_have_no_secrets_in_wp00(services: dict[str, dict[str, Any]]) -> None:
    """У WP-00 api/workers не мають DB credentials (§13: ролі per-component додають WP-01A/B)."""
    for name, svc in services.items():
        if name == "api" or name.endswith("-worker") or name == "scheduler":
            assert not _secret_names(svc), f"{name}: секрети без потреби"
            env = svc.get("environment", {})
            assert not [k for k in env if k.endswith("_FILE")], f"{name}: *_FILE без secret"


def test_secret_file_env_points_to_mounted_secret(services: dict[str, dict[str, Any]]) -> None:
    """Кожен `*_FILE=/run/secrets/<x>` посилається на secret, змонтований у цей сервіс."""
    for name, svc in services.items():
        mounted = _secret_names(svc)
        for key, value in svc.get("environment", {}).items():
            if key.endswith("_FILE"):
                match = re.fullmatch(r"/run/secrets/([\w-]+)", str(value))
                assert match, f"{name}: {key}={value} не /run/secrets/*"
                assert match.group(1) in mounted, f"{name}: {key} → незмонтований secret"


# --- healthcheck = process + critical dependency (§7.5) --------------------------------------


def test_application_healthchecks_name_a_critical_dependency(
    services: dict[str, dict[str, Any]],
) -> None:
    for name, svc in services.items():
        if name in STATEFUL | ONE_SHOTS:
            continue
        test = svc["healthcheck"]["test"]
        joined = " ".join(map(str, test))
        assert test[0] == "CMD", f"{name}: healthcheck має бути exec-формою CMD"
        if name == "api":
            # api: HTTP до власного health endpoint, який перевіряє всі три компоненти
            # і віддає 200 лише за ready (503 інакше) — залежність перевіряється через нього.
            assert "/api/v1/health/components" in joined and "200" in joined, name
            continue
        assert any(component in joined for component in COMPONENT_NAMES), (
            f"{name}: healthcheck не перевіряє критичну dependency (§7.5)"
        )
        if name.endswith("-worker") or name == "scheduler":
            assert "postgres" in joined, f"{name}: черга/lease живуть у PostgreSQL"
        assert svc["healthcheck"]["retries"] >= 3 and svc["healthcheck"]["timeout"], name


def test_projector_export_check_mongo_fetch_parse_export_check_minio(
    services: dict[str, dict[str, Any]],
) -> None:
    """§7.6: projector/export пишуть у Mongo; fetch/parse/export працюють з raw bucket."""
    for name in ("projector-worker", "export-worker"):
        assert "mongo" in services[name]["healthcheck"]["test"], name
    for name in ("fetch-worker", "parse-worker", "export-worker"):
        assert "minio" in services[name]["healthcheck"]["test"], name


# --- drain-контракт workers (§7.5) -----------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "discovery-worker",
        "fetch-worker",
        "parse-worker",
        "projector-worker",
        "translation-worker",
        "export-worker",
        "maintenance-worker",
        "browser-worker",
    ],
)
def test_worker_drain_contract(services: dict[str, dict[str, Any]], name: str) -> None:
    svc = services[name]
    assert svc["restart"] == "unless-stopped"
    assert svc.get("stop_signal", "SIGTERM") == "SIGTERM", "SIGTERM = drain (§7.5)"
    assert svc["init"] is True
    assert svc["depends_on"]["migrate-postgres"]["condition"] == "service_completed_successfully"
    assert "logging" in svc and svc["logging"]["options"]["max-size"], "логи обмежені"


def test_one_shots_have_no_restart_loop_and_no_healthcheck(
    services: dict[str, dict[str, Any]],
) -> None:
    for name in ONE_SHOTS:
        svc = services[name]
        assert svc["restart"] == "no", f"{name}: one-shot не повинен рестартувати"
        assert "healthcheck" not in svc
        assert "stop_grace_period" not in svc


# --- dev override не послаблює безпеку -------------------------------------------------------


def test_dev_override_only_adds_loopback_ports_and_environment() -> None:
    override = _load(DEV_OVERRIDE_PATH)
    assert set(override) == {"services"}, "override без networks/volumes/secrets"
    for name, svc in override["services"].items():
        assert set(svc) <= {"ports", "environment"}, f"{name}: {set(svc)}"
        for port in svc.get("ports", []):
            assert re.fullmatch(r"127\.0\.0\.1:\$\{DEV_\w+_PORT:-\d+\}:\d+", str(port)), port
        for key in svc.get("environment", {}):
            assert not re.search(r"(?i)password|secret|token", key), f"{name}: {key}"


# --- Dockerfile -----------------------------------------------------------------------------


def test_dockerfile_final_user_is_non_root_and_no_context_wide_copy() -> None:
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
    assert users, "USER відсутній"
    assert users[-1] == "10001:10001", "останній USER має бути non-root"
    assert "USER root" not in text
    assert not re.search(r"^COPY\s+\.\s+\.", text, re.MULTILINE), "COPY . . тягне секрети"
    assert not re.search(r"^COPY\s+(deploy|\.env|tests)", text, re.MULTILINE)
    assert "--mount=type=secret" not in text, "секрети не потрібні у build (§7.5)"
    assert re.search(r"^ENTRYPOINT \[\]$", text, re.MULTILINE), "command задає Compose"
    healthcheck = re.search(
        r"^HEALTHCHECK (?:.*\
)*.*$",
        text,
        re.MULTILINE,
    )
    assert healthcheck and re.search(r"CMD \[", healthcheck.group(0)), "HEALTHCHECK exec-форма"
    assert "sudo" not in text and "setcap" not in text


def test_dockerfile_runtime_stage_has_no_uv_and_no_pip() -> None:
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    runtime = text.split("AS runtime", 1)[1]
    assert "COPY --from=uv" not in runtime, "uv лише у builder"
    assert "pip install" not in runtime and "uv sync" not in runtime
    assert "rm -rf" in runtime and "pip" in runtime, "pip/ensurepip видаляються з runtime"


# --- CI job docker ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def docker_job() -> dict[str, Any]:
    job: dict[str, Any] = _load(CI_PATH)["jobs"]["docker"]
    return job


def _step_index(steps: list[dict[str, Any]], needle: str) -> int:
    for index, step in enumerate(steps):
        haystack = (step.get("uses") or "") + (step.get("run") or "")
        if needle in haystack:
            return index
    raise AssertionError(f"крок «{needle}» відсутній")


def test_ci_docker_job_order_build_sbom_scan_up_down(docker_job: dict[str, Any]) -> None:
    steps = docker_job["steps"]
    config = _step_index(steps, "docker compose config --quiet")
    build = _step_index(steps, "docker build")
    sbom = _step_index(steps, "sbom")
    scan = _step_index(steps, "trivy")
    up = _step_index(steps, "up -d --wait")
    down = _step_index(steps, "down -v")
    assert config < build < sbom < scan < up < down
    assert steps[down].get("if") == "always()", "down -v має виконуватись завжди"
    assert steps[build]["run"].count("docker inspect") >= 1, "перевірка Config.User"
    assert "10001:10001" in steps[build]["run"]


def test_ci_docker_job_profiles_and_no_socket_or_hardcoded_secrets(
    docker_job: dict[str, Any],
) -> None:
    assert docker_job["env"]["COMPOSE_PROFILES"] == "core,workers"
    text = CI_PATH.read_text(encoding="utf-8")
    assert "docker.sock" not in text and "privileged" not in text
    for line in text.splitlines():
        if re.search(r"(?i)(password|token|secret)\s*[:=]", line) and "${{" not in line:
            assert line.strip().startswith("#"), f"hardcoded credential: {line.strip()}"
    assert "init-secrets.sh" in text, "секрети для CI — з *.example, не з YAML"


def test_ci_trivy_blocks_on_critical(docker_job: dict[str, Any]) -> None:
    scan = next(s for s in docker_job["steps"] if "trivy" in (s.get("uses") or ""))
    assert scan["with"]["severity"] == "CRITICAL"
    assert str(scan["with"]["exit-code"]) == "1", "CRITICAL має блокувати (§13)"
    assert re.fullmatch(r"[\w-]+/[\w-]+@v\d+\.\d+\.\d+", scan["uses"]), "pinned version"


def test_ci_workflow_permissions_are_read_only() -> None:
    ci = _load(CI_PATH)
    assert ci["permissions"] == {"contents": "read"}
