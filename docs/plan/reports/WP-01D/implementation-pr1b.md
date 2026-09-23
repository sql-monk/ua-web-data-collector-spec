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
