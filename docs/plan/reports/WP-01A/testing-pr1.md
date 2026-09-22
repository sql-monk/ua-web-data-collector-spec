# WP-01A PR1 — звіт незалежного тестування (`wp/01a-1-control-queue`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR1 «control plane, job queue, origin limiter, worker pools» |
| Branch / worktree | `wp/01a-1-control-queue` / `.worktrees/wp-01a` |
| Картка | `docs/plan/cards/WP-01A.md` — «Спільні вимоги» + «PR1» |
| ТЗ | §7.2, §7.6, §9.1, §13, §15, §16.1 п.3; REVIEW.md R-28, R-32, R-53 |
| Середовище | Windows 11, uv 0.12.13, CPython 3.13, Docker 29.8.0, PostgreSQL 18.6 (`postgres:18@sha256:86c951e0…`) |
| Базовий commit | `6272884` (HEAD гілки на момент прогону) |
| **Вердикт** | **pass** |

Порядок роботи дотримано: картку і ТЗ прочитано, власний прогін виконано, adversarial-тести
дописано та перевірено мутаціями, і лише після цього прочитано
`docs/plan/reports/WP-01A/implementation-pr1.md` (звірка — наприкінці звіту).

---

## 1. Команди перевірки картки та дослівний вивід

```text
$ uv sync --frozen
Checked 60 packages in 5ms
```

```text
$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
=== FORMAT ===
146 files already formatted
=== MYPY ===
Success: no issues found in 60 source files
```

`alembic` проти зовнішнього PostgreSQL 18 (`postgresql://tester:***@127.0.0.1:55438/collector_cli`,
контейнер тестувальника `wp01a-tester-pg`, окремий від compose-стека WP-00):

```text
$ uv run alembic upgrade head && uv run alembic check
--- upgrade head (1st) ---
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_control_queue, WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.
EXIT=0
--- alembic check ---
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.schemas
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.tables
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.types
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.constraints
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.defaults
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.comments
No new upgrade operations detected.
EXIT=0
--- upgrade head (2nd, idempotent) ---
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
EXIT=0
```

```text
$ uv run alembic downgrade base && psql -c "\dt" && uv run alembic upgrade head
--- downgrade base ---
INFO  [alembic.runtime.migration] Running downgrade 0001_control_queue -> , WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.
EXIT=0
              List of tables
 Schema |      Name       | Type  | Owner
--------+-----------------+-------+--------
 public | alembic_version | table | tester
(1 row)
--- re-upgrade ---
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_control_queue, WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.
```

Повний набір (після додавання моїх тестів):

```text
$ uv run pytest -m "not live" -q
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
576 passed, 1 skipped, 6 warnings in 65.48s (0:01:05)
```

```text
$ uv run pytest -m integration tests/integration/postgres -q      # testcontainers
(до моїх тестів)  36 passed in 80.58s (0:01:20)

$ COLLECTOR_TEST_POSTGRES_ADMIN_DSN=postgresql://tester:***@127.0.0.1:55438/postgres \
  COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres -q
.......................................................................  [100%]
71 passed in 52.54s
```

Обидва шляхи, яких вимагав план тестування, працюють: **testcontainers** (локальний прогін) і
**зовнішній DSN** (`COLLECTOR_TEST_POSTGRES_ADMIN_DSN`, шлях CI із service container). З
`COLLECTOR_TEST_REQUIRE_DOCKER=1` skip перетворюється на fail — тобто мовчазного пропуску
integration-набору в CI бути не може.

Перевірка обов'язкових indexes картки — безпосередньо в БД:

```text
$ psql -c "SELECT indexdef FROM pg_indexes WHERE tablename IN (...)"
 CREATE INDEX ix_crawl_jobs_status_not_before_priority ON public.crawl_jobs USING btree (status, not_before, priority, job_id)
 CREATE UNIQUE INDEX uq_crawl_jobs_idempotency_key ON public.crawl_jobs USING btree (idempotency_key)
 CREATE INDEX ix_crawl_jobs_lease_expires_at ON public.crawl_jobs USING btree (lease_expires_at) WHERE ((status)::text = 'leased'::text)
 CREATE INDEX ix_origin_rate_permits_origin_lease_expires_at ON public.origin_rate_permits USING btree (origin, lease_expires_at)
 CREATE INDEX ix_worker_instances_role_status_last_heartbeat_at ON public.worker_instances USING btree (role, status, last_heartbeat_at)
 CREATE INDEX ix_audit_log_created_at ON ONLY public.audit_log USING btree (created_at)
 CREATE UNIQUE INDEX uq_crawl_runs_running_full ON public.crawl_runs USING btree (source_id) WHERE (((status)::text = 'running'::text) AND ((kind)::text = 'full'::text))
```

