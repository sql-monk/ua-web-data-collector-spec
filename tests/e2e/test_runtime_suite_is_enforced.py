"""Захист від мовчазного skip runtime-тестів `gui` (код-рев'ю WP-00 PR3, знахідка H-1).

Тести `test_gui_runtime_contract.py` і `test_gui_api_down_branch.py` — єдине, що доводить
§13-інваріанти nginx на живому контейнері (пастка `add_header` у вкладеному location і фази
rewrite/access). Обидва модулі пропускаються, якщо стек не піднято, тому «зелений» прогін
без стека нічого не означає. Цей модуль робить таку ситуацію видимою: там, де стек
обіцяний (`COLLECTOR_E2E_REQUIRED=1` — прапорець виставляє крок job `docker` після
`up -d --wait`), він вимагає, щоб передумови були виконані, тобто щоб суїт справді
виконувався.

Локально (без прапорця) сам пропускається — розробник не зобов'язаний тримати стек піднятим.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

E2E_REQUIRED = os.environ.get("COLLECTOR_E2E_REQUIRED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
GUI_PORT = os.environ.get("GUI_PORT", "80")
BASE_URL = f"http://127.0.0.1:{GUI_PORT}"
GUI_IMAGE = os.environ.get("COLLECTOR_GUI_IMAGE", "collector-gui:dev")

# Модулі, кожен тест яких має реально виконуватись у CI.
ENFORCED_MODULES = ("test_gui_runtime_contract.py", "test_gui_api_down_branch.py")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not E2E_REQUIRED,
        reason=(
            "перевірка діє лише там, де стек обіцяний "
            "(COLLECTOR_E2E_REQUIRED=1 — крок job `docker` після `up -d --wait`)"
        ),
    ),
]


def test_gui_stack_is_up_so_runtime_contract_runs() -> None:
    """У CI `docker compose up -d --wait` уже відпрацював — gui мусить відповідати."""
    try:
        with urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=5) as resp:  # noqa: S310
            status = int(resp.status)
    except (OSError, urllib.error.HTTPError) as exc:  # pragma: no cover — діагностика CI
        pytest.fail(
            f"gui недоступний на {BASE_URL} ({exc}), тому тести {ENFORCED_MODULES} були б "
            "пропущені. У CI цей крок має йти ПІСЛЯ `docker compose up -d --wait`."
        )
    assert status == 200


def test_gui_image_exists_so_api_down_branch_runs() -> None:
    """Гілка `not_ready` піднімає одноразовий контейнер із цього ж образу."""
    docker = shutil.which("docker")
    assert docker is not None, "docker CLI відсутній — test_gui_api_down_branch.py був би skip"
    proc = subprocess.run(  # noqa: S603 — фіксований argv, без shell
        [docker, "image", "inspect", GUI_IMAGE],
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"немає image {GUI_IMAGE}; у CI він збирається кроком `docker compose build gui`"
    )
