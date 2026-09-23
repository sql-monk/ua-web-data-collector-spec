# WP-01D PR1 — worker runtime, lease, heartbeat, registration, drain, singleton scheduler

Branch: `wp/01d-1-worker-runtime` (worktree `.worktrees/wp-01d`, від `main` з WP-00 PR1/PR2,
WP-01C, WP-01A PR1). Картка: `docs/plan/cards/WP-01D.md`, розділ PR1.

## Що зроблено

Placeholder-процеси `collector worker <role>` і `collector scheduler` замінено на справжній
stateless runtime. Нічого з домену тут немає — це каркас, у який WP-02/03/04/01B вставляють
`handle(task)`.

| Вимога PR1 | Реалізація | Тест |
|---|---|---|
| 1. `WorkerRuntime`: boot UUIDv7, реєстрація в `worker_instances` (`starting`), readiness → `ready`, heartbeat < lease TTL | `src/collector/workers/runtime.py` (`_boot`, `_check_ready`, `_heartbeat_loop`); конфіг `config.py` забороняє heartbeat > lease/2 | `test_worker_runtime.py::test_instance_registers_becomes_ready_and_stops_on_drain`, `::test_bootstraps_missing_pool_from_spec_defaults`, `tests/unit/workers/test_config.py::test_heartbeat_must_leave_room_for_one_missed_beat` |
| 2. Claim-loop до `desired_concurrency`, `handle(task)`, продовження lease, `complete/retry/quarantine` | `runtime.py` (`_claim_loop`, `_execute`, `_report`, `_heartbeat`); інтерфейс `handlers.py` (`TaskHandler`, `Task`, `TaskResult`, `NoopHandler`) | `test_worker_runtime.py::test_handler_failure_becomes_a_retry_with_backoff`, `tests/unit/workers/test_handlers.py` (9 тестів) |
| 3. SIGTERM → `draining` → активні дотягуються в межах `stop_grace_period` → lease повертаються → exit 0; SIGKILL — fault case | `runtime.py::_drain`, `_release_leases`; `signals.py`; скасування корутини навмисно не робить drain | `::test_drain_finishes_active_task_and_takes_no_new_jobs`, `::test_drain_timeout_returns_the_lease_to_the_queue`, `::test_killed_replica_lease_is_recovered_and_finished_by_another_instance`, `tests/unit/workers/test_signals_and_cli.py` |
| 4. Гаряча зміна `desired_concurrency` без рестарту | кожен heartbeat перечитує `worker_pools`; claim бере не більше `desired − active` | `::test_concurrency_hot_change_opens_and_closes_slots_without_restart` |
| 5. Жодного стану на локальному диску; `worker_instance_id` на boot; hostname лише metadata | увесь стан — у PostgreSQL; `hostname`/`container_id` пишуться в `worker_instances` і ні на що не впливають | `tests/unit/workers/test_roles_and_state.py::test_workers_package_writes_nothing_to_local_disk`, `::test_instance_registers_becomes_ready_and_stops_on_drain` (UUIDv7, metadata) |
| 6. `scheduler` — singleton через advisory lease; другий чекає; втрата lease зупиняє планування | `advisory.py` (`pg_try_advisory_lock` + перевірка `pg_locks`), `scheduler.py` (`SchedulerRuntime`, `run_maintenance`) | `test_scheduler_singleton.py` (4 тести) |

Додатково:

- **Барʼєр drain — примітив для role-wide drain (R-57, PR3):** це барʼєр **одного** instance; роль зупиняється, коли його виставлено кожному живому instance (це робить `PoolController` у PR3). Claim зупиняє не лише SIGTERM, а й
  `worker_instances.drain_requested_at` власного рядка — instance сам знімає себе з claim,
  тому барʼєр не залежить від того, який контейнер вирішить видалити Compose/Swarm
  (`::test_role_wide_drain_barrier_stops_claim_without_sigterm`).
- **Bootstrap pools:** на чистій БД runtime створює рядок `worker_pools` своєї ролі з defaults
  §7.6 (`workers/roles.py::DEFAULT_POOL_SPECS`) — без нього FK `worker_instances.role` не дав
  би зареєструватись. `min/max_replicas` — MVP-межі, PR3 їх уточнює разом із `PoolController`.
- **Compose:** worker- і scheduler-сервіси отримали `COLLECTOR_POSTGRES_DSN_FILE` + secret
  `postgres_dsn`, `COLLECTOR_WORKER_STOP_GRACE_SECONDS=90` (менше за `stop_grace_period: 120s`)
  і rollback-прапорець `COLLECTOR_WORKER_PLACEHOLDER`.
- **CI:** у job `integration-postgres` доданий крок
  `pytest -m integration tests/integration/scaling` (окремих job-ів не створювалось).

### Файли

Нове: `src/collector/workers/{config,handlers,advisory,runtime,scheduler,signals}.py`,
`tests/unit/workers/**` (4 модулі), `tests/integration/scaling/**` (conftest + 2 модулі),
`docs/plan/deps/WP-01D-to-WP-01A.md`.
Змінено: `src/collector/workers/{__init__,roles}.py`, `src/collector/cli.py` (лише `worker`,
`scheduler` + їхні helpers), `docker-compose.yml` (лише worker/scheduler-сервіси),
`.github/workflows/ci.yml` (один крок), `deploy/compose/README.md` (рядок таблиці секретів) і
три тестові модулі WP-00, які пінили placeholder-поведінку (див. «Ризики»).

## Команди та вивід

### `uv sync --frozen`

```text
Checked 66 packages in 6ms
```