Усі шість обов'язкових indexes картки + FR-002 partial unique присутні дослівно (R-32).

---

## 2. Acceptance-пункт → тест → результат

| Пункт картки PR1 | Тест | Результат |
|---|---|---|
| clean DB → `upgrade head` → `check` → `downgrade` | `test_migrations.py::test_upgrade_check_downgrade_cycle_on_clean_database` + ручний прогін CLI вище | pass |
| `alembic upgrade head` двічі поспіль | **new** `test_adversarial.py::test_upgrade_head_twice_in_a_row_is_idempotent` + ручний прогін | pass |
| `alembic check` без drift; падає при drift | ручна мутація M0 (див. §5) | pass |
| 4 паралельні claimers × 100 jobs → кожен рівно раз | `test_queue.py::test_four_parallel_claimers_claim_each_job_exactly_once` | pass |
| heartbeat чужого lease відхиляється | `test_queue.py::test_heartbeat_by_non_owner_is_rejected` | pass |
| expired lease → повторний claim іншим worker | `test_queue.py::test_expired_lease_is_recovered_and_reclaimed_by_another_worker` | pass |
| повторний enqueue з тим самим ключем не дублює | `test_queue.py::test_enqueue_with_same_idempotency_key_returns_existing_job`; **new** `…::test_concurrent_enqueue_of_same_key_yields_one_row_without_unique_violation` | pass |
| після `max_attempts` — dead letter і `quarantined` | `test_queue.py::test_retry_backoff_then_max_attempts_quarantines_with_dead_letter`; **new** `…::test_retry_after_max_attempts_writes_exactly_one_dead_letter` | pass |
| 8 паралельних `acquire_permit`, concurrency 1 → рівно 1 slot | `test_limiter.py::test_eight_parallel_acquirers_get_exactly_one_slot_with_concurrency_one` (+ варіант 3) | pass |
| 0.2 rps за 10 с ≤ 3 | `test_limiter.py::test_rate_limit_0_2_rps_grants_at_most_three_in_ten_seconds` (рівно 3) | pass |
| expired permit відновлює slot | `test_limiter.py::test_expired_permit_restores_slot_and_release_is_idempotent`; **new** `…::test_expiry_frees_concurrency_slot_even_after_rate_token_spent` | pass |
| `block_origin` блокує до `until` | `test_limiter.py::test_block_origin_denies_until_deadline`; **new** `…::test_block_origin_with_live_permits_denies_new_but_keeps_existing` | pass |
| pools: stale revision відхиляється | `test_pools.py::test_stale_revision_is_rejected`; **new** `…::test_scale_command_with_stale_revision_leaves_no_partial_writes`, `…::test_scale_command_with_foreign_pool_revision_is_rejected` | pass |
| `scale_command` недозволений перехід відхиляється | `test_pools.py::test_scale_command_disallowed_transition_is_rejected`; **new** `…::test_scale_command_terminal_and_skipping_transitions_are_rejected` | pass |
| ролі: `collector_api_ro` INSERT → permission denied | `test_roles.py::test_read_only_roles_cannot_insert`; **new** `test_role_connections.py::*` (окремі з'єднання) | pass |
| FR-002: один running `full` run на джерело | `test_control_plane.py::test_only_one_running_full_crawl_run_per_source`; **new** `test_adversarial.py::test_parallel_start_full_run_grants_exactly_one` | pass |
| жодного JSONB payload (R-27) | `test_metadata.py::test_no_domain_jsonb_…`, `test_migrations.py::test_models_match_card_contracts` | pass |
| ролі й GRANT задокументовані | `src/collector/persistence/postgres/sql/roles.sql` (таблиця ролей у шапці) + `deploy/compose/postgres/init/README.md` | pass |
| CI job `integration-postgres` із pinned digest | `.github/workflows/ci.yml` (перевірено читанням; локально прогнано той самий шлях — зовнішній DSN) | pass (не запускався в GitHub) |

---

## 3. Рівні §16.1 → тести

| Рівень §16.1 (призначення картки) | Тести | Кількість |
|---|---|---|
| 3 Integration (PostgreSQL з нуля) | `tests/integration/postgres/test_migrations.py`, `test_queue.py`, `test_limiter.py`, `test_pools.py`, `test_control_plane.py`, `test_roles.py`, `test_cli_db.py`, **new** `test_adversarial.py`, **new** `test_role_connections.py` | 71 |
| 1 Unit (repository logic без БД) | `tests/unit/persistence/postgres/test_metadata.py`, `test_partitions.py`, `test_policies.py`, `test_config.py`, `test_cli_db.py` | 31 |
| Ізоляція мережі (§16.1 базова вимога) | `tests/conftest.py` allow-hosts `127.0.0.1/::1`; testcontainers host примусово `127.0.0.1` | — |
| Чиста схема на тест | `conftest.py`: template DB (`upgrade head` + партиції + ролі) → `CREATE DATABASE … TEMPLATE` на кожен тест | — |

Усього `-m "not live"`: **576 passed, 1 skipped** (був 541 + 35 доданих).

---

## 4. Додані тести

`tests/integration/postgres/test_adversarial.py` (18 тестів):

| Тест | Сценарій |
|---|---|
| `test_heartbeat_after_lease_expiry_contract` | heartbeat після закінчення lease — **фіксує контракт**: власник ще може продовжити, доки lease не відновлено; чужий — ні; після `recover_expired_leases` власнику теж відмовлено (потрібен повторний claim), `attempt` збережено |
| `test_complete_by_foreign_worker_and_double_complete_are_rejected` | `complete` чужого job; подвійний `complete`; жодного dead letter |
| `test_retry_after_max_attempts_writes_exactly_one_dead_letter` | `max_attempts=1`; 6 повторних `retry`/`quarantine` після карантину → **рівно один** dead letter |
| `test_claim_with_empty_job_types_claims_nothing` | `claim([])` нічого не захоплює і не змінює статусів |
| `test_priority_wins_for_identical_not_before` | 5 jobs з однаковим `not_before` → порядок лише за priority DESC, tie-break `job_id` |
| `test_concurrent_enqueue_of_same_key_yields_one_row_without_unique_violation` | 6 сесій із перекриттям транзакцій ставлять той самий idempotency key → один рядок, один `job_id`, **жодного `UniqueViolation` назовні** |
| `test_release_of_unknown_permit_is_false_not_error` | `release_permit` невідомого id → `False`, транзакція не зіпсована |
| `test_double_release_is_idempotent_and_frees_slot_once` | потрійний release → `True/False/False`; concurrency не «розмножився» |
| `test_block_origin_with_live_permits_denies_new_but_keeps_existing` | `block_origin` під час активних permits: активний не відкликано, release приймається, нові відмовлено до `until` |
| `test_long_pause_refill_never_exceeds_burst_capacity` | година простою при 2 rps → burst рівно `capacity_tokens`, не пропорційно часу |
| `test_two_origins_do_not_block_each_other` | блок origin A не впливає на токени/slots/`blocked_until` origin B |
| `test_expiry_frees_concurrency_slot_even_after_rate_token_spent` | crash worker без release: rate token уже витрачено, але після expiry slot вільний і видача проходить |
| `test_parallel_start_full_run_grants_exactly_one` | 5 паралельних `start_run(full)` одного джерела → 1 `started`, 4 `ConflictError`, у БД один running full |
| `test_scale_command_with_stale_revision_leaves_no_partial_writes` | stale revision → ні команди, ні audit-запису, ні зміни desired state |
| `test_scale_command_with_foreign_pool_revision_is_rejected` | revision іншого pool не підходить (pools версіонуються незалежно) |
| `test_scale_command_terminal_and_skipping_transitions_are_rejected` | `requested→applied` (пропуск), відкат у `requested`, невідомий статус, будь-який перехід із термінального `failed`, неіснуючий command_id |
| `test_heartbeat_from_stopped_instance_is_rejected_and_state_unchanged` | heartbeat/`mark_ready` від `stopped` відхилено; `stopped` не потрапляє у stale-sweep і не додає capacity |
| `test_upgrade_head_twice_in_a_row_is_idempotent` | другий `upgrade head` на тій самій БД — no-op, `check` без drift |

`tests/integration/postgres/test_role_connections.py` (17 тестів) — ролі через **окремі
login-з'єднання** (закриває «Ризик 5» звіту реалізації: наявні тести користувались `SET ROLE`
із superuser-сесії):

