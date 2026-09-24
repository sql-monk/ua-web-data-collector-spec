# WP-01D PR1b — runtime на per-component LOGIN-ролях (блокер pilot §13)

Branch: `wp/01d-1b-runtime-role-dsn` (worktree `.worktrees/wp-01d-1b`, від `main` `bc1af47`).
Картка: `docs/plan/cards/WP-01D.md`, розділ PR1b. Контекст: `docs/plan/deps/WP-01A-to-WP-01D.md`
§1, §2, §6; ризик I-1 `docs/plan/reports/WP-01A/security-pr2.md`; ТЗ §13, §7.6.

Коміти:

```text
fa8ff3a fix(wp-01d): keep collector_migrate out of runtime code (role_connections guard)
5cf9693 docs(wp-01d): per-component DB roles in workers.md/runbook; close §13 and drain risks in card
942f65f feat(wp-01d): per-component postgres_dsn_<component> for scheduler and workers (§13)
ebe038e feat(wp-01d): runtime verifies its own LOGIN role and releases leases via queue.release
```

## Що зроблено

| Вимога PR1b | Реалізація | Тест |
|---|---|---|
| 1. Кожен runtime-сервіс монтує лише свій `postgres_dsn_<component>` як `COLLECTOR_POSTGRES_DSN_FILE`; спільний `postgres_dsn` жоден runtime не монтує; мапінг картки | `docker-compose.yml`: anchor `x-worker` без `secrets`/DSN (`environment: &worker-env`), кожен `*-worker` і `scheduler` — власні `environment` + `secrets`. Той самий мапінг у коді: `collector.workers.roles.DB_ROLE_BY_WORKER_ROLE`, `SCHEDULER_DB_ROLE` | `tests/unit/test_compose_config.py::test_runtime_services_use_only_their_own_login_dsn_13`, `::test_worker_anchor_carries_no_dsn_so_a_new_worker_cannot_inherit_one`, `::test_postgres_dsn_secret_is_scoped_to_migration_and_queue_consumers`; `tests/unit/test_compose_config_adversarial.py::test_each_secret_has_exactly_documented_consumers`, `::test_api_has_no_secrets_and_runtime_has_only_the_dsn`; `tests/unit/workers/test_db_login.py::test_compose_mounts_the_dsn_of_the_role_the_process_verifies`, `::test_mapping_matches_the_card` |
| 2. `export-worker` → `collector_scheduler` (варіант (а)); ризик у картці | мапінг `WorkerRole.EXPORT → collector_scheduler` з коментарем; рядок «Відомі ризики» картки WP-01D (accepted, owner — власник експорту / WP-01A PR3) | `test_mapping_matches_the_card`; `tests/integration/scaling/test_worker_runtime.py::test_bootstraps_missing_pool_from_spec_defaults` (EXPORT-runtime під `collector_scheduler` робить bootstrap pool + реєстрацію + drain) |
| 3. `verify_runtime_login` при старті `WorkerRuntime` і `scheduler` до першого claim; `RoleLoginError` → ненульовий exit, повідомлення без DSN/пароля; rollback `COLLECTOR_WORKER_PLACEHOLDER=1` | новий `src/collector/workers/login.py::verify_component_login` = `verify_runtime_login` (WP-01A) + збіг із мапінгом ролі компонента. Перший крок `WorkerRuntime._boot` (до bootstrap pool, реєстрації, claim) і `SchedulerRuntime.run` (до advisory lease). CLI `worker`/`scheduler`: `_run_runtime` ловить `RoleLoginError` → `role login: …` у stderr, exit 1. Placeholder-шлях до БД не доходить (як і раніше) | integration `tests/integration/scaling/test_runtime_login.py` (9 тестів): superuser → відмова без жодного запису; член `collector_migrate` → відмова; роль чужого компонента → відмова; під `collector_fetcher` стартує, claim-ить, `pg_stat_activity` = `collector_fetcher`/`usesuper=false`; scheduler під superuser/fetcher → відмова до lease, під `collector_scheduler` — тікає; CLI під superuser (worker, scheduler) і з DSN parser-а → exit 1, `role login:`, без traceback і пароля. Unit: `tests/unit/workers/test_db_login.py` (CLI без БД, синтетичний пароль будується в рантаймі) |
| 4. Вартовий `test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` і зонди → позитивний тест §13 | вартовий, `LOGIN_ROLE_SQL`, `_strip_sql_comments` і обидва зонди видалено; додано два позитивні тести (рядок 1) | див. рядок 1 |
| 5. `_release_leases` → `queue.release(job_id, owner)`; тест: drain не змінює `attempt` і не пише полів помилки | `runtime.py::_release_leases` викликає `queue_repo.release` для **всіх** незавершених tasks (включно з останньою спробою); `IMMEDIATE_RETRY_POLICY`/`DRAIN_TIMEOUT_ERROR_CODE` прибрано (зовнішніх використань не було) | `test_worker_runtime.py::test_drain_timeout_returns_the_lease_to_the_queue` (`pending`, `attempt` = до claim, `last_error_*` = NULL, `not_before <= now`, claimable); `test_worker_runtime_adversarial.py::test_drain_timeout_on_the_last_attempt_never_quarantines_a_job_nobody_failed` (`max_attempts=1`: одразу `pending`, `attempt=0`, без dead letter, наступний claim дає `attempt=1`) |
| 6. Publisher loop outbox — out of scope | не чіпалось; вимога лишається за WP-01B (deps §7) | — |
| Тести scaling під LOGIN-ролями | `tests/integration/scaling/conftest.py`: фікстури `login_urls` (той самий шлях, що `db roles --with-login`: секрети в `tmp_path`, `load_role_logins`, `apply_database_roles`; після тесту — NOLOGIN без пароля), `role_engine`, `role_sessions`, `runtime_sessions` (`collector_fetcher`), `scheduler_engine`/`scheduler_sessions`. Усі `WorkerRuntime(...)`/`SchedulerRuntime(...)` у 4 модулях scaling переведено з superuser на ці фікстури; superuser `pg_sessions`/`pg_engine` лишився лише для підготовки даних, перевірок і `pg_terminate_backend`/`AdvisoryLease`-примітиву | увесь `tests/integration/scaling/**` (44 тести) |

