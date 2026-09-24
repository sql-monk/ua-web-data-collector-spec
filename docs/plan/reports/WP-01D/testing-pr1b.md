# WP-01D PR1b — незалежне тестування (runtime на per-component LOGIN-ролях, §13)

| Поле | Значення |
|---|---|
| Worktree / branch | `.worktrees/wp-01d-1b`, `wp/01d-1b-runtime-role-dsn` (tip до тестів `bac6776`) |
| База стеку | `wp/00-4-role-dsn-secrets` = `8775f7e` = `git merge-base` (нових комітів у WP-00 PR4 немає) |
| Diff PR1b | `git diff wp/00-4-role-dsn-secrets...wp/01d-1b-runtime-role-dsn` (19 файлів, +1288/−232) |
| Контракт | картка `WP-01D.md` «PR1b» п.1–6; deps `WP-01A-to-WP-01D.md` §1, §2, §6; ТЗ §13, §7.6 |
| Рівні §16.1 | 15 Scaling, 3 Integration, 1 Unit |
| Хост | Windows 11, Docker Desktop; паралельно працювали стек `puluj-g-*` (не чіпався) і `pytest` іншої сесії у `.worktrees/wp-00-4` (PID 37472/37608) — це і є джерело пауз event loop нижче |

`implementation-pr1b.md` прочитано лише після власного прогону (розділ «Звірка» наприкінці).

## Команди та дослівний вивід

`uv sync --frozen`:

```text
Checked 66 packages in 5ms
```

`uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy src` (до доданих тестів):

```text
All checks passed!
260 files already formatted
Success: no issues found in 78 source files
```

Після доданих тестів `uv run ruff check tests/` → `All checks passed!`; файл відформатовано `ruff format`.

`docker compose config --quiet`:

```text
compose exit=0
```

`uv run pytest -m "not live"` (код PR1b без моїх тестів; `-q -p no:cacheprovider`), хвіст:

```text
SKIPPED [6] tests\e2e\test_gui_runtime_contract.py:167: gui не відповідає на http://127.0.0.1:80 — запустіть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
... (ще 11 рядків тих самих GUI e2e skip)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49: ...(COLLECTOR_E2E_REQUIRED=1 у CI job `docker` після `up -d --wait`)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:62: ...
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
1034 passed, 23 skipped, 9 warnings in 1717.39s (0:28:37)
exit=0
```

23 skipped — GUI e2e без стека `gui` і `test_network_blocked` на Windows; жоден скіп не стосується
PR1b (у scaling/unit-workers скіпів немає, `grep skip|xfail` по нових тестах — порожньо).

`uv run pytest -m integration tests/integration/scaling -q -rs --durations=10` (з моїми тестами, 65
тестів; хост під навантаженням іншої сесії):

```text
............F....F...............................F......
...
============================ slowest 10 durations =============================
134.43s setup    tests/integration/scaling/test_runtime_login.py::test_worker_refuses_to_start_as_superuser
49.35s call     tests/integration/scaling/test_runtime_login_adversarial.py::test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role[fetch]
45.89s call     tests/integration/scaling/test_runtime_login_adversarial.py::test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role[discovery]
...
3 failed, 62 passed in 1444.17s (0:24:04)
exit=1
```

Розбір трьох падінь:

1. `test_runtime_login_adversarial.py::test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role[parse]`
   (мій тест) — `Failed: стан не настав за 15.0 с: parse: три jobs відпрацьовано`; у логах того самого
   тесту `worker.fenced ... 'seconds_since_heartbeat': 39.778, 'fence_after_seconds': 30.0` — event loop
   стояв ~40 с. Голодування хоста, не права ролі (permission denied немає). Тест посилено: `lease_seconds=180`
   і `timeout=60` на кожне очікування стану (все одно чекає на стан, а не на час).
2. `test_runtime_login_adversarial.py::test_drain_never_releases_a_lease_that_another_owner_took_over`
   (мій тест) — `assert [] == [UUID('01a0d0...7623fe0ce30')]`: **помилка мого тесту**, не продукту —
   bootstrap-pool fetch дав 8 слотів, і після `recover_expired_leases` runtime сам перехопив job вільним
   слотом раніше за «other-instance». Виправлено `make_pool(concurrency=1)` (так само, як у
   `test_heartbeat_does_not_extend_a_foreign_lease`).