| Тест | Сценарій |
|---|---|
| `test_read_only_role_has_no_write_privilege_on_any_table` (×2 ролі) | матриця `has_table_privilege` по **всіх 13 таблицях**: SELECT=true, INSERT/UPDATE/DELETE/TRUNCATE=false |
| `test_read_only_role_write_statements_are_denied_at_runtime` (×2) | 7 реальних statements (INSERT/UPDATE/DELETE/TRUNCATE у чергу, pools, permits, audit) → `permission denied` |
| `test_read_only_role_cannot_touch_alembic_version_or_ddl` (×2) | `alembic_version`, CREATE/DROP/ALTER TABLE недоступні |
| `test_parser_cannot_write_audit_log_but_keeps_its_queue` | `collector_parser` не має **жодного** права на `audit_log` (навіть SELECT), але claim/enqueue у `crawl_jobs` працює і DELETE недоступний |
| `test_no_runtime_role_can_update_or_delete_audit_log` (×7 runtime-ролей) | UPDATE/DELETE `audit_log` відхилено для кожної runtime-ролі |
| `test_audit_log_append_only_trigger_applies_to_owner_too` | owner таблиці — `collector_migrate`; UPDATE/DELETE відхиляє **тригер** (`append-only`), не GRANT |
| `test_migrate_role_is_not_referenced_by_runtime_code` | статично: `collector_migrate` згадується лише в `roles.py`/`roles.sql`, жодного runtime-коду |
| `test_role_names_cover_every_component_of_section_13` | перелік ролей §13; `collector_migrate` не входить у `RUNTIME_ROLES` |

