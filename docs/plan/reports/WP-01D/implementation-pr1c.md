# WP-01D PR1c — runtime-передумови доменних handler-ів хвилі 1

Branch: `wp/01d-1c-handler-plumbing` (worktree `.worktrees/wp-01d-1c`, від `main` `626b7e4`).
Картка: `docs/plan/cards/WP-01D.md`, розділ PR1c. Споживачі: WP-02 PR2, WP-04 PR2, WP-01B PR3.
Залежність у польоті: WP-01A PR3a (`wp/01a-3a-queue-outbox-preflight`) — на момент роботи в гілці
немає комітів, тож PR1c написано проти **задокументованого** інтерфейсу (картка WP-01A PR3a
п.1, п.7). **Merge PR1c — лише після PR3a і rebase** (див. «Що не перевірено»).

Коміти:

```text
353a638 fix(wp-01d): 30 s floor for the maintenance tick cap; patient waits in PR1c tests
e78fa69 test(wp-01d): PostgreSQL scaling tests for defer, retry schedule, projection backend and ticks
8da8b64 docs(wp-01d): workers.md §5-§6 for PR1c; expected WP-01A PR3a signatures
678d7af feat(wp-01d): scheduler tick composition with lazy domain ticks
6e4b4c7 feat(wp-01d): defer, retry_schedule, lazy handler registry and queue backends
```

## Що зроблено