### `uv run ruff check . && uv run ruff format --check . && uv run mypy src`

```text
All checks passed!
195 files already formatted
Success: no issues found in 68 source files
```

### `uv run pytest -m "not live"`

```text
$ uv run pytest -m "not live" -q
........................................................................ [  9%]
...  (скорочено)  ...
........................................................................ [ 99%]
=========================== short test summary info ===========================
SKIPPED [1] tests/unit/test_network_blocked.py:27: Windows: loopback потрібен asyncio
793 passed, 1 skipped, 8 warnings in 1380.93s (0:23:00)
```

Єдиний skip — успадкований від WP-00 (`tests/unit/test_network_blocked.py`: на Windows
loopback лишається дозволеним, бо без нього asyncio не створює event loop; у CI на Linux
діє повний `disable_socket`). 23 хвилини — не норма проєкту, а стан хоста: паралельно
працювали стек сусіднього worktree і сторонні контейнери користувача (один тримав ~270%
CPU), тому кожен integration-тест із `CREATE DATABASE ... TEMPLATE` чекав на I/O; той самий
набір до навантаження проходив за 191 с.

### `uv run pytest -m integration tests/integration/scaling`

```text
$ uv run pytest -m integration tests/integration/scaling -q
.............                                                            [100%]
13 passed in 28.88s
```

Розклад цих 13: 9 у `test_worker_runtime.py` (реєстрація/readiness/stop, bootstrap pool,
killed replica → recovery, drain під активним task, drain-timeout, hot-change concurrency,
чужий lease, drain barrier, handler-помилка → retry) і 4 у `test_scheduler_singleton.py`
(два scheduler + takeover, повторний acquire після втрати lease, ексклюзивність
`AdvisoryLease`, maintenance tick).

### `docker compose config --quiet`

```text
$ docker compose config --quiet
(порожній вивід, exit=0)
```

### `docker compose --profile core --profile workers up -d --wait`

**Увага до відтворення.** У момент перевірки на хості вже працював стек паралельного worktree
(`.worktrees/wp-00-3`, web scaffold) із того самого `docker-compose.yml`. Мережі в ньому мають
**фіксовані** `name:` (`collector_backend`, …), тому другий проєкт потрапляє в ту саму мережу і
DNS-ім'я `postgres` резолвиться у два контейнери: частина з'єднань ішла в чужий кластер
(`postgres error: password authentication failed for user "collector"` у клієнта — при тому, що
в логах **свого** сервера жодного FATAL немає). Перший прогін через це впав на
`container collector-wp01d-fetch-worker-2 is unhealthy`. Це не дефект PR: у CI і на чистому
хості проєкт один. Щоб отримати чисту перевірку, стек піднято з окремими іменами мереж
(локальний override поза репозиторієм) і власним ім'ям проєкту, щоб не зачепити сусідній
worktree:

```text
$ COLLECTOR_IMAGE=collector:wp01d docker compose -f docker-compose.yml -f <override з іменами мереж> \
    -p collector-wp01d --profile core --profile workers up -d --wait --wait-timeout 420
 Volume collector-wp01d_mongo-data Creating
 Network collector_wp01d_provider_egress Creating
 ...
 Container collector-wp01d-projector-worker-1 Healthy
 Container collector-wp01d-export-worker-1 Healthy
 Container collector-wp01d-api-1 Healthy
 Container collector-wp01d-parse-worker-2 Healthy
 Container collector-wp01d-parse-worker-1 Healthy
exit=0
```

### `docker compose ps -a` + health

```text
$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 16 containers healthy or exited 0
check-healthy exit=0

$ docker compose exec -T api python -m collector.api.health
postgres: ok (tcp reachable (no SQL check yet; owner WP-01A))
mongo: ok (writable primary of replica set)
minio: ok (liveness HTTP 200)

$ docker compose ps -a --format '{{.Service}}\t{{.Status}}'
api                  Up 57 seconds (healthy)
discovery-worker     Up 39 seconds (healthy)
ensure-mongo         Exited (0) About a minute ago
export-worker        Up 52 seconds (healthy)
fetch-worker         Up 55 seconds (healthy)
fetch-worker         Up 36 seconds (healthy)
maintenance-worker   Up 49 seconds (healthy)
migrate-postgres     Exited (0) About a minute ago
minio                Up About a minute (healthy)
mongo                Up About a minute (healthy)
parse-worker         Up 38 seconds (healthy)
parse-worker         Up 47 seconds (healthy)
postgres             Up About a minute (healthy)
projector-worker     Up 51 seconds (healthy)
scheduler            Up 38 seconds (healthy)
translation-worker   Up 35 seconds (healthy)
```

Контейнери справді працюють на новому runtime, а не на placeholder-і — `worker_instances`
у БД стека (defaults §7.6; `parse` = 2 × 12, бо `os.process_cpu_count()` у контейнері 12):

```text
$ psql -c "select role, status, count(*) instances, sum(slots_total) slots
           from worker_instances group by role, status order by role"
    role     | status | instances | slots
-------------+--------+-----------+-------
 discovery   | ready  |         1 |     4
 export      | ready  |         1 |     2
 fetch       | ready  |         2 |    16
 maintenance | ready  |         1 |     1
 parse       | ready  |         2 |    24
 projector   | ready  |         1 |     8
 translation | ready  |         1 |     4
```

Логи scheduler-а підтверджують advisory lease:
`{"event": "scheduler.activated", "lease": "scheduler", "backend_pid": 125, ...}`.

### SIGTERM → drain → exit 0 у контейнері (§7.5)

