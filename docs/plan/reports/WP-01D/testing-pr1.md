# WP-01D PR1 — незалежне тестування (wp-tester)

Гілка: `wp/01d-1-worker-runtime`, HEAD `5ad8d4b`, worktree `.worktrees/wp-01d`.
Картка: `docs/plan/cards/WP-01D.md` (розділ PR1). ТЗ: §7.5, §7.6, §13, §15, §16.1 п.15/3/1,
FR-031, FR-032; REVIEW R-52, R-57.

**Вердикт: `pass`** (дві знахідки `high`: F0 — flaky-тест, виправлено мною в `tests/**`;
F1 — порушення §13, яке PR1 не міг закрити власними силами).

Порядок роботи: спершу власний прогін команд перевірки, потім написання adversarial-тестів,
потім mutation-перевірка, і лише після цього — звірка з `implementation-pr1.md`.

---

## 1. Команди та дослівний вивід

### `uv sync --frozen`

```text
Checked 66 packages in 7ms
```

### `uv run ruff check . && uv run ruff format --check . && uv run mypy src`

```text
All checks passed!
200 files already formatted
Success: no issues found in 68 source files
```

### `uv run pytest -m "not live"` (baseline, до моїх тестів)

```text
=========================== short test summary info ===========================
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
793 passed, 1 skipped, 8 warnings in 800.73s (0:13:20)
[exited with code 0]
```

### `uv run pytest -m "not live"` (після додавання моїх тестів)

```text
=========================== short test summary info ===========================
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
817 passed, 1 skipped, 8 warnings in 215.61s (0:03:35)
[exited with code 0]
```

Skip — успадкований від WP-00 (Windows-специфічний), не стосується WP-01D.

### `uv run pytest -m integration tests/integration/scaling`

Baseline гілки (13 тестів реалізатора):

```text
13 passed
```

Після моїх тестів (13 + 14 доданих):

```text
27 passed
```

### `docker compose config --quiet`

```text
COMPOSE_CONFIG_OK        # порожній вивід, exit=0
```

### Docker-стек

**Ізоляція.** `docker ps` на початку прогону показав лише сторонні контейнери користувача
(`puluj-g-*`), стека `collector` не було. Оскільки `docker-compose.yml` задає мережам
**фіксовані** `name: collector_*` (два проєкти з того самого файлу потрапили б в одну мережу і
`postgres` резолвився б у два контейнери), стек піднято під власним ім'ям проєкту `-p wp01d`
**і** з override, що перейменовує мережі у `wp01d_*`. Override лежить поза репозиторієм
(scratchpad сесії), жодного файлу в `.worktrees/wp-01d` і в `C:\repos\webscraper` не змінено.
Образ зібрано з цього worktree: `docker build -t collector:wp01d-test --build-arg
COLLECTOR_GIT_SHA=5ad8d4b .`.

```text
$ COLLECTOR_IMAGE=collector:wp01d-test docker compose -f docker-compose.yml -f <scratchpad>/wp01d-isolation.override.yml \
    -p wp01d --profile core --profile workers up -d --wait --wait-timeout 420
 Container wp01d-mongo-1 Healthy
 Container wp01d-ensure-mongo-1 Exited
 Container wp01d-migrate-postgres-1 Exited
 Container wp01d-minio-1 Healthy
 Container wp01d-postgres-1 Healthy
 Container wp01d-discovery-worker-1 Healthy
 Container wp01d-translation-worker-1 Healthy
 Container wp01d-api-1 Healthy
 Container wp01d-scheduler-1 Healthy
 Container wp01d-projector-worker-1 Healthy
 Container wp01d-export-worker-1 Healthy
 Container wp01d-maintenance-worker-1 Healthy
 Container wp01d-parse-worker-2 Healthy
 Container wp01d-parse-worker-1 Healthy
 Container wp01d-fetch-worker-1 Healthy
 Container wp01d-fetch-worker-2 Healthy
[exited with code 0]
```

Реєстрація instances (defaults §7.6; `parse` = 2 × 12 за `os.process_cpu_count()` у контейнері):

