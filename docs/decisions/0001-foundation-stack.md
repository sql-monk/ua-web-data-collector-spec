# ADR-0001: Стек та інструменти foundation (WP-00 PR1)

| Поле | Значення |
|---|---|
| Date | 2026-09-22 |
| Owner | WP-00 |
| Status | accepted |

## Context

Картка `docs/plan/cards/WP-00.md` (PR1, вимога 2) вимагає зафіксувати вибір
CLI framework (`typer` або `click`) в ADR. ТЗ §8 фіксує Python 3.13, `uv`,
Pydantic v2, `pytest`/`pytest-asyncio`/`respx`, Ruff/mypy strict, але лишає
CLI framework і формат логування на розсуд foundation PR; §16.1/§16.2
вимагають, щоб звичайні тести не зверталися до мережі, а команди §16.2 були
контрактом CI. §13 вимагає, щоб секрети й контакти не потрапляли в технічні
логи. §20 вимагає ADR для рішень поза буквою ТЗ.

Ці рішення приймались і уточнювались протягом трьох gate PR1: спершу
реалізацією (`docs/plan/reports/WP-00/implementation-pr1.md`), потім
виправленнями після тестування (`testing-pr1.md`, вердикт `fail` → `pass`) і
код-рев'ю (`code-review-pr1.md`, вердикт `approve`). Пострев'ю за ТЗ
(`spec-review-pr1.md`, вердикт `accept`) залишило три low-знахідки щодо
змісту цього ADR (відсутність ADR-0001 у diff PR1, документування
Windows-відхилення мережевої політики, та колізія exit code 2 стабів із
usage-помилками Click) — усі три відображені нижче.

## Decision

### CLI framework — Typer

Обрано **Typer** (`typer>=0.27`), а не голий Click:

1. Сигнатури команд — типізовані Python-функції з `Annotated[...]`;
   `mypy --strict` перевіряє параметри команд без ручних обгорток, а
   `Enum`-роль worker (`collector.workers.roles.WorkerRole`, §7.6) стає
   валідованим аргументом `collector worker <role>` автоматично: невідома
   роль — usage-помилка Click, а не виклик стаба.
2. Той самий підхід «типи → контракт», що й у Pydantic v2/FastAPI (§8) —
   один стиль опису інтерфейсу для CLI, API та контрактів даних.
3. Стаб для власника WP — одна функція на команду з тілом
   `not_implemented("WP-XX")`; заміна тіла не змінює назву чи параметри
   команди, тож контракт §16.2 лишається зафіксованим тестами
   (`tests/unit/test_cli.py`, `tests/unit/test_cli_adversarial.py`).
4. Typer з версії 0.2x має власне ядро над Click і не тягне Click як окрему
   залежність (`click` відсутній у `uv.lock`); транзитивні `rich` і
   `shellingham` — прийнятна ціна. Rich-форматування help вимкнено
   (`rich_markup_mode=None` у `collector.cli.app`) заради детермінованого
   plain-text виводу в CI-логах, Docker-логах і тестах.

Кожна команда контракту §16.2, крім `collector version`, — типізований стаб
у `src/collector/cli.py`: друкує `not implemented: owned by WP-XX` у stderr і
завершується з кодом `NOT_IMPLEMENTED_EXIT_CODE = 2`. `collector version`
реалізована повністю: друкує `package_version` (`importlib.metadata`),
`git_sha` (env `COLLECTOR_GIT_SHA`, інакше `unknown`) і
`schema_version=0.0.0-placeholder` (`collector.core.version`; реальну версію
контрактів визначає WP-01C).