```text
$ time docker compose stop translation-worker
 Container collector-wp01d-translation-worker-1 Stopping
 Container collector-wp01d-translation-worker-1 Stopped
real    0m3.791s

$ docker inspect ... --format 'State={{.State.Status}} ExitCode={{.State.ExitCode}}'
State=exited ExitCode=0

$ docker logs collector-wp01d-translation-worker-1 | tail -4
{"role": "translation", "signal": "15", "event": "worker.stop_requested", ...}
{"role": "translation", "status": "draining", "event": "worker.status", ...}
{"role": "translation", "status": "stopped", "event": "worker.status", ...}
{"role": "translation", "heartbeats": 3, "lost_leases": 0, "event": "worker.stopped", ...}
```

Зупинка зайняла ~3.8 с, а не весь `stop_grace_period` (120 с): drain завершується одразу,
щойно активних tasks не лишилось.

### `docker compose down -v`

```text
$ docker compose ... -p collector-wp01d down -v --remove-orphans
 Container collector-wp01d-projector-worker-1 Stopping
 ...
 Volume collector-wp01d_mongo-config Removed
 Network collector_wp01d_source_egress Removed
 Network collector_wp01d_provider_egress Removed
 Network collector_wp01d_ingress Removed
 Network collector_wp01d_backend Removed
exit=0
```

## Виправлення після gate 2

Вердикт gate 2 — `pass` із знахідками; звіт тестувальника:
`docs/plan/reports/WP-01D/testing-pr1.md` (14 доданих тестів, коміт `9df28d2`).

| # | Знахідка | Рішення | Що саме зроблено |
|---|---|---|---|
| F0 | `high` — flaky-тест `test_scheduler_singleton.py` (5/14 падінь): очікував, що звільнений advisory lock перебере саме резервний процес | **not applicable** (виправлено тестувальником у `9df28d2`) | Продукт був правильний — ні §7.5, ні вимога 6 не обіцяють переможця гонки. Тест уже перевіряє контракт («активний рівно один», «без lease немає планування»); я нічого не міняв, лише перевірив, що набір зелений (28/28). |
| F1 | `high` — усі 8 runtime-процесів ходять у PG під superuser-роллю `collector` (§13) | **accepted** (owner **WP-01A PR2**, заведено 2026-09-23) | Не виправляється в цьому PR: жодної LOGIN-ролі в кластері немає, `migrations/**` і `sql/roles.sql` — forbidden. Підсилено `docs/plan/deps/WP-01D-to-WP-01A.md` §2 (пріоритет «блокер pilot», рантайм-доказ зі звіту тестувальника); ризик #1 нижче оновлено owner-ом і датою; у картку `WP-01D.md` додано розділ «Відомі ризики»; додано тест-вартовий `test_compose_config.py::test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire`, який падає, щойно в `roles.sql` зʼявиться перша LOGIN-роль, і вимагає повернути per-role DSN. |
| F2 | `medium` — drain-timeout на останній спробі відправляв job у `quarantined` + dead letter `max_attempts` | **fixed** | `runtime.py::_release_leases` більше не викликає `retry` для `attempt >= max_attempts`: такий job лишається `leased` і повертається в чергу через `recover_expired_leases` після експірації (той самий шлях, що й після SIGKILL, §15) — без хибного карантину і без вигаданого dead letter, `attempt` збережено. Guard-тест тестувальника переписано під нову поведінку: `test_drain_timeout_on_the_last_attempt_never_quarantines_a_job_nobody_failed`. Залишкова ціна (очікування до `lease_seconds`) названа в dependency-запиті §3 як те, що прибирає `queue.release`. |
| F3 | `medium` — немає self-fencing: при недоступності PG lease спливає, а instance продовжує виконувати task (подвійна обробка для доменних handler-ів) | **fixed** | Додано self-fencing: `_heartbeat` запамʼятовує момент останнього **підтвердженого базою** heartbeat, і якщо він старший за `fence_after` (типово ½ lease TTL, env `COLLECTOR_WORKER_FENCE_AFTER_SECONDS`), runtime піднімає явний стан `WorkerRuntime.fenced`, скасовує всі активні tasks (`worker.lease_lost phase=fence`), не звітує за ними `complete` і не бере нових, поки база не підтвердить heartbeat (`worker.unfenced`). Тест із симуляцією недоступності PG: `test_self_fencing_cancels_active_tasks_when_the_database_stops_confirming_the_lease`; unit-тести вікна — `test_fence_window_defaults_to_half_of_the_lease`, `test_fence_window_longer_than_the_lease_is_rejected`. |
| F4 | `low` — «role-wide drain barrier» у PR1 фактично per-instance | **fixed** (уточнення, не зміна поведінки) | Докстрінг `WorkerRuntime.claiming` тепер прямо каже, що це барʼєр **цього** instance і будівельний блок для R-57, а роль зупиняє `PoolController` (PR3); у картці WP-01D вимога PR3 №2 доповнена зобовʼязанням виставити барʼєр кожному живому instance ролі й дочекатися підтвердження від усіх. Формулювання в цьому звіті нижче виправлено так само. |
| F5 | `low` — `worker_instances.container_id` завжди `NULL` | **fixed** | `WorkerRuntimeConfig.from_env` бере `COLLECTOR_CONTAINER_ID`, а за його відсутності — `HOSTNAME`, який Docker/Swarm виставляють у контейнері в короткий id контейнера (те саме значення, що показує `docker ps`). Компоуз чіпати не довелось; перевірено на піднятому стеку (див. нижче) і unit-тестом `test_container_id_falls_back_to_docker_hostname`. |
| F6 | `low` — локальний прогін піднімав два testcontainers-PostgreSQL | **fixed** | `tests/integration/scaling/conftest.py` тепер (а) бере **той самий обʼєкт модуля** фікстур WP-01A, який уже імпортував pytest, і (б) робить спільними сам ресурс: `_start_container` і `TemplateState` підмінені memoized-обгортками, контейнер зупиняється на `atexit`. Перевірено семплінгом `docker ps` під час спільного прогону двох каталогів: **max 1 контейнер** (було 2), 15 passed. |

