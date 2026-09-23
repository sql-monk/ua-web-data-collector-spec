# Пострев'ю за ТЗ — WP-01D PR1 (worker runtime, lease/heartbeat/fencing, drain, singleton scheduler)

| Поле | Значення |
|---|---|
| Гілка | `wp/01d-1-worker-runtime`, HEAD `4085280` |
| Worktree | `.worktrees/wp-01d` (гілка **не** перебазована на `main`; merge-base `ba57363`, на `main` уже WP-00 PR3 — оцінювалось як є) |
| Diff | `git diff main...HEAD` — 31 файл, +6061/−32 |
| Вхід | картка `docs/plan/cards/WP-01D.md` (PR1), ТЗ §7.5, §7.6, §13, §15, §16.1 п.15, §16.3, §17.2, §18, Додаток C, FR-031/FR-032, REVIEW.md R-52/R-57, звіти `implementation-pr1.md`, `testing-pr1.md`, `code-review-pr1.md` |
| Рев'юер | `wp-spec-reviewer` (read-only, крім цього файла і `docs/acceptance/traceability.md`) |
| Дата | 2026-09-23 |
| **Вердикт** | **`changes_requested`** — 1 `missing` (вимога 7 картки), 6 `partial` |

Доказ — файл:рядок, тест або звіт із фактичним виводом. Статуси: `evidenced` / `partial` /
`missing` / `not applicable (аргумент)`.

## Власна верифікація (запущено в цьому worktree, read-only)

| Команда | Результат |
|---|---|
| `uv run ruff check .` | `All checks passed!` |
| `uv run ruff format --check .` | `201 files already formatted` |
| `uv run mypy src` | `Success: no issues found in 68 source files` |
| `uv run pytest -m "not live and not integration" -q` | `688 passed, 1 skipped, 148 deselected in 18.72s` |
| `uv run pytest -m integration tests/integration/scaling -q -p no:randomly` | `32 passed in 40.93s` |
| `docker compose config --quiet` | порожній вивід, `EXIT=0` |
| зонд regex тест-вартового §13 (нижче, S-2) | 2 з 4 реалістичних форм LOGIN-ролі **не** ловляться |

Docker-стек не піднімався (піднятий стек уже задокументований двома незалежними прогонами —
реалізатора і тестувальника; сторонніх контейнерів користувача не чіпав, жодного контейнера не
створено). Прогін `-m integration tests/integration/scaling` підняв рівно один
testcontainers-PostgreSQL і прибрав його за собою.

---

## 1. Acceptance criteria WP (§17.2) і дотичні пункти §16.3

### 1.1. §17.2 — рядок WP-01D

> «role commands, pool/instance/scale contracts, PostgreSQL origin limiter, heartbeat/drain,
> Compose command adapter і Swarm replica adapter; scale/rate/fault tests green»

| Частина критерію | Доказ | Статус |
|---|---|---|
| **role commands** — `collector worker <role>` для всіх 8 ролей §7.6 + `collector scheduler` як реальні процеси | `src/collector/cli.py:521-580` (`worker`, `scheduler`); `src/collector/workers/roles.py:20-31`; `tests/unit/workers/test_signals_and_cli.py` (4); `implementation-pr1.md` §«Команди та вивід»: 16 контейнерів healthy, `worker_instances` заповнені для 7 ролей | evidenced |
| **pool/instance contracts** — runtime читає `worker_pools` і веде `worker_instances` | `src/collector/workers/runtime.py:328-369` (`_ensure_pool`), `:534-595` (`_heartbeat`); `tests/integration/scaling/test_worker_runtime.py::test_bootstraps_missing_pool_from_spec_defaults`, `::test_instance_registers_becomes_ready_and_stops_on_drain` | evidenced |
| **scale contracts** (`scale_commands`, `PoolController`) | — | not applicable (PR3 за карткою; PG-контракт уже є у WP-01A PR1) |
| **PostgreSQL origin limiter** | — | not applicable (PR2 за карткою) |
| **heartbeat/drain** | `runtime.py:495-533` (heartbeat + watchdog), `:696-717` (`_drain`), `:726-774` (`_release_leases`); `test_worker_runtime.py::test_drain_finishes_active_task_and_takes_no_new_jobs`, `::test_drain_timeout_returns_the_lease_to_the_queue`; Docker: `stop translation-worker` → `draining` → `stopped` → exit 0 за 1.8 с (`testing-pr1.md` §1) | evidenced |
| **Compose command adapter / Swarm replica adapter** | — | not applicable (PR3) |
| **scale/rate/fault tests green** | 32 integration у `tests/integration/scaling` (перевірено мною) + 688 unit; `rate`-тести — PR2, `scale`-тести `1→4→1→0→2` — PR3 | partial (fault-частина — evidenced; scale/rate — PR2/PR3) |

**Разом §17.2: `partial` за побудовою** — PR1 покриває приблизно третину критерію, і решта чесно
віднесена до PR2/PR3 у картці. Жодного `missing`.

### 1.2. §16.3 — дотичні пункти