Продуктивний код не змінювався: Edit лише у `tests/**`.

---

## 5. Mutation-перевірка

Кожна мутація вносилась у продуктивний код, прогонявся набір, потім `git checkout --`.

| # | Мутація | Файл | Очікування | Результат |
|---|---|---|---|---|
| M0 | додано колонку `tester_drift_probe` у модель `CrawlJob` | `models/queue.py:124` | `alembic check` і drift-тест падають | **red** — `FAILED: New upgrade operations detected: [('add_column', None, 'crawl_jobs', Column('tester_drift_probe', …))]`; `test_upgrade_check_downgrade_cycle_on_clean_database` failed |
| M1a | `SKIP LOCKED` прибрано (`.with_for_update(skip_locked=True)` → `.with_for_update()`) | `repositories/queue.py:141` | падіння | **green (19 passed)** — плайн `FOR UPDATE` серіалізує claimers, але не дає подвійного claim; час прогону виріс з ~18 с до ~149 с. Див. знахідку **I-1** |
| M1b | row lock прибрано повністю | `repositories/queue.py:141` | падіння | **red** — `assert job.attempt == 1` → `assert 2 == 1` (job захоплено двічі) |
| M2 | прибрано перевірку owner/status у heartbeat/complete/retry (`_owned` → лише `job_id`) | `repositories/queue.py:322` | падіння | **red** — 6 failed: `test_heartbeat_by_non_owner_is_rejected`, `test_expired_lease_is_recovered_and_reclaimed_by_another_worker`, `test_operator_quarantine_and_complete_are_terminal`, `test_heartbeat_after_lease_expiry_contract`, `test_complete_by_foreign_worker_and_double_complete_are_rejected`, `test_retry_after_max_attempts_writes_exactly_one_dead_letter` |
| M3 | `release_permit` зроблено не-ідемпотентним (прибрано предикат `released_at IS NULL`) | `repositories/limiter.py:205` | падіння | **red** — 2 failed: `test_expired_permit_restores_slot_and_release_is_idempotent`, `test_double_release_is_idempotent_and_frees_slot_once` (`assert True is False`) |
| M4 | прибрано `not_before <= now` з `claim` | `repositories/queue.py:136` | падіння | **red** — 2 failed: `test_claim_respects_priority_not_before_and_job_type`, `test_retry_backoff_then_max_attempts_quarantines_with_dead_letter` |
| M5 | `acquire_permit` ігнорує `blocked_until` | `repositories/limiter.py:125` | падіння | **red** — 3 failed: `test_block_origin_denies_until_deadline`, `test_block_origin_with_live_permits_denies_new_but_keeps_existing`, `test_two_origins_do_not_block_each_other` |