**Правило стаба контракту §16.2:** стаб визначається лише рядком stderr
`not implemented: owned by WP-XX`, а не самим кодом виходу. Typer/Click
завершує usage-помилки (невідома опція, відсутній обов'язковий аргумент,
невалідне значення `Enum`) тим самим кодом 2 — це стандартна поведінка
Click, яку Typer успадковує, і PR1 її не змінює. Сценарії, які повинні
відрізнити «команда ще не реалізована» від «команда викликана неправильно»
(наприклад, one-shot `migrate-postgres` у PR2, healthcheck-скрипти, WP-14
`e2e`), мають перевіряти рядок stderr, а не лише exit code. Групи без
підкоманди (`collector`, `collector db`, `collector release`) — окремий
випадок: вони друкують help і завершуються кодом **0** (не 2), бо виклик без
підкоманди — не помилка і не стаб; про це подбав спільний callback
`_help_when_no_subcommand` (`invoke_without_command=True`), який замінює
типову поведінку Click `no_args_is_help` (код 2 — колізія зі стабами).

### Логування — structlog JSON + `ProcessorFormatter` + redaction

Обрано **structlog** (`structlog>=25.4`) із JSON-рендерингом як єдиний
logging entrypoint для CLI, workers і API (`collector.core.logging`):

- один JSON-рядок на подію, ISO-8601 UTC timestamp, `ensure_ascii=False` для
  українських повідомлень;
- записи сторонніх бібліотек через stdlib `logging` (httpx, pymongo,
  uvicorn, alembic, ...) проходять той самий ланцюг processors: handler
  root logger отримує `structlog.stdlib.ProcessorFormatter` зі спільним
  `foreign_pre_chain` (`_shared_processors()` — contextvars, logger name,
  рівень, `ExtraAdder` для stdlib `extra=`, ISO timestamp, exception info,
  `redact_secrets`) і завершує рендеринг `JSONRenderer`; structlog-подія
  проходить той самий `_shared_processors()` і `wrap_for_formatter`. Це дає
  один формат для обох джерел, а не два різних форматери;
- `configure_logging()` ідемпотентна (повторний виклик не дублює handlers) і
  замінює handlers root logger, зокрема handler pytest `caplog` — у тестах
  логування треба перевіряти через `stream=`, а не `caplog.records`;
  невідомий рівень (`level`) трактується як `INFO`.

Redaction (§13, R-11 REVIEW.md) — процесор `redact_secrets` у спільному
ланцюгу, рекурсивно у вкладених `dict`/`list`/`tuple`, case-insensitive за
назвою ключа, і для structlog-подій, і для stdlib `extra=`. Поточний
`REDACTED_KEYS` (`collector.core.logging`):

- секрети — `authorization`, `proxy-authorization`/`proxy_authorization`,
  `cookie`, `set-cookie`/`set_cookie`, `api_key`/`apikey`/`api-key`,
  `token`, `password`, `secret`;
- контакти (§13, R-11: публічні контакти не дублюються в технічних логах) —
  `phone`/`phones`, `email`/`emails`, `contact`/`contacts`, `messenger`,
  `seller_name`.

Список — стартовий мінімум foundation; власники контрактів із полями
контактів (WP-07 vehicles/sellers, WP-09 catalogs) розширюють
`REDACTED_KEYS` при появі нових ключів, а WP-12 перевіряє повноту в
security/log tests (§16.1 п. 8). URL із credentials/токенами у query-рядку в
поля логів не потрапляють — за це відповідають викликачі (redaction діє на
структуровані поля event dict, не на довільний текст).

### Мережа заблокована в тестах за замовчуванням (pytest-socket) — з Windows-відхиленням

`pytest-socket` (`pytest-socket>=0.7`) блокує мережу глобально
(`--disable-socket --allow-unix-socket` в `addopts`, `pyproject.toml`).
`tests/conftest.py` прив'язує політику до маркерів рівня тесту (§16.1):
`integration`/`e2e` отримують `allow_hosts([127.0.0.1, ::1])`; `live` —
`enable_socket` і виконується лише явно (`-m live`), ніколи не входить у
`pytest -m "not live"`.

