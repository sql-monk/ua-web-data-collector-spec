"""Тести перекладу не можуть «тихо» пропускатися (картка WP-04, спільні вимоги; HANDOFF §6).

AST-скан усіх `.py` у `tests/unit/translation/**` і `tests/integration/translation/**`:
заборонені `pytest.skip`, `skipif`, `importorskip`, імперативний `pytest.xfail` і маркер
`xfail` без `strict=True`. Окремо: кожен `parametrize` має непорожній набір значень —
порожній `parametrize` pytest позначає як skip (`[NOTSET]`), тобто зникнення fixture-файлів
чи мов мовчки зменшило б покриття.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = TESTS_ROOT.parent
SCANNED_DIRS = (TESTS_ROOT / "unit" / "translation", TESTS_ROOT / "integration" / "translation")
FORBIDDEN_NAMES = frozenset({"skip", "skipif", "importorskip"})


def _scanned_files() -> list[Path]:
    return sorted(path for root in SCANNED_DIRS if root.is_dir() for path in root.rglob("*.py"))


SCANNED_FILES = _scanned_files()
assert SCANNED_FILES, "не знайдено жодного файлу тестів перекладу — скан не має бути порожнім"


def find_skip_violations(source: str, filename: str = "<source>") -> list[str]:
    """Порушення політики skip/xfail у вихідному коді тестового модуля."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        name: str | None = None
        if isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.alias):
            name = node.name.rsplit(".", 1)[-1]
        if name in FORBIDDEN_NAMES:
            violations.append(f"{filename}:{getattr(node, 'lineno', '?')}: {name}")
        if isinstance(node, ast.Call) and _call_name(node) == "xfail":
            is_marker = isinstance(node.func, ast.Attribute) and _dotted(node.func).endswith(
                "mark.xfail"
            )
            strict = any(
                kw.arg == "strict" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            )
            if not (is_marker and strict):
                violations.append(f"{filename}:{node.lineno}: xfail без strict=True")
    return violations


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else None


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _parametrize_calls() -> Iterator[tuple[Path, ast.Call]]:
    for path in SCANNED_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "parametrize":
                yield path, node


PARAMETRIZE_CALLS = list(_parametrize_calls())
assert PARAMETRIZE_CALLS


@pytest.mark.parametrize("path", SCANNED_FILES, ids=lambda p: p.relative_to(TESTS_ROOT).as_posix())
def test_no_skip_or_non_strict_xfail(path: Path) -> None:
    assert find_skip_violations(path.read_text(encoding="utf-8"), path.name) == []


@pytest.mark.parametrize(
    ("path", "call"),
    PARAMETRIZE_CALLS,
    ids=[f"{p.name}:{c.lineno}" for p, c in PARAMETRIZE_CALLS],
)
def test_every_parametrize_has_non_empty_values(path: Path, call: ast.Call) -> None:
    values = (
        call.args[1]
        if len(call.args) > 1
        else next(kw.value for kw in call.keywords if kw.arg == "argvalues")
    )
    module_name = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
    namespace = vars(importlib.import_module(module_name))
    # Вираз береться з AST власного тестового файлу (не зовнішній ввід) і обчислюється в
    # namespace модуля, щоб перевірити фактичну довжину набору (константи, sorted(...), glob).
    evaluated = eval(compile(ast.Expression(values), str(path), "eval"), namespace)  # noqa: S307
    assert len(list(evaluated)) > 0, f"{path.name}:{call.lineno}: порожній parametrize"


@pytest.mark.parametrize(
    "snippet",
    [
        'import pytest\nlingua = pytest.importorskip("lingua")\n',
        "import pytest\npytest.skip('no docker')\n",
        "import pytest\n@pytest.mark.skipif(True, reason='x')\ndef test_a(): ...\n",
        "import pytest\n@pytest.mark.xfail(reason='flaky')\ndef test_a(): ...\n",
        "import pytest\ndef test_a():\n    pytest.xfail('later')\n",
        "from pytest import importorskip\n",
    ],
)
def test_scanner_catches_deliberate_skips(snippet: str) -> None:
    assert find_skip_violations(snippet)


def test_scanner_allows_strict_xfail() -> None:
    snippet = "import pytest\n@pytest.mark.xfail(strict=True, reason='bug')\ndef test_a(): ...\n"
    assert find_skip_violations(snippet) == []
