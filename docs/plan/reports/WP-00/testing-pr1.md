# WP-00 PR1 — звіт тестування (`wp/00-1-python-ci`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR1 «Python foundation + CI» |
| Branch / worktree | `wp/00-1-python-ci` (перебазований на `main`) / `.worktrees/wp-00-1` |
| Тестований commit | `a748857` (реалізація `bae3cd9` + звіт) |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR1» (вимоги 1–9, «Команди перевірки», «Acceptance») |
| Розділи ТЗ | §7.6, §8, §16.1 (рівні 1, 14), §16.2, §17.1, §17.3, §18 |
| Середовище | Windows 11, uv 0.12.13, CPython 3.13.9 (uv-managed), Node 24.19.0, gitleaks 8.30.1, `actionlint` відсутній |
| Тестувальник | wp-tester; `implementation-pr1.md` прочитано лише після власного прогону (крок 5) |
| Доданий commit | `fcb5601 test(wp-00): adversarial tests for network block, CLI contract and foundation config` |

## Вердикт

**`fail`** — одна знахідка **high**: на Windows (основна платформа розробки) блокування мережі не покриває
асинхронний шлях (`asyncio.open_connection`, `httpx.AsyncClient`): звичайний unit-тест встановлює реальне
TCP-з'єднання з не-loopback host. Інваріант картки «socket-з'єднання у звичайному тесті кидає виняток» і
твердження `tests/conftest.py` («будь-який інший host кидає `SocketConnectBlockedError`») на цій платформі
не виконуються для того стека (HTTPX async, PyMongo Async), яким користуватимуться всі наступні WP.
Виправлення локальне й перевірене (див. знахідку 1). Решта вимог картки підтверджена; усі 7 команд перевірки
на коді реалізатора зелені.

## 1. Команди перевірки та дослівний вивід

Прогін із чистого стану (`git clean -xfd` → `uv sync --frozen`), до додавання власних тестів.

```text
$ git clean -xfdn
Would remove .mypy_cache/
Would remove .pytest_cache/
Would remove .ruff_cache/
Would remove .venv/
Would remove src/collector/__pycache__/
Would remove src/collector/core/__pycache__/
Would remove src/collector/workers/__pycache__/
Would remove tests/__pycache__/
Would remove tests/unit/__pycache__/

$ git clean -xfd
(видалено все перелічене вище)

$ uv sync --frozen
Using CPython 3.13.9
Creating virtual environment at: .venv
   Building collector @ file:///C:/repos/webscraper/.worktrees/wp-00-1
      Built collector @ file:///C:/repos/webscraper/.worktrees/wp-00-1
Prepared 1 package in 86ms
Installed 44 packages in 666ms
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
exit=0

$ uv run ruff check .
All checks passed!
exit=0

$ uv run ruff format --check .
50 files already formatted
exit=0

$ uv run mypy src
Success: no issues found in 24 source files
exit=0

$ uv run pytest -m "not live"
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
======================== 30 passed, 1 skipped in 0.80s ========================
exit=0

$ PYTHONUTF8=1 uv run collector --help
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
exit=0

$ env -u COLLECTOR_GIT_SHA uv run collector version
package_version=0.1.0
git_sha=unknown
schema_version=0.0.0-placeholder
exit=0

$ COLLECTOR_GIT_SHA=abc123 uv run collector version
package_version=0.1.0
git_sha=abc123
schema_version=0.0.0-placeholder
exit=0

$ PYTHONUTF8=1 uv run pre-commit run --all-files
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
markdownlint-cli2........................................................Passed
exit=0
```

Без `PYTHONUTF8=1` у консолі Git Bash (cp1251) українські рядки `--help` друкуються як `�` — це кодування
консолі, не дефект коду (CI задає `PYTHONUTF8=1`; див. знахідку 9).

### Додаткові перевірки acceptance

```text
$ uv run python -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); print('jobs:', list(d['jobs']))"
jobs: ['python', 'pre-commit', 'secrets']
exit=0

$ which actionlint
which: no actionlint in (...)

$ git ls-files | grep -iE '(^|/)\.env'
(порожньо) grep exit=1

$ git check-ignore -v .env .env.local .env.production deploy/compose/secrets/.env .env.example
.gitignore:6:.env    .env
.gitignore:7:.env.*    .env.local
.gitignore:7:.env.*    .env.production
.gitignore:6:.env    deploy/compose/secrets/.env
.gitignore:8:!.env.example    .env.example

$ gitleaks version
8.30.1

$ gitleaks git --log-opts="main..HEAD" --no-banner --redact .
INF 2 commits scanned.
INF scanned ~117475 bytes (117.47 KB) in 197ms
INF no leaks found
exit=0

$ gitleaks dir --no-banner --redact .
INF scanned ~34118187 bytes (34.12 MB) in 2.27s
INF no leaks found
exit=0

$ uv lock --check
Resolved 44 packages in 0.72ms
exit=0
```

