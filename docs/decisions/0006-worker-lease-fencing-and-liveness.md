# ADR-0006: Часовий self-fencing worker-lease і liveness, відокремлена від readiness

| Поле | Значення |
|---|---|
| Date | 2026-09-23 |
| Owner | WP-01D |
| Status | accepted |

## Context

§7.5 ТЗ вимагає дослівно: «healthcheck перевіряє process і критичну dependency; readiness
лишається false до migrations/validators; scheduler/controller мають singleton advisory
lease» і «`stop_grace_period` довший за максимальний bounded task shutdown; SIGTERM запускає
drain, SIGKILL є fault case з lease recovery». Вимога 7 картки WP-01D PR1 додає до цього
конкретну проблему: healthcheck кожного application-контейнера був `python -m
collector.api.health <deps>` — повний старт інтерпретатора плюс з'єднання з PostgreSQL на
кожну пробу. На образі `collector:dev` це коштувало 2.9 с (1.0 CPU) до 6.0 с (0.25 CPU) за
пробу, і на 2-ядерному CI-runner-і зі стартом 17 контейнерів змусило підняти `timeout` до 15 с
і `start_period` до 90 с (`x-healthcheck-budget`, WP-00 PR3).

Паралельно з цим `WorkerRuntime` (`src/collector/workers/runtime.py`) мусить гарантувати
«lease належить рівно одному instance» (§7.6) — інакше два instance можуть одночасно виконувати
той самий job. Перша ітерація self-fencing (gate 2, знахідка F3) викликала перевірку вікна
fencing лише з `except` heartbeat-у: fencing спрацьовував, якщо PostgreSQL **кидав виняток**.
Код-рев'ю (знахідка H-1, `docs/plan/reports/WP-01D/code-review-pr1.md`) показало, що це закриває
не той сценарій відмови: типова відмова PostgreSQL у продакшені — не `ConnectionRefusedError`,
а **зависання** (мережевий поділ, failover, `idle in transaction` на боці сервера) — запит у
«чорну діру» TCP чекає до RTO ядра (десятки хвилин), а не до `lease_seconds`, і виняток не
приходить ніколи. Зонд, запущений код-рев'юером (`fence_after_seconds=0.2`, `lease_seconds=4`,
сесія, чий `__aenter__` зависає без винятку), підтвердив: через 2.01 с — **10× понад
`fence_after` і 50% lease TTL** — task лишався активним, fencing жодного разу не спрацював.
Це саме той сценарій, заради якого self-fencing і вводили.

## Decision

### Fencing керується часом від останнього підтвердженого heartbeat, а не фактом винятку

`WorkerRuntime._fence_if_lease_unconfirmed()` порівнює `monotonic()` із моментом останнього
**підтвердженого базою** heartbeat (`_last_heartbeat_ok`) і скасовує всі активні tasks, щойно
різниця перевищує `fence_after` — незалежно від того, повернула heartbeat-корутина виняток чи
просто не повернулась. Це працює трьома незалежними шарами, кожен з яких самодостатній:

1. **Сторож окремою `asyncio.Task`** (`_watchdog_loop`) — прокидається кожні
   `fence_after / WATCHDOG_STEPS` (4 рази за вікно fencing) і викликає перевірку незалежно від
   heartbeat-циклу. Якщо `_heartbeat()` зависне в драйвері, `_heartbeat_loop` стоїть, але сторож
   продовжує прокидатись за власним таймером і фенсить вчасно. Той самий сторож ловить
   заблокований event loop (M-2): якщо власний `sleep` прокинувся значно пізніше, ніж мав, —
   `worker.event_loop_stalled` у логах, бо синхронний `handle()` блокує все, включно зі сторожем.
2. **Бюджет на сам тік heartbeat** — `asyncio.wait_for(self._heartbeat(), timeout=heartbeat_tick_budget)`
   (`heartbeat_tick_budget = fence_after × 2`); `TimeoutError` теж веде до перевірки вікна fencing.