| Пункт §16.3 | Доказ | Статус |
|---|---|---|
| «scale-down під активним job завершує або **повертає lease** без втрати» | `test_worker_runtime.py::test_drain_finishes_active_task_and_takes_no_new_jobs` (task дотягується, новий claim не береться), `::test_drain_timeout_returns_the_lease_to_the_queue` (lease повертається `IMMEDIATE_RETRY_POLICY`), `test_worker_runtime_adversarial.py::test_drain_timeout_on_the_last_attempt_never_quarantines_a_job_nobody_failed`; мутація M4 тестувальника (`_release_leases` → `pass`) робить тест червоним | partial — сам drain доведено; **`scale-down`** (зменшення replicas із role-wide барʼєром) — PR3, а job на останній спробі повертається не одразу, а після експірації lease (див. §4 R-52 і ризик 3 картки) |
| «kill replica відновлюється після lease expiry» | `test_worker_runtime.py::test_killed_replica_lease_is_recovered_and_finished_by_another_instance`; Docker: `docker kill --signal=SIGKILL` → exit 137 → `stale` через ~75 с → рестарт того самого контейнера дає **новий** `instance_id` (`testing-pr1.md` §1) | evidenced |
| «чистий Docker host підіймає core/workers однією documented командою» (успадковане від WP-00) | `implementation-pr1.md` і `testing-pr1.md`: `up -d --wait` exit 0, 16/16 healthy/exited-0, `down -v` чисто | evidenced (з застереженням: обидва прогони — до ребейзу на `main` з WP-00 PR3, див. S-6) |
| «GUI/API/worker images не мають Docker socket» | `tests/unit/test_compose_config.py::test_no_docker_socket_mount_anywhere` лишився зеленим (у моєму прогоні unit); `testing-pr1.md` §1: inspect контейнерів — жодного socket, rootfs read-only, user `10001:10001` | evidenced |
| «scale `fetch 1→4→1` … сумарний origin request rate не перевищує source policy» | `--scale fetch-worker=4` перевірено вручну тестувальником (4 ready, 32 slots); rate — PR2 | not applicable (PR2/PR3) |

---

## 2. Вимоги PR1 картки (1–7), тести PR1, розділи картки

### 2.1. Вимоги 1–7