Додатково:

- **Top-level `secrets:`** — блок із картки WP-00 PR4 п.2 вставлено дослівно одразу після
  `postgres_dsn` (перевірено програмно: відступ картки 3 пробіли знято, решта символів
  ідентична — `blk in compose == True`).
- **Документація:** `docs/workers.md` (login check у життєвому циклі, drain через `release`,
  розділ 7.1 «Ролі БД (§13)» з мапінгом), `docs/runbooks/worker-recovery.md` (діагностика
  `role login`, крок 4 drain), картка WP-01D «Відомі ризики» (§13 і drain — closed у PR1b,
  export-worker — accepted), абзац про вартового замінено.
- Для `test_migrate_role_is_not_referenced_by_runtime_code` (WP-01A) назва
  `collector_migrate` у нових докстрінгах runtime замінена на «міграційна роль».

## Команди та вивід

`uv sync --frozen`:

```text
Checked 66 packages in 6ms
```

`uv run ruff check . && uv run ruff format --check . && uv run mypy src`:

```text
All checks passed!
253 files already formatted
Success: no issues found in 78 source files
```

`uv run pre-commit run --from-ref HEAD~2 --to-ref HEAD` (для gitleaks; до останнього fix-коміту)
і `--files` на змінені docs/compose:

```text
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
```

`uv run pytest -m "not live"` (фінальний коміт `fa8ff3a`; `-rfs --tb=short -p no:logging`):

```text
FAILED tests/integration/scaling/test_worker_runtime.py::test_self_fencing_cancels_active_tasks_when_the_database_stops_confirming_the_lease
FAILED tests/unit/test_compose_config.py::test_secrets_are_files_with_examples_and_gitignored
2 failed, 996 passed, 23 skipped, 8 warnings in 939.68s (0:15:39)
```

- `test_secrets_are_files_with_examples_and_gitignored` — **очікуване до rebase на WP-00 PR4**:
  тест WP-00 вимагає `deploy/compose/secrets/<name>.example` для кожного оголошеного секрету, а
  сім `postgres_dsn_<component>.example` створює WP-00 PR4 (`deploy/**` для мене forbidden):

  ```text
  E           AssertionError: немає postgres_dsn_scheduler.example
  ```

