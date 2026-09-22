"""`docker compose config --format json` — рендер із інтерполяцією/merge (WP-00 PR2; §16.1 п.14).

Потребує docker CLI з Compose plugin (daemon не потрібен), тому маркер `integration` і skip
без docker. Інваріанти дублюють tests/unit/test_compose_config.py на вже відрендереному
проєкті: без container_name у workers, без docker.sock, без published ports у base-файлі,
application services read-only/non-root, replicas §7.6, secrets — файли.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI відсутній"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("core", "workers", "browser")


def _compose_plugin_available() -> bool:
    proc = subprocess.run(
        ["docker", "compose", "version"], cwd=REPO_ROOT, capture_output=True, check=False
    )
    return proc.returncode == 0


def _compose_config(*files: str) -> dict[str, Any]:
    """Рендер проєкту. Відсутній Compose plugin → skip; помилка валідації → FAIL.

    Adversarial (wp-tester): `container_name` на масштабованому worker робить проєкт
    невалідним («can't set container_name and … replicas»). Раніше це перетворювалося на
    skip і мутація ставала невидимою для integration-рівня; тепер невалідний проєкт —
    червоний тест.
    """
    if not _compose_plugin_available():
        pytest.skip("docker compose plugin відсутній")
    args = ["docker", "compose"]
    for file in files:
        args += ["-f", file]
    for profile in PROFILES:
        args += ["--profile", profile]
    args += ["config", "--format", "json"]
    proc = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"docker compose config невалідний: {proc.stderr.strip()[:500]}"
    data: dict[str, Any] = json.loads(proc.stdout)
    return data


@pytest.fixture(scope="module")
def rendered() -> dict[str, Any]:
    # Файли секретів не потрібні: `docker compose config` не читає `secrets.*.file`
    # (gate 3, CR-3 — раніше skip ховав тести від CI job `python`).
    return _compose_config("docker-compose.yml")


@pytest.fixture(scope="module")
def rendered_dev(rendered: dict[str, Any]) -> dict[str, Any]:
    return _compose_config("docker-compose.yml", "deploy/compose/dev.override.yml")


def test_workers_have_no_container_name_ports_or_volumes(rendered: dict[str, Any]) -> None:
    workers = {n: s for n, s in rendered["services"].items() if n.endswith("-worker")}
    assert len(workers) == 8
    for name, svc in workers.items():
        assert "container_name" not in svc, name
        assert not svc.get("ports"), name
        assert not svc.get("volumes"), name
        assert (
            svc["deploy"]["replicas"]
            == {
                "discovery-worker": 1,
                "fetch-worker": 2,
                "browser-worker": 0,
                "parse-worker": 2,
                "projector-worker": 1,
                "translation-worker": 1,
                "export-worker": 1,
                "maintenance-worker": 1,
            }[name]
        )


def test_no_docker_socket_and_no_published_ports(rendered: dict[str, Any]) -> None:
    for name, svc in rendered["services"].items():
        for volume in svc.get("volumes", []):
            assert "docker.sock" not in json.dumps(volume), name
        assert not svc.get("ports"), f"{name}: base-файл не публікує портів"


def test_dev_override_binds_loopback_only(rendered_dev: dict[str, Any]) -> None:
    published = {
        name: [p.get("host_ip") for p in svc.get("ports", [])]
        for name, svc in rendered_dev["services"].items()
        if svc.get("ports")
    }
    assert set(published) == {"postgres", "mongo", "minio", "api"}
    for name, host_ips in published.items():
        assert all(ip == "127.0.0.1" for ip in host_ips), name


def test_application_services_read_only_non_root(rendered: dict[str, Any]) -> None:
    for name, svc in rendered["services"].items():
        if name in {"postgres", "mongo", "minio"}:
            continue
        assert svc["read_only"] is True, name
        assert svc["user"] == "10001:10001", name
        assert svc["cap_drop"] == ["ALL"], name
        # S108: це не шлях tmp у тесті, а перевірка tmpfs-монтування контейнера.
        assert any(t.startswith("/tmp") for t in svc["tmpfs"]), name  # noqa: S108


def test_secrets_render_as_files(rendered: dict[str, Any]) -> None:
    for name, secret in rendered["secrets"].items():
        assert Path(secret["file"]).name == name
        assert "environment" not in secret