### Команди після виправлень

```text
$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
200 files already formatted
Success: no issues found in 68 source files

$ uv run pytest -m "not live" -q
........................................................................ [  8%]
...  (скорочено)  ...
=========================== short test summary info ===========================
SKIPPED [1] tests/unit/test_network_blocked.py:27: Windows: loopback потрібен asyncio
822 passed, 1 skipped, 8 warnings in 527.97s (0:08:47)

(817 після тестів gate 2 + 5 доданих цим фіксом: self-fencing, два unit-тести вікна
fencing, container_id з HOSTNAME, §13-вартовий)

$ uv run pytest -m integration tests/integration/scaling -q
............................                                             [100%]
28 passed in 32.29s

$ docker compose config --quiet
(порожній вивід, exit=0)

$ COLLECTOR_IMAGE=collector:wp01d docker compose -f docker-compose.yml -f <override мереж>     -p collector-wp01d --profile core --profile workers up -d --wait --wait-timeout 420
 Container collector-wp01d-projector-worker-1 Healthy
 Container collector-wp01d-fetch-worker-1 Healthy
 Container collector-wp01d-fetch-worker-2 Healthy
exit=0

$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 16 containers healthy or exited 0
exit=0

$ psql -c "select role, container_id, hostname from worker_instances order by role limit 3"
   role    | container_id |   hostname   | deployment
-----------+--------------+--------------+------------
 discovery | 88e40a19230b | 88e40a19230b | compose
 export    | e1a468cf8998 | e1a468cf8998 | compose
 fetch     | fe5ce129bfa4 | fe5ce129bfa4 | compose      <- F5: більше не NULL

$ docker compose ... down -v --remove-orphans
 Network collector_wp01d_source_egress Removed
 Network collector_wp01d_ingress Removed
exit=0
```

## Відповіді на код-рев'ю

Вердикт gate 3 — `changes_requested`; звіт: `docs/plan/reports/WP-01D/code-review-pr1.md`
(1 high, 4 medium, 6 low).