- `test_self_fencing_cancels_active_tasks_…` — відомий флейк класу A
  (`docs/plan/reports/WP-01D/flaky-scaling-tests.md`): під час прогону на хості паралельно
  працював стек іншого агента (`docker ps`: 13 контейнерів, CPU load 76–83 %), логи фіксували
  паузи event loop до 35 с (`worker.event_loop_stalled lag_seconds=35.658`) при вікні fencing
  1.5 с. Той самий тест зелений в окремому прогоні scaling нижче. 23 skipped — GUI e2e без
  піднятого стека і `test_network_blocked` на Windows (як і на `main`).

`uv run pytest -m integration tests/integration/scaling` (фінальний коміт, після спаду навантаження):

```text
44 passed in 101.08s (0:01:41)
```

Попередні прогони scaling на тому самому коді (до fix-коміту, який змінює лише докстрінги) під
навантаженням: `1 failed, 43 passed in 186.79s`; `44 passed in 591.59s`;
`1 failed, 43 passed in 936.60s` — з падінням
`test_self_fencing_fires_when_the_database_hangs_without_raising` (`assert sessions.hangs >= 1`,
`0 >= 1`) — дослівно клас A з `flaky-scaling-tests.md` §2 (baseline `main` дає той самий збій).

Нові тести окремо (`uv run pytest -m integration tests/integration/scaling/test_runtime_login.py -q`):

```text
.........                                                                [100%]
9 passed in 28.03s
```

Unit compose + workers (`tests/unit/test_compose_config*.py tests/unit/workers`):

```text
FAILED tests/unit/test_compose_config.py::test_secrets_are_files_with_examples_and_gitignored
1 failed, 163 passed in 2.98s
```

`docker compose config --quiet`:

```text
exit=0
```

Рендер (`docker compose --profile core --profile workers --profile browser config --format json`,
секрет / `COLLECTOR_POSTGRES_DSN_FILE` / `COLLECTOR_WORKER_STOP_GRACE_SECONDS`):

```text
browser-worker ['postgres_dsn_fetcher'] /run/secrets/postgres_dsn_fetcher 90
discovery-worker ['postgres_dsn_fetcher'] /run/secrets/postgres_dsn_fetcher 90
export-worker ['postgres_dsn_scheduler'] /run/secrets/postgres_dsn_scheduler 90
fetch-worker ['postgres_dsn_fetcher'] /run/secrets/postgres_dsn_fetcher 90
maintenance-worker ['postgres_dsn_scheduler'] /run/secrets/postgres_dsn_scheduler 90
migrate-postgres ['postgres_dsn'] /run/secrets/postgres_dsn None
parse-worker ['postgres_dsn_parser'] /run/secrets/postgres_dsn_parser 90
projector-worker ['postgres_dsn_projector'] /run/secrets/postgres_dsn_projector 90
scheduler ['postgres_dsn_scheduler'] /run/secrets/postgres_dsn_scheduler None
translation-worker ['postgres_dsn_translation'] /run/secrets/postgres_dsn_translation 90
```

`docker compose --profile core --profile workers up -d --wait`, `--scale fetch-worker=4`,
`down -v` — **не виконувались: перевіряється після rebase на WP-00 PR4.** Без PR4 немає ні
файлів `postgres_dsn_<component>`, ні `collector db roles --with-login` у `migrate-postgres`,
тож ролі лишаються NOLOGIN без пароля, і runtime-сервіси коректно не стартують. Окремо: на
хості вже працював стек з тим самим project name `collector` (інший агент), і `down -v`
знищив би його volumes.

## Що не перевірено

- **Acceptance на стеку** (`SELECT usename, usesuper FROM pg_stat_activity JOIN pg_user …` після
  `init-secrets.sh` + `up --wait`) — після rebase на WP-00 PR4. На рівні БД той самий інваріант
  перевіряє `test_worker_starts_and_claims_as_collector_fetcher_without_superuser`
  (`{("collector_fetcher", False)}` у `pg_stat_activity`).
- **CI job `integration (PostgreSQL 18)`** не запускався (push робить оркестратор). Очікування:
  нові тести не skip-аються — вони в `tests/integration/scaling` з `pytestmark = integration`,
  крок `pytest -m integration tests/integration/scaling` у CI вже є, `COLLECTOR_TEST_REQUIRE_DOCKER=1`
  забороняє skip без БД; trust-auth CI приймає LOGIN-ролі з будь-яким паролем, а admin
  `collector_ci` — superuser (тести відмови під superuser працюють). Loopback для
  integration-маркера дозволяє `tests/conftest.py`.