**Задокументоване відхилення від буквального формулювання картки (Windows):**
на Windows loopback дозволений і для звичайних (unit) тестів, а не лише для
`integration`/`e2e`, бо asyncio там реалізує `socket.socketpair()` через
AF_INET 127.0.0.1 — під повним `--disable-socket` не створюється жоден event
loop. Інваріант acceptance картки («socket-з'єднання у звичайному тесті до
будь-якого не-loopback host кидає виняток») виконується на обох платформах;
повна заборона *створення* сокета (`test_socket_creation_is_blocked_on_posix`)
перевірена лише на POSIX і в CI (Linux-контейнер `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`
у Linux-паритетних прогонах реалізатора й рев'юера дає повний
`--disable-socket` без жодного skip). Еталонна платформа для acceptance —
Linux CI; Windows-відхилення — зручність локальної розробки, не послаблення
контракту.

Event loop для async-тестів на Windows — завжди `SelectorEventLoop`, не
типовий `ProactorEventLoop`:

- hook `pytest_asyncio_loop_factories` у `tests/conftest.py` повертає
  `asyncio.SelectorEventLoop` для всіх тестів pytest-asyncio;
- `pytest_configure` додатково встановлює
  `asyncio.WindowsSelectorEventLoopPolicy()` для коду, що викликає
  `asyncio.run(...)` поза pytest-asyncio (наприклад, CLI під `CliRunner`).

Причина: типовий Windows `ProactorEventLoop` з'єднується через
`_overlapped.ConnectEx`, минаючи `socket.connect`, тож
`asyncio.open_connection`/`httpx.AsyncClient` обходили б `pytest-socket` у
режимі allow-hosts (знахідка `high` тестування, gate 2, виправлена в
`52c4166`/`ce0c942`). Selector loop іде через `sock_connect` →
`socket.connect`, і асинхронний шлях блокується так само, як синхронний
(`tests/unit/test_network_block_adversarial.py`: `asyncio.open_connection`,
`httpx.AsyncClient`, `httpx.Client`, `urllib`, сирий `socket.connect`).

**Межа, яку ця policy не покриває:** код, що явно створює
`asyncio.ProactorEventLoop()` або встановлює
`WindowsProactorEventLoopPolicy()` в обхід conftest-policy, не перехоплюється
— на Windows такий код обійде блокування мережі; у CI (Linux) діє повний
`disable_socket` незалежно від loop, тож витік лишається локальним ризиком
розробки, не ризиком CI. Інші відомі межі `pytest-socket` у режимі
allow-hosts: не перехоплюються `socket.connect_ex`, UDP `sendto`,
`socket.getaddrinfo` (DNS-резолв імені) і subprocess; на Windows selector
loop додатково не підтримує asyncio subprocess/pipes — тестам, яким це
потрібно, слід створювати окремий loop явно.

### Build backend — `uv_build`

`pyproject.toml` використовує `uv_build` (`[build-system] requires =
"uv_build>=0.8.0,<0.13.0"`) із src-layout (`module-root = "src"`), а не
`hatchling`/`setuptools`: один інструмент (`uv`) для залежностей, lockfile і
збірки пакета, без додаткового build backend у dev-групі.

### pre-commit hooks

`.pre-commit-config.yaml` — локальні hooks дублюють перевірки CI:
`pre-commit-hooks` (eof/trailing-whitespace/yaml/toml/large-files/
merge-conflict/private-key), `ruff-pre-commit` (ruff check --fix,
ruff-format; версія синхронізована з `uv.lock`), `gitleaks` двічі:

- staged hook (`id: gitleaks`, upstream `--pre-commit --staged`) — на кожен
  commit, лише staged-зміни;
- `gitleaks-history` (`alias: gitleaks-history`, `entry: gitleaks git
  --redact --no-banner --verbose`, `stages: [manual]`) — сканує всю доступну
  git-історію; винесено в `manual` stage, бо вартість лінійна від довжини
  історії і не повинна сповільнювати кожен commit. Запускається вручну
  (`uv run pre-commit run --hook-stage manual gitleaks-history`) і в CI —
  job `secrets` (`.github/workflows/ci.yml`) виконує
  `gitleaks/gitleaks-action@v2` з `actions/checkout@v5, fetch-depth: 0`, тож
  повний скан історії покривається на кожен push/PR незалежно від
  локального manual-запуску;

`markdownlint-cli2` для `*.md` (виключення `^\.claude/` — промпти
субагентів із frontmatter, не документація для MD041).