| Вимога PR1c | Реалізація | Тест |
|---|---|---|
| 1. `not_before` і `defer` без спалювання спроби | `TaskResult`: disposition `defer`, поле `not_before` (лише `retry`/`defer`, timezone-aware), `retryable(..., not_before=)`, `deferred(until, error_code)`; `defer` без `error_code` → `ValueError`, `error_message` для `defer` відхиляється (його нікуди записати). Runtime: `defer` → `backend.defer` → `queue.release(..., not_before=until)`; `until` у минулому → `now`, далі за `COLLECTOR_WORKER_MAX_DEFER_SECONDS` (типово 86400) → clamp + warning `worker.not_before_clamped`; лог `worker.task_deferred` з `error_code`, без `args` | unit `test_handlers.py` (`deferred`/`not_before`/`output`), `test_runtime_plumbing.py::test_defer_goes_through_release_not_retry`, `::test_defer_into_the_past_becomes_now`, `::test_defer_beyond_the_ceiling_is_clamped_with_a_warning`; integration `test_handler_plumbing.py::test_deferred_job_keeps_its_attempt_and_writes_no_error`, `::test_five_defers_in_a_row_never_dead_letter_a_job_with_four_attempts` (NEEDS_PR3A) |
| 2. Retry policy за таблицею §10 | `RetrySchedule(delays, jitter_ratio)` (`delays[min(n, len) - 1]` + jitter ≤ `delay × ratio`), `TaskHandler.retry_schedule` (default `None`). Runtime: `not_before = max(now + delay, result.not_before or now)` → `queue.retry(..., policy=IMMEDIATE_POLICY, not_before=...)`; без розкладу **і** без нижньої межі — виклик рівно як до PR1c (дефолтний `BackoffPolicy` черги). `max_attempts` не чіпається | unit `test_handlers.py::test_retry_schedule_*`, `test_runtime_plumbing.py::test_retry_*`; integration `test_handler_plumbing.py::test_retry_after_lower_bound_beats_a_short_schedule`, `::test_retry_schedule_gives_exactly_the_spec_10_delays`, `::test_fourth_failure_with_four_attempts_is_quarantined_with_a_dead_letter` (NEEDS_PR3A), `::test_handler_without_schedule_keeps_the_queue_backoff` (регресія, зелений зараз) |
| 3. Реєстр із lazy import | новий `collector/workers/registry.py`: `ROLE_HANDLER_MODULES` (FETCH/BROWSER/TRANSLATION/PROJECTOR за карткою), `load_role_bindings`: `ModuleNotFoundError` саме цього модуля або його пакета → `NoopHandler` + warning `worker.handler_module_missing`; будь-який інший `ImportError`/виняток → `HandlerRegistryError`; модуль без реєстрації → `HandlerRegistryError`. `resolve_handler` прибрано (замінено реєстром). Placeholder обходить імпорт (CLI до runtime не доходить) | unit `test_registry.py` (відсутній модуль/пакет → Noop+warning; 4 види зламаного модуля → помилка; модуль без реєстрації → помилка; CLI `worker fetch` зі зламаним модулем → exit ≠ 0; placeholder не імпортує модуль) |
| 4. `HandlerContext` у фабриці | `HandlerFactory = Callable[[HandlerContext], TaskHandler \| Sequence[HandlerBinding]]`, `HandlerContext(role, sessions, worker_instance_id, clock, env)` + `owner`. Runtime будує контекст із **тими самими** `sessions` і `instance_id`. Фабрика старої форми (`lambda role: ...`) у тестах мігрована | unit `test_registry.py::test_factory_receives_the_runtime_sessions_and_instance_id`; integration `test_handler_plumbing.py::test_factory_gets_the_runtime_sessions_and_the_registered_instance_id` (ідентичність `sessions`, id = рядок `worker_instances`) |
| 5. Queue backend для `projection_tasks`, кілька прив'язок, ack у report-транзакції | новий `collector/workers/backends.py`: Protocol `QueueBackend` (`claim`, `heartbeat`, `complete`, `retry`, `defer`, `quarantine`, `release`, `recover_expired`, `check_output`), `CrawlJobsBackend`, `ProjectionTasksBackend`; `HandlerBinding(backend, handler)`. Runtime: спільні слоти, round-robin claim (з наступної за останньою успішною прив'язки) в одній транзакції з барʼєром drain; heartbeat/звіт/drain-release — через backend task-и. `ProjectionTasksBackend.complete` = `acknowledge_projection(..., owner=...)` у транзакції звіту; output не receipt → карантин `invalid_handler_output`; `ProjectionAck(receipt, event)` для події-artifact. `Task` projection: `job_id=task_id`, `job_type=target_collection`, `args` — поля команди з рядка task (+`artifact_id`), `source_id=None`. Карантин projection task власником: `heartbeat_projection_task(owner)` + `quarantine_projection_task` в одній транзакції | unit `test_runtime_plumbing.py::test_two_bindings_share_one_slot_and_neither_starves`, `::test_output_*`; integration `test_projection_backend.py` (10 тестів, 4 з NEEDS_PR3A) |
| 6. Реєстр тіків scheduler-а | `SchedulerRuntime`: композиція тіків — вбудований `maintenance` (`recover_expired_leases` + **`recover_expired_projection_leases`** + `mark_stale_instances`) і доменні тіки з `registry.DOMAIN_TICKS` (lazy import: `reconciler:schedule`, `compactor:schedule`, `publisher:tick` — вимкнений, доки `COLLECTOR_OUTBOX_PUBLISHER_ENABLED` не `1`). Доменний тік — `async def tick(ctx: TickContext)` (`sessions`, `now`, `transaction()`, `lease_is_ours()`, `env`); власний інтервал і `timeout_seconds`; виняток/timeout → `scheduler.tick_failed tick=<name>`, решта тіків і lease не зачіпаються; перед доменним тіком lease перевіряється ще раз. Серверна перевірка lease — під `asyncio.Lock` + `shield` (одне з'єднання, timeout тіку не рве запит) | integration `test_scheduler_ticks.py` (4 тести); unit `test_registry.py` (тіки: відсутній → пропуск+warning, зламаний/синхронний → помилка, вимкнений не імпортується) |
| Етап 5: `docs/workers.md` §5–§6 | §5 розбито на 5.1 (`TaskResult`, `defer`, `retry_schedule`, clamp), 5.2 (реєстр, `HandlerContext`), 5.3 (кілька прив'язок), 5.4 (ack у report-транзакції); §6 — таблиця тіків, `TickContext`, ізоляція, контракт ідемпотентності | markdownlint, `ruff format` (код-блоки) |

Рішення, які варто перевірити рев'юеру:

- **Одна фабрика на роль, `projector` — один модуль.** Картка: «`PROJECTOR → collector.workers.projector`
  (+ `reconciler`, `compactor` — див. п.5)». Реалізовано так, як описує картка WP-01B п.3: фабрика
  `collector.workers.projector` повертає **кілька** `HandlerBinding` (projector на
  `projection_tasks`, reconcile/compact на `crawl_jobs`), а `reconciler`/`compactor` імпортує сам
  projector-модуль. Окремі записи в мапі handler-ів для них дали б дві незалежні фабрики однієї ролі
  без порядку. Для **тіків** `reconciler:schedule`/`compactor:schedule` — окремі записи, як у картці.
- **`CrawlJobsBackend` відхиляє будь-який `output`** (→ `invalid_handler_output`), а не ігнорує:
  мовчки викинутий результат гірший за видимий карантин.
- **Clamp застосовано й до `retryable(not_before=)`** (не лише до `defer`): `Retry-After` на тиждень
  інакше «ховав» би job.
- **`_report` ловить будь-який `Exception`** (раніше — лише помилки persistence): помилка backend-а
  не зникає в GC, а дає `worker.report_failed` і лічильник `report_failures`.
- **Scheduler ловить будь-який `Exception` тіку** (раніше — лише помилки БД; інша помилка валила б
  процес) — вимога ізоляції тіків.

## Команди та вивід

Усі команди — на HEAD `353a638`, Windows 11 + Docker Desktop (testcontainers PostgreSQL 18),
спільний хост із 4 іншими агентами (CPU ~90 %), compose-стек не піднімався.

`uv sync --frozen`:

```text
Checked 66 packages in 6ms
```

`uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy src`:

```text
All checks passed!
278 files already formatted
Success: no issues found in 80 source files
```

`uv run pytest tests/unit/workers -q`:

```text
138 passed in 9.14s
```

`uv run pytest -m integration tests/integration/scaling -q -rxXfE`:

```text
82 passed, 9 xfailed in 1216.50s (0:20:16)
```

9 xfailed — рівно тести з маркером `NEEDS_PR3A` (`xfail(strict=True)`):
`test_handler_plumbing.py::{test_deferred_job_keeps_its_attempt_and_writes_no_error,
test_five_defers_in_a_row_never_dead_letter_a_job_with_four_attempts,
test_retry_after_lower_bound_beats_a_short_schedule,
test_retry_schedule_gives_exactly_the_spec_10_delays,
test_fourth_failure_with_four_attempts_is_quarantined_with_a_dead_letter}`,
`test_projection_backend.py::{test_receipt_is_acknowledged_in_the_report_transaction,
test_fault_between_ack_and_commit_leaves_no_partial_rows,
test_lost_lease_blocks_the_ack_and_the_next_owner_acks_exactly_once,
test_projection_task_defer_keeps_the_attempt}`.

`uv run pytest -m "not live" -q -rxXfE`:

```text
1142 passed, 23 skipped, 9 xfailed, 8 warnings in 878.84s (0:14:38)
```

(23 skipped — наявні пропуски інших WP, не PR1c; 9 xfailed — ті самі `NEEDS_PR3A`.)

`uv run pre-commit run --all-files`:

```text
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
```

### Перевірка проти WP-01A PR3a (тимчасова гілка, не основна)

Тимчасовий worktree у scratchpad: `wp/01a-3a-queue-outbox-preflight` (`ce3d9b4`) + merge
`wp/01d-1c-handler-plumbing` (`353a638`), без конфліктів; після перевірки worktree і гілку
`tmp/wp-01d-1c-on-pr3a` видалено. Основна гілка PR1c **не** перебазована (рішення оркестратора).
Сигнатури з розділу «Нові публічні API» звіту PR3a збігаються з Protocol-ами `_Pr3a*`.

`uv run pytest --runxfail -m integration tests/integration/scaling/test_handler_plumbing.py
tests/integration/scaling/test_projection_backend.py tests/integration/scaling/test_scheduler_ticks.py -q`:

```text
21 passed in 310.99s (0:05:10)
```

Тобто **усі 9 тестів `NEEDS_PR3A` проходять проти PR3a**, решта 12 тестів PR1c теж.
`uv run mypy src` на злитому дереві:

```text
src\collector\workers\backends.py:212: error: Redundant cast to "_Pr3aQueueRelease"  [redundant-cast]
src\collector\workers\backends.py:213: error: Redundant cast to "_Pr3aProjectionRetry"  [redundant-cast]
src\collector\workers\backends.py:214: error: Redundant cast to "_Pr3aProjectionRelease"  [redundant-cast]
src\collector\workers\backends.py:215: error: Redundant cast to "_Pr3aAcknowledge"  [redundant-cast]
Found 4 errors in 1 file (checked 81 source files)
```

Очікувано: `cast` до сигнатур PR3a стає зайвим — прибирається при rebase (чек-лист нижче).
Раніше, до готовності PR3a, ті самі 9 тестів проганялись із локальним shim-ом, що емулює
задокументовані сигнатури (`--runxfail`): `17 passed` для двох файлів — shim не комітився.

### Чек-лист rebase на PR3a (після злиття PR3a)

1. `backends.py`: прибрати `_Pr3a*` Protocol-и і `cast`, викликати репозиторії напряму
   (інакше `mypy --strict` червоний — `redundant-cast`).
2. Прибрати `@NEEDS_PR3A` з 9 тестів і сам маркер у `tests/integration/scaling/conftest.py`
   (інакше strict XPASS червонить прогін).
3. Прогнати `uv run pytest -m integration tests/integration/scaling` і `-m "not live"`.

## Що не перевірено

- **Linux/CI не проганявся** в цій сесії (Windows локально). Код без платформних гілок;
  тести не використовують сигналів ОС чи шляхів Windows. Перевіряється CI після push оркестратором.
- **Compose-стек** (`docker compose ... up --wait`, `--scale fetch-worker=4`) не піднімався —
  заборонено на спільному хості; PR1c не змінює `docker-compose.yml`, образів чи env сервісів.
- **9 тестів `NEEDS_PR3A` у основній гілці** — `xfail(strict=True)`: проти PR3a вони пройшли на
  тимчасовій гілці (вище), але в `wp/01d-1c-handler-plumbing` перевіряються після rebase.
- **Реальні доменні модулі** (`collector.fetch.handler`, `collector.translation.handler`,
  `collector.workers.projector`, тіки WP-01B) ще не існують — реєстр перевірено тимчасовими
  модулями в `tmp_path`; шлях «модуля немає → Noop» — на справжній мапі.
- **Мутаційне тестування** (acceptance: «тестер довів мутаціями») — задача тестера. Тести, що
  мають червоніти: `defer` через `queue.retry` → `test_runtime_plumbing.py::
  test_defer_goes_through_release_not_retry` і integration `test_deferred_job_keeps_its_attempt_*`;
  ack поза report-транзакцією → `test_projection_backend.py::
  test_fault_between_ack_and_commit_leaves_no_partial_rows`; мовчазний `NoopHandler` при
  `ImportError` → `test_registry.py::test_broken_role_module_fails_boot_instead_of_a_silent_noop`.
- **Флак `test_self_fencing_fires_when_the_database_hangs_without_raising`** у всіх прогонах цієї
  сесії пройшов; PR1c не змінює fencing/heartbeat-логіки, лише джерело backend-а в heartbeat.

## Ризики

| Ризик | Статус | Owner |
|---|---|---|
| Прогони на спільному завантаженому хості були повільні (scaling 20–45 хв замість ~4): перший повний прогін дав 4 хибні падіння за 15-секундним `wait_for` (boot projector-а під LOGIN-роллю ~8 с) і `TimeoutError` maintenance-тіку. Виправлено: у тестах PR1c бюджет очікування стану 60 с (`patient_wait_for`, лише нові файли), у runtime — нижня межа 30 с для client-side стелі maintenance-проходу (до PR1c стелі не було взагалі). Наявні scaling-тести не змінювались. У повторному повному прогоні — 0 падінь | mitigated | WP-01D |
| До rebase на PR3a виклики нових API типізовані через `cast` (без `type: ignore`); після rebase mypy сам вимагає їх прибрати (`redundant-cast`) | accepted, чек-лист rebase | WP-01D |
| `ProjectionTasksBackend.quarantine` фенсить карантин через `heartbeat_projection_task` у тій самій транзакції (у репозиторії немає `quarantine_projection_task(owner=)`) | accepted, побажання в deps §7.3 | WP-01A (опційно) |
| `Task.args` для projection не містить повного `NormalizedArtifactRef` — лише `artifact_id` (у рядку task повного ref немає); projector читає artifact сам | accepted, задокументовано (`docs/workers.md` 5.3) | WP-01B |
| Доменний тік, що ігнорує скасування, після `timeout_seconds` лишиться «висіти» у фоні (`wait_for` скасовує, але не може примусити) | accepted: контракт `async`/скасовуваності той самий, що для handler-ів | доменні WP |
| Інтервали доменних тіків (300 с / 3600 с / 5 с) — дефолти WP-01D без env-override | accepted; зміна — dependency-запитом | WP-01B / WP-01D |

## Як вимкнути або відкотити

- **Placeholder без перебудови image:** `COLLECTOR_WORKER_PLACEHOLDER=1` — CLI запускає
  placeholder до runtime, реєстр не імпортує жодного доменного модуля (тест
  `test_registry.py::test_placeholder_bypasses_the_domain_import`).
- **Зламаний доменний модуль ролі** валить boot ролі (навмисно); тимчасово прибрати роль —
  scale відповідного `*-worker` до 0 або placeholder для цього сервісу.
- **Publisher N-2** вимкнений за замовчуванням (`COLLECTOR_OUTBOX_PUBLISHER_ENABLED`).
- **Повний відкат PR1c:** `git revert` комітів PR1c; схема БД і контракти не змінювались, міграцій
  немає. Ролі без доменних модулів (усі на момент PR1c) поводяться як до PR1c.

## Dependency-запити

- `docs/plan/deps/WP-01D-to-WP-01A.md` §7 (новий): очікувані сигнатури і семантика PR3a
  (`queue.retry/release(not_before=)`, `retry/release_projection_task(not_before=)`,
  `acknowledge_projection(owner=)`), чек-лист rebase; побажання (низький пріоритет) —
  `quarantine_projection_task(..., owner=)`.
- Нових таблиць/колонок, змін контрактів, compose чи секретів PR1c не потребує.
