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

- **Role-wide drain barrier (R-57, підготовка PR3):** claim зупиняє не лише SIGTERM, а й
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

## Що не перевірено

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

1. **Runtime ходить у БД з DSN міграційної ролі.** §13 вимагає per-component LOGIN-ролі, яких
   ще немає (`roles.sql` створює group-ролі `NOLOGIN`). Тимчасово worker/scheduler монтують той
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
3. **Drain-timeout використовує `retry`, а не `release`.** Якщо job був на останній спробі,
   повернення lease після timeout відправляє його в карантин із dead letter `max_attempts`,
   хоча його ніхто не «провалив». Запит на `release(job_id, owner)` —
   `docs/plan/deps/WP-01D-to-WP-01A.md` §3.
4. **Два testcontainers-контейнери у повному локальному прогоні.** `tests/integration/scaling`
   реекспортує фікстури `tests/integration/postgres` через завантаження модуля за шляхом
   (`pytest_plugins` у не-кореневому conftest заборонений з pytest 7), тому session-scope
   fixturedef-и різні. У CI сервер зовнішній (`COLLECTOR_TEST_POSTGRES_ADMIN_DSN`), тож
   дублювання немає; локально це лише повільніше.
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
2. LOGIN-ролі per component і per-role DSN (§13) — середній пріоритет;
3. `queue.release(job_id, owner)` без інкременту спроб і dead letter — низький пріоритет.