- `docker compose up -d --no-recreate --scale fetch-worker=4` — див. вище.

## Ризики

1. **Порядок merge.** PR1b не можна зливати в `main` раніше за WP-00 PR4 (або без rebase на
   нього): (а) `test_secrets_are_files_with_examples_and_gitignored` червоний без `.example`;
   (б) без `--with-login` і секретів runtime-сервіси не стартують (свідомо fail-loud).
2. **Конфлікти з WP-00 PR4** поза ідентичним блоком `secrets:`: WP-00 PR4 теж редагує
   `tests/unit/test_compose_config.py::test_postgres_dsn_secret_is_scoped_to_migration_and_queue_consumers`
   (секрети/команда `migrate-postgres`) і, ймовірно, `SECRET_CONSUMERS` у
   `test_compose_config_adversarial.py` (`migrate-postgres` споживає всі сім per-role). Моя
   частина там — `allowed = {"migrate-postgres"}`, докстрінг і нові ключі `postgres_dsn_<component>`
   без `migrate-postgres`. Розвʼязання механічне: об'єднати (до кожного per-role набору додати
   `migrate-postgres`).
3. **Флейки класу A** (fencing-вікно 1.5 с проти пауз event loop під чужим навантаженням) лишаються
   як у `flaky-scaling-tests.md`; PR1b їх не погіршує: зміни в runtime — один запит при старті і
   `release` замість `retry` на drain, фікстури лише додають `ALTER ROLE` до/після тесту.
   Тести не послаблювались.
4. **export-worker під `collector_scheduler`** — ширші права, ніж треба експорту (запис у control
   plane). Записано в картці як accepted; поки handler — `NoopHandler`.
5. **Перевірка збігу ролі строга:** DSN іншої runtime-ролі (навіть коректної за правами)
   тепер фатальний. Нова роль/перерозподіл компонентів вимагає оновити
   `DB_ROLE_BY_WORKER_ROLE` і compose-мапінг разом (тест `test_compose_mounts_the_dsn_of_the_role_the_process_verifies`
   ловить розбіжність).

## Як вимкнути або відкотити

- Оперативно без перебудови image: `COLLECTOR_WORKER_PLACEHOLDER=1` — worker/scheduler стають
  placeholder-процесами WP-00 і в БД не ходять (runbook `worker-recovery.md` §6).
- Повний відкат: `git revert fa8ff3a 5cf9693 942f65f ebe038e` (повертає спільний
  `postgres_dsn`, вартового PR1 і `retry`-обхід на drain). Не відкочувати лише `942f65f` без
  `ebe038e`: runtime під спільним superuser-DSN за дизайном не стартує.

## Dependency-запити

Немає. Зміни в `src/collector/persistence/**`, `sql/**`, `migrations/**`, `deploy/**` не
знадобились: `verify_runtime_login`, `RoleLoginError`, `queue.release`, `apply_database_roles` і
`load_role_logins` WP-01A використано як є. Out of scope п.6 (publisher loop) лишається в картці
WP-01B.

## After rebase on WP-00 PR4

Гілку перебазовано на `wp/00-4-role-dsn-secrets` (спершу на `1eb3e27`, потім на docs-only tip
`8775f7e` — дві нові WP-00 зміни стосуються лише `docs/plan/reports/WP-00/*-pr4.md`). Гілку
WP-00 не змінено. Нові хеші PR1b:

```text
9d93e62 docs(wp-01d): implementation report PR1b
a52ac0b fix(wp-01d): keep collector_migrate out of runtime code (role_connections guard)
343759d docs(wp-01d): per-component DB roles in workers.md/runbook; close §13 and drain risks in card
5968d34 feat(wp-01d): per-component postgres_dsn_<component> for scheduler and workers (§13)
8c65e33 feat(wp-01d): runtime verifies its own LOGIN role and releases leases via queue.release
```

Як розвʼязано конфлікти:

- `docker-compose.yml`, блок top-level `secrets:` — злився автоматично (hunk ідентичний), лишилась
  версія WP-00.