Перевірка, що hook `gitleaks` у pre-commit справді ловить staged-секрет (файл створено, застейджено, після
перевірки видалено; у git нічого не потрапило):

```text
$ printf 'token = "ghp_<36 випадкових символів>"\n' > tests/fixtures/leak_probe.txt && git add tests/fixtures/leak_probe.txt
$ uv run pre-commit run gitleaks --all-files
Detect hardcoded secrets.................................................Failed
- hook id: gitleaks
Finding:     token = "REDACTED
RuleID:      github-pat
Fingerprint: tests/fixtures/leak_probe.txt:github-pat:1
$ git rm -q --cached tests/fixtures/leak_probe.txt && rm tests/fixtures/leak_probe.txt
```

Зауваження: із ключем `AKIAIOSFODNN7EXAMPLE` hook мовчить (built-in allowlist gitleaks для `EXAMPLE`), а з
порожнім stage `pre-commit run gitleaks --all-files` нічого не сканує (знахідка 5).

### Adversarial-зонд блокування мережі (тимчасовий файл, не закомічений)

Listener прив'язано до власної не-loopback адреси хоста (`192.168.50.11`) — трафік не виходить за межі
машини, але для pytest-socket це «чужий» host. Вивід зонда:

```text
LOOP: ProactorEventLoop
getaddrinfo guarded: False
socket.socket is guarded: False
connect_ex rc: 0 -> BYPASS
udp sendto delivered: b'x' -> BYPASS
asyncio.open_connection SUCCEEDED -> BYPASS
httpx async raised: ReadTimeout ReadTimeout('')          # з'єднання встановлено, listener не відповів
httpx sync raised: SocketConnectBlockedError ... host "192.168.50.11" (allowed: "127.0.0.1,::1")
urllib raised: SocketConnectBlockedError ... host "192.168.50.11" (allowed: "127.0.0.1,::1")
curl rc: 28 (28=timeout after connect, 7=connect failed) # subprocess: з'єднання встановлено
```

Той самий зонд із `event_loop_policy = asyncio.WindowsSelectorEventLoopPolicy()` (кандидат на виправлення):

```text
LOOP: _WindowsSelectorEventLoop
asyncio.open_connection raised: SocketConnectBlockedError
httpx async raised: ExceptionGroup(... [SocketConnectBlockedError('A test tried to use socket.socket.connect() with host "192.168.50.11" ...')])
loopback ok under selector loop
3 passed
```

## 2. Фінальний прогін із доданими тестами

```text
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
53 files already formatted
$ uv run mypy src
Success: no issues found in 24 source files
$ PYTHONUTF8=1 uv run pytest -m "not live" -p no:cacheprovider
collected 107 items
tests\unit\test_cli.py .........................                         [ 23%]
tests\unit\test_cli_adversarial.py ..................................... [ 57%]
tests\unit\test_foundation_config.py .....F.....................         [ 88%]
tests\unit\test_logging.py ..                                            [ 90%]
tests\unit\test_network_block_adversarial.py FF....                      [ 96%]
tests\unit\test_network_blocked.py .s..                                  [100%]
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
FAILED tests/unit/test_foundation_config.py::test_appendix_a_package_exists_with_owner_docstring[collector.adapters]
FAILED tests/unit/test_network_block_adversarial.py::test_asyncio_open_connection_to_non_loopback_is_blocked
FAILED tests/unit/test_network_block_adversarial.py::test_httpx_async_client_to_non_loopback_is_blocked
============ 3 failed, 103 passed, 1 skipped, 3 warnings in 2.57s =============
$ PYTHONUTF8=1 uv run pre-commit run --all-files
(усі 11 hooks Passed)
```

Три червоні тести — це знахідки 1 і 4, а не помилки тестів. Деталі падінь:

```text
test_asyncio_open_connection_to_non_loopback_is_blocked
E  AssertionError: TimeoutError()      # замість SocketBlockedError/SocketConnectBlockedError
test_httpx_async_client_to_non_loopback_is_blocked
E  AssertionError: ConnectTimeout('')  # замість блокування
test_appendix_a_package_exists_with_owner_docstring[collector.adapters]
E  AssertionError: collector.adapters: docstring без owner-WP
```

## 3. Acceptance-пункт → тест → результат

| Acceptance / вимога картки | Тест (файл::тест) | Результат |
|---|---|---|
| Усі 7 команд перевірки зелені у чистому стані (`git clean -xfd`) | розділ 1 | **pass** (на коді реалізатора); з доданими тестами `pytest` червоний через знахідки 1, 4 |
| `--help` показує всі команди §16.2; назви збігаються з §16.2 | `test_cli.py::test_help_lists_all_contract_commands`; `test_cli_adversarial.py::test_help_command_set_equals_spec_16_2_exactly`, `::test_group_command_set_is_exact` | pass (множина команд точно = §16.2, зайвих немає; ТЗ не змінювалось) |
| Тест доводить, що socket-з'єднання у звичайному тесті кидає виняток | `test_network_blocked.py::test_connect_to_non_loopback_host_raises_in_plain_test`; `test_network_block_adversarial.py::test_raw_socket_connect_…`, `::test_httpx_sync_client_…`, `::test_urllib_…` | pass для синхронного `socket.connect` |
| — те саме для asyncio/HTTPX async (стек §8) | `test_network_block_adversarial.py::test_asyncio_open_connection_to_non_loopback_is_blocked`, `::test_httpx_async_client_to_non_loopback_is_blocked` | **fail на Windows** (знахідка 1); на POSIX очікувано pass (повний `disable_socket`) — не перевірено тут |
| `gitleaks` чистий; жодного `.env` у git | `gitleaks git main..HEAD`, `gitleaks dir`; `test_foundation_config.py::test_env_files_are_gitignored_but_example_is_not` | pass |
| `ci.yml` валідний (`actionlint` недоступний → `yaml.safe_load`) | `yaml.safe_load` OK; `test_foundation_config.py::test_ci_runs_spec_16_2_commands_without_hardcoded_secrets` | pass (`actionlint` — not testable offline) |
| Вимога 1: layout Додатка A, `__init__.py` з docstring про owner-WP, `py.typed`, `.python-version`=3.13 | `test_foundation_config.py::test_appendix_a_package_exists_with_owner_docstring[*]` (20), `::test_package_is_typed_and_python_pinned` | 19/20 pass; `collector.adapters` — **fail** (знахідка 4, low) |
| Вимога 2: foundation-залежності, без scrapy/httpx/sqlalchemy/pymongo; dev-набір | `test_foundation_config.py::test_foundation_dependencies_exclude_domain_libraries` | pass |
| Вимога 3: кожен стаб → exit 2 + owner-WP (усі команди §16.2, усі 8 ролей), невідома роль відхиляється, типізовані параметри | `test_cli.py::test_stub_returns_exit_code_2_and_owner` (17); `test_cli_adversarial.py::test_stub_message_goes_to_stderr_only` (16), `::test_worker_every_spec_role_is_stub_owned_by_wp_01d` (8), `::test_worker_unknown_or_malformed_role_is_usage_error` (5), `::test_worker_without_role_is_usage_error`, `::test_typed_stubs_reject_missing_required_options` (4), `::test_unknown_top_level_command_is_rejected`, `::test_worker_role_enum_matches_spec_7_6_exactly` | pass |
| Вимога 3: entry point `collector = "collector.cli:app"` | `test_cli_adversarial.py::test_console_script_entry_point_is_registered` | pass |
| Вимога 3: `version` реальна; без `COLLECTOR_GIT_SHA` → `unknown` | `test_cli.py::test_version_*` (2); `test_cli_adversarial.py::test_version_via_module_entry_point_without_git_sha_env` (окремий процес), `::test_version_cli_ignores_whitespace_only_git_sha`, `::test_version_rejects_unknown_option` | pass |
| Вимога 4: `tests/{unit,contract,integration,e2e,fixtures}`, маркери, `--disable-socket`, loopback для integration | `test_foundation_config.py::test_tests_layout_follows_appendix_a`, `::test_pytest_network_policy_and_markers_are_configured`; `test_network_blocked.py::test_integration_marker_allows_only_loopback`; `test_network_block_adversarial.py::test_asyncio_loopback_connection_still_works` | pass (реалізовано через маркер у conftest замість `--allow-hosts` у addopts — еквівалентно) |
| Вимога 5: ruff (E,F,I,B,UP,S; 100), mypy strict, asyncio_mode=auto | `test_foundation_config.py::test_ruff_and_mypy_configuration`, `::test_pytest_network_policy_and_markers_are_configured`; прогін ruff/mypy | pass |
| Вимога 6: pre-commit hooks (ruff, ruff-format, gitleaks, eof/whitespace, markdownlint-cli2) | `pre-commit run --all-files`; staged-секрет перехоплено | pass (обмеження hook `--staged` — знахідка 5) |
| Вимога 7: `ci.yml` — команди §16.2, gitleaks, concurrency per branch, secrets лише з GitHub | `test_foundation_config.py::test_ci_runs_spec_16_2_commands_without_hardcoded_secrets`; ручний огляд | pass; реальний прогін Actions — not testable offline |
| Вимога 8: issue/PR templates за §17.3/§17.1 | ручний огляд проти §17.1/§17.3 (10 полів issue; зміни/тести/provenance/ризики/rollback/not-live/звіти у PR) | pass |
| Вимога 9: unit-тести CLI/version/network | усе вище | pass, крім async-шляху (знахідка 1) |
| Логування (structlog JSON) | `test_logging.py` (2) | pass |