Після всіх мутацій `git status --short` показує лише два нові файли тестів — продуктивний код
повернуто без залишків.

---

## 6. Знахідки

### Low

**L-1. `request_scale` пропускає невалідний `requested_concurrency` у БД замість типізованої
помилки** — `src/collector/persistence/postgres/repositories/pools.py:292` (`request_scale`), UPDATE полів на `:335`.
`request_scale(requested_concurrency=0)` спершу оновлює `worker_pools.desired_concurrency`, і
викликач отримує сирий `sqlalchemy.exc.IntegrityError`
(`ck_worker_pools_concurrency`), а не `errors.*`. Контракт репозиторіїв («викликачі ловлять
`errors.*` замість `sqlalchemy.exc.*`», `errors.py:1`) тут порушено. Дані захищені CHECK-ами
(`worker_pools.desired_concurrency >= 1`, `scale_commands.requested_concurrency >= 1`), тому це
питання API, не цілісності. Виявлено під час написання
`test_scale_command_terminal_and_skipping_transitions_are_rejected`. Пропозиція для PR2/WP-01D:
`ValueError`/`InvalidTransitionError` у `request_scale`/`upsert_pool` до першого UPDATE.

**L-2. Ідемпотентність `enqueue` залежить від READ COMMITTED** —
`src/collector/persistence/postgres/repositories/queue.py:75` (`enqueue`). `INSERT … ON CONFLICT DO NOTHING`
+ наступний `SELECT` бачить чужий щойно закомічений рядок лише тому, що кожен statement у READ
COMMITTED бере свіжий snapshot. Якщо викликач відкриє транзакцію в `REPEATABLE READ`, шлях
впаде у `NotFoundError` («job … зник між INSERT і SELECT»). Перевірено: у READ COMMITTED
6 конкурентних сесій дають рівно один рядок
(`test_concurrent_enqueue_of_same_key_yields_one_row_without_unique_violation`). Достатньо
рядка в docstring про вимогу до isolation level.

**L-3. Зміни у файлах, якими володіє WP-00** — `tests/unit/test_cli.py`,
`tests/unit/test_cli_adversarial.py`, `tests/unit/test_foundation_config.py`. Процесна,
не технічна: зміни мінімальні, узгоджені з реалізацією (`db migrate` більше не стаб, додано
`db roles`, `sqlalchemy`/`alembic` прибрано з `FORBIDDEN_FOUNDATION_DEPS` за прямою вимогою
картки WP-01A) і супроводжені dependency-запитом `docs/plan/deps/WP-01A-to-WP-00.md`. Жоден
тест не послаблено — навпаки, додано покриття `db migrate`/`db roles`. Потребує підтвердження
owner WP-00 перед merge.

### Informational

**I-1. `SKIP LOCKED` у `claim` — питання пропускної здатності, а не коректності.**
Мутація M1a показала: без `SKIP LOCKED` жоден тест не червоніє (плайн `FOR UPDATE` після
розблокування перевіряє предикат заново і не дає подвійного claim), але час того самого набору
виріс ~8×. Це не дефект реалізації — `SKIP LOCKED` на місці, як вимагає §7.2, — але
характеристика набору: **втрату `SKIP LOCKED` спіймає лише деградація латентності**, не
assertion. Тест на це не додавав свідомо (таймінгові assertions flaky). Повна відсутність row
lock (M1b) спіймана надійно.

