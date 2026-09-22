# WP-00 PR1 — код-рев'ю (`wp/00-1-python-ci`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR1 «Python foundation + CI» |
| Branch / worktree | `wp/00-1-python-ci` / `.worktrees/wp-00-1` |
| Рев'юваний commit | `84b62cd` (`git diff main...HEAD`, без `docs/plan/reports/**`) |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR1» |
| Вхідні звіти | `implementation-pr1.md`, `testing-pr1.md` (фінальний вердикт `pass`, залишкові R1 low / R2 info) |
| Розділи ТЗ | §8, §13, §16.2, §18 |
| Середовище | Windows 11, uv 0.12.13, CPython 3.13 (uv-managed), typer 0.27.2 (vendored click), pytest 9.1.1, pytest-asyncio 1.4.0, pytest-socket 0.8.1, structlog 26.1.0 |
| Рев'юер | wp-code-reviewer, read-only; єдиний запис — цей файл |

## Вердикт

**`approve`** — critical: 0, high: 0, medium: 1, low: 10, info: 6.

Код правильний для свого обсягу (стаби + `version` + logging + тестова політика мережі), без гонок,
транзакцій і даних — чек-лист по цих пунктах порожній. Єдина medium-знахідка — `configure_logging`
не переводить stdlib-записи сторонніх бібліотек у JSON, що суперечить docstring модуля і зламає
парсинг логів у Loki, щойно з'являться httpx/pymongo/uvicorn. Її і low-знахідки 2–5 варто закрити
в цьому PR (правки локальні, кожна ≤ 10 рядків); решта low — на розсуд owner або для пострев'ю.

## Знахідки

Формат: `severity | file:line | claim | failure scenario | verdict`.

### medium

**1. medium | `src/collector/core/logging.py:25-29,31-44` | stdlib-записи сторонніх бібліотек виходять plain-text, не JSON**
Handler root logger має `Formatter("%(message)s")`, а JSON рендерить лише structlog-ланцюг
(`JSONRenderer` у `processors`). Записи, що йдуть напряму через `logging` (httpx, httpcore,
pymongo, uvicorn, asyncio, alembic — усі бібліотеки §8), не проходять через structlog і друкуються
як голий текст, а `logger.exception` — як багаторядковий traceback. Docstring модуля обіцяє «один
формат для всіх компонентів: JSON-рядок на подію».
Failure scenario (відтворено): `configure_logging("INFO")`, потім
`logging.getLogger("httpx").warning("GET https://user:pw@host/?token=abc")` →
stderr: `GET https://user:pw@host/?token=abc` (не JSON, без timestamp/level/logger);
`logging.getLogger("pymongo").exception("x")` → 4 рядки traceback. Promtail/Loki JSON pipeline
(§14) отримає невалідні рядки, а alert на `level=error` не спрацює.
Виправлення: на handler поставити `structlog.stdlib.ProcessorFormatter(processors=[
structlog.stdlib.ProcessorFormatter.remove_processors_meta, JSONRenderer(ensure_ascii=False)],
foreign_pre_chain=[merge_contextvars, add_logger_name, add_log_level, TimeStamper(...),
format_exc_info])`, а у `structlog.configure` завершити ланцюг
`ProcessorFormatter.wrap_for_formatter` замість `JSONRenderer`. Тест: stdlib logger → рядок є
валідним JSON з `logger`, `level`, `timestamp`.
**CONFIRMED**

### low

