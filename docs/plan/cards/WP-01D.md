# Картка WP-01D — Worker pool control

| Поле | Значення |
|---|---|
| Owner | wp-implementer (єдиний owner worker runtime і orchestration adapters) |
| Branch | `wp/01d-1-worker-runtime`, `wp/01d-1b-runtime-role-dsn`, `wp/01d-1c-handler-plumbing` (передумова хвилі 1, 2026-09-24), `wp/01d-2-limiter-runtime`, `wp/01d-3-drain-adapters` |
| Worktree | `.worktrees/wp-01d` |
| Залежить від | WP-00 (усі три PR) `merged`, WP-01A PR1 `merged` |
| Розблоковує | WP-02, WP-03, WP-04 (runtime workers), WP-11C (екран Workers), WP-12 |
| Розмір | L → три PR одного owner |
| Розділи ТЗ | §7.5 (scaling, Compose/Swarm), §7.6 (повністю), §13 (без Docker socket, allowlist controller), §15 (capacity ceiling), §16.1 п.15, §16.3 (scale/drain/kill пункти), FR-031—FR-033, FR-035 |
| Рівні тестів §16.1 | 15 Scaling, 3 Integration, 1 Unit |
| Регресії REVIEW.md | R-52 (незалежні pools, drain, lease recovery), R-53 (aggregate rate не залежить від replicas), R-55 (GUI/API без socket; Swarm controller ізольований), R-57 (role-wide drain barrier, не покладатися на вибір контейнера orchestrator-ом) |
| Q-питання | Q-013 (Compose MVP / Swarm для GUI-scaling — default, ADR у PR3), Q-014 (autoscale off за замовчуванням) |

## Scope

Перетворити placeholder-процеси `collector worker <role>`/`scheduler` на справжній stateless runtime: claim із черги WP-01A, lease heartbeat, graceful drain, реєстрація instance, роздача origin-permits усім реплікам, desired/current state pools, scale-команди та два deployment adapters (Compose CLI — без socket; Swarm — ізольований allowlisted controller).

## Out of scope

Доменна робота workers (fetch — WP-02, discovery — WP-03, translation — WP-04, projector — WP-01B): цей WP дає **runtime-каркас**, у який вони вставляють `handle(task)`. GUI (WP-11C), метрики/алерти (WP-12), autoscale-політика в production (після pilot, Q-014).

## Owned files

`src/collector/workers/**` (крім доменних handler-ів інших WP), `src/collector/orchestration/{compose,swarm}/**`, `src/collector/core/limiter_runtime.py` (клієнтська обгортка над PG-лімітером WP-01A), `src/collector/cli.py` (лише команди `worker`, `scheduler`, `controller`), `tests/integration/scaling/**`, `tests/unit/workers/**`, `docs/plan/reports/WP-01D/**`, `docs/plan/deps/WP-01D-to-*.md`, `docker-compose.yml` (лише worker/scheduler/controller-сервіси та їхні env/healthcheck — узгоджувати з WP-00 через dependency-запит, якщо зачіпає інші сервіси).

Forbidden: `src/collector/contracts/**`, `schemas/**`, `migrations/**` (нові таблиці/колонки — dependency-запит до WP-01A), `web/**`, `deploy/compose/gui/**`.

---

## PR1 — `wp/01d-1-worker-runtime`: worker loop, lease, heartbeat, registration

### Вимоги