```text
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

`docker compose up -d --no-recreate --scale fetch-worker=4`:

```text
 Container wp01d-fetch-worker-3 Started
 Container wp01d-fetch-worker-4 Started

 role  | status | instances | slots
-------+--------+-----------+-------
 fetch | ready  |         4 |    32
```

SIGTERM у контейнері (§7.5):

```text
$ time docker compose -p wp01d stop translation-worker
 Container wp01d-translation-worker-1 Stopped
real    0m1.754s

$ docker inspect wp01d-translation-worker-1 --format 'State={{.State.Status}} ExitCode={{.State.ExitCode}}'
State=exited ExitCode=0

... "signal": "15", "event": "worker.stop_requested"
... "status": "draining", "event": "worker.status"
... "status": "stopped",  "event": "worker.status"
... "heartbeats": 5, "lost_leases": 0, "event": "worker.stopped"
```

SIGKILL (fault case §7.5) + stale detection + повторний boot того самого контейнера:

```text
$ docker kill --signal=SIGKILL wp01d-fetch-worker-4   →  ExitCode=137
# через ~75 c (scheduler-ів maintenance tick, stale_after=60 c):
 fetch | ready | 3
 fetch | stale | 1

$ docker start wp01d-fetch-worker-4
             instance_id              |   hostname   | status |          started_at
--------------------------------------+--------------+--------+-------------------------------
 01a0cd57-1a4e-7703-a103-f107637d1a9d | 36a3a1370366 | stale  | 2026-09-23 08:17:12.839375+00
 01a0cd59-ef18-774e-b107-b2f53ef75927 | 36a3a1370366 | ready  | 2026-09-23 08:20:18.391224+00
```

Той самий контейнер (`hostname 36a3a1370366`) після рестарту отримав **новий** `instance_id`,
старий рядок лишився `stale` — §7.5 («hostname лише metadata») і §15 («жодного локального
стану») підтверджено на реальному стеку, а не лише в тесті.

Singleton scheduler у контейнерах (`--scale scheduler=2`):

```text
$ docker ps --filter name=wp01d-scheduler
wp01d-scheduler-2 Up 39 seconds (healthy)
wp01d-scheduler-1 Up 2 minutes (healthy)

$ docker logs wp01d-scheduler-1 | grep -c scheduler.activated
1
$ docker logs wp01d-scheduler-2 | tail -5
(порожньо — резервний не активувався і нічого не планує)
```

Ізоляція контейнерів runtime (§13, §7.5):

```text
/wp01d-fetch-worker-1 Mounts=...\deploy\compose\secrets\postgres_dsn ReadOnly=true User=10001:10001
/wp01d-scheduler-1    Mounts=...\deploy\compose\secrets\postgres_dsn ReadOnly=true User=10001:10001
```

Жодного Docker socket, read-only rootfs, non-root — успадковані інваріанти WP-00 лишились
зеленими.

`docker compose down -v`:

```text
 Volume wp01d_postgres-data Removed
 Volume wp01d_mongo-data Removed
 Volume wp01d_mongo-config Removed
 Volume wp01d_minio-data Removed
 Network wp01d_backend Removed
 ...