- `tests/unit/test_compose_config_adversarial.py::SECRET_CONSUMERS` — об'єднання: `postgres_dsn`
  → лише `migrate-postgres`; кожен `postgres_dsn_<component>` → `migrate-postgres` + runtime-сервіси
  за мапінгом PR1b (`api_ro`/`export_ro` → лише `migrate-postgres`).
- `tests/unit/test_compose_config.py`: assertions WP-00 про секрети `migrate-postgres` взяті як є, моя
  частина — `allowed = {"migrate-postgres"}`. Я видалив хелпер `_strip_sql_comments` разом із
  вартовим, а нові тести WP-00 PR4 (`test_postgres_init_scripts_are_mounted_read_only_for_wp_01a`,
  `test_postgres_init_revokes_public_on_app_and_service_databases`) його використовують, тож його
  повернуто без змін. Тести WP-00 не змінено й не послаблено.

### Команди та вивід (tip `a47ad99` до docs-only rebase; код ідентичний поточному)

`uv run ruff check . && uv run ruff format --check . && uv run mypy src`:

```text
All checks passed!
258 files already formatted
Success: no issues found in 78 source files
```

`uv run pytest -m "not live"`:

```text
1034 passed, 23 skipped, 8 warnings in 1283.01s (0:21:23)
```

(23 skipped — GUI e2e без стека `gui` і `test_network_blocked` на Windows, як на `main`.)

`uv run pytest -m integration tests/integration/scaling`:

```text
44 passed in 232.89s (0:03:52)
```

`./deploy/compose/secrets/init-secrets.sh` (у worktree; файли в `.gitignore`, після перевірки
видалені, не комітились):

```text
gen   postgres_dsn (з postgres_password)
gen   postgres_dsn_api_ro (random, роль collector_api_ro)
gen   postgres_dsn_export_ro (random, роль collector_export_ro)
gen   postgres_dsn_fetcher (random, роль collector_fetcher)
gen   postgres_dsn_parser (random, роль collector_parser)
gen   postgres_dsn_projector (random, роль collector_projector)
gen   postgres_dsn_scheduler (random, роль collector_scheduler)
gen   postgres_dsn_translation (random, роль collector_translation)
```

`docker compose --profile core --profile workers build` (образ `collector:dev` зібрано з цього
worktree, щоб у стеку був код PR1b), потім `docker compose --profile core --profile workers up -d --wait`:

```text
 Container collector-migrate-postgres-1 Exited
 Container collector-ensure-mongo-1 Exited
 Container collector-fetch-worker-1 Healthy
 Container collector-fetch-worker-2 Healthy
 Container collector-maintenance-worker-1 Healthy
 Container collector-export-worker-1 Healthy
 Container collector-projector-worker-1 Healthy
 Container collector-minio-1 Healthy
 Container collector-scheduler-1 Healthy
 Container collector-parse-worker-2 Healthy
 Container collector-parse-worker-1 Healthy
 Container collector-translation-worker-1 Healthy
 Container collector-discovery-worker-1 Healthy
```

`migrate-postgres` (exit 0):

```text
login enabled: collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro
```

Acceptance: `pg_stat_activity` × `pg_roles` у БД `collector` (запит від адмін-`psql`, тому
рядок `psql | collector | t` — це сам запит, а не runtime):

```text
       application_name       |        usename        | rolsuper | conns
------------------------------+-----------------------+----------+-------
 collector-sch                | collector_scheduler   | f        |     2
 collector-worker-discovery   | collector_fetcher     | f        |     1
 collector-worker-export      | collector_scheduler   | f        |     1
 collector-worker-fetch       | collector_fetcher     | f        |     2
 collector-worker-maintenance | collector_scheduler   | f        |     1
 collector-worker-parse       | collector_parser      | f        |     2
 collector-worker-projector   | collector_projector   | f        |     1
 collector-worker-translation | collector_translation | f        |     1
 psql                         | collector             | t        |     1
(9 rows)
```

Логи старту (фрагмент): `"role": "fetch", "db_role": "collector_fetcher", "event": "worker.db_login"`,
`"role": "export", "db_role": "collector_scheduler", "event": "worker.db_login"`,
`"lease": "scheduler", "db_role": "collector_scheduler", "event": "scheduler.db_login"`.

`docker compose --profile core --profile workers up -d --no-recreate --scale fetch-worker=4 --wait`:

```text
 Container collector-fetch-worker-3 Healthy
 Container collector-fetch-worker-4 Healthy
 Container collector-fetch-worker-1 Healthy
 Container collector-fetch-worker-2 Healthy
```

```text
    role     | status | count
-------------+--------+-------
 discovery   | ready  |     1
 export      | ready  |     1
 fetch       | ready  |     4
 maintenance | ready  |     1
 parse       | ready  |     2
 projector   | ready  |     1
 translation | ready  |     1

    application_name    |      usename      | rolsuper | count
------------------------+-------------------+----------+-------
 collector-worker-fetch | collector_fetcher | f        |     4
```

Пароль `collector_fetcher` з DSN-секрету не знайдено ні в `docker inspect` усіх контейнерів стека,
ні в `docker compose logs` (`grep -c` → `0`, `0`).

`docker compose --profile core --profile workers down -v`: усі мережі, контейнери й volumes стека
видалено (після нього `collector-*` контейнерів — `0`, `collector_*` volumes — `0`); стек
`puluj-g-*` не чіпався.

### Що змінилось у висновках вище

- Ризик 1 (порядок merge) і очікуване падіння `test_secrets_are_files_with_examples_and_gitignored`
  знято: після rebase тест зелений; PR1b тепер стоїть поверх WP-00 PR4, тож зливати його треба після
  (або разом з) `wp/00-4-role-dsn-secrets`.
- Ризик 2 (конфлікти) розвʼязано, як описано вище.
- «Acceptance на стеку» з розділу «Що не перевірено» виконано: усі runtime-процеси підключені
  не-superuser ролями згідно з мапінгом. CI job `integration (PostgreSQL 18)` як і раніше не
  запускався (push — за оркестратором).

## Fixes after gate 3

Вхід: `docs/plan/reports/WP-01D/security-pr1b.md` (approve; S-1 medium, S-2…S-4 low, S-5/S-6 info),
`docs/plan/reports/WP-01D/code-review-pr1b.md` (approve; 3 low), вказівки оркестратора 2026-09-24.