**2. low | `tests/conftest.py:53-57` | `asyncio.run(...)` у sync-тесті на Windows обходить блок мережі (R1 звіту тестування)**
Hook `pytest_asyncio_loop_factories` діє лише на тести, які виконує pytest-asyncio. Sync-тест або
helper під тестом, що викликає `asyncio.run(coro)` (типовий патерн CLI-команд `collector worker`
у WP-01D: `asyncio.run(main())` під `CliRunner`), отримує default policy → `ProactorEventLoop` →
`ConnectEx` минає `socket.connect`.
Failure scenario (відтворено зондом під `socket_allow_hosts(["127.0.0.1","::1"])`):
`asyncio.run(open_connection("192.0.2.1", 80))` з default policy → `TIMEOUT` (реальний SYN
пішов, блоку немає); після `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`
→ `SocketConnectBlockedError`.
Оцінка пропозиції тестувальника — **застосувати**: у `tests/conftest.py` додати
`def pytest_configure(config): if IS_WINDOWS: asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`
з коментарем про deprecation policy API у 3.14 (проєкт pinned `<3.14`). Fixture
`event_loop_policy` pytest-asyncio читає поточну policy на старті сесії (після `pytest_configure`),
тому конфлікту з hook немає; hook лишити — він не deprecated і платформонезалежний. Побічний ефект
той самий, що вже прийнято для async-тестів (Selector loop без asyncio subprocess/pipes на Windows);
для production (Linux) Selector і є default, тож тести на Windows стають ближчими до production.
Додатково дописати цю межу у docstring (R2). CI (Linux, повний `disable_socket`) не зачіпає.
**CONFIRMED**

**3. low | `pyproject.toml:48-50` | `"tests/**" = ["S"]` вимикає всі security-правила ruff у тестах, хоча потрібні лише S101/S603/S607**
Разом з `assert`/`subprocess` вимикаються S105/S106/S107 (hardcoded password у літералах — саме те,
що §13/§18 забороняють у fixtures), S113 (`requests` без timeout), S324, S501, S506 (`yaml.load`),
S602. Коментар у pyproject говорить лише про assert і subprocess.
Failure scenario: майбутній тест WP-06 із `password = "real-vendor-pass"` у fixture-конфігу —
ruff мовчить, gitleaks за патерном може не спіймати.
Виправлення: `"tests/**" = ["S101", "S603", "S607"]`. Перевірено:
`ruff check tests --select S --ignore S101,S603,S607` → `All checks passed!` — звуження нічого не
ламає зараз.
**CONFIRMED**

**4. low | `src/collector/core/logging.py:31-44` | немає processor'а редагування секретів (§13: «logs приховують Authorization/Cookie/API keys»)**
Docstring перекладає відповідальність на викликачів. Foundation — єдина точка конфігурації логів
для всіх WP; без централізованого фільтра кожен WP має пам'ятати про це сам.
Failure scenario (відтворено): `get_logger("x").info("req", authorization="Bearer abc",
cookie="sid=1", api_key="k")` → усі три значення у JSON без змін.
Виправлення: невеликий processor (до `JSONRenderer`), що для ключів із множини
`{authorization, proxy-authorization, cookie, set-cookie, api_key, apikey, token, password, secret}`
(case-insensitive, рекурсивно у dict) підміняє значення на `"[redacted]"`; тест на це. Не
блокує PR (жоден поточний код секретів не логує), але дешевше зробити тут, ніж ловити в 20 WP.
**CONFIRMED** (поведінка), ризик — PLAUSIBLE

**5. low | `pyproject.toml:20-30`; `tests/unit/test_network_block_adversarial.py:19`; `tests/unit/test_foundation_config.py:17` | `httpx` і `pyyaml` імпортуються в тестах, але не оголошені — працюють лише як транзитивні залежності `respx` і `pre-commit`**
`types-pyyaml` у dev-group є, а сам `pyyaml` — ні (стаби без runtime-пакета); `httpx` заборонений
карткою як foundation-залежність, але тестовий файл описує його як «основний HTTP-клієнт §8» і
тестує. Тест `test_foundation_dependencies_exclude_domain_libraries` перевіряє тільки `pyproject`,
тому суперечності не бачить.
Failure scenario: `uv sync --frozen --no-group dev` + окремий запуск unit-тестів у Docker
(PR2/WP-14 e2e image) або оновлення `respx`/`pre-commit`, що прибирає транзитивну залежність →
`ModuleNotFoundError` у тестах foundation.
Виправлення: додати `pyyaml>=6.0` у dev-group (поруч із `types-pyyaml`); для `httpx` — або
явно у dev-group з коментарем «лише для тестів мережевої політики», або звузити adversarial-тест до
`asyncio.open_connection` + `urllib` і залишити `httpx`-кейси owner'у WP-02. Уточнити коментар
картки в `test_foundation_config.py` (FORBIDDEN стосується `[project.dependencies]`).
**CONFIRMED**