3. **Таймаути на рівні запиту**, які дають нижню межу двом попереднім шарам:
   `command_timeout` asyncpg (`max(fence_after, 1.0)` с) на engine, який створює `collector.cli`
   для CLI-шляху — клієнтська межа, рятує від зависання, коли сервер **не відповідає взагалі**;
   `statement_timeout` (`max(fence_after × 1000, 100)` мс, `SET LOCAL` через `set_config`) у
   **кожній** транзакції runtime (`collector.workers.session.bounded_transaction`: claim, report,
   статус, bootstrap pool, release lease, heartbeat, тік scheduler-а) — серверна межа, рятує від
   запиту, який сервер прийняв, але виконує довше, ніж worker готовий чекати. Без обох таймаутів
   жоден із двох попередніх шарів не має нижньої межі: сторож і бюджет тіку перевіряють час, але
   сам запит у драйвері міг би все одно висіти вічно і тримати з'єднання.

`fence_after` типово — половина `lease_seconds` (`FENCE_RATIO = 0.5`, `config.py`): один
пропущений heartbeat пробачається, два — вже ризик подвійного виконання. Межа
`heartbeat_seconds × 3 ≤ lease_seconds` (посилена з ×2 після знахідки L-1: рівність
`heartbeat × 2 == lease` робила будь-який RTT фатальним) лишає запас у повний пропущений тік.
`_heartbeat_loop` планує тіки від дедлайну (`deadline += heartbeat_seconds`), а не `sleep` після
роботи — тривалість повільного тіку більше не накопичує дрейф саме тоді, коли запас до
експірації lease потрібен найбільше.

Тест, який довів фікс, відтворює саме зависання, а не виняток:
`test_self_fencing_fires_when_the_database_hangs_without_raising`
(`tests/integration/scaling/test_worker_runtime.py`) — `HangingSessions.__aenter__` робить
`await asyncio.sleep(3600)` без жодного винятку; тест стверджує, що fence спрацьовує в межах
lease TTL, активний task скасовано, `complete` за ним не звітовано, claim зупинено.

### Liveness відокремлена від readiness для довгоживучих worker-процесів

Довгоживучий `WorkerRuntime`/`SchedulerRuntime` сам оновлює mtime маленького маркера в tmpfs
(`collector.workers.liveness.LivenessMarker`, `$TMPDIR/collector-runtime.alive`), і healthcheck
контейнера читає **лише** mtime (`test -f` + `stat -c %Y`, ~5 мс, без старту Python і без
запиту в БД). Маркер оновлює саме сторож lease (`_watchdog_loop`), тому синхронний `handle()`,
дедлок чи зависла корутина роблять контейнер unhealthy без жодного мережевого виклику — це
сильніший сигнал живого процесу, ніж «здатен підняти новий інтерпретатор і достукатись до
PostgreSQL».

Заміри «було/стало» одної проби всередині вже запущеного контейнера (`docker exec`, медіана з
3; ~0.4 с накладних `docker exec` входить в обидва числа порівну):

| CPU-ліміт | було: `python -c "import collector.api.health"` | стало: `test -f` + `stat -c %Y` |
|---|---:|---:|
| 1.00 | 1.77 с | 0.58 с |
| 0.50 | 3.04 с | 0.43 с |
| 0.25 | 6.07 с | 0.44 с |

Нова проба не робить жодного мережевого виклику, тому її вартість не залежить від CPU-ліміту —
на відміну від старої, яка додатково відкривала TCP-з'єднання до PostgreSQL. Бюджет healthcheck
worker-ів і `scheduler` (`docker-compose.yml`, anchor `x-liveness-budget`) повернуто до
секундних значень: `timeout` 15 с → 3 с, `start_period` 90 с → 20 с, `interval` 30 с → 10 с,
`start_interval` 5 с → 2 с, `retries` 5 → 3 (`api`/`gui` лишаються на `x-healthcheck-budget` —
вони справді ходять у залежності). На піднятому ізольованому стеку (16 контейнерів, образ цієї
гілки) `docker compose --profile core --profile workers up -d --wait` доходить до «все healthy»
за **22 с** — замінюючи попередній бюджет, розрахований на 90-секундний `start_period`.

