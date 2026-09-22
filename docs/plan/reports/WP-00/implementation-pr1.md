# WP-00 PR1 — звіт реалізації (`wp/00-1-python-ci`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR1 «Python foundation + CI» |
| Branch / worktree | `wp/00-1-python-ci` / `.worktrees/wp-00-1` |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR1» |
| Розділи ТЗ | §7.6, §8, §16.2, §17.1, §17.3, §18, Додаток A |
| Середовище | Windows 11, uv 0.12.13, CPython 3.13.9 (uv-managed), gitleaks 8.30.1, Docker 29.8 (лише для Linux-паритету) |
| Commits | `c4a9ccf feat(wp-00): Python foundation, CLI contract stubs, test network block, pre-commit and CI` + commit зі звітом |

## Що зроблено

### Python-проєкт (вимоги 1, 2, 5)

- `.python-version` = `3.13`; `pyproject.toml` (build backend `uv_build`, src-layout) і `uv.lock` (44 пакети).
- Пакет `collector` за Додатком A: `contracts, core, fetch, discovery, adapters/{news,vehicles,catalogs}, normalization, translation, persistence/{postgres,mongo}, workers, orchestration/{compose,swarm}, api, telemetry` — кожен `__init__.py` містить одне речення з owner-WP. Додано `src/collector/py.typed`.
- Foundation-залежності: `pydantic>=2.11,<3`, `structlog>=25.4`, `typer>=0.27`. Dev: `mypy`, `pre-commit`, `pytest`, `pytest-asyncio`, `pytest-socket`, `respx`, `ruff`. Жодного scrapy/httpx/sqlalchemy/pymongo як прямої залежності (`httpx` є у lock лише як транзитивна залежність `respx`, dev-group).
- `ruff`: line length 100, `select = E, F, I, B, UP, S`; правила `S` вимкнені для `tests/**` (per-file-ignores). `mypy --strict` + `warn_unreachable` + плагін `pydantic.mypy`. pytest: `asyncio_mode=auto`, `--strict-markers`, `--import-mode=importlib`, маркери `live | integration | e2e`.

### Вибір CLI framework — Typer (матеріал для ADR-0001)

Обрано **Typer** (`typer>=0.27`, поверх Click), а не «чистий» Click:

1. Сигнатури команд — типізовані Python-функції з `Annotated[...]`; `mypy --strict` перевіряє параметри без обгорток, а Enum `WorkerRole` (§7.6) стає валідованим аргументом `collector worker <role>` автоматично (невідома роль → usage error, а не стаб).
2. Той самий підхід «типи → контракт», що у Pydantic v2/FastAPI (§8): один стиль для CLI, API і контрактів.
3. Стаби для власників WP — одна функція на команду; заміна тіла не змінює назву/параметри команди (контракт §16.2 зафіксований тестами).
4. Ціна: транзитивні `rich`, `shellingham` (у сучасних версіях `typer-slim` — лише shim, що тягне `typer`; перевірено `uv tree`). Rich-форматування help вимкнено (`rich_markup_mode=None`) заради детермінованого plain-text виводу в CI/Docker-логах і тестах.

Логування: **structlog** із JSON renderer (`collector.core.logging.configure_logging/get_logger`): одна JSON-подія на рядок, ISO-8601 UTC timestamp, `ensure_ascii=False` для українських повідомлень; конфігурація ідемпотентна.

### CLI-контракт §16.2 (вимога 3)

`collector = "collector.cli:app"`. Стаби друкують `not implemented: owned by WP-XX` у stderr і завершуються з кодом 2:

| Команда | Owner |
|---|---|
| `collector db ensure-mongo [--validators] [--indexes]` | WP-01B |
| `collector db migrate` | WP-01A |
| `collector e2e --source <name> [--offline]` | WP-14 |
| `collector release build --watermark <w> --output <dir>` | WP-11A |
| `collector release verify --manifest <path>` | WP-11A |
| `collector worker <role>`, role ∈ discovery, fetch, browser, parse, projector, translation, export, maintenance | WP-01D |
| `collector api` | WP-11A |
| `collector scheduler`, `collector controller` | WP-01D |
| `collector version` — реальна | WP-00 |

`collector version` друкує `package_version` (importlib.metadata), `git_sha` (env `COLLECTOR_GIT_SHA`, інакше `unknown`) і `schema_version=0.0.0-placeholder` (константа `SCHEMA_VERSION_PLACEHOLDER` у `collector.core.version`; реальну версію задає WP-01C). Назви команд збігаються з §16.2 — правка ТЗ не потрібна.

### Тести (вимоги 4, 9)

