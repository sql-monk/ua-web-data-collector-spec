"""Assert для CI: увесь стек healthy (gate 3, CR-2).

Читає `docker compose ps -a --format json` зі stdin (Compose v2 друкує один JSON-об'єкт на
рядок; старіші версії — один JSON-масив). Успіх (exit 0), лише якщо КОЖЕН контейнер або
`running` + `Health == healthy`, або one-shot `exited` з `ExitCode == 0`. `unhealthy`,
`starting`, `restarting`, `created`, ненульовий exit — помилка (exit 1) з переліком.
Без сторонніх залежностей: запускається системним python3 runner-а.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any


def parse_ps(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Рядки JSON (по одному або масив) → список контейнерів."""
    containers: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        parsed = json.loads(line)
        containers.extend(parsed if isinstance(parsed, list) else [parsed])
    return containers


def unhealthy(containers: Iterable[dict[str, Any]]) -> list[str]:
    """Опис кожного контейнера, що не є healthy/exited-0."""
    problems: list[str] = []
    for c in containers:
        state = c.get("State")
        health = c.get("Health") or ""
        exit_code = c.get("ExitCode")
        ok = (state == "running" and health == "healthy") or (state == "exited" and exit_code == 0)
        if not ok:
            problems.append(
                f"{c.get('Service') or c.get('Name')}: state={state} health={health or '-'} "
                f"exit={exit_code}"
            )
    return problems


def main() -> int:
    containers = parse_ps(sys.stdin)
    if not containers:
        print("no containers in `docker compose ps` output", file=sys.stderr)
        return 1
    problems = unhealthy(containers)
    if problems:
        print("NOT healthy:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"all {len(containers)} containers healthy or exited 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