## 4. Рівень §16.1 → тести

| Рівень | Тести | Стан |
|---|---|---|
| 1 Unit (CLI/конфігурація) | `tests/unit/test_cli.py` (25), `test_cli_adversarial.py` (37), `test_foundation_config.py` (27), `test_logging.py` (2), `test_network_blocked.py` (4), `test_network_block_adversarial.py` (6; 1 з маркером `integration`, бо потребує loopback listener) | 103 pass / 3 fail / 1 skip (Windows) |
| 14 Docker | — | n/a для PR1 (картка: PR2) |

## 5. Додані тести (commit `fcb5601`)

- `tests/unit/test_network_block_adversarial.py` — `asyncio.open_connection`, `httpx.AsyncClient`, `httpx.Client`,
  `urllib`, «сирий» `socket.connect` до TEST-NET-1 мають кидати виняток pytest-socket (обробляється й
  `ExceptionGroup` від anyio); loopback asyncio працює під `integration`. Два async-тести червоні на Windows.
- `tests/unit/test_cli_adversarial.py` — літеральний exit code 2 (а не константа) і повідомлення лише у stderr
  для всіх 16 стабів; кожна з 8 ролей §7.6 → WP-01D; невідомі/спотворені ролі, відсутня роль, відсутні
  обов'язкові опції, невідома команда → usage error без «not implemented»; точна множина команд `--help`,
  `db --help`, `release --help`; entry point `console_scripts`; `python -m collector.cli version` в окремому
  процесі без `COLLECTOR_GIT_SHA`; whitespace-only SHA → `unknown`.
- `tests/unit/test_foundation_config.py` — 20 пакетів Додатка A з docstring `WP-XX`; `py.typed`,
  `.python-version`; заборонені доменні залежності; addopts/маркери/asyncio_mode; правила ruff/mypy;
  `ci.yml` виконує 5 python-команд §16.2, має `concurrency` per ref, без literal-секретів; `.env*`
  ігноруються, `.env.example` — ні, у `git ls-files` немає `.env`; каталоги `tests/*`.

Тести не використовують мережу: у робочому стані блокування спрацьовує до відправки пакета; лише коли
блокування зламане (поточний стан на Windows) два async-тести надсилають один SYN до немаршрутизованої
адреси 192.0.2.1 (RFC 5737) і падають за таймаутом 0.5 с.

## 6. Mutation-перевірка

Кожну мутацію внесено в робочу копію, прогнано, повернуто `git checkout -- <file>` (`git status` чистий).