**6. low | `.pre-commit-config.yaml:33-38` | hook `gitleaks-history` сканує всю історію при кожному `git commit`; у CI job `pre-commit` він вакуумний через shallow checkout**
`always_run: true` без `stages` → виконується на стадії `pre-commit` для кожного коміту всіх
розробників/агентів. Зараз 15 комітів → 1.2 с (виміряно), але вартість лінійна від історії, а
репозиторій за планом накопичить fixtures/golden HTML для десятків адаптерів. У CI job `pre-commit`
`actions/checkout@v5` без `fetch-depth: 0` → `gitleaks git` бачить 1 коміт; реальне покриття історії
дає лише job `secrets`.
Failure scenario: 3 000 комітів з HTML-fixtures → кожен локальний commit чекає десятки секунд;
розробники починають `--no-verify`.
Виправлення: `stages: [manual]` для `gitleaks-history` + рядок у коментарі/README
`uv run pre-commit run --hook-stage manual gitleaks-history`; або обмежити
`--log-opts="origin/main..HEAD"` для локальної стадії. Staged-hook `gitleaks` і CI job `secrets`
лишаються як є.
**CONFIRMED** (механіка), вартість — PLAUSIBLE

**7. low | `.github/workflows/ci.yml:5-12` | подвійний прогін для PR з `wp/**` і `cancel-in-progress` на `main`**
`push: branches: [main, "wp/**"]` + `pull_request` → PR із `wp/00-1-python-ci` запускає обидва
тригери (різні `github.ref`: `refs/heads/wp/...` і `refs/pull/N/merge`), concurrency-група їх не
об'єднує → 6 jobs замість 3. Окремо: `cancel-in-progress: true` без умови скасовує прогін на
`main` при швидких послідовних merge — проміжний коміт `main` може лишитися без завершеного CI
(§18: «PR злитий тільки після CI», а evidence для SHA зникає).
Виправлення: `push: branches: [main]` (тест `test_ci_runs_spec_16_2_commands_without_hardcoded_secrets`
вимагає лише наявності `push`), `cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}`.
**CONFIRMED**

**8. low | `.github/workflows/ci.yml:28,30,65,67,80,93,98` | Actions пінені mutable-тегами (`@v5`, `@v6`, `@v4`, `@v2`), не SHA**
§13 threat model включає «dependency compromise»; `gitleaks/gitleaks-action@v2` отримує
`GITHUB_TOKEN` і `GITLEAKS_LICENSE`. `permissions: contents: read` обмежує шкоду, але secret
`GITLEAKS_LICENSE` і код репозиторію доступні action'у. Реалізатор і тестувальник уже зафіксували як
ризик для WP-13; повторюю, бо це foundation-файл, який копіюватимуть.
Виправлення: `uses: owner/action@<40-hex-sha> # vX.Y.Z` + Dependabot `package-ecosystem: github-actions`.
**CONFIRMED**