**I-2. `ix_crawl_jobs_status_not_before_priority (status, not_before, priority, job_id)` не
обслуговує `ORDER BY priority DESC, not_before, job_id`** у `claim`
(`repositories/queue.py:139`). Порядок колонок index-у заданий карткою дослівно, реалізація
йому відповідає; але через `priority DESC` на третій позиції планувальник робить sort після
фільтра `status`+`not_before`. На робочих обсягах це не критично; якщо §15 (100 jobs/s,
>1 млн pending) стане реальністю — індекс варто переглянути в окремому dependency-запиті.

**I-3. Контракт heartbeat після закінчення lease зафіксовано тестом.** Реалізація свідомо
дозволяє власнику продовжити прострочений, але ще не відновлений lease
(`repositories/queue.py:11-16`). Це безпечно (повторний claim можливий лише через
`status='pending'`), і тепер зафіксовано `test_heartbeat_after_lease_expiry_contract`, щоб
поведінка не змінилась мовчки.

**I-4. Ідемпотентність `audit_log` — best effort** (партиційована таблиця без глобального
unique на `idempotency_key`); реалізатор це задокументував (`repositories/audit.py:35-38`) і
вказав у ризиках. Справжня ідемпотентність дії тримається на unique
`scale_commands.idempotency_key` — перевірено
`test_pools.py::test_request_scale_is_one_transaction_and_idempotent` і
`test_scale_command_with_stale_revision_leaves_no_partial_writes`.

Знахідок severity **high/critical** немає. Flaky-тестів за 8 повних прогонів integration-набору
(testcontainers і зовнішній DSN) не спостерігалось; ретраїв не застосовував.

---

## 7. Звірка з `implementation-pr1.md` (прочитано після прогону)

| Заявлено у звіті реалізації | Підтверджено моїм прогоном |
|---|---|
| 13 таблиць PR1, одна ревізія `0001_control_queue`, повний `downgrade` | так — `\dt` після upgrade/downgrade вище |
| indexes картки (R-32) | так — `pg_indexes` дослівно |
| жодного domain JSONB; JSONB лише `crawl_jobs.args`, `audit_log.before/after_state` | так — `test_models_match_card_contracts` (allowlist) |
| `audit_log` партиційований і append-only через тригер | так; **додатково доведено**, що тригер діє й на owner `collector_migrate` |
| `alembic upgrade head` → `alembic check` без drift | так; **додатково** — двічі поспіль, і drift справді ламає `check` (M0) |
| 4 claimers × 100 jobs, 8 acquirers, 0.2 rps ≤ 3 | так, відтворено |
| `541 passed, 1 skipped` | так (у мене 541 до додавання тестів, 576 після) |
| 36 integration / 31 unit | так (36 → 71 після моїх) |
| CI job `integration-postgres` не запускався | підтверджую; локально прогнано той самий шлях (зовнішній DSN) — 71 passed |
| «Ризик 5: `SET ROLE`-тести не перевіряють реальні login-користувачів» | **закрито** — `test_role_connections.py` працює через окремі login-з'єднання для всіх 8 ролей |
| «Ризик 2: `audit_log` без глобального unique» | підтверджую (I-4), наслідків для коректності дій немає |
| «Ризик 3: `enqueue` не оновлює поля при конфлікті» | підтверджую; `test_enqueue_with_same_idempotency_key_returns_existing_job` це фіксує |

Розбіжностей між заявленим і фактичним не виявлено. Час прогонів у звіті реалізації більший
(224.89 с проти 65.48 с) — різниця машини/навантаження, не розбіжність.

---

## 8. Вердикт

**pass.**

Усі команди перевірки картки зелені; кожен acceptance-пункт PR1 має тест; обидва шляхи
integration (testcontainers і зовнішній DSN) працюють; шість мутацій, з них п'ять надійно
червоніють (шоста, M1a, — задокументована особливість, I-1). Знахідки — three low + four
informational, жодна не блокує merge; L-3 потребує лише підтвердження owner WP-00 за поданим
dependency-запитом.