- `tests/{unit,contract,integration,e2e,fixtures}` (`.gitkeep` у порожніх), `tests/conftest.py` прив'язує політику мережі до маркерів.
- Мережа заблокована глобально: `addopts = --disable-socket --allow-unix-socket ...`. `integration`/`e2e` отримують `allow_hosts([127.0.0.1, ::1])`; `live` — `enable_socket`.
- **Відхилення від картки (обґрунтоване):** на Windows loopback дозволений і для звичайних тестів, бо asyncio там емулює `socket.socketpair()` через AF_INET 127.0.0.1 — під повним `--disable-socket` не створюється жоден event loop (перевірено: `pytest_socket.SocketBlockedError` у setup async-тесту). Інваріант «connect до будь-якого не-loopback host кидає виняток» діє на всіх платформах; повна заборона створення socket — на POSIX (CI). Тест `test_socket_creation_is_blocked_on_posix` skip на Windows, зелений у Linux-контейнері (див. нижче).
- `tests/unit/test_cli.py` (25 тестів): `--help` містить усі команди; help груп `db`/`release`; `worker --help` містить усі 8 ролей §7.6 і `WorkerRole` збігається з ними; кожен стаб (17 варіантів argv) → код 2 і рядок з owner-WP; невідома роль відхиляється; `version` з env і без.
- `tests/unit/test_network_blocked.py` (4): блокування connect у звичайному тесті; блокування створення socket (POSIX); integration-маркер дозволяє лише loopback (реальний listener на 127.0.0.1 + відхилений connect до 192.0.2.1); asyncio event loop працює під блокуванням.
- `tests/unit/test_logging.py` (2): JSON-рядки, рівні, ідемпотентність.

### pre-commit, CI, шаблони (вимоги 6, 7, 8)

- `.pre-commit-config.yaml`: pre-commit-hooks v6.0.0 (eof/trailing-whitespace/yaml/toml/large-files/merge-conflict/private-key), ruff-pre-commit v0.16.8 (= версія ruff у lock), gitleaks v8.30.0, markdownlint-cli2 v0.23.3 (`exclude: ^\.claude/` — промпти субагентів із frontmatter не є документацією). Revs зафіксовано через `pre-commit autoupdate`.
- `.github/workflows/ci.yml`: jobs `python` (uv sync --frozen → ruff check → ruff format --check → mypy src → pytest -m "not live" → `collector --help`/`version`), `pre-commit` (усі hooks, кеш `~/.cache/pre-commit`), `secrets` (`gitleaks/gitleaks-action@v2`, fetch-depth 0). `concurrency` group per workflow+ref з cancel-in-progress; `permissions: contents: read`; secrets тільки через `secrets.*` (`GITHUB_TOKEN`, опційний `GITLEAKS_LICENSE`). `COLLECTOR_GIT_SHA=github.sha` передається у env. Крок `docker compose config --quiet` — у PR2.
- `.github/ISSUE_TEMPLATE/work-package.md` за §17.3 (усі 10 полів), `.github/pull_request_template.md` за §17.1 + DoD §18.
- `.editorconfig`; `.gitignore` доповнено (`*.egg-info/`, `.coverage`, `htmlcov/`, `.artifacts/`, `.uv-cache/`).

## Команди та вивід

Фінальний прогін із чистого стану у worktree (`git clean -xfd` → `uv sync --frozen`), Windows 11, `PYTHONUTF8=1`:

