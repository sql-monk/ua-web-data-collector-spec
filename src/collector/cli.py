"""CLI `collector` — контракт команд §16.2 ТЗ.

У WP-00 усі команди, крім `version`, є типізованими стабами: вони друкують
`not implemented: owned by WP-XX` у stderr і завершуються з кодом 2. Власник WP
замінює тіло відповідної команди, не змінюючи її назву та параметри.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from collector.core.version import version_info
from collector.workers.roles import WorkerRole

NOT_IMPLEMENTED_EXIT_CODE = 2

app = typer.Typer(
    name="collector",
    help="UA Web Data Collector — CLI для workers, API, міграцій, e2e і releases (§16.2).",
    add_completion=False,
    # Plain-text help (без rich): детермінований вивід у CI/Docker логах і тестах.
    rich_markup_mode=None,
)
db_app = typer.Typer(
    help="Схеми сховищ: PostgreSQL migrations (WP-01A), Mongo validators (WP-01B)."
)
release_app = typer.Typer(help="Immutable dataset releases (§9.9); owner — WP-11A.")
contracts_app = typer.Typer(
    help="Shared data contracts: JSON Schema snapshots (§9.4); owner — WP-01C."
)
app.add_typer(db_app, name="db")
app.add_typer(release_app, name="release")
# hidden: §16.2 фіксує точний набір top-level команд, і tests/unit/test_cli_adversarial.py (WP-00)
# перевіряє його дослівно. `collector contracts ...` — dev/CI-команда WP-01C; зробити видимою —
# dependency-запит docs/plan/deps/WP-01C-to-WP-00.md.
app.add_typer(contracts_app, name="contracts", hidden=True)


def _help_when_no_subcommand(ctx: typer.Context) -> None:
    """Група без підкоманди друкує help і завершується кодом 0.

    Click `no_args_is_help` дає код 2 — той самий, що й стаби «not implemented»;
    тут виклик без підкоманди — не помилка і не стаб.
    """
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


for _group in (app, db_app, release_app, contracts_app):
    _group.callback(invoke_without_command=True)(_help_when_no_subcommand)


def not_implemented(owner: str) -> NoReturn:
    """Єдиний вихід для стабів: повідомлення у stderr і exit code 2."""
    typer.echo(f"not implemented: owned by {owner}", err=True)
    raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)


@app.command()
def version() -> None:
    """Друкує версію пакета, Git SHA (env COLLECTOR_GIT_SHA) і версію схеми контрактів."""
    typer.echo(version_info().render())


@db_app.command("ensure-mongo")
def db_ensure_mongo(
    validators: Annotated[
        bool, typer.Option("--validators", help="Застосувати $jsonSchema validators.")
    ] = False,
    indexes: Annotated[
        bool, typer.Option("--indexes", help="Створити/перевірити indexes.")
    ] = False,
) -> None:
    """Ініціалізує MongoDB replica set, validators та індекси (стаб; owner WP-01B)."""
    not_implemented("WP-01B")


@db_app.command("migrate")
def db_migrate() -> None:
    """Застосовує PostgreSQL migrations (стаб; owner WP-01A)."""
    not_implemented("WP-01A")


@app.command()
def e2e(
    source: Annotated[str, typer.Option("--source", help="source_id або `fixtures`.")],
    offline: Annotated[
        bool, typer.Option("--offline", help="Лише offline fixtures, без мережі.")
    ] = False,
) -> None:
    """Наскрізний прогін збору для одного джерела (стаб; owner WP-14)."""
    not_implemented("WP-14")


@release_app.command("build")
def release_build(
    watermark: Annotated[str, typer.Option("--watermark", help="Watermark release (§9.9).")],
    output: Annotated[Path, typer.Option("--output", help="Каталог для parts і manifest.")],
) -> None:
    """Збирає immutable dataset release (стаб; owner WP-11A)."""
    not_implemented("WP-11A")


@release_app.command("verify")
def release_verify(
    manifest: Annotated[Path, typer.Option("--manifest", help="Шлях до manifest.json.")],
) -> None:
    """Перевіряє manifest і checksums release (стаб; owner WP-11A)."""
    not_implemented("WP-11A")


@contracts_app.command("export")
def contracts_export(
    output: Annotated[
        Path, typer.Option("--output", help="Каталог snapshot-ів (`schemas/`).")
    ] = Path("schemas"),
    check: Annotated[
        bool,
        typer.Option("--check", help="Не писати; exit 1, якщо snapshot-и відрізняються."),
    ] = False,
) -> None:
    """Генерує JSON Schema snapshots shared-контрактів у `schemas/<group>/<name>.v<major>.json`."""
    from collector.contracts.schema_export import check_schemas, export_schemas

    if check:
        problems = check_schemas(output)
        for problem in problems:
            typer.echo(problem, err=True)
        if problems:
            typer.echo(
                f"schema drift: {len(problems)} проблем(и); виконайте `collector contracts export`",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"schemas up to date: {output}")
        return
    written = export_schemas(output)
    for path in written:
        typer.echo(path.as_posix())
    typer.echo(f"exported {len(written)} schemas to {output}")


@app.command()
def worker(
    role: Annotated[WorkerRole, typer.Argument(help="Роль worker pool за §7.6.")],
) -> None:
    """Запускає worker відповідної ролі (стаб; owner WP-01D)."""
    not_implemented("WP-01D")


@app.command()
def api() -> None:
    """Запускає operator/read API (стаб; owner WP-11A)."""
    not_implemented("WP-11A")


@app.command()
def scheduler() -> None:
    """Запускає singleton scheduler з advisory lease (стаб; owner WP-01D)."""
    not_implemented("WP-01D")


@app.command()
def controller() -> None:
    """Запускає desired-state controller worker pools (стаб; owner WP-01D)."""
    not_implemented("WP-01D")


def main() -> None:
    """Точка входу для `python -m collector.cli`."""
    app()


if __name__ == "__main__":
    main()
