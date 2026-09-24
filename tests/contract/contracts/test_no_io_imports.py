"""Acceptance: `collector.contracts` — чисті моделі; імпорт не тягне httpx/sqlalchemy/pymongo."""

from __future__ import annotations

import json
import pkgutil
import subprocess
import sys
from pathlib import Path

import collector.contracts

FORBIDDEN_TOP_LEVEL = (
    "httpx",
    "sqlalchemy",
    "pymongo",
    "motor",
    "asyncpg",
    "psycopg",
    "psycopg2",
    "alembic",
    "boto3",
    "botocore",
    "aiobotocore",
    "scrapy",
    "requests",
    "aiohttp",
    "socket",
    "ssl",
)
CONTRACT_MODULES = sorted(
    name
    for _, name, _ in pkgutil.walk_packages(collector.contracts.__path__, "collector.contracts.")
)

PROBE = """
import json, sys
before = set(sys.modules)
for name in {modules!r}:
    __import__(name)
import collector.contracts
loaded = sorted(set(sys.modules) - before)
print(json.dumps(loaded))
"""


def test_import_contracts_does_not_load_io_libraries() -> None:
    code = PROBE.format(modules=CONTRACT_MODULES)
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[3],
    )
    loaded: list[str] = json.loads(result.stdout)
    assert any(name.startswith("collector.contracts") for name in loaded)
    offenders = sorted(name for name in loaded if name.split(".")[0] in FORBIDDEN_TOP_LEVEL)
    assert offenders == [], f"імпорт contracts тягне I/O-бібліотеки: {offenders}"
    # yaml/phonenumbers/idna — лише lazy, всередині функцій
    assert not any(name.split(".")[0] in {"yaml", "phonenumbers", "idna"} for name in loaded)


def test_contract_sources_have_no_io_or_network_imports() -> None:
    root = Path(collector.contracts.__file__).resolve().parent
    banned = (
        "import httpx",
        "import sqlalchemy",
        "import pymongo",
        "import motor",
        "import socket",
    )
    for source in root.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{source.name}: {needle}"
        assert "open(" not in text or source.name == "source_registry.py", source.name


def test_io_probe_covers_pr2_modules() -> None:
    """PR2: нові модулі (payload, records, news) входять у перевірку «жодного I/O»."""
    pr2 = {"collector.contracts.payload", "collector.contracts.records", "collector.contracts.news"}
    assert pr2 <= set(CONTRACT_MODULES)
