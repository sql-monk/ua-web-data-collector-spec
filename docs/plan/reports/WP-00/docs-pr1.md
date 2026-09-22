# WP-00 PR1 — звіт документування (`wp/00-1-python-ci`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR1 «Python foundation + CI» |
| Branch / worktree | `wp/00-1-python-ci` / `.worktrees/wp-00-1` |
| Етап | 5 (docs), після вердикту `accept` пострев'ю (`docs/plan/reports/WP-00/spec-review-pr1.md`) |
| Джерела | картка `docs/plan/cards/WP-00.md` (розділ PR1 і «Docs»); `implementation-pr1.md`, `testing-pr1.md`, `code-review-pr1.md`, `spec-review-pr1.md`; `TECHNICAL_SPECIFICATION.md` §8, §13, §16.2, §20; код `src/collector/cli.py`, `src/collector/core/logging.py`, `src/collector/core/version.py`, `tests/conftest.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` |

## Що зроблено

1. **`README.md`** — додано розділ «Швидкий старт розробника»: вимоги (uv
   0.12+, Python 3.13 через uv, Node 24 і Docker як вимоги майбутніх PR2/PR3),
   `uv sync --frozen` + `uv run pre-commit install`, контракт перевірки §16.2
   (лише команди, що вже зелені в PR1: `ruff check/format`, `mypy src`,
   `pytest -m "not live"`, `collector --help`, `pre-commit run --all-files`),
   `uv run collector --help` з таблицею стабів і їхніх owner-WP, посилання на
   ADR-0001. У список документів на початку файлу додано рядки-посилання на
   `docs/IMPLEMENTATION_PLAN.md` і `docs/plan/ledger.md`. Наявний вміст README
   не змінювався, лише додано новий розділ і два рядки посилань.
2. **`docs/decisions/0001-foundation-stack.md`** (новий файл, каталог
   `docs/decisions/` створено вперше) — ADR за форматом §20 (Context,
   Decision, Consequences, Date: 2026-09-22, Owner: WP-00, Status: accepted):
   Typer як CLI framework з обґрунтуванням реалізатора; structlog JSON +
   `ProcessorFormatter` + redaction processor з переліком ключів
   `REDACTED_KEYS` з коду (`src/collector/core/logging.py:30-54`, секрети та
   контакти); заборона мережі в тестах через pytest-socket із задокументованим
   Windows-відхиленням (loopback дозволений для unit-тестів; `SelectorEventLoop`
   / `WindowsSelectorEventLoopPolicy`; межа — явний `ProactorEventLoop`);
   правило «стаб визначається рядком stderr `not implemented: owned by
   WP-XX`, а не кодом 2 — Click usage-помилки теж повертають 2, споживачі
   мають перевіряти stderr»; вибір `uv_build`; pre-commit hooks, зокрема
   `gitleaks-history` як `manual` stage і CI job `secrets` з `fetch-depth: 0`;
   структура `ci.yml` (jobs `python`/`pre-commit`/`secrets`, concurrency,
   permissions, джерело secrets). ADR покриває знахідки 1–3 пострев'ю
   (`spec-review-pr1.md`): відсутність ADR-0001 (знахідка 2), Windows-межі
   мережевої політики (знахідка 4/2б) і колізію exit code 2 (знахідка 3).
3. **Docstrings** — перевірено `src/collector/cli.py` і
   `src/collector/core/logging.py`: усі публічні функції вже мали docstrings
   (`_help_when_no_subcommand`, `not_implemented`, `version`, `db_ensure_mongo`,
   `db_migrate`, `e2e`, `release_build`, `release_verify`, `worker`, `api`,
   `scheduler`, `controller`, `main` у `cli.py`; `redact_secrets`,
   `configure_logging`, `get_logger` у `core/logging.py`, разом із модульним
   docstring). Прогалин не знайдено — код не змінювався.
4. **`docs/plan/reports/WP-00/docs-pr1.md`** — цей звіт.

## Lint

```text
$ npx --yes markdownlint-cli2 README.md docs/decisions/0001-foundation-stack.md
markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Finding: README.md docs/decisions/0001-foundation-stack.md
Linting: 2 files
Summary: 0 issues in 0 files
[exit 0]

$ npx --yes markdown-link-check -c .markdown-link-check.json README.md
FILE: README.md
  [✓] TECHNICAL_SPECIFICATION.md
  [✓] docs/research/ua-marketplaces.md
  [✓] docs/research/news-central-baltic.md
  [✓] docs/research/news-western.md
  [✓] docs/research/news-southern.md
  [✓] docs/research/source-registry.yaml
  [✓] REVIEW.md
  [✓] docs/IMPLEMENTATION_PLAN.md
  [✓] docs/plan/ledger.md
  [✓] https://docs.astral.sh/uv/
  [✓] docs/decisions/0001-foundation-stack.md
  11 links checked.
[exit 0]
```

Docstring-зміни в коді відсутні (усі публічні функції вже документовані),
тому повторний прогін `ruff check`/`ruff format --check`/`mypy src` для цього
етапу не потрібен — код не змінювався.

## Файли

- `README.md` (оновлено)
- `docs/decisions/0001-foundation-stack.md` (новий)
- `docs/plan/reports/WP-00/docs-pr1.md` (новий, цей звіт)