**Узгодження з §7.5.** Вимога «healthcheck перевіряє process і критичну dependency» не
скасовується — вона розкладається на дві проби замість однієї:

- **liveness** (цей healthcheck) — процес живий, event loop не заблокований;
- **readiness** (залежності) — `depends_on` (`postgres: service_healthy`, one-shots
  `service_completed_successfully`) плюс `worker_instances.status`/`last_heartbeat_at`, які
  бачить оператор на екрані Workers (WP-11C).

`tests/unit/test_compose_config_adversarial.py::test_application_healthchecks_name_a_critical_dependency`
переписано під два класи проб; вимога «projector/export бачать Mongo; fetch/parse/export —
MinIO» перенесена в новий
`::test_worker_readiness_dependencies_are_declared_in_depends_on`, який перевіряє `depends_on`,
а не healthcheck-команду. Навмисний наслідок, зафіксований у тесті: **недоступний PostgreSQL
більше не робить worker-контейнери unhealthy**. Це свідомий вибір, а не регресія: рестарт
контейнера недоступність БД не лікує, а self-fencing (рішення вище) уже зупинив claim і
скасував активні tasks до того, як здогадався б healthcheck. Робити контейнер unhealthy в
цьому випадку означало б просити Docker перезапускати процес, який і так поводиться коректно.

## Consequences

- **Fencing ловить типову форму відмови PostgreSQL, а не лише виняткову.** До фіксу H-1
  self-fencing захищав рівно від «БД кидає помилку» (`FlakySessions` у тесті gate 2) — сценарію,
  який на практиці рідший за «БД не відповідає». Три шари (сторож, бюджет тіку, таймаути
  драйвера/транзакції) не залежать один від одного: навіть якщо сторож чомусь не спрацював,
  бюджет тіку fenсить сам; навіть якщо обидва пропущено, `statement_timeout` не дає запиту жити
  вічно.
- **Self-fencing зменшує вікно подвійного виконання, а не усуває його.** Вікно fencing (типово
  половина lease TTL) — це затримка виявлення, а не гарантія «рівно один виконавець»: між
  моментом, коли інший instance вже підхопив прострочений lease, і моментом, коли перший
  instance себе відгородив, короткий проміжок подвійної роботи теоретично можливий. §9.3
  (ідемпотентність за ключем) лишається обов'язковою вимогою для доменних handler-ів — fencing
  зменшує ймовірність, а не скасовує вимогу (зафіксовано в `docs/workers.md` §3).
- **Liveness більше не доводить, що PostgreSQL доступний.** Оператор, який бачить «healthy»
  контейнер, більше не може з цього робити висновок про стан БД — треба дивитись
  `worker_instances.status`/`last_heartbeat_at` (runbook `docs/runbooks/worker-recovery.md`).
  Це прийнятний компроміс: readiness ніколи не мала means «БД жива весь час роботи процесу»,
  лише «БД була жива на старті».
- **`command_timeout` покриває не всі шляхи запуску.** Він задається лише на CLI-шляху
  (`collector worker`/`collector scheduler`, де engine створює `collector.cli`); вбудований
  запуск (кілька runtime в одному процесі, як у тестах) створює engine сам і може не мати
  `command_timeout` — там нижню межу тримають лише `statement_timeout` (він ставиться в
  `bounded_transaction` для **кожної** транзакції runtime, незалежно від шляху запуску) і сторож.
  Задокументовано в докстрінгу `WorkerRuntimeConfig.command_timeout` і `collector.workers.session`.
- **Стек піднімається швидше, і CI платить менше.** Бюджет healthcheck worker-ів/scheduler
  повернуто до секундних значень; `up --wait` на ізольованому 16-контейнерному стеці доходить до
  healthy за 22 с замість бюджету, розрахованого на 90-секундний `start_period`.