| # | Знахідка | Рішення | Що саме зроблено |
|---|---|---|---|
| H-1 | `high` — self-fencing сліпий до **зависання** PostgreSQL: перевірка викликалась лише з `except` heartbeat-у, а heartbeat-цикл послідовний і без таймаутів | **fixed** | Три зміни, кожна самодостатня: (1) **сторож lease окремою задачею** — `_watchdog_loop` кожні `fence_after/4` порівнює `monotonic() − last_confirmed_heartbeat` із вікном і фенсить незалежно від того, чи heartbeat-корутина повернулась; (2) **бюджет на тік** — `asyncio.wait_for(self._heartbeat(), heartbeat_tick_budget)`, `TimeoutError` теж веде до перевірки вікна; (3) **таймаути драйвера** — `command_timeout` для asyncpg (engine) і `set_config('statement_timeout', …, local)` у транзакції heartbeat, обидва похідні від `fence_after`. Тест відтворює саме зависання (`HangingSessions.__aenter__` спить, винятку немає): `test_self_fencing_fires_when_the_database_hangs_without_raising` — fence у межах lease TTL, task скасовано, `complete` не звітовано, claim зупинено. |
| M-1 | `medium` — одна невдала `_set_status("ready")` робила worker «живим, але німим» | **fixed** | `_set_status` повертає успіх; `_become_ready` повторює перехід 5 разів із експоненційним backoff і, якщо база так і не підтвердила, піднімає `WorkerRuntimeError` — процес завершується кодом 1 і Docker перезапускає репліку (видимо) замість тихого простою. Додатково heartbeat самолікується: поки `status == "starting"`, кожен підтверджений тік ще раз пробує `ready`. Тест: `test_boot_fails_loudly_when_the_ready_transition_cannot_be_written`. |
| M-2 | `medium` — контракт `TaskHandler` не забороняв блокуючий `handle()` | **fixed** | Контракт зафіксовано в докстрінгу `handlers.py` («будь-яка робота, що не віддає керування довше за десятки мілісекунд, — у `asyncio.to_thread`») і **перевіряється на boot**: `check_handler_contract` вимагає `async def handle` і непорожній `job_types`. Сторож додатково логує `worker.event_loop_stalled`, коли власний `sleep` прокидається пізно — інакше заблокований loop виглядав би як проблема бази. Тести: `test_sync_handler_is_rejected_because_it_would_block_the_event_loop`, `test_handler_without_job_types_is_rejected`. У `docs/workers.md` (етап 5) це має бути повторено до того, як WP-02/03/04 почнуть писати handler-и. |
| M-3 | `medium` — pool з'єднань від статичного default, а `desired_concurrency` гарячий і без стелі → `QueuePool` timeout маскується під втрату lease | **fixed** | З'явилась явна стеля процесу `COLLECTOR_WORKER_MAX_CONCURRENCY` (типово default ролі §7.6). CLI створює engine рівно під неї: `pool_size = max_slots + 3` (heartbeat + claim + статус), `max_overflow = 0` — більше одночасних checkout-ів не буває за побудовою, тож `pool_timeout` не виникає. Гарячий `desired_concurrency` понад стелю обрізається з `worker.concurrency_clamped` і в heartbeat звітується як фактичні `slots_total` (контролер не отримує обіцянки, якої репліка не виконає). Тест: `test_hot_change_above_the_connection_ceiling_is_clamped`. |
| M-4 | `medium` — TOCTOU singleton-lease: тік працює на іншому з'єднанні, ніж lease | **fixed (контрактом + вартовим)** | Прив'язати тік до lease-з'єднання не можна без того, щоб тримати його `idle in transaction`, тому гарантія сформульована явно: докстрінги модуля, аліаса `SchedulerTick` і `run()` вимагають **ідемпотентного** тіку і прямо забороняють не-ідемпотентні доменні дії без власного ключа ідемпотентності (§9.3 п.3). Дефолтний тік цьому відповідає за побудовою (`SKIP LOCKED`), і це доведено вартовим `test_maintenance_tick_is_safe_when_two_schedulers_overlap`: два одночасні проходи повертають lease рівно один раз. |
| L-1 | `low` — `heartbeat × 2 ≤ lease` допускає рівність; фактичний період більший за налаштований | **fixed** | Межа посилена до `heartbeat × 3 ≤ lease` (default Compose 20/60 проходить), а `_heartbeat_loop` планує тіки **від дедлайну**, а не `sleep` після роботи — дрейф на повільній базі більше не накопичується. Тест оновлено: `test_heartbeat_must_leave_room_for_a_missed_beat`. |
| L-2 | `low` — fenced-стан не видно ззовні процесу | **fixed (у межах PR1)** | `worker.fenced`/`worker.unfenced`/`worker.lease_lost` несуть лічильники `fences`/`lost_leases`, вони ж — у `worker.stopped`; у `worker_instances` фенснутий instance одразу звітує `slots_active = 0`/`active_leases = 0`. Окреме поле причини у схемі — це колонка WP-01A (`migrations/**` — forbidden), метрики — WP-12. |
| L-3 | `low` — `_abandon` губив handle: скасовані tasks ніхто не чекав | **fixed** | Скасовані handles складаються в `_cancelled` і дочікуються `_await_cancelled_tasks()` — і в `_drain`, і у `finally` самого `run()`, тому у вбудованому запуску доменний handler встигає закрити свої ресурси. |
| L-4 | `low` — `_report` ловив вужчий набір помилок, ніж решта runtime | **fixed** | Додано `PersistenceError` до `except` у `_report`: `ConflictError`/`InvalidTransitionError`/`NotFoundError` більше не вбивають task мовчки, а дають `worker.report_failed`. |
| L-5 | `low` — плановий drain-timeout витрачає спробу і пише `last_error_code='drain_timeout'` | **accepted** (owner **WP-01A PR2**, заведено 2026-09-23) | Виправити в PR1 нічим: щоб повернути lease без інкременту спроби, потрібен `queue.release(job_id, owner)` у репозиторії WP-01A (`repositories/**` — не мій файл). Вимогу уточнено в `docs/plan/deps/WP-01D-to-WP-01A.md` §3: `release` має лишати `attempt` і **не** писати `last_error_code`. Сьогоднішній наслідок обмежений: карантину немає (фікс F2), а спроба втрачається лише тоді, коли task не вклався у `stop_grace_period`. |
| L-6 | `low` — `_discard()` повертав з'єднання в pool без підтвердженого unlock | **fixed** | `release()` перевіряє boolean-результат `pg_advisory_unlock`; якщо він `false` або запит впав — лог `advisory_lease.unlock_unconfirmed` і `connection.invalidate()` замість повернення в pool, тож lock не може «поїхати» разом із pooled-з'єднанням. |
| L-7 | `low` — дрібниці (дублювання lifecycle, `_ensure_pool` без audit, claim без backoff, нередагований текст винятків, порожній `job_types`) | **частково fixed, решта accepted** (owner **WP-01D PR3**, заведено 2026-09-23) | **fixed:** backoff claim-retry (`min(poll × 2ⁿ, 30 с)`, лог `next_attempt_in`); редакція текстів винятків (`handlers.redact` — credentials у URL і значення `token/api_key/password/secret/signature`) у `result_for_exception`, `worker.task_failed`, `worker.report_failed`, `worker.claim_failed`, `worker.status_update_failed`; порожній `job_types` відхиляється на boot. **accepted:** спільна база lifecycle для `WorkerRuntime`/`SchedulerRuntime` і audit-рядок для bootstrap-pool — обидва природно лягають на PR3 разом із `PoolController` (там же вирішується, чи worker взагалі має право створювати pool). |

Зміна поза owned files: `create_engine` (`src/collector/persistence/postgres/engine.py`, WP-01A)
отримав **необов'язковий** параметр `command_timeout` — без нього таймаути H-1 не мають нижньої
межі на рівні драйвера. Зміна адитивна (default `None` — поведінка міграцій не змінилась) і
записана в `docs/plan/deps/WP-01D-to-WP-01A.md` §4 для підтвердження власником.

Через посилення межі `heartbeat × 3 ≤ lease` (L-1) оновлено один тест gate 2
(`test_worker_runtime_adversarial.py`: `heartbeat_seconds` 60 → 40 при `lease_seconds=120`) —
намір тесту («heartbeat не встигне спрацювати») збережено.

### Команди після виправлень gate 3

