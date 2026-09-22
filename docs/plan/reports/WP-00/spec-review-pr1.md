# WP-00 PR1 — пострев'ю за ТЗ (`wp/00-1-python-ci`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR1 «Python foundation + CI» |
| Branch / worktree | `wp/00-1-python-ci` / `.worktrees/wp-00-1` |
| Рев'юваний commit | `8947f40` (`git diff main...HEAD`, base `b3dafd8`; `main` попереду лише на 3 ledger-коміти) |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR1» (вимоги 1–9, Acceptance, Rollback) + «Спільні правила» |
| Розділи ТЗ | §17.2 (WP-00, частково), §16.2, §16.3, §18, Додаток A, Додаток C, §8, §13, §17.1, §17.3, FR-013, §20 Q-006 |
| REVIEW.md | R-51 (частина CI/repo), R-11 (redaction у логах) |
| Вхідні звіти | `implementation-pr1.md` (з розділами «Виправлення після gate 2» і «Відповіді на код-рев'ю»), `testing-pr1.md` (фінал `pass`), `code-review-pr1.md` (`approve`, 0 critical/high) |
| Рев'юер | wp-spec-reviewer, read-only; записи — лише цей файл і `docs/acceptance/traceability.md` |

## Вердикт

**`accept`** — `missing`: 0; `partial`: 5 (усі — або очікувані межі під-PR1, або заплановані у конвеєрі етапи docs/merge, або low-знахідка без блокування); знахідок critical/high: 0. Усі 11 low/medium знахідок код-рев'ю мають статус `fixed`/`accepted (owner, дата)`/`not applicable (аргумент)`, і кожен «fixed» підтверджено у коді (розділ 6).

Обсяг приймання: **лише частина PR1** рядка WP-00 у §17.2 — repo layout, Python locks, CI. Docker images, Compose profiles/networks/volumes/secrets, migrations, SBOM і clean-host smoke — PR2; web locks — PR3. Ці елементи в матриці позначені `not applicable (PR2/PR3)` і **не** закриваються цим вердиктом.

## Власна верифікація (Windows 11 + Linux-контейнер)

Усі команди виконано рев'юером у worktree (`PYTHONUTF8=1`, uv 0.12.13, CPython 3.13.9):

| Команда | Результат |
|---|---|
| `uv sync --frozen` | exit 0, 45 пакетів; `uv lock --check` — exit 0 |
| `uv run ruff check .` | `All checks passed!`, exit 0 |
| `uv run ruff format --check .` | `55 files already formatted`, exit 0 |
| `uv run mypy src` | `Success: no issues found in 24 source files`, exit 0 |
| `uv run pytest -m "not live"` | **118 passed, 1 skipped** (POSIX-only тест), exit 0 |
| `uv run collector --help` | exit 0; Commands: `version e2e worker api scheduler controller db release` |
| `uv run collector version` | `package_version=0.1.0 / git_sha=unknown / schema_version=0.0.0-placeholder`, exit 0 |
| 10 стабів §16.2 (`db migrate`, `db ensure-mongo --validators --indexes`, `e2e --source fixtures --offline`, `release build …`, `release verify …`, `worker fetch`, `api`, `scheduler`, `controller`) | кожен: stderr `not implemented: owned by WP-XX`, **exit 2** |
| `uv run pre-commit run --all-files` | усі 11 hooks `Passed`, exit 0 |
| `gitleaks git --no-banner .` (8.30.1) | `21 commits scanned … no leaks found`, exit 0 |
| `git ls-files \| grep -i "\.env"` | порожньо |
| `python -c "yaml.safe_load(ci.yml)"` | валідний YAML; `actionlint` недоступний |
| Linux-паритет: `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`, свіжий `git archive HEAD` → `uv sync --frozen --no-editable && ruff check . && mypy src && pytest -m "not live"` | `All checks passed!`; `no issues found in 24 source files`; **119 passed, 0 skipped** (повний `disable_socket`: `SocketBlockedError: A test tried to use socket.getaddrinfo`) |

## 1. Acceptance criteria (картка PR1, §17.2, §16.2, §16.3)

### 1.1. Acceptance картки PR1

| Вимога | Доказ | Статус |
|---|---|---|
| Усі 7 команд перевірки зелені у чистому clone | Власний прогін (таблиця вище); `implementation-pr1.md` «Команди та вивід після код-рев'ю» (`git clean -xfd` → усі зелені); `testing-pr1.md` §10.1. Окремий `git clone` не робився — еквівалент: `git archive HEAD` у свіжий репозиторій у Linux-контейнері (119 passed) | evidenced |
| `collector --help` показує всі команди §16.2; назви збігаються з §16.2 | `src/collector/cli.py:56-132` (version, db {ensure-mongo, migrate}, e2e, release {build, verify}, worker, api, scheduler, controller); `tests/unit/test_cli_adversarial.py:117-130` (точна множина команд = §16.2, груп db/release); `tests/unit/test_cli.py:42-65`; ТЗ §16.2 не змінювалось (diff не торкається `TECHNICAL_SPECIFICATION.md`) | evidenced |
| Тест доводить, що socket-з'єднання у звичайному тесті кидає виняток | `tests/unit/test_network_blocked.py:22-24` (sync `create_connection` → `SocketBlockedError`/`SocketConnectBlockedError`), `:59-69` (`asyncio.run(open_connection)` у sync-тесті); `tests/unit/test_network_block_adversarial.py:38-76` (`asyncio.open_connection`, `httpx.AsyncClient`, `httpx.Client`, `urllib`, сирий `socket.connect`); POSIX-only `test_socket_creation_is_blocked_on_posix` (`:27-33`) зелений у Linux-контейнері | evidenced |
| `gitleaks` чистий; жодного `.env` у git | Власний прогін `gitleaks git` → no leaks (21 commits); `git ls-files` без `.env`; `.gitignore:6-8` (`.env`, `.env.*`, `!.env.example`); `tests/unit/test_foundation_config.py:131-148` | evidenced |
| `ci.yml` валідний (`actionlint` або `yaml.safe_load`) | `yaml.safe_load` OK (власний прогін); `tests/unit/test_foundation_config.py:110-128` (структура, команди §16.2, `concurrency`, без literal-секретів); `actionlint` недоступний — реальний прогін Actions не виконувався (див. знахідку 6) | evidenced (actionlint — not testable offline) |
| Rollback/disable: PR без runtime-ефекту, відкат — revert merge commit | Diff: лише каркас, стаби з кодом 2, CI/pre-commit/templates; `implementation-pr1.md` «Як вимкнути або відкотити» | evidenced |

### 1.2. Вимоги 1–9 картки PR1

| # | Вимога | Доказ | Статус |
|---|---|---|---|
| 1 | Python 3.13, uv + pyproject + uv.lock; layout Додатка A з `__init__.py` і docstring про owner-WP | `.python-version` = `3.13`; `pyproject.toml:6` (`>=3.13,<3.14`), `:37-43` (`uv_build`, src-layout); `uv.lock` (45 пакетів, `uv lock --check` OK); 20 пакетів `src/collector/**/__init__.py` — кожен з `WP-XX` (перевірено `cat`; `tests/unit/test_foundation_config.py:22-60` — 20 параметризованих кейсів зелені); `src/collector/py.typed` | evidenced |
| 2 | Foundation-залежності (`pydantic>=2`, typer/click, structlog); dev: ruff, mypy, pytest, pytest-asyncio, pytest-socket, respx, pre-commit; без scrapy/httpx/sqlalchemy/pymongo; вибір CLI зафіксувати в ADR-0001 | `pyproject.toml:11-15` (`pydantic>=2.11,<3`, `structlog>=25.4`, `typer>=0.27`), `:20-35` (dev-група; `httpx` — лише dev, з коментарем; `pyyaml`/`types-pyyaml` для тестів); `tests/unit/test_foundation_config.py:69-88`. **ADR-0001 у diff відсутній** — за карткою розділ «Docs (етап 5, після кожного PR)» ADR пише docs-writer; матеріал — `implementation-pr1.md` «Вибір CLI framework — Typer» | partial (ADR-0001 — етап 5; знахідка 2) |
| 3 | CLI `collector` (entry point `collector.cli:app`) зі стабами §16.2 → exit 2 + `not implemented: owned by WP-XX`; `worker <role>` для 8 ролей §7.6; `version` реальна (package version, Git SHA з `COLLECTOR_GIT_SHA` або `unknown`, schema placeholder) | `pyproject.toml:17-18`; `src/collector/cli.py:50-53` (`not_implemented`), `:62-132` (стаби з owner), `:56-59` (`version`); `src/collector/workers/roles.py:8-18` (8 ролей); `src/collector/core/version.py:11-14,45-56`; власний прогін 10 стабів → exit 2; `tests/unit/test_cli.py:68-72,90-104`; `tests/unit/test_cli_adversarial.py:59-76,133-163` (stderr-only, entry point `console_scripts`, `python -m collector.cli version` в окремому процесі) | evidenced |
| 4 | `tests/{unit,contract,integration,e2e,fixtures}`; `conftest.py` з маркерами `live/integration/e2e`; мережа заблокована за замовчуванням (`--disable-socket` в addopts, loopback для integration); тест блокування | `tests/{contract,e2e,fixtures,integration}/.gitkeep`; `pyproject.toml:71-78` (addopts `--disable-socket --allow-unix-socket --strict-markers`, маркери); `tests/conftest.py:49-55` (`allow_hosts(127.0.0.1,::1)` для `integration`/`e2e` через маркер — еквівалент `--allow-hosts`; `enable_socket` для `live`), `:58-72` (Selector loop policy/factory); `tests/unit/test_network_blocked.py:36-50` (integration → лише loopback). **Відхилення:** на Windows loopback дозволений і для unit-тестів (`conftest.py:54`, docstring `:5-27`) — обґрунтовано (asyncio socketpair через AF_INET), у CI (Linux) повний блок підтверджено власним прогоном | evidenced (з документованим Windows-відхиленням; знахідка 4) |
| 5 | ruff (E, F, I, B, UP, S; line 100) + format; `mypy --strict` для `src`; pytest `asyncio_mode=auto`, markers | `pyproject.toml:45-59` (`line-length = 100`, `select = E F I B UP S`, per-file-ignores для `tests/**` лише `S101,S603,S607`), `:61-65` (`strict = true`, `warn_unreachable`, `pydantic.mypy`), `:72-78`; `tests/unit/test_foundation_config.py:91-107`; власні прогони ruff/mypy зелені | evidenced |
| 6 | `.pre-commit-config.yaml`: ruff, ruff-format, gitleaks, eof/trailing-whitespace, markdownlint-cli2 | `.pre-commit-config.yaml:9-10` (eof/trailing), `:20-24` (ruff v0.16.8 = `uv.lock`), `:27-40` (gitleaks staged + manual `gitleaks-history`), `:43-48` (markdownlint-cli2 для `*.md`); власний `pre-commit run --all-files` — усі hooks Passed | evidenced |
| 7 | `ci.yml`: push/PR → `uv sync --frozen`, ruff check, ruff format --check, mypy src, pytest -m "not live", gitleaks; concurrency per branch; secrets лише з GitHub Environments | `.github/workflows/ci.yml:7-10` (push main + pull_request), `:12-14` (`concurrency: ${{ github.workflow }}-${{ github.ref }}`), `:41-60` (усі 5 python-команд §16.2 + `--help`/`version`), `:90-104` (job `secrets`: gitleaks-action, `fetch-depth: 0`, `${{ secrets.GITHUB_TOKEN }}`/`${{ secrets.GITLEAKS_LICENSE }}`), `:16-17` (`permissions: contents: read`); `tests/unit/test_foundation_config.py:110-128`. Крок `docker compose config --quiet` — PR2 (за карткою) | evidenced (реальний прогін Actions — not testable offline; знахідка 6) |
| 8 | Issue template §17.3; PR template §17.1 | `.github/ISSUE_TEMPLATE/work-package.md:18-65` — усі 10 полів §17.3 (scope, out-of-scope, owned files, input/output contract+version, fixtures, команди, acceptance, залежності, source coverage, rollback/disable); `.github/pull_request_template.md:10-49` — зміни, тести з виводом, fixture provenance, ризики, як вимкнути/відкотити, що не перевірено live, посилання на звіти етапів, чекліст DoD | evidenced |
| 9 | Unit-тести: `--help` містить усі команди; кожен стаб → 2 + owner; `version`; network block | `tests/unit/test_cli.py` (27), `test_cli_adversarial.py` (43), `test_network_blocked.py` (5), `test_network_block_adversarial.py` (6), `test_foundation_config.py` (27), `test_logging.py` (11) — 118 passed / 1 skipped (Windows), 119 passed (Linux); mutation-звіт `testing-pr1.md` §6 (мутації A/A2/B2/C/D ловляться) | evidenced |

### 1.3. Спільні правила картки

| Правило | Доказ | Статус |
|---|---|---|
| Owned files лише перелічені; forbidden `docs/research/**`, `TECHNICAL_SPECIFICATION.md`, `REVIEW.md` | `git diff main...HEAD --stat`: 49 файлів, усі в owned-переліку PR1, крім `docs/plan/deps/WP-00-to-repo-config.md` — процесний dependency-запит, дозволений планом (`docs/IMPLEMENTATION_PLAN.md` §4.1: «створює `docs/plan/deps/<WP>-to-<owner>.md`»), стан `resolved`. Forbidden-файли не змінені | evidenced |
| Версії pinned через `uv.lock` | `uv.lock` з hashes; CI `UV_FROZEN=1` (`ci.yml:20`); `uv lock --check` OK | evidenced |
| Українська у README/коментарях, ідентифікатори англійською | `cli.py`, `logging.py`, `conftest.py`, templates — українською; ідентифікатори англійською | evidenced |

### 1.4. §17.2 рядок WP-00 (частково — PR1)

| Елемент результату §17.2 | Доказ | Статус |
|---|---|---|
| repo layout | розділ 1.2 вимога 1; Додаток A: `src/collector/**` (усі 20 підпакетів), `tests/{unit,contract,integration,e2e,fixtures}`, `.github/{workflows,ISSUE_TEMPLATE}`, `pyproject.toml`, `uv.lock`. Решта дерева Додатка A (`web/`, `schemas/`, `migrations/`, `research/`, `deploy/`, `sources/`, `docs/{adr,runbooks,decisions}`, `dashboards/`, `docker-compose.yml`) — PR2/PR3 та WP-власники | evidenced (частина PR1) |
| Python locks | `uv.lock`, `.python-version`, `UV_FROZEN` у CI | evidenced |
| web locks | — | not applicable (PR3) |
| multi-stage images, Compose profiles/networks/volumes/secrets, migrations, SBOM | — | not applicable (PR2) |
| CI | `ci.yml` jobs `python`, `pre-commit`, `secrets`; compose/build/SBOM кроки — PR2 | partial (за задумом: python-частина CI; compose/build/SBOM у PR2) |
| clean-host stack smoke green | — | not applicable (PR2/PR3; закриває acceptance усього WP-00) |

### 1.5. §16.2 — кожна команда контракту має відповідник у CLI/CI або обґрунтоване відхилення

| Команда §16.2 | Відповідник | Статус |
|---|---|---|
| `uv sync --frozen`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -m "not live"` | `ci.yml:41-54`; pre-commit hooks ruff; власний прогін | evidenced |
| `docker compose config --quiet` / `build --pull` / `up -d --wait` / `--scale` | — | not applicable (PR2, WP-01D) |
| `uv run alembic upgrade head` | alembic не є foundation-залежністю (картка, вимога 2 — додає WP-01A); CLI-обгортка `collector db migrate` — стаб `cli.py:75-78` → WP-01A | not applicable (owner WP-01A; стаб є) |
| `uv run collector db ensure-mongo --validators --indexes` | `cli.py:62-72`; exit 2, owner WP-01B | evidenced (стаб) |
| `uv run collector e2e --source fixtures --offline` | `cli.py:81-89`; owner WP-14 | evidenced (стаб) |
| `uv run collector release build --watermark test --output .artifacts/release` / `release verify --manifest …` | `cli.py:92-106`; owner WP-11A; `.artifacts/` у `.gitignore` | evidenced (стаб) |
| `duckdb ':memory:' -c …` | — | not applicable (WP-11B) |
| `cd web && npm ci && …` | — | not applicable (PR3) |
| «Фінальні назви CLI можуть змінитися один раз у foundation PR» | Назви збігаються з §16.2 буквально; ТЗ не змінювалось; додаткові команди `worker <role>`, `api`, `scheduler`, `controller`, `version` — за карткою (§7.5/§7.6), не суперечать §16.2 | evidenced |

### 1.6. §16.3 дотичні пункти

| Пункт §16.3 | Статус |
|---|---|
| «чистий Docker host підіймає core/workers/gui однією documented командою; migrations/validators до readiness; restart не втрачає named-volume data» | not applicable (PR2/PR3 за карткою; для PR1 нема runtime) |
| «GUI/API не мають Docker socket» | not applicable (PR2; R-55) |
| Решта пунктів §16.3 | not applicable (домені WP) |

## 2. DoD §18 — дев'ять пунктів

| # | Пункт DoD | Доказ | Статус |
|---|---|---|---|
| 1 | Реалізація відповідає одному issue/WP, без сторонніх змін | `git diff main...HEAD --stat` — 49 файлів у owned-переліку PR1 (+ дозволений deps-файл); жодного доменного коду, лише стаби з owner | evidenced |
| 2 | formatter, lint, types, unit/contract/integration tests пройшли | Власний прогін: ruff check/format, mypy src, pytest (118+1 skip Windows; 119 Linux); contract/integration — порожні рівні з `.gitkeep` (нема що виконувати) | evidenced |
| 3 | Зміна схеми має migration і compatibility evidence | Схем у PR1 немає (`schema_version=0.0.0-placeholder`, owner WP-01C) | not applicable (без змін схем) |
| 4 | Зміна timestamp/matching/release contract має temporal/replay/reproducibility evidence | Таких контрактів у PR1 немає (release — стаби) | not applicable |
| 5 | Новий адаптер має manifest, fixtures, golden, coverage, quality sample, live smoke | Адаптерів немає | not applicable |
| 6 | Документація, метрики й runbook оновлені | Звіти етапів є (`implementation/testing/code-review/spec-review-pr1.md`); docstrings усіх модулів/команд; README «Швидкий старт розробника» та ADR-0001 — етап 5 (docs-writer) за карткою «Docs (етап 5, після кожного PR)» і планом §4.6; метрик/runbook у PR1 не з'явилось | partial (README/ADR-0001 — етап 5; знахідка 2) |
| 7 | Secret scan чистий; публічні контакти — лише у Mongo domain/artifacts, не в fixtures/логах | gitleaks: власний прогін + pre-commit staged/history hooks + CI job `secrets`; fixtures відсутні; логи: `core/logging.py:28-66` redaction (secrets); контактів у PR1 немає (див. R-11 у розділі 4) | evidenced |
| 8 | Знахідки рев'юерів позначені `fixed`/`accepted with owner/date`/`not applicable` | `implementation-pr1.md` «Відповіді на код-рев'ю»: 11 low/medium + 6 info — усі зі статусом; «Виправлення після gate 2»: знахідки 1–7 тестування закриті, 8–11 accepted; `testing-pr1.md` §10.3 підтверджує. «fixed» перевірено в коді — розділ 6 | evidenced |
| 9 | PR злитий лише після CI та required review; commit SHA і release evidence зафіксовані | Не злитий (етап 6 — оркестратор); code-review `approve`, spec-review `accept` (цей звіт); commit `8947f40`; реальний прогін GitHub Actions не виконувався (push на цьому етапі заборонений) | partial (очікує етап 6: CI зелений на GitHub → merge; знахідка 6) |

## 3. Додаток C — рядки, які покриває PR1

| Ціль Додатка C | Вимоги | Що покриває PR1 | Доказ | Статус |
|---|---|---|---|---|
| Незалежна реалізація | §17, §18 | Issue/PR templates, CI-контракт, owner-стаби, WP acceptance | `.github/ISSUE_TEMPLATE/work-package.md`, `.github/pull_request_template.md`, `ci.yml`, `cli.py` (owner у кожному стабі), цей звіт | evidenced |
| Технічна безпека | FR-013, §13 | Secrets: `.env` ігноруються, CI secrets лише `${{ secrets.* }}`, gitleaks pre-commit+CI, redaction Authorization/Cookie/API keys у логах | `.gitignore:6-8`; `ci.yml:101-104`; `.pre-commit-config.yaml:27-40`; `core/logging.py:28-66`; `tests/unit/test_logging.py:73-117` (redaction case-insensitive, рекурсивно, stdlib `extra`); `test_foundation_config.py:124-128,131-148` | evidenced (частина: secret checks/scans; SSRF/XXE — WP-02/WP-13) |
| Docker і масштабування | FR-030—FR-033, §7.5—§7.6 | Лише перелік ролей §7.6 і стаби `worker/scheduler/controller` | `workers/roles.py:8-18`; `cli.py:109-132`; `tests/unit/test_cli_adversarial.py:68-76` | partial (ролі/CLI-контракт; images/Compose/scale — PR2, WP-01D) |
| Керований збір, Доказовість, Історія, Якість, Переклад, Повні поля, Експлуатація, Узгодженість БД, Часова коректність, Керована історія, Оборотний matching, Відтворювані дослідження, Capacity, Operator GUI | — | — | — | not applicable (доменні WP; PR1 дає лише layout/стаби) |

## 4. Регресія REVIEW.md

| R-знахідка (severity) — що вимагає | Доказ у коді/тестах PR1 | Статус |
|---|---|---|
| R-51 (high) — контейнерна поставка і відтворюваний clean-host start | Частина PR1: відтворювана Python-збірка (lockfile, `UV_FROZEN`), CI на кожен push/PR, layout: `uv.lock` + `uv lock --check`; `ci.yml:19-21,41-42`; `.python-version`; Linux-паритет підтверджено контейнерним прогоном | evidenced (частина CI/repo; images/Compose/clean-host — PR2/PR3) |
| R-11 (medium) — контакти не дублюються у технічних logs/metrics | Централізований processor `redact_secrets` у спільному ланцюгу structlog + stdlib (`core/logging.py:57-66,69-81`) — приховує Authorization/Cookie/API keys/token/password/secret; тести `test_logging.py:73-117`. **Ключів контактів (`phone`, `email`, `contacts`) у `REDACTED_KEYS` (`logging.py:28-43`) немає**; контактних даних у PR1 не існує | partial (механізм є і перевірений; контактні ключі — знахідка 1, owner нижче) |

## 5. Q-питання §20

| Q — default §20 | Перевірка у PR1 | Статус |
|---|---|---|
| Q-006 — інфраструктурний бюджет/SLO: один хост MVP, SLO §2.4 | PR1 не містить інфраструктурних рішень (без Docker/Compose/ресурсів); нічого не суперечить single-host default; ADR — у PR2 за карткою | evidenced (default не порушено; ADR-0002 — PR2) |
| Q-013 — deployment mode: Compose MVP | Не зачіпає PR1; стаби `orchestration/{compose,swarm}` мають owner WP-01D | not applicable (PR2/WP-01D) |

## 6. Звірка «fixed» код-рев'ю з кодом

| Знахідка код-рев'ю | Заявлено | Перевірено у коді | Збіг |
|---|---|---|---|
| 1 medium — stdlib-записи не JSON | fixed `ce0c942` | `core/logging.py:93-99` `ProcessorFormatter(foreign_pre_chain=shared, processors=[remove_processors_meta, JSONRenderer])`, `:106-111` `wrap_for_formatter`; тест `test_logging.py:47-70` | так |
| 2 low — `asyncio.run` у sync-тесті на Windows | fixed | `tests/conftest.py:58-65` `WindowsSelectorEventLoopPolicy` у `pytest_configure`; тест `test_network_blocked.py:59-69` | так |
| 3 low — `"tests/**" = ["S"]` | fixed | `pyproject.toml:53-56` → `["S101", "S603", "S607"]` | так |
| 4 low — redaction processor | fixed | `core/logging.py:28-66`; 3 тести `test_logging.py:73-117` | так |
| 5 low — `httpx`/`pyyaml` не оголошені | fixed | `pyproject.toml:22-24,30-31`; `uv.lock` (45 пакетів) | так |
| 6 low — `gitleaks-history` на кожен commit | fixed | `.pre-commit-config.yaml:31-40` `stages: [manual]` + коментар з командою | так |
| 7 low — подвійний прогін/cancel на main | fixed | `ci.yml:7-14` `push: branches: [main]`, `cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}` | так |
| 8 low — Actions за mutable-тегами | accepted (owner WP-13, дата — етап WP-13) | статус з owner і датою; `ci.yml:30,32,67,69,82,95,100` — теги `@v5/@v6/@v4/@v2` (без змін, як заявлено) | коректно |
| 9 low — exit 2 стабів = usage error | fixed мінімально | `cli.py:35-47` callback `invoke_without_command` → help + exit 0 для груп; тест `test_cli.py:81-87`. Колізія 2/2 — spec-mismatch кандидат, див. знахідку 3 | так |
| 10 low — дублювання тестів | accepted (owner wp-tester, дата — пострев'ю WP-00) | два файли лишаються; owner/дата вказані | коректно |
| 11a low — `pretty_exceptions_enable` | accepted (owner WP-00 PR2, дата — PR2) | `cli.py:20-26` без параметра, як заявлено | коректно |
| 11b low — `os._Environ` у сигнатурі | fixed | `core/version.py:45` `Mapping[str, str] \| None` | так |
| info caplog / unknown level / ruff drift / `.python-version` / `mypy tests` / ADR | fixed (docstring) / accepted WP-01D / n.a. ×4 з аргументами | `logging.py:13-14,88`; аргументи n.a. прийнятні для PR1 | коректно |

Знахідки тестування (gate 2): 1 high — fixed (`conftest.py:68-72` hook `pytest_asyncio_loop_factories` → `SelectorEventLoop`; async-тести `test_network_block_adversarial.py:38-52` зелені на Windows і Linux); 2 medium — fixed (`test_cli.py:71` літерал 2); 3 low — accepted, задокументовано (`conftest.py:24-27`); 4 low — fixed (`adapters/__init__.py`); 5 low — fixed (`gitleaks-history`); 6–7 info — closed; 8–11 info — accepted (docs-writer/WP-13). Усі мають статус; «fixed» підтверджено.

## 7. Знахідки пострев'ю

Формат: `severity | file:line | суть | пропозиція | адресат`.

1. **low | `src/collector/core/logging.py:28-43` | R-11 частково: `REDACTED_KEYS` не містить ключів контактів (`phone`, `email`, `contacts`, `seller_phone` тощо)** — §13/R-11 вимагають, щоб публічні контакти не дублювалися у технічних логах. У PR1 контактів немає, тому не блокує; але foundation — єдина точка конфігурації логів. Пропозиція: або додати мінімальний набір контактних ключів у `REDACTED_KEYS` зараз (≤ 5 рядків + 1 тест), або зафіксувати owner у ADR-0001: власники контрактів контактів (WP-07 vehicles/sellers, WP-09 catalogs) розширюють список при появі полів, WP-12 перевіряє в security/log tests (§16.1 п. 8). Адресат: implementer WP-00 (PR2 — там же `pretty_exceptions_enable`) або docs-writer → ADR-0001 з owner WP-07/WP-09.
2. **low | картка PR1, вимога 2 («зафіксувати в ADR-0001») | ADR-0001 у diff відсутній** — за карткою розділ «Docs (етап 5)» і планом §4.6 ADR пише docs-writer після accept; матеріал готовий (`implementation-pr1.md` «Вибір CLI framework — Typer»). ADR-0001 має додатково зафіксувати: (а) structlog + JSON + redaction як єдиний logging entrypoint; (б) Windows-відхилення політики мережі (loopback у unit-тестах, selector loop policy, межі allow-hosts — `conftest.py:5-27`) і те, що еталон — Linux CI з повним `disable_socket`; (в) exit code 2 стабів і його колізію з usage errors (знахідка 3). Адресат: docs-writer, етап 5. Статус вимоги 2 — `partial` до появи ADR.
3. **low (spec-mismatch кандидат, не дефект коду) | `src/collector/cli.py:18,50-53`; картка PR1 вимога 3 | exit code 2 стабів «not implemented» збігається з exit code 2 usage-помилок Typer/Click** — код відповідає картці буквально, тест-контракт зафіксований; але споживачі PR2 (compose one-shot `migrate-postgres`, healthcheck) і WP-14 (скрипт e2e) не можуть відрізнити «стаб» від «неправильний виклик» без парсингу stderr. Пропозиція для owner картки (до PR2, бо там `db migrate`/`ensure-mongo` стаби замінюються мінімальною реальною поведінкою): або зафіксувати в ADR-0001 правило «стаб визначається лише рядком stderr `not implemented: owned by WP-XX`; exit code не є ознакою стаба», або змінити картку на окремий код (напр. 69 `EX_UNAVAILABLE`) одним PR разом із тестами. ТЗ §16.2 кодів не фіксує — зміна ТЗ не потрібна. Адресат: orchestrator/owner картки WP-00.
4. **info | `tests/conftest.py:54` | Windows: loopback дозволений для всіх звичайних тестів** — документоване відхилення від буквального формулювання вимоги 4; інваріант acceptance («socket-з'єднання у звичайному тесті кидає виняток») виконується для будь-якого не-loopback host на обох платформах, повний блок — у CI (підтверджено контейнером). Ризик: локальний unit-тест на Windows може з'єднатися з локальним PostgreSQL/MinIO розробника непомітно. Прийнятно; зафіксувати в ADR-0001 (знахідка 2б).
5. **info | `implementation-pr1.md` | §17.1 «fixture provenance»: у звіті немає явного рядка «нових fixtures немає»** — `tests/fixtures/` містить лише `.gitkeep`, тож фактично провenance порожня; у тілі PR (шаблон має розділ «Fixture provenance») вказати «нових fixtures немає». Адресат: orchestrator при створенні PR.
6. **info | `.github/workflows/ci.yml` | Реальний прогін GitHub Actions не виконувався; `actionlint` недоступний** — локальна валідація: `yaml.safe_load`, тест структури, Linux-контейнерний паритет усіх python-кроків. DoD п. 9 закривається на етапі 6 лише після зеленого CI на GitHub (перший PR — також перевірка `gitleaks-action` без `GITLEAKS_LICENSE` для особистого репозиторію). Адресат: orchestrator, етап 6.

Критичних/високих знахідок немає; жодна знахідка не вимагає зміни ТЗ.

## 8. Підсумок статусів

| Блок | evidenced | partial | missing | not applicable |
|---|---|---|---|---|
| 1. Acceptance (картка 6 + вимоги 9 + правила 3 + §17.2 6 + §16.2 9 + §16.3 3) | 26 | 3 (вимога 2 ADR; §17.2 CI-частина; — ) | 0 | 7 |
| 2. DoD §18 | 4 | 2 (п. 6 docs етап 5; п. 9 merge/CI етап 6) | 0 | 3 |
| 3. Додаток C | 2 | 1 (Docker і масштабування — ролі/CLI лише) | 0 | 14 |
| 4. REVIEW.md | 1 | 1 (R-11 — контактні ключі) | 0 | 0 |
| 5. Q-питання | 1 | 0 | 0 | 1 |

Разом `missing` = 0; `partial` = 5 унікальних (вимога 2/DoD-6 — та сама ADR/README-прогалина етапу 5; §17.2 CI і Додаток C «Docker» — межі під-PR1; DoD-9 — етап 6; R-11 — знахідка 1).

**Вердикт: `accept`.** Умови для етапу 5/6: docs-writer створює ADR-0001 з пунктами знахідки 2 і README «Швидкий старт»; owner картки вирішує знахідку 3 до PR2; orchestrator підтверджує зелений CI на GitHub перед merge.