| # | Мутація | Очікування | Результат |
|---|---|---|---|
| A | `src/collector/cli.py`: `NOT_IMPLEMENTED_EXIT_CODE = 2` → `1` | стаб-тести червоні | `test_cli.py`: **25 passed** (тест порівнює з тією ж константою — знахідка 2); `test_cli_adversarial.py`: 24 failed (`test_stub_message_goes_to_stderr_only` ×16, `test_worker_every_spec_role_…` ×8) |
| A2 | `cli.py`: `raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)` → `code=0` | стаб-тести червоні | 41 failed, 27 passed (`test_cli.py::test_stub_returns_exit_code_2_and_owner` ×17 + 24 adversarial) |
| B1 | `pyproject.toml`: прибрати `--disable-socket` з addopts | network-тести червоні | на Windows network-тести **без змін** (ті самі 2 fail, що й до мутації): блокування тут забезпечує лише маркер `allow_hosts` з conftest; `test_foundation_config.py::test_pytest_network_policy_and_markers_are_configured` — failed (ловить) |
| B2 | `tests/conftest.py`: `allow_hosts(loopback)` → `enable_socket` для всіх | network-тести червоні | 7 failed: `test_network_blocked.py::test_connect_to_non_loopback_host_raises_in_plain_test`, `::test_integration_marker_allows_only_loopback` + 5 у `test_network_block_adversarial.py` |
| C | `core/version.py`: `return value or UNKNOWN_GIT_SHA` → `return value` | version-тести червоні | 3 failed: `test_cli.py::test_version_git_sha_defaults_to_unknown`, `test_cli_adversarial.py::test_version_via_module_entry_point_without_git_sha_env`, `::test_version_cli_ignores_whitespace_only_git_sha` |
| D | `workers/roles.py`: видалити `MAINTENANCE` | role-тести червоні | 5 failed (`test_cli.py` ×2, `test_cli_adversarial.py` ×3) |

## 7. Знахідки