**Rebase (S-6).** WP-00 PR4 злито в `main` (PR #7, `ae63917`), гілку `wp/00-4-role-dsn-secrets`
видалено. Виконано `git fetch && git rebase --onto origin/main 8775f7e wp/01d-1b-runtime-role-dsn`:
усі 8 комітів PR1b (включно з `c32f4e1` тестувальника і `4201e1e` рев'юерів — зміст не змінено)
лягли на `8be045d` без конфліктів (нові хеші: `c32f4e1` → `193928f`, `4201e1e` → `32eb25a`).

| Знахідка | Рішення | Де / тест |
|---|---|---|
| S-1 (medium) `export-worker` під `collector_scheduler` | лишається тимчасово; у картці WP-01D «Відомі ризики» — жорсткий тригер «закрити до merge першого реального export handler (WP-11A) або до pilot, що раніше», owner WP-11A / WP-01A, дата 2026-09-24, перелік зайвих прав зі звіту безпеки; рядок §13 уточнено: «closed, крім export-worker — §13 для exporter закрито не повністю» | тест-вартовий `tests/unit/workers/test_db_login.py::test_export_worker_keeps_scheduler_role_only_while_its_handler_is_noop` (падає, якщо `resolve_handler(EXPORT)` не `NoopHandler` або в `src/` зареєстровано фабрику для EXPORT, поки export мапиться на `collector_scheduler`/монтує `postgres_dsn_scheduler`) |
| S-2 (low) перевірявся лише `current_user` | виняток оркестратора з owned files: `verify_runtime_login` вимагає `session_user = current_user` і `is_superuser = off` | `tests/integration/scaling/test_runtime_login.py::test_worker_refuses_a_privileged_session_with_a_default_role` (superuser-логін з `server_settings role=collector_fetcher` → відмова до будь-якого запису) |
| S-3 (low) неповний контроль членства | той самий виняток: `privileged_memberships` відмовляє також за членство в будь-якій іншій `collector_*` і в `pg_read_all_stats`, `pg_read_all_settings`, `pg_stat_scan_tables`, `pg_monitor`, `pg_create_subscription`, `pg_checkpoint`, `pg_use_reserved_connections`, `pg_database_owner` (обґрунтування — докстрінг `PRIVILEGED_BUILTIN_ROLES` і `docs/plan/deps/WP-01D-to-WP-01A.md` §6) | `::test_worker_refuses_membership_in_another_component_or_monitoring_role[collector_scheduler, collector_api_ro, pg_read_all_stats, pg_monitor]`; `tests/integration/postgres/test_role_logins.py` зелений |
| S-4 (low) trust-auth у CI | **accepted**, owner **WP-13** (hardening CI/deploy: SCRAM + згенерований пароль admin, тест «неправильний пароль → відмова», порт `127.0.0.1:5432`), дата 2026-09-24. Перевірка ролі від trust не залежить; SCRAM/автентифікацію підтверджено на Docker-стеку | — |
| S-5 (info) browser-worker ділить `postgres_dsn_fetcher` | **accepted** (мапінг узгоджено з карткою/§13); окрему `collector_browser` розглянути з browser handler (WP-02 PR3), 2026-09-24 | — |
| CR low #1 backoff 30 с у тесті повного циклу | runtime у тесті отримує заморожений годинник `CYCLE_CLOCK = PAST + 1 h`: claim бачить jobs, покладені на `PAST`, а `retry` ставить `not_before = CYCLE_CLOCK + backoff`, тож `n=1` не claim-иться вдруге незалежно від тривалості тесту; додано `retried.not_before > CYCLE_CLOCK` і `handler.started.count(1) == 1` | `test_runtime_login_adversarial.py::test_full_worker_cycle_fits_the_grants_of_the_mapped_login_role` |
| CR low #2 порядок teardown | фікстура `running` залежить від `role_engine`: pytest скасовує runtime раніше, ніж `dispose()` engine-ів і `ALTER ROLE … NOLOGIN` | `tests/integration/scaling/conftest.py` |
| CR low #3 `_cli_env` | `COLLECTOR_POSTGRES_DSN_FILE: None` у env `CliRunner` (прибирає змінну хоста) | `test_runtime_login.py::_cli_env` |

Інше: `pre-commit run --all-files` падав на `docs/plan/reports/WP-01D/security-pr1b.md:23` (MD036,
`**approve**` як єдиний рядок абзацу) — рядок переписано на `Вердикт: **approve**.`; зміст звіту
не змінено.

### Команди та вивід

`uv run ruff check . && uv run ruff format --check . && uv run mypy src`:

```text
All checks passed!
265 files already formatted
Success: no issues found in 78 source files
```

`uv run pytest -m "not live"`:

```text
1068 passed, 23 skipped, 9 warnings in 767.40s (0:12:47)
```

`uv run pytest -m integration tests/integration/scaling tests/integration/postgres/test_role_logins.py`:

```text
87 passed in 497.06s (0:08:17)
```

`uv run pre-commit run --all-files` — усі hooks `Passed` після виправлення MD036
(`markdownlint-cli2........Passed`).

Docker-стек (один раз після rebase): `init-secrets.sh` (12 файлів `gen`),
`docker compose --profile core --profile workers up -d --wait --build` — усі сервіси `Healthy`,
`migrate-postgres` `Exited (0)`, `ensure-mongo` `Exited`. `pg_stat_activity` × `pg_roles`:

```text
       application_name       |        usename        | rolsuper | conns
------------------------------+-----------------------+----------+-------
 collector-sch                | collector_scheduler   | f        |     2
 collector-worker-discovery   | collector_fetcher     | f        |     1
 collector-worker-export      | collector_scheduler   | f        |     1
 collector-worker-fetch       | collector_fetcher     | f        |     2
 collector-worker-maintenance | collector_scheduler   | f        |     1
 collector-worker-parse       | collector_parser      | f        |     2
 collector-worker-projector   | collector_projector   | f        |     1
 collector-worker-translation | collector_translation | f        |     1
 psql                         | collector             | t        |     1
(9 rows)
```

(`psql | collector | t` — сам адмін-запит.) Пароль `collector_fetcher` у `docker inspect` / логах:
`leaks inspect=0 logs=0`. `down -v` → `containers=0 volumes=0`; згенеровані секрети видалено, не
комітились; стек `puluj-g-*` не чіпався. Жорсткіші перевірки S-2/S-3 не заважають реальним
LOGIN-ролям стека: усі runtime-процеси стартували.
