# Dependency-запит: WP-01C → WP-00 (CLI-контракт §16.2, `collector version`, Docker image)

| Поле | Значення |
|---|---|
| Від | WP-01C (`wp/01c-contracts`) |
| До | WP-00 (owner `tests/unit/test_cli.py`, `tests/unit/test_cli_adversarial.py`, `src/collector/core/version.py`, `Dockerfile` PR2) |
| Файли | `tests/unit/test_cli_adversarial.py`, `tests/unit/test_cli.py`, `src/collector/core/version.py`, `Dockerfile` |
| Стан | resolved (п.1–3 у `wp/01c-contracts` за рішенням оркестратора; п. Dockerfile → WP-00 PR2) |

## 1. Зробити групу `collector contracts` видимою у `--help`

Картка WP-01C додає до `src/collector/cli.py` sub-Typer `contracts` з командою
`export [--check] [--output <dir>]` (owned extension). `tests/unit/test_cli_adversarial.py::test_help_command_set_equals_spec_16_2_exactly`
(owned WP-00) вимагає, щоб набір top-level команд **дорівнював** §16.2 дослівно, тому в
WP-01C група зареєстрована як `hidden=True`: `collector contracts export` і
`collector contracts --help` працюють, але `collector --help` групу не показує.

Прохання: додати `"contracts"` до `SPEC_16_2_TOP_LEVEL` у `tests/unit/test_cli_adversarial.py`
(і, за бажанням, до `TOP_LEVEL_COMMANDS` у `tests/unit/test_cli.py`), після чого WP-01C
прибере `hidden=True` в `app.add_typer(contracts_app, name="contracts", hidden=True)`.
Альтернатива — лишити групу прихованою назавжди як dev/CI-команду; тоді запит закривається
без змін і CI крок `uv run collector contracts export --check` додається у `.github/workflows/ci.yml`
(owned WP-00) — див. п. 3.

## 2. `collector version` → реальна версія контрактів

`src/collector/core/version.py` друкує `schema_version=0.0.0-placeholder`
(`SCHEMA_VERSION_PLACEHOLDER`, з коментарем «реальну версію визначає WP-01C»). WP-01C
експортує `collector.contracts.CONTRACTS_VERSION = "1.0"`. Прохання замінити placeholder на
`from collector.contracts import CONTRACTS_VERSION` (імпорт легкий — `collector.contracts` не
тягне I/O-бібліотек, є тест `tests/contract/contracts/test_no_io_imports.py`) і оновити
`tests/unit/test_cli.py`/`test_cli_adversarial.py`, які порівнюють вивід із placeholder.

## 3. CI-крок drift-check і реєстр джерел у Docker image

- `.github/workflows/ci.yml` job `python`: додати `uv run collector contracts export --check`
  після `pytest` (дублює `tests/contract/contracts/test_schema_snapshots.py`, але дає явний
  рядок у логах CI за §16.2-стилем «команди як контракт»).
- `Dockerfile` (PR2): `SourceIdentity.source_id` валідується проти
  `docs/research/source-registry.yaml` (read-only loader; шлях — env `COLLECTOR_SOURCE_REGISTRY`
  або пошук `docs/research/source-registry.yaml` угору від пакета/cwd). Image має або
  `COPY docs/research/source-registry.yaml /app/docs/research/source-registry.yaml`, або
  `ENV COLLECTOR_SOURCE_REGISTRY=/app/config/source-registry.yaml` з відповідним `COPY`. Без
  цього будь-яка валідація `SourceIdentity` у контейнері кине `SourceRegistryError`.