```text
$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
201 files already formatted
Success: no issues found in 68 source files

$ uv run pytest -m "not live" -q
........................................................................ [  8%]
...  (скорочено)  ...
=========================== short test summary info ===========================
SKIPPED [1] tests/unit/test_network_blocked.py:27: Windows: loopback потрібен asyncio
836 passed, 1 skipped, 9 warnings in 146.08s (0:02:26)

(822 до gate 3 + 14 доданих цим фіксом: зависання бази, стеля concurrency, boot без ready,
контракт handler-а, редакція текстів, бюджети таймаутів, перекриття тіків scheduler-а)

$ uv run pytest -m integration tests/integration/scaling -q
................................                                         [100%]
32 passed in 47.60s

$ docker compose config --quiet
(порожній вивід, exit=0)

$ COLLECTOR_IMAGE=collector:wp01d docker compose -f docker-compose.yml -f <override мереж> \
    -p collector-wp01d --profile core --profile workers up -d --wait --wait-timeout 420
 Container collector-wp01d-parse-worker-2 Healthy
 Container collector-wp01d-parse-worker-1 Healthy
 Container collector-wp01d-discovery-worker-1 Healthy
exit=0

$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 16 containers healthy or exited 0
exit=0

$ psql -c "select role, status, slots_total, container_id is not null from worker_instances"
    role     | status | slots_total | has_container_id
-------------+--------+-------------+------------------
 discovery   | ready  |           4 | t
 fetch       | ready  |           8 | t
 fetch       | ready  |           8 | t
 parse       | ready  |          12 | t   <- 2 × CPU контейнера, у межах стелі процесу
 ...         |        |             |
(9 rows)

$ docker compose ... down -v --remove-orphans
 Network collector_wp01d_ingress Removed
 Network collector_wp01d_source_egress Removed
exit=0
```

## Відповіді на пострев'ю (spec review)

Вердикт — `changes_requested` (0 critical/high; 1 missing, 6 partial); звіт:
`docs/plan/reports/WP-01D/spec-review-pr1.md`. Гілку спершу перебазовано на `origin/main`
(там уже WP-00 PR3), конфлікт був один — `docs/acceptance/traceability.md` (обидві сторони
дописували рядки в кінець таблиці; збережено обидва набори). Усі докази нижче зняті **після**
rebase.

| # | Знахідка | Рішення | Що саме зроблено |
|---|---|---|---|
| S-1 | **missing** — вимога 7 картки PR1: дешевий liveness-probe для Docker healthcheck | **fixed** | Довгоживучий runtime сам оновлює mtime маркера в tmpfs (`collector.workers.liveness`), healthcheck читає лише mtime через `stat`+`date` — без старту інтерпретатора і без БД. Маркер оновлює **сторож lease**, тому заблокований event loop робить контейнер unhealthy. Семантику §7.5 розведено разом із тестом (нижче), бюджет worker/scheduler повернуто до секундних значень. |
| S-2 | тест-вартовий на LOGIN-ролі слабший, ніж заявлено | **fixed** | Вартовий переписано: case-insensitive, `CREATE ROLE … LOGIN`, `ALTER … WITH LOGIN` і `CREATE USER` (LOGIN без ключового слова), обидва SQL-файли ролей (пакетний `roles.sql` + `deploy/compose/postgres/init/01-roles.sql`), `--` і `/* */` коментарі вирізаються. Сам вартовий накритий зондами: `test_login_tripwire_detects_every_way_to_create_a_login_role` (5 форм) і `test_login_tripwire_is_quiet_on_nologin_and_comments` (5 негативних). Формулювання в картці уточнено під фактичну гарантію. |
| S-3 | дві внутрішні суперечності у звіті | **fixed** | (а) Ризик 4 переписано: два контейнери — це стан **до** F6; тепер там залишковий ризик крихкості обгортки (перейменування `_start_container`/`TemplateState` у WP-01A ламає набір гучно). Те саме застаріле формулювання прибрано з докстрінга `tests/integration/scaling/conftest.py`. (б) У «Dependency-запитах» LOGIN-ролі названо високим пріоритетом і блокером pilot — як у deps-файлі §2 і картці. |
| S-4 | `statement_timeout` лише в heartbeat, `command_timeout` лише на CLI-шляху | **fixed** | З'явився спільний `collector.workers.session.bounded_transaction`: **кожна** транзакція runtime (claim, report, статус, bootstrap pool, release lease, heartbeat) і тік scheduler-а ставлять `statement_timeout`. Докстрінг `command_timeout` більше не обіцяє зайвого: названо обидва рівні (клієнтський asyncpg на CLI-шляху + серверний у транзакції) і явно сказано, що у вбудованому запуску діють `statement_timeout` і сторож. |
| S-5 | залишковий TOCTOU M-4 не в «Відомих ризиках» картки | **fixed** | Третій рядок у таблиці картки: статус `mitigated (контракт + тест-вартовий)`, owner `WP-01D PR3 / доменні WP`, дата 2026-09-23, з явним зобов'язанням для WP-03 (`enqueue` discovery-jobs потребує власного ключа ідемпотентності §9.3 п.3). |
| DoD §18 п. 6 | немає `docs/workers.md` | **fixed** | Створено `docs/workers.md`: ролі й defaults §7.6, життєвий цикл, lease/self-fencing/таймаути, liveness vs readiness, контракт `TaskHandler` (тільки `async def`, CPU-bound — у `asyncio.to_thread`, скасовуваність, заборона локального стану), покроково «як додати handler», scheduler і його контракт ідемпотентності, повна таблиця env і операційні рецепти. |

### S-1: що саме змінено в семантиці healthcheck