| # | Вимога картки | Доказ | Статус |
|---|---|---|---|
| 1 | `WorkerRuntime`: boot `worker_instance_id` (UUIDv7), реєстрація в `worker_instances` (role, version, deployment metadata, status `starting`), перехід у `ready` після readiness, heartbeat < lease TTL | `runtime.py:137` (`new_entity_id()` → `contracts/identity.py:106-108` — UUIDv7), `:275-301` (`_boot`), `:371-375` (`_check_ready`: `SELECT 1` + `handler.check_ready()`), `:303-326` (`_become_ready` з 5 спробами), `:495-512` (heartbeat за дедлайном); `config.py:__post_init__` вимагає `heartbeat × 3 ≤ lease`; `test_worker_runtime.py::test_instance_registers_becomes_ready_and_stops_on_drain`, `::test_boot_fails_loudly_when_the_ready_transition_cannot_be_written`; `tests/unit/workers/test_config.py::test_heartbeat_must_leave_room_for_a_missed_beat` | evidenced |
| 2 | Claim-loop до `desired_concurrency`, `handle(task)` через `TaskHandler` (`NoopHandler` для тестів), продовження lease, `complete/retry/quarantine` | `runtime.py:377-421` (`_claim_loop`/`_claim`), `:427-491` (`_execute`/`_report`), `:569-575` (продовження lease у тій самій транзакції heartbeat); `handlers.py` (`TaskHandler`, `Task`, `TaskResult`, `NoopHandler`, `PermanentTaskError`); `test_worker_runtime.py::test_handler_failure_becomes_a_retry_with_backoff`, `test_worker_runtime_adversarial.py::test_permanent_handler_error_quarantines_with_a_dead_letter`, `::test_two_runtimes_claiming_one_job_exactly_one_wins`; `tests/unit/workers/test_handlers.py` (13) | evidenced |
| 3 | SIGTERM → `draining` (claim заборонено, активні завершуються в `stop_grace_period`, lease повертаються) → exit 0; SIGKILL — fault case, lease відновлює `recover_expired_leases` | `signals.py` (POSIX `add_signal_handler` + Windows fallback, `restore()`); `runtime.py:205-218` (`claiming`), `:696-717` (`_drain`), `:726-774` (`_release_leases`), `:259-261` (скасування корутини **не** робить drain — саме поведінка SIGKILL); `test_worker_runtime.py::test_drain_finishes_active_task_and_takes_no_new_jobs`, `::test_drain_timeout_returns_the_lease_to_the_queue`, `::test_killed_replica_lease_is_recovered_and_finished_by_another_instance`; `test_worker_runtime_adversarial.py::test_repeated_stop_requests_under_an_active_task_are_idempotent`, `::test_instance_marked_stopped_claims_nothing_and_exits`; Docker: exit 0 за 1.8 с / exit 137 → `stale` | evidenced (з ремаркою: job на `attempt >= max_attempts` lease не повертає одразу — ризик 3 картки, owner WP-01A PR2) |
| 4 | `desired_concurrency` без рестарту: нові slots одразу, зайві після завершення активних | `runtime.py:551-556` (кожен heartbeat перечитує `worker_pools`), `:637-664` (`_apply_pool`), `:382-389` (`free = effective_concurrency − active`); `test_worker_runtime.py::test_concurrency_hot_change_opens_and_closes_slots_without_restart` (1→3→1, активні tasks не скасовуються), `::test_hot_change_above_the_connection_ceiling_is_clamped` | evidenced |
| 5 | Жодного стану на локальному диску; `worker_instance_id` на boot; Docker hostname лише metadata | `config.py:from_env` (hostname/container_id пишуться лише як metadata), `runtime.py:137` (id на boot); `tests/unit/workers/test_roles_and_state.py::test_workers_package_writes_nothing_to_local_disk` (статичний вартовий по всьому пакету); `test_worker_runtime_adversarial.py::test_second_boot_of_the_same_container_creates_a_new_instance`; Docker-доказ: той самий `hostname 36a3a1370366` після рестарту → новий UUIDv7, старий рядок лишився `stale` (`testing-pr1.md` §1) | evidenced |
| 6 | `scheduler` — singleton через advisory lease у PostgreSQL: другий чекає, при втраті lease планування припиняється | `advisory.py` (`pg_try_advisory_lock` + серверна перевірка `pg_locks` за `pg_backend_pid()`, AUTOCOMMIT-зʼєднання поза pool-ом, `release()` з перевіркою результату unlock); `scheduler.py:152-175` (`_try_activate`/`_still_active`); `test_scheduler_singleton.py` (5), `test_scheduler_singleton_adversarial.py` (4); мутація M3 тестувальника робить 3 тести червоними; Docker: 2 контейнери scheduler → рівно один `scheduler.activated` | evidenced |
| 7 | **Дешевий liveness-probe для Docker healthcheck** (запит WP-00 PR3, CI PR #4): довгоживучий процес має віддавати liveness без запуску нового інтерпретатора і без звертання до БД, щоб повернути `x-healthcheck-budget` до секундних значень | У гілці немає **жодного** сліду: `docker-compose.yml:102-107` (гілка) — той самий `python -m collector.api.health postgres`; `grep -i "liveness\|healthcheck\|start_period"` по `docs/plan/reports/WP-01D/**`, `docs/plan/deps/**` і `src/collector/workers/**` не дає жодної згадки вимоги; у трьох звітах PR1 її немає ні як виконаної, ні як віднесеної до PR2/PR3 | **missing** — див. знахідку S-1 |

**Пояснення до вимоги 7.** Її додано в картку на `main` коммітом WP-00 PR3 **після** merge-base
`ba57363`, тому в гілковій копії `docs/plan/cards/WP-01D.md` її фізично немає — реалізатор
її, найімовірніше, не бачив. Це пояснює пропуск, але не закриває його: вимога адресована саме
власнику runtime, обґрунтована заміряними числами (1.0 CPU — 2.9 с, 0.25 CPU — 6.0 с) і вже
коштує проєкту роздутого бюджету healthcheck (`timeout: 15s`, `start_period: 90s`). Після
ребейзу вона зʼявиться в картці PR1 і має бути або реалізована, або явно перенесена з
owner/датою.

### 2.2. Тести PR1, названі в картці

| Тест із картки | Де | Статус |
|---|---|---|
| Killed replica → lease recovery іншим instance | `test_worker_runtime.py::test_killed_replica_lease_is_recovered_and_finished_by_another_instance` | evidenced |
| Drain під активним task (task завершується, lease не втрачено) | `::test_drain_finishes_active_task_and_takes_no_new_jobs`, `::test_drain_timeout_returns_the_lease_to_the_queue` | evidenced |
| Гаряча зміна concurrency | `::test_concurrency_hot_change_opens_and_closes_slots_without_restart` | evidenced |
| Два scheduler → рівно один активний | `test_scheduler_singleton.py::test_two_schedulers_keep_exactly_one_active_and_lease_is_retaken_after_a_kill`, `test_scheduler_singleton_adversarial.py::test_standby_scheduler_does_no_maintenance_while_the_active_one_does` | evidenced |
| Heartbeat не продовжує чужий lease | `test_worker_runtime.py::test_heartbeat_does_not_extend_a_foreign_lease`; мутація M1 (зняти owner-предикат) робить його червоним | evidenced |

### 2.3. «Команди перевірки» картки

| Команда | Ким виконана | Статус |
|---|---|---|
| `uv sync --frozen` | реалізатор, тестувальник | evidenced |
| `ruff check` / `ruff format --check` / `mypy src` | **мною** + реалізатор + код-рев'юер | evidenced |
| `uv run pytest -m "not live"` | реалізатор (836 passed), тестувальник (817 passed); **мною** — unit-частина 688 passed | evidenced |
| `uv run pytest -m integration tests/integration/scaling` | **мною** — 32 passed; реалізатор 32, код-рев'юер 28 | evidenced |
| `docker compose config --quiet` | **мною** — exit 0 | evidenced |
| `docker compose --profile core --profile workers up -d --wait` | реалізатор і тестувальник (незалежно, з ізоляцією мереж) | partial — обидва прогони до ребейзу на `main` з WP-00 PR3, який змінює healthcheck-бюджет і додає сервіс `gui`; повторити після ребейзу (S-6) |
| `docker compose up -d --no-recreate --scale fetch-worker=4` | тестувальник (4 ready, 32 slots); реалізатор чесно писав «не перевірено» | evidenced (формально — предмет PR3) |
| `docker compose down -v` | реалізатор і тестувальник | evidenced |

### 2.4. «Acceptance», «Rollback/disable», «Відомі ризики»

| Розділ картки | Доказ | Статус |
|---|---|---|
| Acceptance (§17.2) | див. §1.1 | partial (за побудовою PR1) |
| Rollback/disable — «worker-сервіси можна повернути на placeholder-команду через env (задокументувати)» | `config.py:placeholder_requested` + `PLACEHOLDER_ENV`; `cli.py:530-533`, `:573-576`; `docker-compose.yml` пробрасує `COLLECTOR_WORKER_PLACEHOLDER:-0` у `x-worker` і `scheduler`; `tests/unit/test_cli_compose_commands.py::test_worker_command_falls_back_to_placeholder_under_rollback_flag` (параметризовано по всіх 8 ролях), `::test_scheduler_command_falls_back_to_placeholder_under_rollback_flag`, `tests/unit/workers/test_config.py::test_placeholder_rollback_flag`; задокументовано в `implementation-pr1.md` §«Як вимкнути або відкотити» | evidenced |
| Rollback/disable — «autoscale off», «controller у окремому profile» | коду autoscale і controller у PR1 немає | not applicable (PR3) |
| «Відомі ризики» — таблиця §13 і drain/`queue.release` з owner/датою + тест-вартовий | `docs/plan/cards/WP-01D.md` (гілкова версія, розділ «Відомі ризики»): 2 рядки, обидва з owner (`WP-01A PR2`) і датою `2026-09-23`; тест-вартовий названо | partial — таблиця є і коректна, але (а) тест-вартовий слабший, ніж обіцяно (S-2), (б) залишковий TOCTOU singleton-тіку (M-4) у таблицю не потрапив (S-5), (в) ризик 4 звіту реалізатора суперечить власному рядку F6 «fixed» у тому самому документі (S-3) |

---

## 3. ТЗ дослівно: §7.5, §7.6, §13, §15, FR-031, FR-032

| Вимога ТЗ | Доказ | Статус |
|---|---|---|
| §7.5 «`stop_grace_period` довший за максимальний bounded task shutdown; SIGTERM запускає drain, SIGKILL є fault case з lease recovery» | compose `stop_grace_period: 120s` (worker) / `90s` (scheduler) проти `COLLECTOR_WORKER_STOP_GRACE_SECONDS: "90"`; `tests/unit/workers/test_desired_state_bounds.py::test_stop_grace_must_fit_into_the_compose_grace_period`; drain/kill-тести §2.2 | evidenced |
| §7.5 «worker services … без local persistent state; `worker_instance_id` генерується на boot, а Docker hostname зберігається лише як metadata» | див. вимогу 5 у §2.1 | evidenced |
| §7.5 «scheduler/controller мають singleton advisory lease» | `advisory.py`; `controller` — PR3 | evidenced (scheduler), not applicable (controller — PR3) |
| §7.5 «healthcheck перевіряє process і критичну dependency» + вимога 7 картки про дешевий liveness | `docker-compose.yml:102-107`; вимога 7 не закрита | **missing** (S-1) |
| §7.6 контракт `worker_instances`: boot UUID, role, deployment/container metadata, version, status `starting\|ready\|draining\|stopped\|stale`, slots, heartbeat, active leases | `models/pools.py:101-137` (усі поля присутні, `enum_check` по 5 статусах); runtime заповнює всі: `runtime.py:277-289` (register), `:557-567` (heartbeat зі `slots_total`/`slots_active`/`active_leases`/`pool_revision`) | evidenced |
| §7.6 «Default replicas × concurrency» (таблиця з 8 рядків) | `roles.py:56-70` (`DEFAULT_POOL_SPECS`), `parse` = `2 × os.process_cpu_count()` з підлогою 2, `browser` = `0 × 1`, `maintenance` = `1 × 1`; `tests/unit/workers/test_roles_and_state.py::test_every_role_has_defaults_from_spec_7_6`, `test_desired_state_bounds.py::test_browser_pool_default_is_zero_replicas_but_never_zero_concurrency`; рантайм-доказ у БД піднятого стека (`discovery 1×4`, `fetch 2×8`, `parse 2×12`, `projector 1×8`, `translation 1×4`, `export 1×2`, `maintenance 1×1`) | evidenced |
| §7.6 «claim виконується через `FOR UPDATE SKIP LOCKED`; кожна task має lease owner/expiry і heartbeat» | runtime не має власного SQL черги — усе через `repositories/queue.py` WP-01A; `owner = str(instance_id)` наскрізь (`runtime.py:167-170`); `test_worker_runtime_adversarial.py::test_two_runtimes_claiming_one_job_exactly_one_wins` | evidenced |
| §7.6 «зміна `desired_concurrency` застосовується без restart на межі task» | вимога 4 §2.1 | evidenced |
| §7.6 «перед зменшенням container replicas controller ставить **role-wide** drain barrier … не покладається на те, який container Compose/Swarm вирішить видалити» | PR1 дає **per-instance** примітив (`drain_requested_at` власного рядка), і це чесно названо: `runtime.py:204-218` (докстрінг `claiming`), картка PR3 вимога 2 (доповнена цим PR), `implementation-pr1.md` F4, `testing-pr1.md` F4; `test_worker_runtime_adversarial.py::test_drain_barrier_must_be_set_on_every_instance_of_the_role` показує межу прямо | partial — чесно задокументовано, role-wide гарантія за PR3 |
| §13 «Облікові дані БД розділені за компонентами … Migration role не використовується runtime-процесами» | **порушено свідомо**: `docker-compose.yml` роздає secret `postgres_dsn` (роль `collector`, `rolsuper = t`) 8 worker-сервісам + `scheduler`; рантайм-доказ — `testing-pr1.md` §6 F1 | **порушення зафіксовано якісно, але не повністю** — оформлення див. §6 (S-2) |
| §15 «Worker не зберігає job/data на локальному filesystem; replacement replica підхоплює expired/released lease після readiness» | вимога 5 §2.1 + `test_killed_replica_lease_is_recovered_and_finished_by_another_instance` | evidenced |
| §15 «Replica count × concurrency є capacity ceiling» | `effective_concurrency = min(desired, max_slots)` (`runtime.py:190-193`), heartbeat звітує фактичні `slots_total`; сам origin-лімітер — PR2 | partial (PR2) |
| FR-031 «workers є stateless role-based pools, які масштабуються незалежно без зміни image» | один image, роль — аргумент CLI; `--scale fetch-worker=4` без перебудови (`testing-pr1.md` §1) | evidenced |
| FR-032 «replica scale-down використовує role-wide drain barrier» | те саме, що §7.6 вище | partial (PR3) |

---

## 4. Регресія REVIEW.md

| R | Що вимагає | Втілення в коді/тестах | Статус |
|---|---|---|---|
| **R-52** — «незалежні pools, drain, lease recovery» | вісім pools, desired/current state, heartbeats, drain, lease recovery | 8 ролей з власними defaults (`roles.py:56-70`), кожна роль — свій pool-рядок і свої instances; drain (`_drain`, `_release_leases`) і recovery (`test_killed_replica_...`, `scheduler.run_maintenance`) доведені тестами; current state виводиться з heartbeat, локального стану немає | evidenced (у межах PR1: pools/drain/recovery; scale-команди — PR3) |
| **R-57** — «role-wide drain barrier, не покладатися на вибір контейнера orchestrator-ом» | барʼєр має зупиняти claim незалежно від того, який контейнер видалить Compose/Swarm | PR1 дає **примітив**: claim зупиняє не лише SIGTERM, а й `worker_instances.drain_requested_at` **власного** рядка — тобто рішення приймає сам instance, а не оркестратор. Це **чесно** названо per-instance у трьох місцях (`runtime.py:204-218`, картка PR3 вимога 2, `implementation-pr1.md` F4) і **доведено тестом межі** `test_drain_barrier_must_be_set_on_every_instance_of_the_role` (барʼєр на одному instance роль не зупиняє). Мутація M2 тестувальника робить 3 тести червоними. За role-wide відповідає `PoolController` PR3 | **partial, але за видане не видано** — формулювання «Role-wide drain barrier (R-57)» у ранній версії звіту виправлене після F4; претензій немає |
| R-53 — aggregate rate не залежить від replicas | — | — | not applicable (PR2) |
| R-55 — GUI/API без socket; Swarm controller ізольований | `test_no_docker_socket_mount_anywhere` лишився зеленим; controller — PR3 | — | evidenced (частина «без socket»), not applicable (controller — PR3) |

---

## 5. Q-питання §20

| Q | Default до відповіді | Як обійшлися в PR1 | Статус |
|---|---|---|---|
| Q-013 (deployment mode: Compose MVP / Swarm для GUI-scaling) | «Compose для local/single-host MVP» | safe default використано і конфігурований: `WorkerRuntimeConfig.deployment` = `compose` з env `COLLECTOR_WORKER_DEPLOYMENT`, записується в `worker_instances.deployment`; жодного коду Swarm у PR1; ADR-0006 заплановано на PR3 (розділ «Docs (етап 5)» картки) | evidenced (safe default + конфігурований + запланований ADR) |
| Q-014 (autoscale on/off) | «ні; manual desired replicas/concurrency до pilot evidence» | коду autoscale у PR1 немає взагалі; desired state змінює лише оператор/контролер; ADR-0007 — PR3 | evidenced (за побудовою) |

---

## 6. Окремі перевірки, замовлені завданням

### 6.1. Чи кожен `fixed` справді в коді

**Gate 3 (код-рев'ю).**

| # | Заявлено | Перевірка по коду | Вердикт |
|---|---|---|---|
| **H-1** `high` — fencing сліпий до **зависання** БД | (1) сторож окремою задачею, (2) бюджет на тік, (3) таймаути драйвера | (1) ✔ `runtime.py:253-256` створює `_watchdog_loop`, `:514-532` рахує **час** від `_last_heartbeat_ok` незалежно від heartbeat-корутини; (2) ✔ `:509-512` `asyncio.wait_for(self._heartbeat(), heartbeat_tick_budget)`, `TimeoutError` → `_fence_if_lease_unconfirmed()`; (3) ✔ `engine.py:31-48` (`command_timeout` у `connect_args`), `cli.py:_run_worker` передає `config.command_timeout`, `runtime.py:547-550` ставить `statement_timeout` у транзакції heartbeat. **Тест відтворює саме зависання, а не виняток:** `HangingSessions.__aenter__` робить `await asyncio.sleep(3600)` без жодного винятку, а тест окремо стверджує `sessions.hangs >= 1` і `runtime.heartbeats == beats` («жодного підтвердженого heartbeat не було») — `test_worker_runtime.py:470-542` | **fixed** (з ремаркою S-4: `statement_timeout` ставиться лише в heartbeat і в fallback `_ensure_pool`, а `command_timeout` — лише на CLI-шляху) |
| **M-1** `medium` — «живий, але німий» worker | retry + fail-fast | ✔ `runtime.py:303-326` (`_become_ready`, 5 спроб з експоненційним backoff, інакше `WorkerRuntimeError` з `run()`), `:588-591` (heartbeat самолікується, поки `status == "starting"`); тест `test_boot_fails_loudly_when_the_ready_transition_cannot_be_written` | fixed |
| **M-2** `medium` — контракт handler-а не забороняв блокуючий `handle()` | контракт + перевірка на boot + watchdog-лог | ✔ `handlers.py` докстрінг модуля (явне правило про `asyncio.to_thread`), `check_handler_contract` (`async def handle`, непорожній `job_types`) викликається в `WorkerRuntime.__init__` (`runtime.py:139`); `_watchdog_loop` логує `worker.event_loop_stalled` (`:526-531`); тести `test_sync_handler_is_rejected_because_it_would_block_the_event_loop`, `test_handler_without_job_types_is_rejected` | fixed (з боргом: `docs/workers.md` ще немає — має зʼявитись **до** того, як WP-02/03/04 почнуть писати handler-и; це DoD §18 п. 6, див. §7) |
| **M-3** `medium` — pool з'єднань vs гарячий concurrency | стеля процесу + точний pool | ✔ `config.py` (`max_concurrency`, `max_slots`, `CONNECTION_RESERVE = 3`), `runtime.py:190-193` (`effective_concurrency`), `:637-653` (`worker.concurrency_clamped`), `:560-563` (heartbeat звітує фактичні slots), `cli.py:_run_worker` (`pool_size = max_slots + 3`, `max_overflow = 0`); тест `test_hot_change_above_the_connection_ceiling_is_clamped` | fixed (верхня межа `desired_concurrency` у `PoolDesiredState.validate` не додана — файл WP-01A; clamp у runtime закриває наслідок) |
| **M-4** `medium` — TOCTOU singleton-lease | «fixed контрактом + вартовим» | ✔ контракт у `scheduler.py` (докстрінг модуля, аліас `SchedulerTick`, `run()`), вартовий `test_scheduler_singleton.py::test_maintenance_tick_is_safe_when_two_schedulers_overlap`. **Код не змінено** — вікно TOCTOU лишилось відкритим для майбутнього неідемпотентного доменного тіку | **accepted, не fixed** — маркування «fixed (контрактом + вартовим)» чесне, але залишковий ризик не потрапив у «Відомі ризики» картки (S-5) |
| L-1 … L-7 | різне | ✔ L-1: `heartbeat × 3 ≤ lease` + тіки від дедлайну (`config.py:__post_init__`, `runtime.py:503-507`); ✔ L-2: лічильники `fences`/`lost_leases` у логах і `worker.stopped`; ✔ L-3: `_cancelled` + `_await_cancelled_tasks()` у `_drain` і у `finally` `run()`; ✔ L-4: `PersistenceError` у `except` `_report` (`:480`); ✔ L-6: `release()` перевіряє boolean `pg_advisory_unlock` і робить `invalidate()` (`advisory.py:129-160`); ✔ L-7 частково: claim-backoff (`:404-413`), `redact()` (`handlers.py:154-173`), порожній `job_types` відхиляється; решта L-7 — `accepted`, owner WP-01D PR3, дата | fixed / accepted з owner+датою |

**Gate 2 (тестування).** F0 — виправлено тестувальником у `tests/**`, тест існує під новою назвою і
мутація M3 і далі робить його червоним; F1 — `accepted`, owner WP-01A PR2, дата 2026-09-23; F2 —
`fixed` (`_release_leases` викликає `retry` лише при `attempt < max_attempts`, `runtime.py:741-751`;
guard-тест перейменовано на `..._never_quarantines_a_job_nobody_failed`); F3 — `fixed`
(self-fencing, підсилений H-1); F4 — `fixed` (уточнення формулювань, див. §4 R-57); F5 — `fixed`
(`container_id` з `COLLECTOR_CONTAINER_ID`/`HOSTNAME`, `config.py:from_env`, тест
`test_container_id_falls_back_to_docker_hostname`, рантайм-доказ у звіті); F6 — `fixed`
(`conftest.py::_share_server_and_template`; мій прогін підняв рівно один контейнер).

**Висновок:** усі `fixed` підтверджені в коді; жодного «виправлено лише в ТЗ/звіті». M-4
коректніше читати як `accepted`, і звіт це фактично й пише.

### 6.2. Чи `docs/plan/deps/WP-01D-to-WP-01A.md` містить усе, що потрібно WP-01A PR2

| Потрібне WP-01A PR2 | У deps-файлі | Оцінка |
|---|---|---|
| LOGIN-ролі per component + per-role DSN (§13) | §2 — «високий пріоритет, блокер pilot», з рантайм-доказом (`rolsuper = t` для всіх 8 процесів), поясненням, чому альтернативи не було, і **конкретним списком того, що просимо**: (1) LOGIN-ролі з `GRANT <group> TO <login>`, (2) узгоджений формат імен секретів (`postgres_dsn_fetcher`, …, з явним зазначенням, що `init-secrets.sh` — owner WP-00), (3) підтвердження мінімальних прав пооб'єктно (`crawl_jobs`, `dead_letters`, `worker_pools`, `worker_instances`, `audit_log`) | **повно** — WP-01A PR2 має достатньо, щоб почати, не ходячи по звітах |
| `queue.release(job_id, owner)` | §3 + §5: `leased → pending`, lease очищено, `attempt` **не** змінюється, dead letter **не** пишеться, `last_error_code`/`last_error_message` **не** пишуться; пояснено, чим це відрізняється від `retry` і від `recover_expired_leases`; вказано, що пріоритет низький, бо коректність уже забезпечена фіксом F2 | **повно** |
| `command_timeout` в `engine.py` | §4: зміна названа, обґрунтована (H-1), помічена як адитивна і зворотно сумісна (`None` за замовчуванням), із прямим проханням підтвердити при merge | **повно** |
| §1 «нових таблиць/колонок не потрібно» | зафіксовано з аргументом (session advisory lock) | корисно — знімає питання наперед |

**Оцінка: достатньо.** Єдина розбіжність — `implementation-pr1.md:433` називає §2 «середнім
пріоритетом», тоді як сам deps-файл і «Відомі ризики» картки кажуть «високий, блокер pilot»
(S-3).

### 6.3. Чи тест-вартовий на LOGIN-ролі справді впаде, коли вони зʼявляться

Тест: `tests/unit/test_compose_config.py::test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire`.
Механіка: читає `src/collector/persistence/postgres/sql/roles.sql`, відкидає рядки, що
**починаються** з `--`, і шукає `re.search(r"(?<![A-Z])LOGIN", …)`.

Зонд (запущено мною, той самий regex, реалістичні форми):

```text
SILENT  current style (NOLOGIN)                              ← правильно, зараз зелений
FIRES   CREATE ROLE fetch_01 LOGIN PASSWORD '...' IN ROLE …  ← спрацює
FIRES   ALTER ROLE collector_fetcher WITH LOGIN;             ← спрацює
SILENT  create role fetch_01 login password '...' …          ← ПРОПУСК (lowercase)
SILENT  CREATE USER fetch_01 PASSWORD '...' IN ROLE …        ← ПРОПУСК (USER = LOGIN без слова)
FIRES   … NOLOGIN', r); -- LOGIN roles TBD                   ← ХИБНА тривога (trailing comment)
```

**Відповідь: частково.** Вартовий спрацює на найімовірнішій формі (та, що вже стоїть прикладом у
коментарі `roles.sql:5`, — `CREATE ROLE … LOGIN`), але мовчить на двох цілком реалістичних
формах і не бачить нічого поза `roles.sql` (нового `sql/*.sql`, міграції, `init-secrets.sh`).
Тобто твердження deps-файла «ця вимога не може бути виконана тихо» і рядок картки «падає,
щойно у `roles.sql` зʼявиться перша LOGIN-роль» сильніші за фактичну гарантію. Див. S-2.

---

## 7. DoD §18 — дев'ять пунктів

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| 1 | реалізація відповідає одному issue/WP і не містить сторонніх змін | diff обмежений owned files картки + 4 тестові модулі WP-00 (оновлені за прецедентом `9ed5ed8`) + `.github/workflows/ci.yml` (один крок) + `deploy/compose/README.md` (один рядок); єдина зміна поза owned — `engine.py` (`command_timeout`, адитивна), оформлена запитом §4 | partial — зміна у файлі WP-01A ще **не підтверджена власником**; підтвердження має бути до merge |
| 2 | formatter, lint, types, unit/contract/integration tests пройшли | перевірено мною: ruff/format/mypy зелені, 688 unit + 32 integration scaling зелені | evidenced |
| 3 | зміна схеми має migration і compatibility evidence | схема не змінювалась (deps §1 фіксує це навмисно); `migrations/**` не торкнуто | not applicable |
| 4 | зміна timestamp/matching/release contract має temporal/replay evidence | таких змін немає; усі timestamps — через `clock.utcnow`/`resolve_now` WP-01A | not applicable |
| 5 | новий адаптер має manifest/fixtures/golden/coverage | адаптерів немає (out of scope PR1) | not applicable |
| 6 | документація, метрики й runbook оновлені | оновлено: докстрінги CLI/модулів (дуже змістовні), `deploy/compose/README.md`, картка, deps-файл. **Немає:** `docs/workers.md` (ролі, lease/drain, як додати handler) і `docs/runbooks/scale-drain-recover.md` — картка відносить їх до «Docs (етап 5)»; метрики — WP-12. Але контракт M-2 («handle не блокує event loop») зараз живе **тільки** в докстрінгу `handlers.py`, а WP-02/03/04 писатимуть handler-и, читаючи `docs/workers.md` | partial — прийнятно для PR1 за карткою, але `docs/workers.md` має вийти **до** старту доменних WP, не «колись на етапі 5» |
| 7 | secret scan чистий | gitleaks у pre-commit і CI job `secrets` (успадковано від WP-00); PR1 додатково вводить `handlers.redact()` для текстів винятків перед записом у `crawl_jobs.last_error_message`/`dead_letters`/логи, з тестами | evidenced (сканів на цій гілці в CI ще не було — див. п. 9) |
| 8 | reviewers' findings позначені `fixed` / `accepted with owner/date` / `not applicable` з аргументом | gate 2: F0 `not applicable` (аргумент), F1 `accepted` (WP-01A PR2, 2026-09-23), F2–F6 `fixed`; gate 3: H-1 і M-1…M-3 `fixed`, M-4 `fixed контрактом` (фактично `accepted`), L-1…L-4, L-6 `fixed`, L-5 `accepted` (WP-01A PR2, 2026-09-23), L-7 частково `fixed`, решта `accepted` (WP-01D PR3, 2026-09-23). Усі мої перевірки в §6.1 підтвердились | evidenced |
| 9 | PR злитий лише після CI та required review; commit SHA і release evidence зафіксовані | гілка не push-иться до merge-gate, тож job `integration-postgres` із **новим** кроком `pytest -m integration tests/integration/scaling` ще жодного разу не виконувався в CI; гілка не перебазована на `main` | partial — умова merge-gate, не порушення; але після ребейзу докази компоуза треба переробити (S-6) |

---

## 8. Знахідки

| # | Severity | Файл:рядок | Суть | Кому |
|---|---|---|---|---|
| **S-1** | **medium** | `docs/plan/cards/WP-01D.md` (версія на `main`), PR1 вимога 7; `docker-compose.yml:102-107` | **Вимога 7 PR1 (дешевий liveness-probe) не виконана і не віднесена до іншого етапу.** У жодному з трьох звітів PR1 її немає; у гілці healthcheck лишається `python -m collector.api.health <deps>` — повний старт інтерпретатора + зʼєднання з БД на кожну пробу. Через це WP-00 PR3 був змушений підняти `timeout` до 15 с і `start_period` до 90 с (`x-healthcheck-budget` на `main`), і поки вимога не закрита, цей борг платить уся CI. Пропуск пояснюється тим, що вимогу додано на `main` після merge-base гілки, але власником її названо саме WP-01D. **Що зробити:** при ребейзі або реалізувати (наприклад, файл-heartbeat у tmpfs, який пише той самий цикл, що й `_heartbeat`, + `CMD test $(( $(date +%s) - $(stat -c %Y /tmp/live) )) -lt N`; або мінімальний HTTP-endpoint у процесі), або записати явне перенесення в PR2/PR3 з owner і датою — і синхронно вирішити долю тесту `test_application_healthchecks_name_a_critical_dependency`, який §7.5 закріплює, тобто зміна семантики має йти разом зі зміною ТЗ/тесту. | реалізатор WP-01D |
| **S-2** | **low** | `tests/unit/test_compose_config.py::test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` | **Тест-вартовий §13 слабший, ніж обіцяно.** Зонд (§6.3) показує: мовчить на `create role … login …` (lowercase) і на `CREATE USER … PASSWORD …` (LOGIN без ключового слова), бачить лише `roles.sql` (не бачить нового `sql/*.sql`, міграції чи `init-secrets.sh`) і хибно спрацьовує на trailing-коментар `-- LOGIN`. Deps-файл і картка стверджують сильніше («не може бути виконана тихо»). **Що зробити:** `re.IGNORECASE`, додати патерни `CREATE\s+USER`, `WITH\s+LOGIN`, `rolcanlogin`, сканувати весь каталог `sql/` (+ імена секретів у `deploy/compose/secrets/`), і/або перевести вартового на **позитивний** інваріант: `secrets` сервісів `*-worker`/`scheduler` не збігаються з секретом `migrate-postgres`. | реалізатор WP-01D |
| **S-3** | **low** | `docs/plan/reports/WP-01D/implementation-pr1.md:403-407` і `:228`; `:433` | **Дві внутрішні суперечності у звіті реалізатора.** (а) Ризик 4 стверджує «два testcontainers-контейнери у повному локальному прогоні», тоді як рядок F6 у тому самому документі каже «fixed, max 1 контейнер» — і мій прогін підтверджує F6; ризик 4 треба прибрати або переписати. Те саме застаріле формулювання лишилось у докстрінгу `tests/integration/scaling/conftest.py:6-9`. (б) §«Dependency-запити» п. 2 називає LOGIN-ролі «середнім пріоритетом», тоді як deps-файл §2 і картка кажуть «високий, блокер pilot». | реалізатор WP-01D |
| **S-4** | **low** | `src/collector/workers/config.py` (`command_timeout` docstring); `runtime.py:391-421`, `:452-491`, `:776-792` | **Таймаути покривають не всі шляхи.** `statement_timeout` ставиться лише в транзакції heartbeat і у fallback-гілці `_ensure_pool`; `_claim`, `_report`, `_set_status`, `_check_ready`, `_release_leases` його не ставлять. Нижню межу для них дає лише драйверний `command_timeout`, який задається **тільки** на CLI-шляху (`_run_worker`/`_run_scheduler`) — вбудований запуск (`run(install_signals=False)`, який модуль явно підтримує) не має ні того, ні іншого. Наслідок обмежений (сторож фенсить незалежно), але докстрінг `command_timeout` «жоден запит не має жити довше за вікно fencing» істинний лише для CLI. **Що зробити:** або ставити `statement_timeout` у спільному хелпері сесії, або пом'якшити формулювання і назвати обмеження явно. | реалізатор WP-01D (можна в PR3) |
| **S-5** | **low** | `docs/plan/cards/WP-01D.md` розділ «Відомі ризики» | **Залишковий TOCTOU singleton-тіку (M-4) не в таблиці ризиків.** Він закритий контрактом («тік мусить бути ідемпотентним») і вартовим-тестом, але сам контракт тепер зобовʼязує **майбутні** WP (насамперед WP-03 з `enqueue` discovery-jobs). Такі зобовʼязання мають жити у видимому місці, а не лише в докстрінгу `scheduler.py`. **Що зробити:** третій рядок у «Відомі ризики» зі статусом `mitigated (контракт + тест-вартовий)`, owner WP-01D PR3 / доменні WP, дата. | реалізатор WP-01D |
| **S-6** | **info** | гілка загалом | **Ребейз попереду, і він змінює докази.** merge-base `ba57363`; на `main` уже WP-00 PR3. Обидві сторони змінили **сім** спільних файлів: `docker-compose.yml` (обидві правлять anchor `x-worker` і сервіс `scheduler` — гілка додає `environment`/`secrets`, `main` додає `healthcheck: <<: *healthcheck-budget`), `tests/unit/test_compose_config.py`, `tests/unit/test_compose_config_adversarial.py`, `deploy/compose/README.md`, `.github/workflows/ci.yml`, `docs/plan/cards/WP-01D.md`, `docs/acceptance/traceability.md` — конфлікти очікувані в усіх. Після ребейзу: (а) в картці зʼявиться вимога 7 (S-1); (б) healthcheck-бюджет стане `timeout 15s / start_period 90s`, і `up -d --wait` треба перезапустити — worker тепер підключається до PostgreSQL на boot, тобто readiness контейнера подовжилась порівняно з placeholder-ом; (в) на `main` зʼявився сервіс `gui` — перевірити, що `SECRET_CONSUMERS` і `test_api_has_no_secrets_and_runtime_has_only_the_dsn` його коректно охоплюють. | реалізатор WP-01D |

Розбіжностей «код проти ТЗ», які вимагали б зміни ТЗ або ADR, я не знайшов: єдине свідоме
відхилення (§13, спільний DSN) оформлене як тимчасове, з owner, датою, dependency-запитом,
рядком у картці й тест-вартовим — тобто як відхилення, а не як нова норма. ADR тут не потрібен
саме тому, що §13 змінювати не збираються — інваріант треба повернути.

---

## 9. Вердикт

### `changes_requested`

**Що зроблено добре.** Це той рідкий випадок, коли каркас справді є каркасом: runtime не має
власного SQL черги (усе через репозиторії WP-01A), не тримає транзакцію під `handle`, а `Task` —
знімок без ORM і без session, тож доменні WP фізично не зможуть протекти в транзакційні межі
runtime. Інваріант «lease належить instance» витриманий наскрізь. Шість вимог PR1 із семи
`evidenced`, усі пʼять названих карткою тестів існують і доведені мутаціями. Три найнеприємніші
речі в такому runtime — зависла база, «живий, але німий» worker і гарячий concurrency понад
розмір pool-у — закриті **після** код-рев'ю, причому H-1 закрито трьома незалежними шарами, а
тест на нього відтворює саме зависання (`asyncio.sleep(3600)` без винятку), а не підставлений
виняток. Межі власного scope названі чесно: per-instance барʼєр не виданий за role-wide, а
порушення §13 задокументоване жорсткіше, ніж того вимагав би мінімум.

**Що блокує.** Одна вимога картки PR1 (`7 — дешевий liveness-probe`) має статус `missing`: її
немає ні в коді, ні в «Що не перевірено», ні в переліку перенесеного. Пояснення (вимогу додано
на `main` після merge-base) поважне, але вимога адресована саме власнику runtime, і поки вона
відкрита, кожна CI-збірка платить `start_period: 90s`. Це виправляється або кількома рядками
коду, або одним абзацом явного перенесення з owner/датою — але має бути зроблено до merge, бо
після ребейзу вимога зʼявиться в картці й мовчазний пропуск стане невидимим боргом.

Решта — `partial`/`low`: пористий тест-вартовий §13 (S-2), дві суперечності всередині звіту
реалізатора (S-3), неповне покриття таймаутами (S-4), M-4 поза таблицею ризиків (S-5).
Критичних і high-знахідок без статусу немає: F1 (`high`, §13) оформлений як `accepted` з owner
**WP-01A PR2** і датою `2026-09-23`, що DoD §18 п. 8 прямо дозволяє.

**Що потрібно для `accept`:** закрити S-1 (реалізацією або явним перенесенням із owner/датою),
підсилити вартового S-2, прибрати суперечності S-3; S-4 і S-5 можна віднести в PR3 із
owner/датою. Після ребейзу на `main` — повторити `docker compose --profile core --profile
workers up -d --wait` і `pytest -m "not live"`, бо чинні докази зняті до появи
`x-healthcheck-budget` і сервісу `gui`.

`missing`: **1** (вимога 7 картки PR1).
`partial`: **6** (§17.2 загалом; §16.3 scale-down; §7.6/FR-032 role-wide барʼєр; §15 capacity
ceiling; DoD п. 1, 6, 9 — рахую як три рядки одного блоку, звідси 6 унікальних предметів).
`critical/high` без рішення: **0**.