3. `test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising` —
   `Failed: стан не настав за 15.0 с: task у роботі`; у логах `worker.event_loop_stalled lag_seconds=1.968`,
   `worker.fenced ... seconds_since_heartbeat 3.453, fence_after_seconds 1.5`. **Відомий флейк класу A**
   (`flaky-scaling-tests.md`), не дефект PR1b.

Ізольований повтор усіх трьох (після виправлення тестів 1–2):

```text
uv run pytest -q ".../test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising" \
  ".../test_runtime_login_adversarial.py::test_drain_never_releases_a_lease_that_another_owner_took_over" \
  ".../test_runtime_login_adversarial.py::test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role[parse]"
...                                                                      [100%]
3 passed in 28.95s
exit=0
```

Фінальний прогін login-тестів (реалізатора + мої) після виправлень:

```text
uv run pytest -q -m integration tests/integration/scaling/test_runtime_login_adversarial.py tests/integration/scaling/test_runtime_login.py
..............................                                           [100%]
30 passed in 123.41s (0:02:03)
exit=0
```

### Docker acceptance

`deploy/compose/secrets/init-secrets.sh`:

```text
gen   minio_root_password (random)
copy  minio_root_user (from example — non-secret)
gen   mongo_keyfile (random keyfile)
gen   mongo_root_password (random)
gen   postgres_password (random, для DSN)
gen   postgres_dsn (з postgres_password)
gen   postgres_dsn_api_ro (random, роль collector_api_ro)
gen   postgres_dsn_export_ro (random, роль collector_export_ro)
gen   postgres_dsn_fetcher (random, роль collector_fetcher)
gen   postgres_dsn_parser (random, роль collector_parser)
gen   postgres_dsn_projector (random, роль collector_projector)
gen   postgres_dsn_scheduler (random, роль collector_scheduler)
gen   postgres_dsn_translation (random, роль collector_translation)
skip  postgres_password (exists)
```

`docker compose --profile core --profile workers up -d --wait --build` (хвіст):

```text
 Container collector-migrate-postgres-1 Exited
 Container collector-export-worker-1 Healthy
 Container collector-projector-worker-1 Healthy
 Container collector-api-1 Healthy
 Container collector-postgres-1 Healthy
 Container collector-mongo-1 Healthy
 Container collector-maintenance-worker-1 Healthy
 Container collector-minio-1 Healthy
 Container collector-translation-worker-1 Healthy
 Container collector-ensure-mongo-1 Exited
 Container collector-fetch-worker-1 Healthy
 Container collector-fetch-worker-2 Healthy
 Container collector-discovery-worker-1 Healthy
 Container collector-parse-worker-2 Healthy
 Container collector-parse-worker-1 Healthy
 Container collector-scheduler-1 Healthy
exit=0
```

`pg_stat_activity × pg_roles` (адмін-`psql` у контейнері `postgres`; рядок `psql | t` — сам запит):

```text
        usename        | rolsuper |       application_name       | conns
-----------------------+----------+------------------------------+-------
 collector_scheduler   | f        | collector-sch                |     2
 collector_fetcher     | f        | collector-worker-discovery   |     1
 collector_scheduler   | f        | collector-worker-export      |     1
 collector_fetcher     | f        | collector-worker-fetch       |     2
 collector_scheduler   | f        | collector-worker-maintenance |     1
 collector_parser      | f        | collector-worker-parse       |     2
 collector_projector   | f        | collector-worker-projector   |     1
 collector_translation | f        | collector-worker-translation |     1
 collector             | t        | psql                         |     1
(9 rows)
```

Логи стека (`docker compose --profile core --profile workers logs`, 913 рядків): 10 подій
`worker.db_login`/`scheduler.db_login` з очікуваними ролями, наприклад

```text
export-worker-1     | { "role": "export", "db_role": "collector_scheduler", "event": "worker.db_login", ... }
scheduler-1         | {"lease": "scheduler", "db_role": "collector_scheduler", "event": "scheduler.db_login", ... }
```

Пошук у логах паролів із кожного `postgres_dsn*`/`postgres_password`: `leaks=0`; `postgresql://` — 0;
`permission denied|"level": "error"|Traceback` — 0.

`docker compose --profile core --profile workers up -d --no-recreate --scale fetch-worker=4`:

```text
 Container collector-fetch-worker-3 Started
 Container collector-fetch-worker-4 Started
exit=0
wait exit=0
```

```text
 collector_fetcher     | f        | collector-worker-fetch       |     4
    role     | status | count
-------------+--------+-------
 discovery   | ready  |     1
 export      | ready  |     1
 fetch       | ready  |     4
 maintenance | ready  |     1
 parse       | ready  |     2
 projector   | ready  |     1
 translation | ready  |     1
```