EXIT=0
# `docker ps -a | grep wp01d` — порожньо
```

---

## 2. Acceptance-пункт → тест → результат

| Вимога PR1 (картка) | Тест | Результат |
|---|---|---|
| 1. Boot UUIDv7, реєстрація `starting`, readiness → `ready`, heartbeat < lease TTL | `test_worker_runtime.py::test_instance_registers_becomes_ready_and_stops_on_drain`; `tests/unit/workers/test_config.py::test_heartbeat_must_leave_room_for_one_missed_beat`; **додано** `test_desired_state_bounds.py::test_heartbeat_faster_than_half_the_lease_survives_one_missed_beat`; **додано** `test_worker_runtime_adversarial.py::test_second_boot_of_the_same_container_creates_a_new_instance` | pass |
| 2. Claim-loop до `desired_concurrency`, `handle(task)`, продовження lease, `complete/retry/quarantine` | `::test_handler_failure_becomes_a_retry_with_backoff`; **додано** `::test_two_runtimes_claiming_one_job_exactly_one_wins`, `::test_permanent_handler_error_quarantines_with_a_dead_letter`, `::test_recover_expired_leases_spares_a_live_heartbeating_task` | pass |
| 3. SIGTERM → `draining` → активні дотягуються в межах grace → lease повернуто → exit 0; SIGKILL — fault case | `::test_drain_finishes_active_task_and_takes_no_new_jobs`, `::test_drain_timeout_returns_the_lease_to_the_queue`, `::test_killed_replica_lease_is_recovered_and_finished_by_another_instance`; **додано** `::test_repeated_stop_requests_under_an_active_task_are_idempotent`, `::test_instance_marked_stopped_claims_nothing_and_exits`; Docker: `stop translation-worker` → exit 0 за 1.8 c, `kill -9` → exit 137 → `stale` | pass |
| 4. Гаряча зміна `desired_concurrency` без рестарту (нові слоти одразу, зайві після завершення) | `::test_concurrency_hot_change_opens_and_closes_slots_without_restart` (1→3→1, перевіряє і `slots_total` у БД, і те, що активні tasks не скасовуються) | pass |
| 5. Жодного локального стану; `worker_instance_id` на boot; hostname лише metadata | `tests/unit/workers/test_roles_and_state.py::test_workers_package_writes_nothing_to_local_disk`; **додано** `::test_second_boot_of_the_same_container_creates_a_new_instance`; Docker-доказ вище | pass |
| 6. `scheduler` — singleton через advisory lease; другий чекає; втрата lease зупиняє планування | `test_scheduler_singleton.py` (4 тести); **додано** `test_scheduler_singleton_adversarial.py::test_standby_scheduler_does_no_maintenance_while_the_active_one_does`, `::test_graceful_stop_hands_the_lease_over_without_waiting_for_a_ttl`, `::test_active_scheduler_ticks_only_while_the_server_confirms_the_lease`, `::test_named_leases_are_isolated_from_each_other`; Docker: 2 контейнери scheduler → рівно один `scheduler.activated` | pass |
| Тести картки: «heartbeat не продовжує чужий lease» | `::test_heartbeat_does_not_extend_a_foreign_lease`; **додано** `::test_hanging_task_does_not_extend_its_lease_and_never_reports_a_silent_complete` | pass |
| R-57 / FR-032: drain barrier не покладається на вибір контейнера | `::test_role_wide_drain_barrier_stops_claim_without_sigterm`; **додано** `::test_drain_barrier_must_be_set_on_every_instance_of_the_role` | pass (з застереженням F4) |
| R-52: незалежні pools, drain, lease recovery | `::test_killed_replica_lease_is_recovered_and_finished_by_another_instance`, `test_maintenance_tick_recovers_expired_leases_and_marks_stale_instances`; **додано** `::test_stale_marking_spares_an_instance_with_a_fresh_heartbeat` | pass |
| §13 (розділення DB-credentials) | немає зеленого тесту — **знахідка F1** | fail (див. F1) |

---

## 3. Рівень §16.1 → тести

| Рівень §16.1 | Де | Оцінка |
|---|---|---|
| 1 Unit | `tests/unit/workers/` — 5 модулів (config, handlers, roles/advisory/local-state, signals/CLI, **додано** desired-state bounds), 60 тестів | покрито |
| 3 Integration (job lease/recovery) | `tests/integration/scaling/` проти реального PostgreSQL 18 (testcontainers/зовнішній сервер), 26 тестів | покрито |
| 15 Scaling | `concurrency hot-change`, `drain during active task`, `killed replica lease recovery` — покрито тестами; `replicas 1→4→1→0→2`, `concurrent global rate-limit`, `expired permit recovery`, `stale command/revision`, `controller allowlist` — **не в PR1** (PR2/PR3 за карткою); `--scale fetch-worker=4` перевірено вручну на стеку | покрито в межах PR1 |
| 14 Docker | успадковано від WP-00 (`tests/unit/test_compose_config*.py`, CI job `docker`); worker/scheduler-частину перевірено реальним стеком (див. §1) | покрито |
| 8 Security | §13-частина **не** покрита — F1 | розрив |

---

## 4. Додані тести

Edit лише в `tests/**`. Три нові файли (14 тестів) і одне виправлення наявного тесту.

`tests/integration/scaling/test_worker_runtime_adversarial.py` (10):

1. `test_two_runtimes_claiming_one_job_exactly_one_wins` — два runtime, одна job: `started`
   містить її рівно один раз, `attempt == 1` (другий claim підняв би лічильник);
2. `test_hanging_task_does_not_extend_its_lease_and_never_reports_a_silent_complete` — з
   heartbeat-ом, який не встигає спрацювати, lease не рухається сам; після
   `recover_expired_leases` runtime **не** переводить job у `succeeded`, а фіксує `lost_leases`;
3. `test_recover_expired_leases_spares_a_live_heartbeating_task` — maintenance-прохід під
   активним heartbeat повертає `[]`, lease лишається за живим instance і рухається вперед;
4. `test_drain_barrier_must_be_set_on_every_instance_of_the_role` — R-57: барʼєр на одному
   instance не зупиняє решту ролі; з барʼєром на всіх — жодна нова job не береться;
5. `test_repeated_stop_requests_under_an_active_task_are_idempotent` — другий і третій SIGTERM
   не скасовують активний task, результат drain не змінюється;
6. `test_instance_marked_stopped_claims_nothing_and_exits` — `worker_instances.status=stopped`
   відхиляє heartbeat, процес завершується, черга не зачеплена;
7. `test_second_boot_of_the_same_container_creates_a_new_instance` — той самий
   `hostname`/`container_id`, новий UUIDv7, старий рядок лишається `stopped`;
8. `test_stale_marking_spares_an_instance_with_a_fresh_heartbeat` — `stale` за віком heartbeat,
   живий instance лишається `ready`;
9. `test_permanent_handler_error_quarantines_with_a_dead_letter` — `PermanentTaskError` →
   `quarantined` + рівно один dead letter (шлях runtime → БД, а не лише мапінг у `handlers`);
10. `test_drain_timeout_on_the_last_attempt_quarantines_a_job_nobody_failed` — **guard знахідки
    F2**: закріплює фактичну (небажану) поведінку, щоб її виправлення було помітним.

`tests/integration/scaling/test_scheduler_singleton_adversarial.py` (4):

1. `test_standby_scheduler_does_no_maintenance_while_the_active_one_does` — резервний процес не
    робить жодного `run_maintenance`; прострочений lease повернуто рівно один раз;
2. `test_named_leases_are_isolated_from_each_other` — `scheduler` і `controller` не блокують
    один одного (важливо для PR3);
3. `test_graceful_stop_hands_the_lease_over_without_waiting_for_a_ttl` — після SIGTERM
    активного lease вільний одразу, зупинений процес більше не тікає;
4. `test_active_scheduler_ticks_only_while_the_server_confirms_the_lease` —
    `pg_terminate_backend` → `is_active=False`, планування зупинено до нового `try_acquire`,
    новий backend pid.

Змінений (не доданий) файл: `tests/integration/scaling/test_scheduler_singleton.py` —
виправлення flaky-тесту, знахідка F0 (деталі там же). Продуктивний код не змінювався.

`tests/unit/workers/test_desired_state_bounds.py` (6): `desired_concurrency = 0` відхиляється
(пауза — це `desired_replicas = 0` або drain barrier, не нульова concurrency); межі
`min/desired/max`; `mode`; browser `0 × 1`; `stop_grace_seconds` < Compose `stop_grace_period`;
heartbeat ≤ ½ lease.

Детермінізм: жодного `sleep` у тестах — стан очікується предикатами `wait_for`, «прострочення»
моделюється явним `now` у репозиторії, «минув час» — лічильником heartbeat-ів (хелпер `settle`).

---

## 5. Mutation-перевірка

Чотири мутації продуктивного коду, після кожної — `git checkout --` (робоче дерево наприкінці
містить лише три нові тестові файли, `git status` це підтверджує).

| # | Мутація | Файл | Червоні тести |
|---|---|---|---|
| M1 | прибрано перевірку owner у `heartbeat` (`.where(_owned(job_id, owner))` → `.where(CrawlJob.job_id == job_id, CrawlJob.status == "leased")`) | `repositories/queue.py:207` | `test_heartbeat_does_not_extend_a_foreign_lease` → **1 failed, 2 passed** |
| M2 | прибрано drain barrier з `claiming` (per-instance барʼєр більше не зупиняє claim) | `workers/runtime.py::WorkerRuntime.claiming` | `test_role_wide_drain_barrier_stops_claim_without_sigterm`, `test_drain_barrier_must_be_set_on_every_instance_of_the_role`, `test_instance_marked_stopped_claims_nothing_and_exits` → **3 failed** |
| M3 | результат advisory lock ігнорується (`pg_try_advisory_lock(...) IS NOT NULL` — lock береться, але `try_acquire` завжди `True`) | `workers/advisory.py::try_acquire` | `test_two_schedulers_keep_exactly_one_active_*`, `test_advisory_lease_is_exclusive_and_survives_only_its_session`, `test_standby_scheduler_does_no_maintenance_while_the_active_one_does` → **3 failed, 5 passed** (перевірено двічі: до і після виправлення F0 — фікс не послабив тест) |
| M4 | drain-timeout більше не повертає lease (`await self._release_leases()` → `pass`) | `workers/runtime.py::_drain` | `test_drain_timeout_returns_the_lease_to_the_queue` → **1 failed** |

---

## 6. Знахідки

### F0 — `high` — flaky-тест `test_scheduler_singleton.py` (5 падінь із 14 прогонів)

`tests/integration/scaling/test_scheduler_singleton.py:88` (до виправлення):

```python
await terminate_backend(pg_engine, active.lease.backend_pid)
await wait_for(lambda: standby.is_active, what="резервний перебрав lease після смерті першого")
```

Тест вимагав, щоб звільнений advisory lock перебрав **саме резервний** процес. Це хибне
припущення: обидва процеси пробують `pg_try_advisory_lock` з тим самим інтервалом
(`lease_retry_seconds`), і колишній активний, помітивши втрату, має рівні шанси взяти lease
першим. Логи падіння це показують дослівно:

```text
[info   ] scheduler.activated   backend_pid=95  lease=scheduler-test
[warning] scheduler.lease_lost  ticks=5         lease=scheduler-test
[info   ] scheduler.activated   backend_pid=98  lease=scheduler-test   ← той самий процес
...
[info   ] scheduler.stopped     activations=0 ticks=0     ← резервний так і не активувався
[info   ] scheduler.stopped     activations=2 ticks=340   ← перший активувався двічі
```

**Продукт поводиться правильно** — активний завжди рівно один, а ні §7.5, ні вимога 6 картки
не обіцяють, хто саме виграє гонку за звільнений lock. Дефект у тесті.

Частота на цьому хості: 1/6 в одному батчі, 4/8 у наступному, +1 у прогоні набору — разом
**5 із 14**. У CI це означало б випадково червоний `integration-postgres`.

Виправлено (Edit дозволений у `tests/**`): тест перейменовано на
`test_two_schedulers_keep_exactly_one_active_and_lease_is_retaken_after_a_kill` і переписано
другу половину — тепер перевіряється контракт, а не переможець гонки:

- «двох активних не буває» стало інваріантом, який перевіряється **на кожному кроці**
  очікування (`never_both_active()` усередині предикатів `wait_for`), а не разово;
- після kill чекаємо «lease знову взято — рівно одним процесом», визначаємо winner/loser за
  фактом і перевіряємо, що lease тримає **нова** сесія (`backend_pid != killed_pid`);
- «без lease планування не відбувається» перевіряється на тому процесі, який lease не має
  (`loser.ticks` не росте протягом ≥3 його спроб).

Доведено, що тест не послаблено: після виправлення **10/10 зелених**, а мутація M3
(результат `pg_try_advisory_lock` ігнорується) робить його червоним так само, як і раніше.

### F1 — `high` — worker і scheduler ходять у PostgreSQL із superuser/міграційної ролі (§13)

`docker-compose.yml:86-92` (`x-worker`) і `docker-compose.yml:390-397` (`scheduler`) монтують
той самий secret `postgres_dsn`, що й one-shot `migrate-postgres`. Перевірка **в рантаймі** на
піднятому стеку:

```text
  usename  | rolsuper | rolbypassrls |       application_name       | count
-----------+----------+--------------+------------------------------+-------
 collector | t        | t            | collector-sch                |     2
 collector | t        | t            | collector-worker-discovery   |     1
 collector | t        | t            | collector-worker-export      |     1
 collector | t        | t            | collector-worker-fetch       |     2
 collector | t        | t            | collector-worker-maintenance |     1
 collector | t        | t            | collector-worker-parse       |     2
 collector | t        | t            | collector-worker-projector   |     1
 collector | t        | t            | collector-worker-translation |     1
```

Тобто всі вісім довгоживучих runtime-процесів працюють як **PostgreSQL superuser**
(`rolsuper = t`, `rolbypassrls = t`), і це та сама роль, якою виконуються міграції. §13 вимагає
протилежного: «Облікові дані БД розділені за компонентами… Migration role не використовується
runtime-процесами».

Що змінилось саме цим PR: на `main` secret `postgres_dsn` мав **лише** `migrate-postgres`
(`git show main:docker-compose.yml | grep postgres_dsn` → один сервіс). PR1 роздав його восьми
сервісам, тобто радіус ураження виріс з одного короткоживучого one-shot до всього worker pool.
Разом із цим послаблено два §13-інваріанти WP-00:
`tests/unit/test_compose_config.py::test_migration_dsn_secret_is_scoped_to_the_one_shot`
(перейменовано, allowlist розширено) і `tests/unit/test_compose_config_adversarial.py`
(`SECRET_CONSUMERS["postgres_dsn"]`).

Пом'якшувальні обставини (перевірені, не зі слів реалізатора):

- альтернативи в PR1 не існувало: у кластері немає жодної LOGIN-ролі, крім `collector` —
  `select rolname, rolcanlogin from pg_roles where rolname like 'collector%'` показує, що всі
  `collector_api_ro`, `collector_fetcher`, `collector_migrate`, `collector_parser`,
  `collector_projector`, `collector_scheduler`, `collector_translation`, `collector_export_ro`
  мають `rolcanlogin = f`; `migrations/**` і `schemas/**` для WP-01D — forbidden;
- жодних інших credentials (Mongo, MinIO) worker/scheduler не отримали; `api` лишився без
  секретів узагалі; Docker socket не змонтований; rootfs read-only, user `10001:10001`;
- порушення задокументоване і оформлене dependency-запитом
  (`docs/plan/deps/WP-01D-to-WP-01A.md` §2) та ризиком №1 у звіті реалізатора.

Рекомендація: **блокер для pilot/production і для будь-якого live-збору**, але не блокер merge
PR1 — закривати треба у WP-01A (LOGIN-ролі + per-component DSN) і синхронно повернути
§13-інваріанти в `tests/unit/test_compose_config*.py` до строгого вигляду. До того моменту
`COLLECTOR_WORKER_PLACEHOLDER=1` лишається робочим способом зупинити claim без перебудови
image.

### F2 — `medium` — плановий drain-timeout на останній спробі відправляє job у карантин

`src/collector/workers/runtime.py::_release_leases` повертає lease через
`queue.retry(..., policy=IMMEDIATE_RETRY_POLICY, error_code="drain_timeout")`, а
`src/collector/persistence/postgres/repositories/queue.py:253` на `attempt >= max_attempts`
переводить job у `quarantined` і пише dead letter `reason = max_attempts`. Тобто при звичайному
scale-down (§7.6: «завершують/повертають leases») job на останній спробі не повертається в
чергу, а «згорає» — з причиною, яка не відповідає дійсності. З дефолтом `max_attempts = 5`
трапляється рідко, але саме на проблемних jobs, які вже витратили спроби.

Закріплено тестом `test_drain_timeout_on_the_last_attempt_quarantines_a_job_nobody_failed`
(фіксує фактичну поведінку + перелічує очікування, які мають змінитись). Реалізатор знає про це
(ризик №3, dependency-запит §3 — `queue.release(job_id, owner)` без інкременту спроб). Знахідка
лишається `medium`, бо до появи `release()` це реальна втрата роботи при drain.

### F3 — `medium` — немає self-fencing при недоступності PostgreSQL

`runtime.py::_heartbeat` ловить `SQLAlchemyError | OSError | PersistenceError`, логує
`worker.heartbeat_failed` і повертається; лічильника послідовних невдач немає. Якщо PG
недоступний довше за `lease_seconds`, lease мовчки спливає, `recover_expired_leases` віддає job
іншому instance, а цей instance **продовжує виконувати той самий task** — про втрату власності
він дізнається лише в момент звіту (`_report` → `LeaseNotOwnedError` → `lost_leases += 1`).

Механіку видно у доданому тесті
`test_hanging_task_does_not_extend_its_lease_and_never_reports_a_silent_complete`: job забрано,
handler ще працює, і runtime дізнається про це тільки під час звіту. Для `NoopHandler` це
нешкідливо, а от доменні handler-и (WP-01B projector пише в Mongo, WP-02 fetch робить зовнішні
запити) отримають подвійне виконання. §9.3 вимагає ідемпотентності, тому це не катастрофа, але
дешева компенсація напрошується: скасовувати активні tasks після N підряд невдалих heartbeat-ів
(або коли з моменту останнього успішного heartbeat минуло > `lease_seconds`). Реалізатор
свідомо не писав fault-тест («Що не перевірено», п. 4) — але й guard-у в коді немає.

### F4 — `low` — «role-wide drain barrier» у PR1 є per-instance примітивом

`drain_requested_at` живе у рядку конкретного instance, тому «зупинити роль» = поставити
барʼєр кожному instance окремо. Доки немає `PoolController` (PR3), role-wide гарантії немає:
тест `test_drain_barrier_must_be_set_on_every_instance_of_the_role` показує, що барʼєр на одному
instance не зупиняє решту ролі. Це відповідає розподілу scope між PR1 і PR3 і не суперечить
R-57 (який вимагає саме не покладатись на вибір контейнера), але формулювання «Role-wide drain
barrier (R-57)» у `implementation-pr1.md` варто читати як «будівельний блок для R-57».

### F5 — `low` — `worker_instances.container_id` завжди `NULL` у Compose

`collector.workers.config` документує і читає `COLLECTOR_CONTAINER_ID`, але жоден сервіс у
`docker-compose.yml` його не задає. На піднятому стеку:

```text
 role  |   hostname   | container_id | deployment |    version    | pool_revision
-------+--------------+--------------+------------+---------------+---------------
 fetch | 830b526f8929 |              | compose    | 0.1.0+5ad8d4b |             1
 ... (4 рядки, container_id порожній у всіх)
```

§7.6 вимагає «deployment/container metadata» у `worker_instances`. На логіку не впливає
(`hostname` заповнений і його достатньо для діагностики), але екран Workers (WP-11C)
показуватиме порожню колонку. Виправлення — один рядок
`COLLECTOR_CONTAINER_ID: ${HOSTNAME}`-подібний у `x-worker`.

### F6 — `low` — локальний прогін піднімає два testcontainers-контейнери PostgreSQL

`tests/integration/scaling/conftest.py` завантажує `tests/integration/postgres/conftest.py` за
шляхом і реекспортує session-scope фікстури, тому в межах однієї сесії існують два різні
`fixturedef` і два сервери. У CI сервер зовнішній
(`COLLECTOR_TEST_POSTGRES_ADMIN_DSN`), тож там цього немає; локально це лише повільніше.
Реалізатор задокументував (ризик №4) — підтверджую, дефектом не вважаю.

### Flaky-тести

Один — F0, зафіксований і виправлений (не «переретраєний»). Решта `tests/integration/scaling`
(включно з моїми 13 тестами) — стабільно зелена: набір виконано 5 разів (з `-p no:randomly` і з
дефолтним випадковим порядком), плюс 10 ізольованих прогонів виправленого тесту і 14 прогонів
до виправлення. Жодного ретраю для «позеленіння» не робилось.

---

## 7. Звірка з `implementation-pr1.md` (читано після власного прогону)

| Заявлено | Підтверджено моїм прогоном |
|---|---|
| lint/format/mypy зелені | так |
| `pytest -m "not live"` — 793 passed, 1 skipped | так, дослівно (baseline); 817 після моїх тестів |
| `pytest -m integration tests/integration/scaling` — 13 passed | так; 27 після моїх тестів. Але один із 13 — flaky (F0): у реалізатора він пройшов, у мене падав у 5 прогонах із 14 |
| `docker compose config --quiet` — exit 0 | так |
| стек піднімається `--wait`, усі healthy, instances реєструються з defaults §7.6 | так, відтворено незалежно (з власним `-p wp01d` і своїми іменами мереж) |
| SIGTERM → `draining` → `stopped` → exit 0, швидше за grace | так (1.8 c, exit 0) |
| `down -v` чистий | так |
| ризик №1 (DSN міграційної ролі) | підтверджено і посилено: роль ще й `rolsuper`/`rolbypassrls`; підвищено до знахідки `high` (F1) |
| ризик №3 (`retry` замість `release` на drain-timeout) | підтверджено, закріплено тестом (F2) |
| ризик №4 (два testcontainers) | підтверджено (F6) |
| «Що не перевірено»: `--scale fetch-worker=4` | **перевірено мною**: 4 репліки, 4 `ready` instances, 32 slots |
| «Що не перевірено»: падіння PostgreSQL посеред claim/heartbeat | не перевіряв примусовим падінням сервера, але зафіксував відсутність self-fencing як F3 |
| «Що не перевірено»: SIGTERM у контейнері під активним task | лишається неперевіреним і в мене (черга порожня без доменних handler-ів); покрито тестом у процесі |

Заявлене реалізатором підтверджується. Розбіжність одна: ризик №1 у звіті реалізатора
класифікований м'якше, ніж він є після рантайм-перевірки прав ролі.

---

## 8. Вердикт

**`pass`.**

Усі шість вимог PR1 реалізовані й покриті тестами, які я довів «червоними» на зламаному коді
(4 мутації). Adversarial-сценарії — claim-гонка, lease без heartbeat, recovery під живим
heartbeat, повторний SIGTERM, `stopped` instance, повторний boot контейнера, stale-детекція,
standby scheduler, втрата зʼєднання з PG — додані і зелені. Docker-стек піднімається,
масштабується до 4 реплік, коректно drain-иться і чисто прибирається.

F0 (`high`) — дефект тесту, а не продукту; виправлено в цій же гілці, стабільність доведена
(10/10) і сила збережена (мутація M3 далі робить його червоним).

F1 (`high`) — порушення §13, яке PR1 **не міг** закрити власними силами (LOGIN-ролей не існує,
`migrations/**` для WP-01D forbidden), задокументоване і оформлене dependency-запитом. Не
блокує merge PR1, але має бути закрита у WP-01A **до pilot і до будь-якого live-збору**.

F2 і F3 (`medium`) варто закрити в межах WP-01D: F2 — після появи `queue.release(job_id,
owner)`, F3 — self-fencing у `_heartbeat` (скасовувати активні tasks, якщо з моменту останнього
успішного heartbeat минуло більше за `lease_seconds`).