```text
$ git clean -xfd
Removing .mypy_cache/
Removing .pytest_cache/
Removing .ruff_cache/
Removing .venv/
Removing src/collector/__pycache__/
Removing src/collector/core/__pycache__/
Removing src/collector/workers/__pycache__/
Removing tests/__pycache__/
Removing tests/unit/__pycache__/
[exit 0]

$ uv sync --frozen
Using CPython 3.13.9
Creating virtual environment at: .venv
Installed 44 packages in 1.48s
 + annotated-doc==0.0.5
 + annotated-types==0.8.0
 + anyio==4.15.1
 + ast-serialize==0.11.2
 + certifi==2026.7.22
 + cfgv==3.5.0
 + collector==0.1.0 (from file:///C:/repos/webscraper/.worktrees/wp-00-1)
 + colorama==0.4.6
 + distlib==0.4.3
 + filelock==3.32.7
 + h11==0.16.0
 + httpcore==1.0.9
 + httpx==0.28.1
 + identify==2.6.19
 + idna==3.20
 + iniconfig==2.3.0
 + librt==0.15.0
 + markdown-it-py==4.2.0
 + mdurl==0.1.2
 + mypy==2.3.1
 + mypy-extensions==1.1.0
 + nodeenv==1.10.0
 + packaging==26.3
 + pathspec==1.1.1
 + platformdirs==4.11.12
 + pluggy==1.6.0
 + pre-commit==4.6.2
 + pydantic==2.13.5
 + pydantic-core==2.46.5
 + pygments==2.21.0
 + pytest==9.1.1
 + pytest-asyncio==1.4.0
 + pytest-socket==0.8.1
 + python-discovery==1.6.1
 + pyyaml==6.0.3
 + respx==0.23.1
 + rich==15.0.0
 + ruff==0.16.8
 + shellingham==1.5.4
 + structlog==26.1.0
 + typer==0.27.2
 + typing-extensions==4.16.0
 + typing-inspection==0.4.4
 + virtualenv==21.9.1
[exit 0]

$ uv run ruff check .
All checks passed!
[exit 0]

$ uv run ruff format --check .
48 files already formatted
[exit 0]

$ uv run mypy src
Success: no issues found in 24 source files
[exit 0]

$ uv run pytest -m not live
============================= test session starts =============================
platform win32 -- Python 3.13.9, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\repos\webscraper\.worktrees\wp-00-1
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0, socket-0.8.1, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collected 31 items

tests\unit\test_cli.py .........................                         [ 80%]
tests\unit\test_logging.py ..                                            [ 87%]
tests\unit\test_network_blocked.py .s..                                  [100%]

=========================== short test summary info ===========================
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
======================== 30 passed, 1 skipped in 3.04s ========================
[exit 0]

$ uv run collector --help
Usage: collector [OPTIONS] COMMAND [ARGS]...

  UA Web Data Collector — CLI для workers, API, міграцій, e2e і releases
  (§16.2).

Options:
  --help  Show this message and exit.

Commands:
  version     Друкує версію пакета, Git SHA (env COLLECTOR_GIT_SHA) і...
  e2e         Наскрізний прогін збору для одного джерела (стаб; owner...
  worker      Запускає worker відповідної ролі (стаб; owner WP-01D).
  api         Запускає operator/read API (стаб; owner WP-11A).
  scheduler   Запускає singleton scheduler з advisory lease (стаб; owner...
  controller  Запускає desired-state controller worker pools (стаб; owner...
  db          Схеми сховищ: PostgreSQL migrations (WP-01A), Mongo...
  release     Immutable dataset releases (§9.9); owner — WP-11A.
[exit 0]

$ uv run collector version
package_version=0.1.0
git_sha=unknown
schema_version=0.0.0-placeholder
[exit 0]

$ uv run pre-commit run --all-files
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Failed
- hook id: markdownlint-cli2
- exit code: 1

markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Finding: .github/ISSUE_TEMPLATE/work-package.md docs/plan/cards/WP-00.md README.md TECHNICAL_SPECIFICATION.md
Linting: 4 files
Summary: 5 issues in 1 file
docs/plan/cards/WP-00.md:85 error MD024/no-duplicate-heading Multiple headings with the same content [Context: "Owned files"]
docs/plan/cards/WP-00.md:101 error MD024/no-duplicate-heading Multiple headings with the same content [Context: "Команди перевірки"]
docs/plan/cards/WP-00.md:124 error MD024/no-duplicate-heading Multiple headings with the same content [Context: "Owned files"]
docs/plan/cards/WP-00.md:136 error MD024/no-duplicate-heading Multiple headings with the same content [Context: "Команди перевірки"]
docs/plan/cards/WP-00.md:144 error MD024/no-duplicate-heading Multiple headings with the same content [Context: "Acceptance"]
markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Finding: .github/pull_request_template.md docs/research/news-southern.md docs/research/news-western.md docs/plan/ledger.md
Linting: 4 files
Summary: 0 issues in 0 files
markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Finding: docs/IMPLEMENTATION_PLAN.md REVIEW.md docs/research/ua-marketplaces.md docs/plan/deps/WP-00-to-repo-config.md
Linting: 4 files
Summary: 0 issues in 0 files
markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Finding: docs/research/news-central-baltic.md
Linting: 1 file
Summary: 0 issues in 0 files

[exit 1]

$ uv run python -c import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml: valid YAML')
ci.yml: valid YAML
[exit 0]

$ gitleaks git --no-banner .
8:26AM INF 8 commits scanned.
8:26AM INF scanned ~528968 bytes (528.97 KB) in 703ms
8:26AM INF no leaks found
[exit 0]
```

### Паритет із CI (Linux)

Той самий commit у контейнері `ghcr.io/astral-sh/uv:python3.13-bookworm-slim` (Debian, read-only bind mount worktree, окремі cache dirs):

