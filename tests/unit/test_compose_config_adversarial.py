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
    # Міграційний DSN — лише one-shot `migrate-postgres` (§13: migration role не використовується
    # runtime-процесами).
    "postgres_dsn": {"migrate-postgres"},
    # Per-component LOGIN-ролі §13: `migrate-postgres` монтує всі (WP-00 PR4: `db roles
    # --with-login` бере з них паролі ролей); кожен runtime-сервіс — лише свій (WP-01D PR1b).
    # `export-worker` → scheduler тимчасово (ризик у картці WP-01D); `api_ro`/`export_ro` поки
    # не має жоден runtime-сервіс (api — WP-11A, read-only з'єднання експорту — його власник).
    "postgres_dsn_scheduler": {
        "migrate-postgres",
        "scheduler",
        "maintenance-worker",
        "export-worker",
    },
    "postgres_dsn_fetcher": {
        "migrate-postgres",
        "discovery-worker",
        "fetch-worker",
        "browser-worker",
    },
    "postgres_dsn_parser": {"migrate-postgres", "parse-worker"},
    "postgres_dsn_projector": {"migrate-postgres", "projector-worker"},
    "postgres_dsn_translation": {"migrate-postgres", "translation-worker"},
    "postgres_dsn_api_ro": {"migrate-postgres"},
    "postgres_dsn_export_ro": {"migrate-postgres"},
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


def test_api_has_no_secrets_and_runtime_has_only_the_dsn(
    services: dict[str, dict[str, Any]],
) -> None:
    """`api` без DB credentials (owner WP-11A); worker/scheduler — рівно один secret: DSN.

    WP-01D PR1 замінив placeholder-процеси на runtime, який читає чергу, тому DSN їм потрібен;
    PR1b — це DSN власної LOGIN-ролі компонента (§13). Жодних інших credentials (Mongo/MinIO)
    вони не отримують — це залишається least privilege.
    """
    for name, svc in services.items():
        env = svc.get("environment", {})
        if name == "api":
            assert not _secret_names(svc), f"{name}: секрети без потреби"
            assert not [k for k in env if k.endswith("_FILE")], f"{name}: *_FILE без secret"
        elif name.endswith("-worker") or name == "scheduler":
            (secret,) = _secret_names(svc)
            assert secret.startswith("postgres_dsn_"), f"{name}: {secret} замість per-role DSN"
            assert [k for k in env if k.endswith("_FILE")] == ["COLLECTOR_POSTGRES_DSN_FILE"], name


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
    """§7.5 «process + критична dependency» після WP-01D PR1 (вимога 7 картки).

    Вимога ТЗ не змінилась, змінився спосіб її виконання для **довгоживучих runtime-процесів**,
    і саме цей тест картка називає місцем, де зміну семантики треба зафіксувати. Два класи:

    - `api`/`gui` — процес короткий на кожну пробу (стартує інтерпретатор/HTTP-клієнт), і сама
      проба ходить у критичну dependency: лишається як було;
    - worker-и і `scheduler` — довгоживучий процес, який сам оновлює mtime маркера в tmpfs.
      Проба читає лише mtime: **liveness** (процес живий і його event loop не заблокований —
      маркер оновлює сторож lease, тож синхронний handler чи дедлок роблять контейнер
      unhealthy). **Readiness** для них лишається обов'язковою, але доводиться не пробою, а
      `depends_on` (`postgres: service_healthy`, one-shots `service_completed_successfully`) —
      контейнер узагалі не стартує, доки критична dependency не готова, — і станом
      `worker_instances` (`status`/`last_heartbeat_at`), який бачить оператор.

    Навмисний наслідок: падіння PostgreSQL більше не робить worker-контейнери unhealthy.
    Рестарт цього не лікує (черга все одно недоступна), а self-fencing уже зупиняє claim і
    скасовує активні tasks — див. `collector.workers.liveness`.
    """
    for name, svc in services.items():
        if name in STATEFUL | ONE_SHOTS:
            continue
        healthcheck = svc["healthcheck"]
        test = healthcheck["test"]
        joined = " ".join(map(str, test))
        assert test[0] in {"CMD", "CMD-SHELL"}, f"{name}: healthcheck має бути exec-формою"
        assert healthcheck["retries"] >= 3 and healthcheck["timeout"], name
        if name == "api":
            # api: HTTP до власного health endpoint, який перевіряє всі три компоненти
            # і віддає 200 лише за ready (503 інакше) — залежність перевіряється через нього.
            assert "/api/v1/health/components" in joined and "200" in joined, name
            continue
        if name == "gui":
            # gui (WP-00 PR3): критична dependency — api через same-origin proxy; БД і
            # object store gui не бачить узагалі (мережі ingress+frontend, §13).
            assert "/api/v1/health/components" in joined, name
            continue
        assert name.endswith("-worker") or name == "scheduler", f"{name}: новий клас сервісу?"
        assert "alive" in joined and "stat" in joined, (
            f"{name}: liveness-проба має читати mtime маркера процесу, а не стартувати Python"
        )
        assert "python" not in joined.lower(), f"{name}: проба не має стартувати інтерпретатор"
        assert not any(component in joined for component in COMPONENT_NAMES), (
            f"{name}: liveness-проба не має ходити в залежності — це робить depends_on"
        )
        # Readiness довгоживучого runtime: критична dependency — у depends_on, не в пробі.
        conditions = {dep: cfg["condition"] for dep, cfg in svc["depends_on"].items()}
        assert conditions.get("postgres") == "service_healthy", f"{name}: черга живе в PG"
        assert conditions.get("migrate-postgres") == "service_completed_successfully", name


def test_worker_readiness_dependencies_are_declared_in_depends_on(
    services: dict[str, dict[str, Any]],
) -> None:
    """§7.6: projector/export пишуть у Mongo; fetch/parse/export працюють з raw bucket.

    Раніше це доводила healthcheck-проба кожного worker-а; після WP-01D PR1 (вимога 7) проба
    стала liveness-only, тому та сама вимога перевіряється там, де вона тепер живе, — у
    `depends_on`: контейнер не стартує, поки залежність не healthy, а one-shot ініціалізації
    не завершився успішно.
    """
    for name in ("projector-worker", "export-worker"):
        conditions = {dep: cfg["condition"] for dep, cfg in services[name]["depends_on"].items()}
        assert conditions.get("mongo") == "service_healthy", name
        assert conditions.get("ensure-mongo") == "service_completed_successfully", name
    for name in ("fetch-worker", "parse-worker", "export-worker"):
        conditions = {dep: cfg["condition"] for dep, cfg in services[name]["depends_on"].items()}
        assert conditions.get("minio") == "service_healthy", name


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
    assert docker_job["env"]["COMPOSE_PROFILES"] == "core,workers,gui"
    text = CI_PATH.read_text(encoding="utf-8")
    assert "docker.sock" not in text
    # Саме privileged-режим, а не будь-яке входження підрядка: назва базового образу GUI —
    # `nginx-unprivileged` (WP-00 PR3) — містить «privileged» і давала хибне спрацювання.
    assert not re.search(r"(?<![\w-])privileged\s*[:=]|--privileged", text), "privileged mode"
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
