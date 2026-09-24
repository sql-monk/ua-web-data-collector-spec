"""Pure-модулі перекладу не тягнуть I/O-бібліотек (картка WP-04, Acceptance PR1)."""

from __future__ import annotations

import subprocess
import sys

import pytest

PURE_MODULES = (
    "collector.translation.segmenter",
    "collector.translation.memory",
    "collector.translation.glossary",
    "collector.translation.preservation",
    "collector.translation.planner",
    "collector.translation.pipeline",
)
IO_LIBRARIES = ("sqlalchemy", "httpx", "asyncpg", "pymongo", "google")


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_module_does_not_import_io_libraries(module: str) -> None:
    probe = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        f"print(','.join(sorted(m for m in {IO_LIBRARIES!r} if m in sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == "", f"{module} імпортує {result.stdout.strip()}"