### CI-структура

`.github/workflows/ci.yml` — три jobs:

- `python`: `uv sync --frozen` → `uv run ruff check .` → `uv run ruff format
  --check .` → `uv run mypy src` → `uv run pytest -m "not live"` →
  `uv run collector --help`/`uv run collector version` (всі команди §16.2,
  доступні в PR1); `COLLECTOR_GIT_SHA=${{ github.sha }}` в env;
- `pre-commit`: усі pre-commit hooks (staged-режим, без `gitleaks-history`),
  кеш `~/.cache/pre-commit`;
- `secrets`: `gitleaks/gitleaks-action@v2` з `actions/checkout@v5,
  fetch-depth: 0` — повний скан git-історії, незалежно від manual-hook.

`push` запускається лише для `main`, `pull_request` — для решти гілок;
`concurrency` group за `${{ github.workflow }}-${{ github.ref }}`,
`cancel-in-progress` — тільки поза `main` (щоб не скасовувати CI-прогін, що
вже захищає `main`); `permissions: contents: read`; секрети лише через
`${{ secrets.* }}` (`GITHUB_TOKEN`, опційний `GITLEAKS_LICENSE`), нічого не
hardcoded. Крок `docker compose config --quiet` та build/SBOM-кроки
додаються в PR2 разом з `Dockerfile`/`docker-compose.yml`; web-кроки
(`npm ci && npm run lint && ...`) — у PR3.

## Consequences

- Заміна тіла будь-якого стаба власником WP не вимагає зміни сигнатури
  команди чи тестів контракту §16.2 — лише видалення `not_implemented(...)`.
- Усі логи (CLI, майбутні workers/API) виходять у єдиному JSON-форматі з
  першого дня, включно з логами сторонніх бібліотек — не потрібен окремий
  адаптер формату при появі httpx/pymongo/uvicorn у наступних WP.
- Список `REDACTED_KEYS` — точка розширення: власники доменних контрактів
  повинні пам'ятати додавати нові контактні поля сюди; відсутність поля в
  списку не блокує PR1 (у PR1 контактних даних немає), але є ризиком для
  наступних WP, якщо забути про це розширення (спостерігається пострев'ю,
  знахідка 1 — прийнято, мінімальний набір уже додано в PR1).
- Windows лишається придатною платформою локальної розробки, але не є
  еталоном для acceptance мережевої політики — CI (Linux) є джерелом істини;
  розробник на Windows, що явно створює `ProactorEventLoop`, втрачає захист
  від мережі локально (не в CI).
- Споживачі стабів (PR2 one-shots, WP-14 `e2e`, будь-який скрипт, що читає
  вихід `collector`) мають парсити stderr-рядок `not implemented: owned by
  WP-XX`, а не покладатися лише на код виходу 2, оскільки той самий код
  повертають usage-помилки Click.
- `gitleaks-history` як manual stage означає, що звичайний `pre-commit run`
  (pre-push hook і локальний commit) не ловить секрети, додані в
  попередніх, уже закомічених ревізіях — це покриває лише CI job `secrets`
  (fetch-depth 0) і явний manual-запуск.

## Related

- Реалізація: `src/collector/cli.py`, `src/collector/core/logging.py`,
  `src/collector/core/version.py`, `tests/conftest.py`, `pyproject.toml`,
  `.pre-commit-config.yaml`, `.github/workflows/ci.yml`.
- Звіти: `docs/plan/reports/WP-00/implementation-pr1.md` (розділи «Вибір CLI
  framework — Typer», «Виправлення після gate 2», «Відповіді на код-рев'ю»),
  `docs/plan/reports/WP-00/testing-pr1.md`, `docs/plan/reports/WP-00/code-review-pr1.md`,
  `docs/plan/reports/WP-00/spec-review-pr1.md` (знахідки 1–3 пострев'ю).
- Наступний ADR: `docs/decisions/0002-docker-compose-single-host.md` (PR2,
  Q-006/Q-013 §20) — Docker Compose single-host MVP, ще не створений.