1. `WorkerRuntime`: boot `worker_instance_id` (UUIDv7), реєстрація в `worker_instances` (role, version, deployment metadata, status `starting`), перехід у `ready` після readiness-перевірки залежностей, heartbeat-таск із інтервалом менше lease TTL.
2. Claim-loop: бере до `desired_concurrency` tasks через репозиторій WP-01A, виконує `handle(task)` (інтерфейс `TaskHandler`, реалізації — інші WP; тут — `NoopHandler` для тестів), продовжує lease під час виконання, звітує `complete`/`retry`/`quarantine`.
3. Обробка сигналів: SIGTERM → `draining` (нові claim заборонені, активні завершуються в межах `stop_grace_period`, lease повертаються), потім exit 0; SIGKILL — fault case, lease відновлює `recover_expired_leases` іншого instance (тест).
4. `desired_concurrency` змінюється без рестарту: нові slots відкриваються одразу, зайві закриваються після завершення активних tasks (тест на гарячу зміну).
5. Жодного стану на локальному диску; `worker_instance_id` генерується на boot; Docker hostname лише як metadata.
6. `scheduler` — singleton через advisory lease у PostgreSQL: другий instance не стає активним, а чекає; при втраті lease — припиняє планування (тест на два scheduler).
7. **Дешевий liveness-probe для Docker healthcheck** (запит від WP-00 PR3, CI PR #4). Зараз healthcheck кожного application-контейнера — `python -m collector.api.health <deps>`, тобто **повний старт інтерпретатора** на кожну пробу плюс з'єднання з БД. Заміряно на образі `collector:dev` (`docker run --cpus=N`, медіана з 3): 1.0 CPU — 2.9 с, 0.5 CPU — 4.1 с, 0.25 CPU — 6.0 с; на 2-ядерному CI-runner-і зі стартом 17 контейнерів це вимагало підняти `timeout` до 15 с і `start_period` до 90 с (`docker-compose.yml`, anchor `x-healthcheck-budget`). Коли з'явиться реальний worker runtime (довгоживучий процес), він має віддавати liveness **без** запуску нового інтерпретатора і **без** звертання до БД — наприклад unix-socket/файл heartbeat у tmpfs або мінімальний HTTP-endpoint у самому процесі; readiness (залежності) лишається за `depends_on`/one-shots і за `worker_instances.status`. Тоді бюджет healthcheck можна повернути до секундних значень. Розділяти liveness і readiness у WP-00 не можна: §7.5 прямо вимагає, щоб healthcheck перевіряв «process + критичну dependency», і це закріплено тестом `tests/unit/test_compose_config_adversarial.py::test_application_healthchecks_name_a_critical_dependency`, тож зміна семантики має йти разом зі зміною ТЗ/тесту — тобто у власника runtime.

### Тести

Killed replica → lease recovery іншим instance; drain під активним task (task завершується, lease не втрачено); гаряча зміна concurrency; два scheduler → рівно один активний; heartbeat не продовжує чужий lease.

---

## PR1b — `wp/01d-1b-runtime-role-dsn`: runtime на per-component LOGIN-ролях (блокер pilot §13)

Закриває `docs/plan/deps/WP-01A-to-WP-01D.md` §1–§2 (і ризик I-1 `security-pr2.md`). Worktree `.worktrees/wp-01d-1b`. Паралельно з WP-00 PR4 (`wp/00-4-role-dsn-secrets`), який генерує секрети й виставляє паролі ролям у `migrate-postgres`.

### Owned files

Як у PR1, у `docker-compose.yml` — лише `x-worker`, `scheduler`, `*-worker` сервіси, плюс ідентичний блок top-level `secrets:` з картки WP-00 PR4 п.2 (дослівно; тільки щоб `docker compose config` був валідний у цій гілці — owner блоку WP-00). `tests/unit/test_compose_config*.py` — лише тест-вартовий і нові тести на worker/scheduler-сервіси.

### Вимоги

1. Кожен runtime-сервіс монтує **лише свій** `postgres_dsn_<component>` як `COLLECTOR_POSTGRES_DSN_FILE`; спільний `postgres_dsn` жоден runtime-сервіс не монтує. Мапінг: `scheduler`, `maintenance-worker` → `scheduler`; `discovery-`, `fetch-`, `browser-worker` → `fetcher`; `parse-worker` → `parser`; `projector-worker` → `projector`; `translation-worker` → `translation`.
2. `export-worker`: рішення оркестратора — варіант (а) deps §1: runtime-черга під `collector_scheduler` (`postgres_dsn_scheduler`) як тимчасовий крок, бо доменного читання даних експортом ще немає; read-only з'єднання `collector_export_ro` додає власник експорту. Записати як відомий ризик у картці (owner — WP експорту / WP-01A PR3 за потреби окремої ролі `collector_exporter`).
3. `verify_runtime_login(conn)` викликається при старті `WorkerRuntime` і `scheduler` до першого claim; `RoleLoginError` → процес завершується ненульовим кодом із зрозумілим повідомленням (без DSN/пароля в лозі). Rollback: `COLLECTOR_WORKER_PLACEHOLDER=1` як і раніше.
4. Тест-вартовий `test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` і його зонди замінити позитивним тестом §13: жоден сервіс, крім `migrate-postgres`, не монтує `postgres_dsn`; кожен runtime-сервіс монтує рівно один `postgres_dsn_<component>` згідно з мапінгом.
5. `_release_leases` під час планового drain використовує `queue.release(job_id, owner)` замість обходу через `retry(..., IMMEDIATE_RETRY_POLICY)`; тест: drain не змінює `attempt` і не пише полів помилки.
6. Out of scope: publisher loop outbox (deps §7, N-2) — publisher-а ще немає; вимога переходить у картку його власника (WP-01B).

### Тести

Unit compose (п.1, п.4); unit/integration: runtime під superuser DSN або членом `collector_migrate` не стартує (п.3), під `collector_fetcher` стартує і claim-ить; integration drain → `release` (п.5). Повний `tests/integration/scaling/**` — під LOGIN-ролями, а не під superuser, де це можливо (фікстури `tests/integration/postgres/test_role_logins.py` WP-01A — як зразок).

### Acceptance

Після `init-secrets.sh` + `docker compose --profile core --profile workers up -d --wait` усі runtime-процеси підключені не-superuser ролями (`SELECT usename, usesuper FROM pg_stat_activity JOIN pg_user ...` у звіті); тест-вартовий прибрано, позитивний тест §13 зелений.

---

## PR1c — `wp/01d-1c-handler-plumbing`: runtime-передумови для доменних handler-ів хвилі 1

Передумова хвилі 1 (рішення оркестратора, 2026-09-24): збирає dependency-запити чернеток
WP-01B (§«Як projector-worker підключається до runtime WP-01D» п.2 (а)–(г)), WP-02 (запит до
WP-01D п.1–3) і WP-04 (запит до WP-01D п.3а–3б). Worktree `.worktrees/wp-01d-1c`. Стартує
одразу (U-3); **merge — після WP-01A PR3a** (новий `not_before` у `queue.retry`,
`queue.release(..., not_before=)` і їхні аналоги для `projection_tasks`, fencing у
`acknowledge_projection` — див. `WP-01A.md` PR3a п.1, п.7).

**Розблоковує:** WP-02 PR2 (п.1–4), WP-04 PR2 (п.1, п.3, п.4), WP-01B PR3 (п.3–6).

### Факти з коду (перевірено 2026-09-24)

- `TaskResult` (`src/collector/workers/handlers.py`) має лише `disposition ∈ {complete, retry,
  quarantine}`, `error_code`, `error_message`; немає `not_before`, немає `defer`, немає
  результату для report-транзакції.
- `WorkerRuntime._report` (`runtime.py`) викликає `queue_repo.retry(...)` **без** `policy` →
  дефолт `BackoffPolicy()` = 30 с × 2 до 6 год + jitter 20 % (`repositories/queue.py`); таблиця
  §10 (5 с / 30 с / 2 хв / 10 хв) не експоненційна і цим типом не виражається.
- `WorkerRuntime._claim` і `_report` жорстко працюють із `crawl_jobs` через `queue_repo`;
  `Task` — знімок `CrawlJob` (`_task_from_job`).
- `HandlerFactory = Callable[[WorkerRole], TaskHandler]`; `HANDLER_FACTORIES` наповнюється «при
  імпорті модуля ролі», але жоден код не імпортує доменних модулів — `resolve_handler` завжди
  повертає `NoopHandler`. Handler не отримує ні `async_sessionmaker`, ні `worker_instance_id`.
- `SchedulerRuntime` бере один `tick` (дефолт `make_maintenance_tick`: `recover_expired_leases`,
  `mark_stale_instances`); `recover_expired_projection_leases` ніхто не викликає; тік має
  сигнатуру `(session, now)`, тобто працює всередині однієї транзакції.

### Owned files

`src/collector/workers/{handlers,runtime,scheduler,config}.py`, нові
`src/collector/workers/{registry,backends}.py`, `tests/unit/workers/**` (нові й зачеплені),
`tests/integration/scaling/**`, `docs/plan/reports/WP-01D/*-pr1c.md`; етап 5 — `docs/workers.md`
§5–§6.

Forbidden: `src/collector/persistence/**` (потрібні зміни — WP-01A PR3a), доменні модулі
(`collector.fetch`, `collector.translation`, `collector/workers/{projector,reconciler,compactor,
publisher}.py`), `docker-compose.yml` (нові секрети/env воркерів — WP-00 PR5 за винятком
оркестратора), `src/collector/contracts/**`.

### Вимоги

1. **`not_before` і `defer` без спалювання спроби (WP-02 п.1–2, WP-04 п.3а).**
   `TaskResult.retryable(error_code, error_message=None, *, not_before=None)` — нижня межа для
   наступної спроби (наприклад, з `Retry-After`); нова disposition `defer`:
   `TaskResult.deferred(until, error_code)` → runtime повертає job через
   `queue.release(..., not_before=until)` (WP-01A PR3a): `attempt` не змінюється (компенсація
   інкременту claim, як у плановому drain), поля помилки не пишуться, dead letter не
   створюється. `until` у минулому → `now`; `until` далі за конфігурований максимум
   (`COLLECTOR_WORKER_MAX_DEFER_SECONDS`, default 24 год) → clamp з warning. Лог
   `worker.task_deferred` з `error_code`, без тексту задачі.
2. **Retry policy за таблицею §10, яку передає handler.** `TaskHandler.retry_schedule` (property,
   default `None` → поточний `BackoffPolicy()` без зміни поведінки для наявних ролей) повертає
   `RetrySchedule(delays: tuple[timedelta, ...], jitter_ratio)` — затримка для спроби `n` =
   `delays[min(n, len) - 1]` + jitter, обмежений зверху; runtime обчислює
   `not_before = max(now + schedule.delay(attempt), result.not_before or now)` і викликає
   `queue.retry(..., not_before=...)`. `max_attempts` лишається властивістю job-и (ставить
   планувальник домену при enqueue, для fetch — 4, §10); runtime його не перевизначає.
3. **Реєстр доменних handler-ів з lazy import за `WorkerRole`** — `collector/workers/registry.py`:
   статична мапа роль → dotted-path модуля, що при імпорті реєструє фабрику:
   `FETCH → collector.fetch.handler`, `BROWSER → collector.fetch.browser`,
   `TRANSLATION → collector.translation.handler`, `PROJECTOR → collector.workers.projector`
   (+ `collector.workers.reconciler`, `collector.workers.compactor` — див. п.5). Нові записи
   (WP-03 discovery, WP-05 parse, WP-11A export) — dependency-запитом до WP-01D.
   `resolve_handler` імпортує модуль ролі: `ModuleNotFoundError` **саме цього** модуля (модуля
   ще немає в `main`) → `NoopHandler` + warning `worker.handler_module_missing`; будь-який інший
   `ImportError`/виняток під час імпорту або модуль імпортувався, але фабрику не зареєстрував →
   boot падає ненульовим кодом (без мовчазного Noop). `COLLECTOR_WORKER_PLACEHOLDER=1` як і
   раніше обходить імпорт.
4. **Передача контексту у фабрику (WP-02 п.3).** `HandlerFactory = Callable[[HandlerContext],
   TaskHandler]`, `HandlerContext(role, sessions: async_sessionmaker[AsyncSession],
   worker_instance_id, clock, env)`; handler не відкриває другого пулу під тим самим DSN і
   використовує `worker_instance_id` як `owner` upload claims/permits. Сумісність: фабрики
   старої форми в `tests/**` мігрують у цьому PR; `NoopHandler` не змінюється.
5. **Queue backend для `projection_tasks` (WP-01B п.(а)–(б)).** `collector/workers/backends.py`:
   Protocol `QueueBackend` (`claim`, `heartbeat`, `complete`, `retry`, `defer`, `quarantine`,
   `release`, `recover_expired`) і дві реалізації — `CrawlJobsBackend` (поточна поведінка через
   `repositories/queue.py`) і `ProjectionTasksBackend` (через `repositories/projection.py`:
   `claim_projection_tasks`/`heartbeat_projection_task`/`retry_projection_task`/
   `release_projection_task`/`quarantine_projection_task`/`recover_expired_projection_leases`).
   Роль може мати **кілька прив'язок** `HandlerBinding(backend, handler)`: для `projector` —
   `ProjectionTasksBackend` + `ProjectorHandler` і `CrawlJobsBackend` + handler-и
   reconcile/compact (`job_types` `projection.reconcile`, `projection.compact`; рішення
   оркестратора WP-01B п.1 — reconciler і compactor працюють під `collector_projector`, а
   scheduler лише ставить task у чергу). Слоти `desired_concurrency` спільні для всіх
   прив'язок ролі; claim по прив'язках — round-robin, щоб одна черга не голодувала іншу.
   **Ack у report-транзакції:** `TaskResult.success(output=...)`; для `ProjectionTasksBackend`
   `complete` = `acknowledge_projection(session, task_id, receipt, owner=self.owner)` у тій
   самій транзакції, що й звіт (task → `succeeded` робить сам ack; `output` не
   `AppliedProjectionReceipt` → `quarantine` з `error_code="invalid_handler_output"`).
   `LeaseNotOwnedError`/`ConflictError` від ack обробляються як у `_report` сьогодні
   (lease_lost / report_failed) — без повторної серіалізації event bytes.
   `Task` для projection: `job_id = task_id`, `job_type = target_collection`, `args` — поля
   `ProjectionCommand` (entity, version, artifact ref), `source_id = None`.
6. **Реєстр тіків scheduler-а (WP-01B п.(г)).** `SchedulerRuntime` виконує композицію тіків:
   вбудований maintenance (`recover_expired_leases`, `mark_stale_instances`) **плюс**
   `recover_expired_projection_leases` (роль `collector_scheduler` має column UPDATE на
   `projection_tasks`, `roles.sql`), і доменні тіки з мапи lazy import (як п.3): WP-01B
   `collector.workers.reconciler:schedule` / `collector.workers.compactor:schedule` (лише
   `queue.enqueue` задач `projection.reconcile`/`projection.compact` з ідемпотентним ключем
   від вікна часу), `collector.workers.publisher:tick` (N-2, вимкнений за замовчуванням). Нова
   сигнатура доменного тіку — `async def tick(ctx: TickContext)` з `sessions`, `now`,
   `lease_is_ours()` (publisher робить коротку транзакцію → доставку поза транзакцією →
   коротку транзакцію, тож одна транзакція на тік не підходить). Кожен тік має власний інтервал,
   власну транзакцію(ї) і ізольований від інших: виняток одного тіку логується
   (`scheduler.tick_failed`, `tick=<name>`) і не зупиняє решту та lease. Контракт
   ідемпотентності тіку (`docs/workers.md` §6) поширюється на доменні тіки.

### Тести

- Unit `TaskResult`: `deferred` без `error_code` → `ValueError`; `not_before` лише для `retry`;
  `output` лише для `complete`.
- Integration (PostgreSQL 18, LOGIN-ролі як у PR1b): handler повертає `deferred(now+10 хв)` →
  job `pending`, `attempt` той самий, `last_error_*` не змінені, `not_before` = until; п'ять
  `deferred` поспіль на job з `max_attempts=4` → **жодного** dead letter; `retryable(not_before=
  now+2 год)` при `retry_schedule` 5 с → `not_before ≥ now+2 год`; `retry_schedule`
  (5 с/30 с/2 хв/10 хв, jitter 0) → чотири послідовні retry дають рівно ці затримки, 4-та
  невдача з `max_attempts=4` → `quarantined` + dead letter; без `retry_schedule` — поточний
  `BackoffPolicy` (регресія).
- Реєстр: модуль ролі відсутній → `NoopHandler` + warning; модуль кидає `ImportError`
  всередині → boot exit ≠ 0; модуль імпортується, але не реєструє фабрику → boot exit ≠ 0;
  фабрика отримує `HandlerContext` з тим самим `sessions`, що runtime (ідентичність об'єкта), і
  `worker_instance_id` зареєстрованого instance.
- Projection backend (PostgreSQL, роль `collector_projector`): fake `ProjectorHandler` повертає
  receipt → ack, task `succeeded`, `projection_acknowledgements` 1 рядок — в **одній**
  транзакції (fault-seam між ack і commit → жодного часткового запису); lease забрали до report
  → ack не виконано (`lease_lost`), повторний claim іншим instance → ack рівно один; retry /
  defer / quarantine / drain-release для projection task працюють як для `crawl_jobs`;
  дві прив'язки (`projection_tasks` + `crawl_jobs` `projection.reconcile`) з
  `desired_concurrency=1` → обидві черги обслуговуються (жодна не голодує 10 циклів поспіль).
- Scheduler: `recover_expired_projection_leases` у дефолтному тіку повертає прострочений task у
  чергу; доменний тік, що кидає, не зупиняє maintenance-тік і не відпускає lease; два
  scheduler-и з однаковими доменними тіками (перекриття, вартовий
  `test_maintenance_tick_is_safe_when_two_schedulers_overlap`) → жодного дубля enqueue.
- Регресія: весь `tests/integration/scaling/**` і `tests/unit/workers/**` зелені; відомий флак
  `test_self_fencing_fires_when_the_database_hangs_without_raising` — не внесений цим PR
  (доказ — прогін на `main`).

### Acceptance PR1c

Команди перевірки зелені локально і в CI (Linux); `NoopHandler`-ролі поводяться як до PR1c
(регресія scaling); `docs/workers.md` §5–§6 описують `HandlerContext`, `defer`, `retry_schedule`,
кілька прив'язок ролі й доменні тіки; тестер довів мутаціями: `defer` через `queue.retry` →
тест червоний; ack поза report-транзакцією → тест червоний; мовчазний `NoopHandler` при
`ImportError` → тест червоний.

---

## PR2 — `wp/01d-2-limiter-runtime`: global origin permits у runtime

### Вимоги (R-53, FR-033)

1. `OriginPermitClient`: перед кожним зовнішнім запитом бере leased permit через PG-лімітер WP-01A; повертає ідемпотентно; при expiry — не робить запит.
2. Локальний семафор на контейнер лише **додатково** обмежує concurrency, ніколи не підвищує дозволену частоту.
3. `Retry-After`/429 від джерела → `block_origin` через лімітер; усі репліки бачать блок (тест з двома runtime-процесами).
4. Aggregate-тест: N реплік × M concurrency проти одного origin із політикою 0.2 rps/1 concurrent → сумарна частота не перевищує політику (детермінований вимір через симульований годинник або підрахунок виданих permits, не wall-clock).
5. Метрики-заготовки (`origin_rate_permits_total{origin_group,result}`, `origin_inflight`) — лише лічильники в коді, експорт — WP-12.

---

## PR3 — `wp/01d-3-drain-adapters`: pools desired state, scale commands, Compose/Swarm adapters

### Вимоги (§7.5, §7.6, R-55, R-57)

1. `PoolController`: звіряє desired (`worker_pools`) і current (heartbeat-derived) стан; формує `scale_commands` із idempotency key і expected revision.
2. **Role-wide drain barrier** перед зменшенням replicas (PR1 дає лише per-instance примітив — `worker_instances.drain_requested_at` зупиняє claim того instance, у якого він виставлений; «role-wide» означає, що `PoolController` зобовʼязаний виставити барʼєр **кожному** живому instance ролі й дочекатися підтвердження від усіх, перш ніж зменшувати replicas): усі instances ролі припиняють claim, повертають lease, orchestrator зменшує replicas, survivors відновлюють claim після підтвердження нової revision. Не покладатися на те, який контейнер видалить Compose/Swarm (R-57) — тест, що після `4→1` жоден task не втрачено і не дубльовано.
3. **Compose adapter:** не має Docker socket; переводить команду в `awaiting_manual_apply` з точним CLI-рядком; `applied` лише коли heartbeat-derived replicas збігаються з desired revision.
4. **Swarm adapter:** окремий процес `collector controller` (запускається лише на manager node); allowlist за label `collector.scalable=true`; дозволений diff — **лише** `replicas` у межах min/max; заборонено змінювати images/mounts/networks/secrets/stateful services (тест, що спроба відхиляється); читає лише committed `scale_commands` з audit link.
5. Autoscale вимкнений за замовчуванням (Q-014); політика (queue oldest age + pending/running ratio, 3 вікна, 5-хв cooldown, min/max, окремий бюджет browser/translation) реалізована, але активується прапорцем; manual override має пріоритет.
6. Compose: worker-сервіси отримують реальні команди `collector worker <role>` замість placeholders; scheduler — singleton; controller — окремий сервіс у profile (за замовчуванням не запускається).

### Тести (§16.1 п.15, §16.3)

Replicas `1→4→1→0→2`; concurrent global rate-limit; expired permit recovery; concurrency hot-change; drain during active task; killed replica lease recovery; stale command/revision відхиляється; controller allowlist (спроба змінити image/mount/network → відмова); Compose mode повертає audited CLI; тест, що GUI/API/worker images не мають socket (успадкований від WP-00 — перевірити, що лишається зеленим).

## Команди перевірки (усі PR)

```bash
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run mypy src
uv run pytest -m "not live"
uv run pytest -m integration tests/integration/scaling
docker compose config --quiet
docker compose --profile core --profile workers up -d --wait
docker compose up -d --no-recreate --scale fetch-worker=4
docker compose down -v
```

## Acceptance (§17.2)

«role commands, pool/instance/scale contracts, PostgreSQL origin limiter, heartbeat/drain, Compose command adapter і Swarm replica adapter; scale/rate/fault tests green».

## Відомі ризики (перенесені з PR1)

| Ризик | Статус | Owner | Дата |
|---|---|---|---|
| §13: усі 8 runtime-процесів ходять у PostgreSQL під тим самим DSN, що й міграції (роль `collector`, superuser), бо всі `collector_*` ролі — `NOLOGIN`. Радіус ураження — увесь worker pool; **блокер pilot/production і live-збору**, не блокер merge PR1 (знахідка F1 gate 2, доказ у `docs/plan/reports/WP-01D/testing-pr1.md`) | **closed у PR1b, крім export-worker** (див. рядок про export-worker нижче — для exporter §13 «read-only роль» закрито не повністю): кожен runtime-сервіс монтує лише свій `postgres_dsn_<component>`, процес при старті перевіряє роль (`collector.workers.login`), позитивний тест §13 `tests/unit/test_compose_config.py::test_runtime_services_use_only_their_own_login_dsn_13`; доказ на стеку (`pg_stat_activity` × `pg_roles`) — `docs/plan/reports/WP-01D/implementation-pr1b.md` | WP-01A PR2 (LOGIN-ролі) + WP-00 PR4 (секрети, `--with-login`) + WP-01D PR1b | заведено 2026-09-23, закрито PR1b |
| Плановий drain на останній спробі не повертає lease одразу, а чекає експірації (немає `queue.release`) | **closed у PR1b**: `_release_leases` → `queue.release` (job одразу `pending`, `attempt` не змінюється, полів помилки немає) | WP-01A PR2 + WP-01D PR1b | заведено 2026-09-23, закрито PR1b |
| `export-worker` працює під `collector_scheduler` (варіант (а) deps WP-01A→WP-01D §1; S-1 medium `docs/plan/reports/WP-01D/security-pr1b.md`): runtime-черга пише `worker_instances`/`crawl_jobs`/`audit_log`, а `collector_export_ro` — read-only. **§13 для exporter закрито не повністю.** Зайві для експорту права `collector_scheduler`: INSERT/UPDATE на `sources`, `source_policy_versions`, `source_routes`, `crawl_runs`, `crawl_jobs`, `origin_rate_buckets`, `scale_commands`, `worker_pools`; INSERT `audit_log`; UPDATE `dead_letters`; column-UPDATE колонок публікації `outbox_events` і `projection_tasks`; SELECT `fetches`/`raw_objects`/`entity_index` — компрометація export-процесу дала б зміну політик джерел/rate buckets, довільні jobs, підробку `scale_commands`+`audit_log` і «опубліковані» outbox-події. Сьогодні handler — `NoopHandler`, фактичного використання немає. Тест-вартовий `tests/unit/workers/test_db_login.py::test_export_worker_keeps_scheduler_role_only_while_its_handler_is_noop` падає, щойно export отримує не-Noop handler, поки мапиться на `collector_scheduler`/монтує `postgres_dsn_scheduler` | accepted (тимчасово). **Жорсткий тригер: закрити до merge першого реального export handler (WP-11A) або до pilot — що настане раніше** (окрема роль `collector_exporter` з базою черги як у fetcher/parser + read-only з'єднання `collector_export_ro` для даних) | WP-11A (export handler, `collector_export_ro`-з'єднання) / WP-01A (роль `collector_exporter`) | заведено 2026-09-24 (PR1b), тригер закриття — див. статус |
| Залишковий TOCTOU singleton-тіку scheduler-а: перевірка lease і сам тік ідуть різними зʼєднаннями, тому два тіки теоретично можуть перекритися. Закрито **контрактом** «тік мусить бути ідемпотентним» (докстрінги `scheduler.py`, `docs/workers.md` §6) і вартовим `test_maintenance_tick_is_safe_when_two_schedulers_overlap`, а не конструкцією — зобовʼязання лягає на майбутні доменні тіки (насамперед WP-03 `enqueue` discovery-jobs: потрібен власний ключ ідемпотентності §9.3 п.3) | mitigated (контракт + тест-вартовий) | WP-01D PR3 (fencing-токен у тіку, якщо зʼявиться неідемпотентне планування) / доменні WP | заведено 2026-09-23 |

Тест-вартовий PR1 (`test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` і його зонди) прибрано в PR1b: його передумова («LOGIN-ролей ще не існує») перестала виконуватись з WP-01A PR2. Замість нього — позитивний тест §13 `tests/unit/test_compose_config.py::test_runtime_services_use_only_their_own_login_dsn_13` (жоден сервіс, крім `migrate-postgres`, не монтує `postgres_dsn`; кожен runtime-сервіс — рівно один `postgres_dsn_<component>` за мапінгом) і runtime-перевірка ролі при старті.

## Rollback/disable

Autoscale off; controller у окремому profile (не запускається за замовчуванням); worker-сервіси можна повернути на placeholder-команду через env (задокументувати).

## Docs (етап 5)

`docs/runbooks/scale-drain-recover.md`, ADR-0006 «Deployment mode: Compose MVP, Swarm для GUI-scaling» (Q-013), ADR-0007 «Autoscale вимкнений до pilot evidence» (Q-014), `docs/workers.md` (ролі, lease/drain, як додати handler).