Adversarial на живому стеку — `collector worker fetch` у контейнері `maintenance-worker` (змонтований лише
`postgres_dsn_scheduler`):

```text
docker compose --profile core --profile workers run --rm --no-deps -T maintenance-worker collector worker fetch
role login: runtime-підключення під 'collector_scheduler', а цей процес має працювати під 'collector_fetcher': змонтуйте DSN свого компонента (postgres_dsn_<component>), §13
exit=1
```

`docker compose --profile core --profile workers down -v` → `exit=0`, контейнерів `collector*` — 0.
Згенеровані секрети видалено (у `deploy/compose/secrets/` лишились лише `*.example` та `init-secrets.sh`),
не комітились. `puluj-g-*` не чіпався.

Рендер compose (`config --format json`, профілі core+workers+browser): кожен runtime-сервіс має рівно один
`postgres_dsn_<component>` за мапінгом, `migrate-postgres` — `postgres_dsn` + усі сім per-role, `api`,
`ensure-mongo`, stateful — жодного DSN; merge `<<: *worker-env` зберіг `COLLECTOR_WORKER_STOP_GRACE_SECONDS`
і `COLLECTOR_WORKER_PLACEHOLDER`.

## Acceptance-пункт → тест → результат

| # | Пункт (картка PR1b / завдання) | Тест(и) | Результат |
|---|---|---|---|
| 1 | Кожен runtime монтує лише свій `postgres_dsn_<component>`; `postgres_dsn` — лише `migrate-postgres`; мапінг картки | `tests/unit/test_compose_config.py::test_runtime_services_use_only_their_own_login_dsn_13`, `::test_worker_anchor_carries_no_dsn_so_a_new_worker_cannot_inherit_one`, `::test_postgres_dsn_secret_is_scoped_to_migration_and_queue_consumers`; `tests/unit/workers/test_db_login.py::test_compose_mounts_the_dsn_of_the_role_the_process_verifies`, `::test_mapping_matches_the_card`; рендер `config --format json` | pass (мутація M5 → 3 red) |
| 2 | `export-worker` під `collector_scheduler`, повний цикл черги працює | `test_runtime_login_adversarial.py::test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role[export]`; `test_worker_runtime.py::test_bootstraps_missing_pool_from_spec_defaults`; стек: `collector-worker-export` → `collector_scheduler`, `rolsuper=f` | pass |
| 3a | superuser DSN → exit ≠ 0 до першого claim/запису у `worker_instances`, без DSN/пароля | `test_runtime_login.py::test_worker_refuses_to_start_as_superuser`, `::test_cli_exits_1_under_the_migration_superuser`; **новий** `test_runtime_login_adversarial.py::test_process_exits_nonzero_as_superuser_before_any_write[worker fetch / worker export / scheduler]` (справжній процес, DEBUG-логи, 0 рядків `worker_instances`/`worker_pools`, 0 claim) | pass (M1, M4 → red) |
| 3b | Член `collector_migrate` → відмова | `test_runtime_login.py::test_worker_refuses_to_start_as_a_member_of_collector_migrate`; **новий** `::test_process_exits_nonzero_as_member_of_collector_migrate` | pass |
| 3c | Роль, що не збігається з `WorkerRole` → відмова | `test_runtime_login.py::test_worker_refuses_the_login_role_of_another_component`, `::test_cli_worker_exits_1_with_the_dsn_of_another_component`; **новий** `::test_process_exits_nonzero_with_a_login_role_that_does_not_match_its_worker_role` (6 кейсів, включно з read-only `collector_export_ro`/`collector_api_ro` і scheduler під fetcher); стек `run maintenance-worker collector worker fetch` → exit 1 | pass (M1, M4 → red) |
| 3d | `verify_runtime_login` викликається і в `scheduler` | `test_runtime_login.py::test_scheduler_refuses_superuser_and_foreign_role` (0 спроб advisory lease), `::test_scheduler_runs_as_collector_scheduler`; новий `[argv5-collector_fetcher]`, `[argv2]` (scheduler) | pass (M4 → red) |
| 3e | Rollback `COLLECTOR_WORKER_PLACEHOLDER=1` | `tests/unit/test_cli_compose_commands.py::test_worker_command_falls_back_to_placeholder_under_rollback_flag`, `::test_scheduler_command_falls_back_...`; **новий** `::test_placeholder_rollback_ignores_the_database_even_with_a_refused_dsn` (superuser DSN, процес живий, 0 з'єднань `collector-%`, 0 записів) | pass |
| 4 | Вартового прибрано, позитивний тест §13 зелений | вартовий і зонди відсутні в `tests/unit/test_compose_config.py`; позитивний тест — п.1 | pass |
| 5a | Drain через `queue.release`: `attempt` компенсовано, полів помилки немає | `test_worker_runtime.py::test_drain_timeout_returns_the_lease_to_the_queue`; **новий** `test_full_worker_cycle_...` (release під кожною роллю: `('pending', None, 0)`, `last_error_* = NULL`) | pass (M3 → red) |
| 5b | Drain на останній спробі не карантинить | `test_worker_runtime_adversarial.py::test_drain_timeout_on_the_last_attempt_never_quarantines_a_job_nobody_failed` | pass |
| 5c | Чужий lease на drain → помилка, чужий lease не чіпається | **новий** `test_runtime_login_adversarial.py::test_drain_never_releases_a_lease_that_another_owner_took_over` (lease перехоплено до heartbeat; після drain `('leased','other-instance')`, `lease_expires_at` і `attempt=2` незмінні); контракт репозиторію — `tests/integration/postgres/test_queue_release.py` (WP-01A) | pass |
| 6 | Права ролі достатні для повного циклу (bootstrap pool+audit, register, claim, heartbeat, complete, retry, quarantine+dead letter, release) під fetcher/parser/projector/translation/scheduler | **новий** `test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role[discovery,fetch,browser,parse,projector,translation,export,maintenance]` | pass (8/8) |
| 7 | Acceptance стеку: усі runtime — не-superuser | `pg_stat_activity × pg_roles` вище (+ `--scale fetch-worker=4`) | pass |
| 8 | Нові integration-тести не пропускаються мовчки в CI `integration (PostgreSQL 18)` | `.github/workflows/ci.yml:126-132` — крок `pytest -m integration tests/integration/scaling`; `COLLECTOR_TEST_REQUIRE_DOCKER: "1"` (`ci.yml:101`) перетворює skip фікстур на fail (`tests/integration/postgres/conftest.py:80-83`); у нових файлах немає `skip`/`xfail`, `pytestmark = integration` | pass |
| — | Out of scope п.6 (publisher loop) | не чіпалось | n/a |

## Рівень §16.1 → тести

| Рівень | Тести |
|---|---|
| 15 Scaling | `tests/integration/scaling/test_runtime_login.py` (9), `test_runtime_login_adversarial.py` (21, новий), `test_worker_runtime*.py`, `test_scheduler_singleton*.py` — усі runtime під LOGIN-ролями; стек `--scale fetch-worker=4` під `collector_fetcher` |
| 3 Integration | ті самі проти реального PostgreSQL 18 (testcontainers локально / service container у CI); subprocess-тести CLI; `docker compose` стек |
| 1 Unit | `tests/unit/workers/test_db_login.py`, `tests/unit/test_compose_config.py`, `tests/unit/test_compose_config_adversarial.py`, `tests/unit/test_cli_compose_commands.py` (placeholder) |

## Додані тести

`tests/integration/scaling/test_runtime_login_adversarial.py` (21 тест-кейс):

- `test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role[×8 WorkerRole]` — без попереднього pool
  (bootstrap + audit), success / retry (`handler_error`) / quarantine (`bad_input` + dead letter), ≥2
  heartbeat під активним lease, drain по grace → `release`; `lost_leases == fences == 0`.
- `test_drain_never_releases_a_lease_that_another_owner_took_over`.
- `test_process_exits_nonzero_as_superuser_before_any_write[×3]`,
  `test_process_exits_nonzero_as_member_of_collector_migrate`,
  `test_process_exits_nonzero_with_a_login_role_that_does_not_match_its_worker_role[×6]` — справжній
  `python -m collector.cli` з `COLLECTOR_LOG_LEVEL=DEBUG`: exit ≠ 0, `role login:` у stderr, без Traceback,
  без DSN і `:<password>@` у stdout+stderr, 0 рядків `worker_instances`/`worker_pools`, 0 claim.
- `test_placeholder_rollback_ignores_the_database_even_with_a_refused_dsn[×2]`.

Продуктивний код не змінювався.

## Mutation-перевірка

Кожна мутація — тимчасова, повернута `git checkout -- <file>` (після кожної `git status --short` показував
лише новий тестовий файл).

| # | Мутація | Тест | Результат на мутанті |
|---|---|---|---|
| M1 | `src/collector/workers/runtime.py:279` — виклик `verify_component_login` у `_boot` замінено на константу | `test_process_exits_nonzero_with_a_login_role_that_does_not_match_its_worker_role[argv0-collector_projector]`, `test_process_exits_nonzero_as_superuser_before_any_write[argv0]` | `subprocess.TimeoutExpired: ... 'collector.cli', 'worker', 'fetch' timed out after 90 seconds` ×2 → `2 failed in 196.20s` |
| M3 | `runtime.py:781` — `queue_repo.release` замінено старим шляхом `queue_repo.retry(..., error_code="drain_timeout", zero backoff)` | `test_full_worker_cycle_...[projector]`, `[export]` | `AssertionError: assert ('retry', None, 1) == ('pending', None, 0)` ×2 → `2 failed in 24.00s` |
| M4 | `src/collector/workers/scheduler.py:155` — виклик `verify_component_login` прибрано | `test_process_exits_nonzero_with_a_login_role_that_does_not_match_its_worker_role[argv5-collector_fetcher]` | `subprocess.TimeoutExpired: ... 'collector.cli', 'scheduler' timed out after 90 seconds` → `1 failed in 106.35s` |
| M5 | `docker-compose.yml` — `fetch-worker` монтує `postgres_dsn` замість `postgres_dsn_fetcher` | `tests/unit/test_compose_config.py`, `tests/unit/workers/test_db_login.py` | `AssertionError: fetch-worker: міграційний DSN у runtime (§13)` та ін. → `3 failed, 73 passed` |

## Знахідки

| # | Severity | file:line | Опис |
|---|---|---|---|
| T-1 | info (відомий флейк, не PR1b) | `tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising` | Упав 1 раз у повному scaling-прогоні під чужим навантаженням (`event_loop_stalled`, fence 1.5 с), зелений в ізольованому повторі. Клас A з `flaky-scaling-tests.md`. |
| T-2 | low (тестове покриття, закрито) | `tests/integration/scaling/test_runtime_login.py:211-234` | CLI-відмови реалізатора йшли через `CliRunner` в-процесі й не перевіряли відсутність записів у БД та витік у DEBUG-логах справжнього процесу; не було тестів на «чужу» роль для `scheduler`/`maintenance`/`export` і read-only ролі, на повний цикл прав під parser/projector/translation/scheduler, на drain із чужим lease та rollback із забороненим DSN. Закрито доданими тестами. |
| T-3 | info | `.github/workflows/ci.yml:88` | CI `integration (PostgreSQL 18)` працює з `POSTGRES_HOST_AUTH_METHOD: trust`: SCRAM-автентифікація LOGIN-ролей паролями з `postgres_dsn_<component>` у CI не перевіряється (лише локально/на стеку — підтверджено вище). Перевірка ролі (`current_user`, атрибути, членство) від цього не залежить. |
| T-4 | info (accepted risk) | `src/collector/workers/roles.py` (`WorkerRole.EXPORT: SCHEDULER_DB_ROLE`) | `export-worker` під `collector_scheduler` — ширші права, ніж потрібно; ризик записаний у картці (owner WP-11A / WP-01A PR3). Повний цикл під цією роллю працює. |

Дефектів продуктивного коду PR1b не знайдено. Дві проблеми з першого прогону моїх тестів були в самих тестах
(гонка вільного слоту; замалий таймаут під голодуванням хоста) і виправлені без послаблення перевірок.

## Звірка з `implementation-pr1b.md`

- Вимоги 1–5 і мапінг — підтверджено моїм прогоном (таблиця вище); out of scope п.6 — так.
- `pytest -m "not live"`: заявлено `1034 passed, 23 skipped` — у мене те саме `1034 passed, 23 skipped`.
- Scaling: заявлено `44 passed`; у мене 44 тести реалізатора + 21 мій, з 3 падіннями в повному прогоні (1 —
  відомий флейк, 2 — дефекти моїх тестів, виправлені), усі зелені ізольовано. Опис флейків класу A у звіті
  реалізатора збігається з побаченим.
- Acceptance на стеку (`pg_stat_activity`, `--scale fetch-worker=4`, відсутність пароля в логах) —
  відтворено з тим самим результатом.
- Заява «CI job не запускався» — так; статично підтверджено, що скіпу там бути не може (п.8).

## Вердикт

Вердикт: **pass** — дефектів продуктивного коду PR1b немає.
