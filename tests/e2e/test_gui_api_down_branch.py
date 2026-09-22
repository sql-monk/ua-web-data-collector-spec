"""Гілка `not_ready` nginx-конфігу gui, коли api недоступний (WP-00 PR3; §7.5, §13).

Навіщо окремий файл: `location = /api/v1/health/components` віддає «ready» лише після
успішного `auth_request`. Якщо успішну відповідь формувати через `return 200` у тому ж
location, вона виконається у **rewrite-фазі** — тобто ДО access-фази, у якій працює
`auth_request`, — і endpoint віддаватиме «ready» навіть з мертвим api. Реалізація обходить
це через `try_files` → named location (content-фаза). Позитивну гілку перевіряє
`test_gui_runtime_contract.py` на живому стеку; тут перевіряється негативна.

Стек чіпати не можна (зупинка `api` зробила б gui `unhealthy` на хвилини), тому тест
піднімає **окремий одноразовий контейнер** з того самого image у власній мережі, де імені
`api` не існує: docker DNS відповідає NXDOMAIN → `proxy_pass` падає → `auth_request` → 500 →
`error_page` → 503 `not_ready`. Стек і його мережі лишаються незмінними.

Мережа тесту — лише loopback (маркер `e2e`, `allow_hosts` у tests/conftest.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator

import pytest

GUI_IMAGE = os.environ.get("COLLECTOR_GUI_IMAGE", "collector-gui:dev")
DOCKER = shutil.which("docker")


def _image_exists() -> bool:
    if DOCKER is None:
        return False
    proc = subprocess.run(  # noqa: S603 — фіксований argv, без shell
        [DOCKER, "image", "inspect", GUI_IMAGE],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


# Код-рев'ю PR3, H-1: у CI пропуск цих тестів заборонений. Локально skip зручний (стек
# піднімають не завжди), але в CI стек піднімає крок `up -d --wait`, і мовчазний skip
# означав би, що §13-інваріанти nginx не перевіряються взагалі. Тому в CI умова skip
# вимикається: тест впаде гучно, а не зникне з переліку.
CI = os.environ.get("CI", "").strip().lower() in {"1", "true", "yes", "on"}


def skip_unless_available(available: bool, reason: str) -> pytest.MarkDecorator:
    return pytest.mark.skipif(not available and not CI, reason=reason)


pytestmark = [
    pytest.mark.e2e,
    skip_unless_available(
        _image_exists(),
        reason=f"немає image {GUI_IMAGE} — зберіть `docker compose build gui`",
    ),
]


def _docker(*args: str, check: bool = True) -> str:
    assert DOCKER is not None
    proc = subprocess.run(  # noqa: S603 — фіксований argv, без shell
        [DOCKER, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} → {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _published_port(container: str) -> int:
    """Реальний порт, який Docker призначив контейнеру.

    Раніше порт вибирався заздалегідь (`bind(0)` → `close()` → `docker run -p`) — класичний
    TOCTOU: під паралельним прогоном між звільненням і публікацією порт міг зайняти хтось
    інший (`port is already allocated`). Тепер порт призначає Docker (`-p 127.0.0.1::8080`),
    а ми його лише зчитуємо (код-рев'ю M-4).
    """
    mapping = _docker("port", container, "8080")
    for line in mapping.splitlines():
        host, _, port = line.strip().rpartition(":")
        if host.strip("[]") in {"127.0.0.1", "::1"} and port.isdigit():
            return int(port)
    raise AssertionError(f"не знайдено опублікований порт {container}: {mapping!r}")


@pytest.fixture(scope="module")
def gui_without_api() -> Iterator[str]:
    """Одноразовий gui у мережі без сервісу `api`; повертає base URL на loopback."""
    suffix = uuid.uuid4().hex[:8]
    network = f"collector-test-noapi-{suffix}"
    container = f"collector-test-gui-{suffix}"

    _docker("network", "create", network)
    try:
        _docker(
            "run",
            "-d",
            "--name",
            container,
            "--network",
            network,
            "--read-only",
            "--user",
            "101:101",
            "--tmpfs",
            "/tmp:mode=1777,size=16m",  # noqa: S108 — tmpfs контейнера, не шлях у тесті
            "--tmpfs",
            "/var/cache/nginx:mode=0700,uid=101,gid=101,size=32m",
            "-p",
            "127.0.0.1::8080",
            GUI_IMAGE,
        )
        base = f"http://127.0.0.1:{_published_port(container)}"
        _wait_until_serving(base, container)
        yield base
    finally:
        _docker("rm", "-f", container, check=False)
        _docker("network", "rm", network, check=False)


def _wait_until_serving(base: str, container: str, timeout: float = 60.0) -> None:
    """Чекати, доки nginx почне відповідати, але не довше `timeout` секунд.

    Раніше цикл крутився `range(60)` без пауз: одразу після `docker run -d` порт ще не
    слухається, тому `urlopen` падає `ConnectionRefusedError` МИТТЄВО, і всі 60 спроб
    відпрацьовували за ~0.05 с — фактичного очікування не було, а на повільнішому хості
    тест падав без зв'язку з предметом перевірки (код-рев'ю M-4). Тепер — явний дедлайн за
    монотонним годинником і пауза між спробами.
    """
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except (OSError, urllib.error.HTTPError) as exc:  # контейнер ще стартує
            last = exc
        time.sleep(0.5)
    logs = _docker("logs", "--tail", "50", container, check=False)
    detail = f"gui не піднявся на {base} за {timeout:g} с: {last}"
    raise AssertionError(detail + chr(10) + logs)


def _fetch(url: str) -> tuple[int, dict[str, str], str]:
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 — loopback
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), {k.lower(): v for k, v in resp.headers.items()}, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), {k.lower(): v for k, v in exc.headers.items()}, body


def test_health_reports_not_ready_when_api_is_unreachable(gui_without_api: str) -> None:
    """Регресія rewrite-фази: без api endpoint має бути 503 `not_ready`, а не «ready»."""
    status, _, body = _fetch(f"{gui_without_api}/api/v1/health/components")

    assert status == 503, f"{status}: {body[:200]}"
    assert json.loads(body) == {"status": "not_ready"}


def test_static_spa_still_served_without_api(gui_without_api: str) -> None:
    """Недоступний api не має ламати видачу статики — оператор бачить сторінку, не 502."""
    status, headers, body = _fetch(f"{gui_without_api}/")

    assert status == 200, status
    assert '<div id="root">' in body
    assert headers.get("content-security-policy", "").startswith("default-src 'none'")


def test_security_headers_survive_the_not_ready_branch(gui_without_api: str) -> None:
    """`error_page` → named location не має зрізати успадковані заголовки §13."""
    _, headers, _ = _fetch(f"{gui_without_api}/api/v1/health/components")

    assert headers.get("content-security-policy", "").startswith("default-src 'none'")
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("referrer-policy") == "no-referrer"