**9. low | `src/collector/cli.py:18,20-33,39` | exit code 2 стабів збігається з usage-помилками Typer/Click; `collector` без аргументів і `collector db` без підкоманди теж дають 2**
Перевірено: `collector worker bogus` → 2 (`Invalid value`), `collector e2e` → 2 (`Missing option`),
`collector` → 2 (help через `no_args_is_help`, click ≥ 8.2), `collector db` → 2 (`Missing
command.`, бо `db_app`/`release_app` без `no_args_is_help=True`), `collector db migrate` → 2
(стаб). Розрізнити «не реалізовано» від «неправильно викликано» можна лише за текстом stderr.
Для §16.2/CI у PR1 проблеми немає: CI виконує лише `--help` і `version` (обидва 0). Ризик — PR2
(compose one-shot `migrate-postgres`, healthcheck) і WP-14 (скрипт e2e), якщо вони трактують 2 як
«стаб, пропустити». Картка явно фіксує 2, тому не змінювати тут; для пострев'ю/owner картки:
розглянути окремий код для стабів (наприклад 69 `EX_UNAVAILABLE` або 3) — `spec-mismatch`
кандидат, не дефект коду. Мінімально в цьому PR: `no_args_is_help=True` для `db_app` і
`release_app` (однакова поведінка з root) і assert exit code у `test_no_args_prints_help`.
**CONFIRMED**

**10. low | `tests/unit/test_cli.py` ↔ `tests/unit/test_cli_adversarial.py` | дублювання констант і сценаріїв**
Два файли тримають паралельні копії `WORKER_ROLES`/`SPEC_7_6_ROLES`,
`TOP_LEVEL_COMMANDS`/`SPEC_16_2_TOP_LEVEL`, `STUBS`/`STUB_ARGV`; сценарії «стаб → 2 + owner»,
«worker для кожної ролі», «невідома роль», «help містить команди» перевіряються двічі
(25 + 24 параметризованих кейси). Adversarial-файл строгіший (stderr-only, точна множина команд,
entry point), тож базовий файл здебільшого підмножина.
Failure scenario: зміна контракту (нова роль у §7.6) вимагає правки у трьох місцях (enum + два
тести); розсинхрон ловиться, але шумно.
Виправлення: лишити один модуль `test_cli.py` з adversarial-версіями перевірок; константи ролей
виводити з `WorkerRole` лише в одному місці (порівняння з літеральною множиною §7.6 — один тест).
**CONFIRMED**

**11. low | `src/collector/cli.py:20-27`; `src/collector/core/version.py:44` | дрібні спрощення**
a) `typer.Typer(...)` без `pretty_exceptions_enable=False`: необроблений виняток у майбутній
команді (`collector worker fetch` у Docker) друкує rich-рамку traceback (box-drawing, 80 колонок)
у stderr поверх JSON-логів. Перевірено зондом: `show_locals` у typer 0.27.2 за замовчуванням
`False`, тож секрети з локальних змінних не витікають — лише формат. Для service-CLI, чий stderr
іде в Loki, доречно `pretty_exceptions_enable=False` (або env `TYPER_STANDARD_TRACEBACK=1` в
образі — але це вже PR2).
b) `git_sha(environ: os._Environ[str] | dict[str, str] | None)` — приватний тип `os._Environ`;
достатньо `Mapping[str, str] | None`.
**CONFIRMED**

### info (без дії, для пострев'ю/наступних WP)

- `src/collector/core/logging.py:27-28`: `root.handlers[:] = [handler]` знімає і handler pytest
  `caplog`; тест, що викликає `configure_logging()` без `stream=`, втратить `caplog.records`.
  Поточні тести використовують `stream=` — ок; варто зафіксувати в docstring.
- `src/collector/core/logging.py:24`: невідомий рівень (`"DEBGU"`) мовчки → INFO (відтворено).
  Для env-конфігурації WP-01D краще `ValueError`.
- `.pre-commit-config.yaml:19-21` ↔ `uv.lock`: ruff у pre-commit (`v0.16.8`, окремий env) і в
  `uv.lock` (0.16.8) збігаються зараз, але керуються двома джерелами; при дрейфі локальний hook і
  CI `uv run ruff` розійдуться. Альтернатива — `repo: local` hook `uv run ruff`.
- `.github/workflows/ci.yml:36-37`: `uv python install` за `.python-version = 3.13` ставить останній
  patch на момент прогону; для повної відтворюваності — `3.13.x` у `.python-version` або
  `uv python pin`.
- `tests/`: `uv run mypy tests` зелений (перевірено), але в CI/картці лише `mypy src`; можна
  безкоштовно додати `tests` у крок CI.