§7.5 вимагає «process + критична dependency». Вимога не змінилась — змінився спосіб її
виконання для довгоживучих процесів, і картка прямо називає цей тест місцем, де зміну треба
зафіксувати:

- **liveness** (healthcheck worker-ів і scheduler-а) — свіжість маркера процесу;
- **readiness** — `depends_on` (`postgres: service_healthy`, one-shots
  `service_completed_successfully`) плюс `worker_instances.status`/`last_heartbeat_at`.

`tests/unit/test_compose_config_adversarial.py::test_application_healthchecks_name_a_critical_dependency`
переписано під два класи проб (докстрінг пояснює чому), а вимогу «projector/export бачать
Mongo; fetch/parse/export — MinIO» перенесено туди, де вона тепер живе:
`::test_worker_readiness_dependencies_are_declared_in_depends_on` перевіряє `depends_on`.
Навмисний наслідок, названий у докстрінгу: падіння PostgreSQL більше не робить worker-контейнери
unhealthy — рестарт цього не лікує, а self-fencing уже зупинив claim.

### S-1: заміри «було/стало»

Вартість **однієї проби** всередині вже запущеного контейнера (`docker exec`, медіана з 3;
у кожне число входить ~0.4 с накладних витрат самого `docker exec`, однакових для обох проб):

| CPU-ліміт | було: старт інтерпретатора (`python -c "import collector.api.health"`) | стало: `test -f` + `stat -c %Y` |
|---|---:|---:|
| 1.00 | 1.77 с | 0.58 с |
| 0.50 | 3.04 с | 0.43 с |
| 0.25 | 6.07 с | 0.44 с |

Стара проба додатково відкривала TCP-з'єднання до PostgreSQL (звідси 2.9–6.0 с у вимірі картки);
нова не робить жодного мережевого виклику, тому її вартість не залежить від CPU-ліміту.

Бюджет healthcheck (`docker compose config`, лише worker-и і `scheduler`; `api`/`gui`
лишаються на `x-healthcheck-budget`):

| Параметр | було (`x-healthcheck-budget`) | стало (`x-liveness-budget`) |
|---|---:|---:|
| `timeout` | 15 с | 3 с |
| `start_period` | 90 с | 20 с |
| `interval` | 30 с | 10 с |
| `start_interval` | 5 с | 2 с |
| `retries` | 5 | 3 |

Поведінка в Docker після зміни (ізольований проєкт, образ із цією гілкою):
`docker compose --profile core --profile workers up -d --wait` → **22 с** до «все healthy»
(16/16), маркер у контейнері оновлюється сторожем кожні ~7 с (спостережений вік маркера
коливається 0–7 с при порозі свіжості 30 с). Семантика проби перевірена в самому контейнері:
свіжий маркер → `exit 0`, маркер віком 60 с → `exit 1`, відсутній маркер → `exit 1`.

### Команди після пострев'ю (на перебазованій гілці)

```text
$ git rebase origin/main
Successfully rebased and updated refs/heads/wp/01d-1-worker-runtime.

$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
216 files already formatted
Success: no issues found in 70 source files

$ uv run pytest -m "not live" -q
873 passed, 23 skipped, 8 warnings in 149.21s (0:02:29)

$ uv run pytest -m integration tests/integration/scaling -q
.................................                                        [100%]
33 passed in 60.42s (0:01:00)

$ docker compose config --quiet
(порожній вивід, exit=0)

$ COLLECTOR_IMAGE=collector:wp01d docker compose -f docker-compose.yml -f <override мереж> \
    -p collector-wp01d --profile core --profile workers up -d --wait --wait-timeout 420
up exit=0, seconds=22

$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 16 containers healthy or exited 0

$ docker exec <worker> sh -c '<проба healthcheck>'   # семантика порогу
fresh marker -> exit=0
stale (60s) marker -> exit=1
missing marker -> exit=1

$ docker compose ... down -v --remove-orphans
down exit=0
```

23 skip-и — це успадковані від WP-00 PR3 e2e/web-тести, які вмикаються прапорцями
(`COLLECTOR_E2E_REQUIRED=1` у job `docker`), плюс Windows-специфічний
`test_network_blocked.py`; до WP-01D вони не стосуються.

## Що не перевірено

- **Liveness-маркер під реально заблокованим процесом у Docker.** Семантику порогу перевірено
  в контейнері (свіжий маркер → exit 0, вік 60 с → exit 1, відсутній → exit 1), а
  «заблокований event loop → unhealthy» — логікою (маркер оновлює саме сторож lease) і
  integration-тестом оновлення mtime. Заморозити процес усередині контейнера не вдалося:
  в slim-образі немає `ps`/`procps`, щоб знайти PID для `kill -STOP`. Крок лишається для
  fault-тестів §16.3 (там же, де SIGTERM під активним task).
- **`docker compose up -d --no-recreate --scale fetch-worker=4`** — scale-специфічна команда
  PR3 (`PoolController`, drain barrier, Compose adapter); у PR1 не виконувалась.
- **SIGTERM у контейнері під активним task.** Сам SIGTERM у Docker перевірено вручну
  (`docker compose stop translation-worker` → `draining` → `stopped` → exit 0, див. вище), але
  черга в тому стеку порожня, тож «дотягування» task-и під SIGTERM доведене лише
  integration-тестом у процесі (`test_drain_finishes_active_task_and_takes_no_new_jobs`), а не
  в контейнері. Контейнерний fault-тест — §16.3, WP-01D PR3/WP-14.