```text
$ uv sync --frozen --no-editable && uv run --no-sync ruff check . && uv run --no-sync mypy src \
  && uv run --no-sync pytest -m 'not live' -q -p no:cacheprovider && uv run --no-sync collector version
 + virtualenv==21.9.1
All checks passed!
Success: no issues found in 24 source files
...............................                                          [100%]
31 passed in 2.68s
package_version=0.1.0
git_sha=unknown
schema_version=0.0.0-placeholder
```

На Linux проходять усі 31 тест (без skip): повне блокування створення socket і asyncio під `--allow-unix-socket`.

### Додаткові перевірки

```text
$ uv run mypy tests
Success: no issues found in 4 source files

$ git ls-files | grep -i "\.env"
(порожньо — жодного .env у git)

$ gitleaks dir --no-banner .
INF no leaks found
```

## Підсумок команд картки

| Команда | Результат |
|---|---|
| `uv sync --frozen` | зелена |
| `uv run ruff check .` | зелена |
| `uv run ruff format --check .` | зелена |
| `uv run mypy src` | зелена |
| `uv run pytest -m "not live"` | зелена (Windows: 30 passed, 1 skipped; Linux: 31 passed) |
| `uv run collector --help` | зелена, усі команди §16.2 |
| `uv run pre-commit run --all-files` | **червона лише hook `markdownlint-cli2`** на `docs/plan/cards/WP-00.md` (MD024, 5 знахідок) — файл поза owned files; решта 10 hooks зелені; dependency-запит нижче |

## Що не перевірено

- `actionlint` недоступний локально; `ci.yml` перевірено лише `yaml.safe_load` (валідний YAML) і візуально. Реальний прогін GitHub Actions не виконувався (push заборонений правилами етапу) — `not testable offline`.
- `gitleaks/gitleaks-action@v2` у CI: поведінка ліцензії для org-репозиторіїв (`GITLEAKS_LICENSE`) не перевірена; локально gitleaks 8.30.1 (`gitleaks git`, `gitleaks dir`, pre-commit hook) — чистий.
- Hook `gitleaks` у pre-commit локально зібрано самим pre-commit (Go у PATH відсутній; pre-commit ≥3 завантажує toolchain сам) — так само працюватиме на ubuntu-latest, але у самому CI не перевірено.
- Windows-варіант policy мережі допускає loopback у unit-тестах (див. вище); повне блокування підтверджено лише у Linux-контейнері.
- Acceptance «чистий clone»: виконано через `git clean -xfd` у worktree, не через окремий `git clone`.

## Ризики

1. **markdownlint MD024 на картках WP** — до зміни `.markdownlint-cli2.jsonc` job `pre-commit` у CI буде червоним на будь-якому branch, де є картки з повторюваними заголовками. Мітигація: dependency-запит (нижче); тимчасова альтернатива — `exclude: ^docs/plan/cards/` у hook.
2. GitHub Actions пінені за major-тегом (`actions/checkout@v5`, `astral-sh/setup-uv@v6`, `actions/cache@v4`, `gitleaks/gitleaks-action@v2`), не за SHA — supply-chain ризик; пін на SHA рекомендується у WP-13 (security review).
3. `typer` тягне `rich`/`shellingham` у runtime image (PR2) — невеликий розмір, але зайва поверхня; перевірити у WP-13.
4. `pytest-socket` на Windows патчить лише `connect` (allow-hosts режим): DNS-резолв імені хоста в unit-тесті на Windows технічно можливий до відхилення connect. На CI (Linux) заборонено все.
5. `respx` тягне `httpx` у dev-group; при появі `httpx` як runtime-залежності (WP-02/03) версію треба узгодити з `respx`.

## Як вимкнути або відкотити

PR не має runtime-ефекту (каркас, стаби з кодом 2, CI). Відкат — `git revert` merge commit або видалення branch `wp/00-1-python-ci`. Тимчасово увімкнути мережу в конкретному тесті — маркер `@pytest.mark.enable_socket` (лише для `live`); глобально — прибрати `--disable-socket` з `addopts` (не рекомендовано, порушує §8/§16.1).

## Dependency-запити

- `docs/plan/deps/WP-00-to-repo-config.md` — власнику repo-level конфігів: додати `"MD024": { "siblings_only": true }` у `.markdownlint-cli2.jsonc` (файл поза owned files WP-00). Після цього `uv run pre-commit run --all-files` очікувано повністю зелений.
- Для docs-writer (етап 5, поза scope цього PR): ADR-0001 «Стек та інструменти foundation» — матеріал у розділі «Вибір CLI framework — Typer»; README «Швидкий старт розробника»: `uv sync --frozen`, `uv run pre-commit install`, команди §16.2.