- Картка вимога 2 («зафіксувати в ADR-0001») — ADR у diff відсутній, матеріал перенесено в
  `implementation-pr1.md` для етапу docs (`docs/decisions/**` не в owned files PR1). Не код;
  для пострев'ю. Також `docs/plan/deps/WP-00-to-repo-config.md` поза owned files PR1 (процесний
  файл dependency-запиту, стан resolved).

## Чек-лист рев'ю — що перевірено окремо

| Пункт | Результат |
|---|---|
| Гонки / ідемпотентність / транзакції / lease | n/a — у diff немає I/O з БД чи чергами; `configure_logging` ідемпотентний (тест + прогін) |
| Помилки: retryable/permanent, «ковтання» | `not_implemented` → `typer.Exit(2)`, `NoReturn`; `package_version` ловить лише `PackageNotFoundError`; `git_sha` не ковтає |
| Дані (гроші/timestamps) | n/a; `TimeStamper(fmt="iso", utc=True)` → `...Z` (тест) |
| Безпека: secrets у логах/CI | знахідки 1, 4, 8; `ci.yml` — секрети лише `${{ secrets.* }}`, `permissions: contents: read` мінімальні (gitleaks-action без PR-коментарів — прийнятно); `.env*` у `.gitignore`, `!.env.example`; gitleaks staged + history + CI job |
| Docker socket | n/a (PR2) |
| Спрощення / зайві абстракції | знахідки 10, 11; `VersionInfo` як pydantic-модель виправдана (pydantic — обов'язкова залежність, frozen) |
| Типізація | `uv run mypy src` і `uv run mypy tests` — 0 issues; `# type: ignore` у diff відсутні; `pydantic.mypy` плагін |
| Тести перевіряють поведінку | так: exit code літерал 2, stderr-only, точні множини команд/ролей, реальний subprocess для entry point, mutation-звіт тестувальника прочитано |
| Мережевий блок (POSIX-шлях, не запускався) | за кодом pytest-socket 0.8.1: `disable_socket` патчить `socket.socket`, `getaddrinfo`, `gethostbyname`; `SocketBlockedError` — `RuntimeError`, не `OSError`, тому anyio `try_connect` (`except OSError`) не ковтає, httpcore/httpx не мапить → `ExceptionGroup`, який `_assert_blocked` приймає. Очікую зелений CI на Linux |
| `--help` детермінованість | `rich_markup_mode=None` успадковується sub-Typer через `ctx.obj` (перевірено `collector db --help` plain); ширина Click ≤ 80 у non-tty; `PYTHONUTF8=1` у CI env і в subprocess-тесті; без нього в cp1251-консолі `�` (косметика, README) |
| `uv.lock` | `uv lock --check` ok; 45 пакетів, sdist+wheel hashes; `requires-python == 3.13.*`; `uv_build` pinned `<0.13` |
| Прогін | `ruff check` / `ruff format --check` / `mypy src` — зелені; `pytest -m "not live"` → 106 passed, 1 skipped (POSIX-only), 1.3 с; `collector --help`, `version` → 0 |
| Що не перевірено | реальний GitHub Actions прогін і Linux-контейнер (offline); `actionlint` відсутній |

## Рекомендований мінімальний набір правок перед merge

1. Знахідка 1 — `ProcessorFormatter` для stdlib-записів (+ тест).
2. Знахідка 2 — `WindowsSelectorEventLoopPolicy` у `pytest_configure` на `win32` + docstring (R1/R2).
3. Знахідка 3 — звузити per-file-ignores до `S101, S603, S607`.
4. Знахідка 5 — `pyyaml` у dev-group; рішення щодо `httpx` у тестах.
5. Знахідки 6–7 — `stages: [manual]` для history-hook; `push: [main]`, умовний `cancel-in-progress`.

Решта (4, 8–11, info) — на розсуд owner або в пострев'ю; вердикт `approve` від них не залежить.