- **Чистий `docker compose up --wait` без ізоляції мереж.** Перевірку зроблено з окремими
  іменами мереж і проєкту, бо на хості паралельно працював стек іншого worktree (див. блок
  вище). Прогін «як у CI» (один проєкт, дефолтні імена мереж) відтворюється на чистому хості і
  покритий job-ом `docker` у CI.
- **Поведінка при падінні PostgreSQL посеред claim/heartbeat.** Код логує і продовжує спроби
  (`worker.heartbeat_failed`, `worker.claim_failed`), але тест із примусовим падінням сервера
  не писався — це fault-сценарій §16.3.
- **Доменні handler-и** (`fetch`, `discovery`, `translation`, `projector`) — out of scope PR1;
  усі ролі працюють на `NoopHandler`.
- **Метрики/експорт** (`worker_*`) — WP-12; у PR1 лише структуровані логи.

## Ризики

1. **Runtime ходить у БД з DSN міграційної ролі** (знахідка F1 gate 2; owner **WP-01A PR2**,
   заведено 2026-09-23; блокер pilot/production і live-збору, не блокер merge).
   Рантайм-доказ — `docs/plan/reports/WP-01D/testing-pr1.md` §6 F1: усі вісім процесів
   під'єднані як `rolsuper = t`. Тест-вартовий:
   `test_compose_config.py::test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire`
   (падає, щойно в `roles.sql` зʼявиться перша LOGIN-роль). §13 вимагає per-component
   LOGIN-ролі, яких ще немає (`roles.sql` створює group-ролі `NOLOGIN`). Тимчасово worker/scheduler монтують той
   самий secret `postgres_dsn`, що й `migrate-postgres` — тобто мають більше прав, ніж їм
   потрібно. Запит: `docs/plan/deps/WP-01D-to-WP-01A.md` §2. Компенсація: жодних інших
   credentials (Mongo/MinIO) worker-и не отримують; тест
   `test_compose_config_adversarial.py::test_api_has_no_secrets_and_runtime_has_only_the_dsn`
   пінить це і впаде, якщо хтось додасть зайвий secret.
2. **Оновлені тести WP-00.** Три модулі WP-00 пінили placeholder-поведінку
   (`test_cli_compose_commands.py`) і «workers без секретів»
   (`test_compose_config.py`, `test_compose_config_adversarial.py`). Вони оновлені за
   прецедентом WP-01A (commit `9ed5ed8`), а не видалені: placeholder лишився як rollback-шлях і
   далі тестується під `COLLECTOR_WORKER_PLACEHOLDER=1`.
3. **Drain-timeout на останній спробі чекає експірації lease** (після фіксу F2). Хибного
   карантину більше немає: `retry` викликається лише при `attempt < max_attempts`, а job на
   останній спробі повертається в чергу через `recover_expired_leases` — тобто до
   `lease_seconds` (типово 60 с) затримки при плановому scale-down. Прибирає це
   `queue.release(job_id, owner)` — `docs/plan/deps/WP-01D-to-WP-01A.md` §3.
4. **Фікстури PostgreSQL для `tests/integration/scaling` реекспортуються з каталогу WP-01A.**
   `pytest_plugins` у не-кореневому conftest заборонений з pytest 7, тому модуль фікстур
   береться за шляхом (а якщо pytest уже його імпортував — використовується той самий обʼєкт),
   і контейнер із template-БД робиться спільним для обох каталогів memoized-обгортками.
   Перевірено семплінгом `docker ps`: **1 контейнер** на процес (знахідка F6 gate 2 закрита).
   Залишковий ризик — крихкість самої обгортки: якщо WP-01A перейменує `_start_container`
   або `TemplateState`, тест-набір впаде на старті (гучно, не тихо), і обгортку треба буде
   оновити разом із фікстурами.
5. **`job_type` = ім'я ролі, поки немає доменних handler-ів.** `NoopHandler.job_types`
   повертає `(role,)`; справжні типи оголосять доменні handler-и у своєму `job_types`. Якщо
   домен обере інші назви і забуде оновити handler, worker просто нічого не claim-итиме —
   видно в логах (`worker.registered job_types=[...]`) і в `worker_instances.active_leases=0`.

## Як вимкнути або відкотити

- **Повернути placeholder-процес WP-00 без перебудови image:**
  `COLLECTOR_WORKER_PLACEHOLDER=1 docker compose up -d` (змінна проброшена у worker- і
  scheduler-сервіси). Процес лишається живим, друкує `not implemented: owned by WP-01D`, не
  claim-ить нічого; healthcheck і `stop_grace_period` не змінюються.
- **Зупинити claim окремої репліки без рестарту:** `mark_draining(instance_id)` (SQL або
  репозиторій `pools`) — instance перестає брати jobs на наступному heartbeat; `mark_ready`
  повертає його в роботу.
- **Зупинити планування:** зупинити сервіс `scheduler`; advisory lease звільняється разом із
  сесією, жодного стану чистити не треба.
- **Повний відкат PR:** `git revert` коміту(ів) гілки — крім `workers/**`, торкнуто лише
  `cli.py` (дві команди), `docker-compose.yml` (worker/scheduler env+secrets), один крок CI і
  чотири тестові модулі.

## Dependency-запити

`docs/plan/deps/WP-01D-to-WP-01A.md`:

1. нових таблиць/колонок **не потрібно** (singleton — `pg_advisory_lock`);
2. LOGIN-ролі per component і per-role DSN (§13) — **високий пріоритет, блокер pilot**
   (owner WP-01A PR2, заведено 2026-09-23; та сама класифікація, що в deps-файлі §2 і картці);
3. `queue.release(job_id, owner)` без інкременту спроб і dead letter — низький пріоритет.