## Residual risks

1. **TOCTOU у scheduler-тіку (код-рев'ю M-4).** `SchedulerRuntime.run()` перевіряє
   `_still_active()` (сервер-сайд `pg_locks` по lease-з'єднанню), а сам тік
   (`_run_tick`) відкриває **іншу** сесію з pool-у. Між перевіркою і commit-ом тіку lease
   теоретично може зникнути (`pg_terminate_backend`, failover, мережевий поділ), і другий
   scheduler, який його підхопить, почне свій тік паралельно. Прив'язати тік до самого
   lease-з'єднання не можна без того, щоб тримати його `idle in transaction`, тому гарантія
   лишається **контрактом**, а не конструкцією: докстрінги `scheduler.py` (модуль, `SchedulerTick`,
   `run()`) прямо вимагають, щоб тік був ідемпотентним і безпечним при перекритті, а дефолтний
   тік (`recover_expired_leases` + `mark_stale_instances`, обидва через `FOR UPDATE SKIP LOCKED`)
   цьому відповідає за побудовою — закрито тест-вартовим
   `test_maintenance_tick_is_safe_when_two_schedulers_overlap` (два одночасні проходи повертають
   lease рівно один раз). Зобов'язання лягає на майбутні доменні тіки — насамперед WP-03
   (`enqueue` discovery-jobs потребує власного ключа ідемпотентності, §9.3 п.3). Owner: WP-01D
   PR3 (fencing-токен у тіку, якщо з'явиться неідемпотентне доменне планування) / доменні WP.
2. **Drain-барʼєр — per-instance, не role-wide, до PR3.** `WorkerRuntime.claiming` зупиняє claim
   і за SIGTERM, і за `worker_instances.drain_requested_at` **власного** рядка — це будівельний
   блок для R-57 (§7.6: «перед зменшенням replicas controller ставить role-wide барʼєр і чекає
   підтвердження від усіх instances ролі»), а не сама гарантія: доки немає `PoolController`,
   зупинити всю роль означає виставити барʼєр кожному живому instance окремо. Owner: WP-01D PR3.

## Related

- ТЗ: §7.5 (healthcheck «process + критична dependency», SIGTERM/SIGKILL, `stop_grace_period`),
  §7.6 (lease belongs to instance, singleton advisory lease), §9.3 (ідемпотентність за ключем),
  §20 (формат ADR).
- Реалізація: `src/collector/workers/runtime.py` (`_fence_if_lease_unconfirmed`,
  `_watchdog_loop`, `_heartbeat_loop`), `src/collector/workers/config.py` (`fence_after`,
  `watchdog_interval`, `heartbeat_tick_budget`, `statement_timeout_ms`, `command_timeout`),
  `src/collector/workers/session.py` (`bounded_transaction`), `src/collector/workers/liveness.py`
  (`LivenessMarker`), `src/collector/persistence/postgres/engine.py` (`command_timeout` параметр),
  `src/collector/workers/scheduler.py` (контракт ідемпотентного тіку).
- Тести: `tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising`,
  `::test_hanging_task_does_not_extend_its_lease_and_never_reports_a_silent_complete`,
  `tests/integration/scaling/test_scheduler_singleton.py::test_maintenance_tick_is_safe_when_two_schedulers_overlap`,
  `tests/unit/test_compose_config_adversarial.py::test_application_healthchecks_name_a_critical_dependency`,
  `::test_worker_readiness_dependencies_are_declared_in_depends_on`.
- Документація: `docs/workers.md` (§3 self-fencing, §4 liveness vs readiness),
  `docs/runbooks/worker-recovery.md`.
- Звіти: `docs/plan/reports/WP-01D/code-review-pr1.md` (знахідки H-1, M-4),
  `docs/plan/reports/WP-01D/spec-review-pr1.md` (знахідка S-1, заміри «було/стало»),
  `docs/plan/reports/WP-01D/implementation-pr1.md` («Відповіді на код-рев'ю», «Відповіді на
  пострев'ю»).