| # | Severity | Місце | Опис |
|---|---|---|---|
| 1 | **high** | `tests/conftest.py:37-38` (гілка `IS_WINDOWS`), docstring рядки 5-9; `pyproject.toml` addopts | На Windows усі тести отримують `allow_hosts([127.0.0.1, ::1])`, а pytest-socket у цьому режимі патчить лише `socket.socket.connect`. `ProactorEventLoop` (default на Windows) з'єднується через `_overlapped.ConnectEx`, тому `asyncio.open_connection` і `httpx.AsyncClient` (anyio→asyncio) **встановлюють реальне TCP-з'єднання** з не-loopback host у звичайному unit-тесті (зонд: `asyncio.open_connection SUCCEEDED`, httpx `ReadTimeout` = з'єднання є). Це саме той стек (§8: HTTPX, PyMongo Async), яким користуватимуться WP-02/03/01B; на локальних прогонах розробників випадковий live-запит до джерела не буде помічений. CI (ubuntu) не постраждає — там повний `disable_socket`. Твердження conftest «інваріант діє на всіх платформах» хибне. **Виправлення (перевірено зондом):** у `tests/conftest.py` на `win32` задати для тестів `asyncio.WindowsSelectorEventLoopPolicy()` (fixture `event_loop_policy` scope=session, pytest-asyncio 1.x) — тоді `sock_connect` іде через `socket.connect`, обидва шляхи кидають `SocketConnectBlockedError`, loopback працює; альтернатива — обгорнути `IocpProactor.connect` перевіркою allowed hosts. Тести: `test_network_block_adversarial.py::test_asyncio_open_connection_to_non_loopback_is_blocked`, `::test_httpx_async_client_to_non_loopback_is_blocked` (червоні). |
| 2 | medium | `tests/unit/test_cli.py:73` | `assert result.exit_code == NOT_IMPLEMENTED_EXIT_CODE` тавтологічний щодо константи: мутація `NOT_IMPLEMENTED_EXIT_CODE = 1` проходить 25/25 (mutation A). Картка фіксує саме код 2 — порівнювати з літералом. Покрито `test_cli_adversarial.py`; рекомендовано виправити і в `test_cli.py`. |
| 3 | low | `tests/conftest.py` (режим `allow_hosts`: усі тести на Windows; `integration`/`e2e` на всіх платформах) | pytest-socket у режимі allow-hosts не перехоплює `socket.connect_ex` (зонд: rc 0, з'єднано), UDP `sendto` (доставлено), `socket.getaddrinfo` (не патчиться → DNS-egress для імен хостів можливий), а також subprocess (`curl` rc 28 після успішного connect). Обмеження інструмента; задокументувати в conftest і, за бажання, додатково патчити `getaddrinfo` для не-loopback імен. Зауважити: правила ruff `S` (S603/S607 subprocess) вимкнені для `tests/**`, тож subprocess у тестах не підсвічується. |
| 4 | low | `src/collector/adapters/__init__.py:1` | Docstring «кожен subpackage належить окремому WP» без конкретного `WP-XX` — картка (вимога 1) вимагає речення про owner-WP; решта 19 пакетів мають ідентифікатор. Пропозиція: «…news — WP-05/06, vehicles — WP-08, catalogs — WP-10». Тест `test_foundation_config.py::test_appendix_a_package_exists_with_owner_docstring[collector.adapters]` червоний. |
| 5 | low | `.pre-commit-config.yaml:27-30`; `.github/workflows/ci.yml` job `pre-commit` | Hook `gitleaks` виконує `gitleaks git --pre-commit --staged`: при `pre-commit run --all-files` на чистому дереві (і в CI job `pre-commit`) він нічого не сканує — «Passed» вакуумний. Історію покриває окремий job `secrets` (`gitleaks-action`, `fetch-depth: 0`), тож дірки в CI немає, але локальний «pre-commit зелений» ≠ «gitleaks чистий» (acceptance). Для локального acceptance використовувати `gitleaks git`/`gitleaks dir` (зроблено, чисто) або hook `gitleaks-system` з `gitleaks dir`. |
| 6 | info | `docs/plan/deps/WP-00-to-repo-config.md` (Стан: open); `implementation-pr1.md` розділ «Підсумок команд» | Після перебазування на `main` (`b3dafd8` додає `MD024: siblings_only` у `.markdownlint-cli2.jsonc`) hook `markdownlint-cli2` зелений — dependency-запит фактично закритий; оновити стан і підсумок звіту («червона лише hook markdownlint» неактуально). |
| 7 | info | `implementation-pr1.md` розділ «Вибір CLI framework» | «Typer (поверх Click)» — typer 0.27.2 не залежить від click (`uv.lock`: annotated-doc, colorama, rich, shellingham; `click` у lock відсутній). Для ADR-0001 формулювання виправити. |
| 8 | info | `src/collector/cli.py` | Usage-помилки Typer (невідома роль, відсутня опція) також завершуються кодом 2 — тим самим, що й стаби «not implemented». Розрізнення лише за текстом stderr. Картка фіксує 2 для стабів, тож без змін; врахувати у скриптах/health-перевірках PR2. |
| 9 | info | `src/collector/cli.py` (help-рядки українською) | У консолі без UTF-8 (Git Bash cp1251, cmd cp866) `collector --help` друкує `�`; потрібен `PYTHONUTF8=1` (CI його задає). Для README «Швидкий старт» (етап docs). |
| 10 | info | `collector worker --help` | Typer переносить перелік ролей посеред слова (`projecto\n r`) — косметика plain-text help. |
| 11 | info | `.github/workflows/ci.yml` | Actions пінені за major-тегом, не SHA (реалізатор уже зафіксував як ризик для WP-13). |

## 8. Звірка з `implementation-pr1.md` (крок 5)

- Вивід усіх 7 команд збігається з моїм прогоном, крім `pre-commit`: у звіті hook `markdownlint-cli2` червоний
  (MD024), у мене — зелений після перебазування на `main` (знахідка 6).
- Твердження «Інваріант “connect до будь-якого не-loopback host кидає виняток” діє на всіх платформах» —
  **спростовано** для asyncio/HTTPX async на Windows (знахідка 1); ризик 4 звіту згадує лише DNS-резолв.
- «Typer поверх Click» — не відповідає lockfile (знахідка 7). Решта (layout, залежності, стаби, version,
  templates, CI-кроки, gitleaks чистий, відсутність `.env`) підтверджена.
- Linux-паритет (31 passed у контейнері) не відтворювався: потребує мережі для `uv sync` у контейнері.
  Для доданих async-тестів на POSIX очікуваний результат — pass (`socket.socket()`/`getaddrinfo`
  заблоковані до connect; `_assert_blocked` приймає і `SocketBlockedError`, і `ExceptionGroup`).

## 9. Що не перевірено

- `actionlint` (недоступний) і реальний прогін GitHub Actions — not testable offline.
- Повне блокування socket на POSIX і поведінка доданих тестів у Linux — лише за аналізом коду pytest-socket
  (`disable_socket` патчить `socket.socket`, `getaddrinfo`, `gethostbyname`).
- Acceptance «чистий clone» — через `git clean -xfd` у worktree, не окремий `git clone`.
