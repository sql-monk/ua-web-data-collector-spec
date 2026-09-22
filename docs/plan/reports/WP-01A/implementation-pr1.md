# WP-01A PR1 — звіт реалізації (`wp/01a-1-control-queue`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR1 «control plane, job queue, origin limiter, worker pools» |
| Branch / worktree | `wp/01a-1-control-queue` / `.worktrees/wp-01a` |
| Картка | `docs/plan/cards/WP-01A.md` — «Спільні вимоги» + «PR1» |
| Розділи ТЗ | §5.5, §7.2, §7.6, §9.1 (рядки PR1), §9.3, §13, §15, §18; REVIEW.md R-27, R-28, R-32, R-53 |
| Середовище | Windows 11, uv 0.12.13, CPython 3.13, Docker 29.8, PostgreSQL 18 (`postgres:18@sha256:86c951e0…`) |
| Commits | `84e946f` міграції/моделі, `ed402b7` queue/runs/sources/audit, `f46aa03` limiter, `325fd03` pools, `418fc80` CLI + ролі, `09171ac` тести + CI; після gate 2 — `fix(wp-01a)` (L-1, L-2, I-1, I-2); після gate 3 — `fix(wp-01a)` (H-1, M-1…M-5, 7 low) |

## Що зроблено

### Схема і міграції (`migrations/postgres/**`, `alembic.ini`, `models/**`)

Forward-only ревізії: `0001_control_queue` створює всі 13 таблиць PR1 (§9.1): `sources`,
`source_policy_versions`, `source_routes`, `source_cursors`, `crawl_runs`, `crawl_jobs`,
`dead_letters`, `origin_rate_buckets`, `origin_rate_permits`, `worker_pools`,
`worker_instances`, `scale_commands`, `audit_log`. `downgrade` реалізовано повністю (початкова
ревізія), тож `downgrade base → upgrade head` тестується. Після gate 2 додано `0002_claim_index` — partial index під hot path `claim` (розділ «Виправлення після gate 2»).

- **Enum-колонки — TEXT + CHECK** зі значень shared-контрактів (`SourceState`, `RouteState`,
  `DataDomain`, `WorkerRole`), не PG enum — еволюція без `ALTER TYPE`; `enum_check()` будує
  CHECK прямо з enum-класу, тож розбіжність з контрактом неможлива (тест
  `test_enum_checks_use_shared_contract_values`). Власних «осей стану» не створено.
- **PK — UUID** (UUIDv7 генерує застосунок через `collector.contracts.new_entity_id`); natural
  keys там, де вони природні: `worker_pools.role`, `origin_rate_buckets.origin`.
  `gen_random_uuid()` як DB default — лише в `audit_log` (єдиний дозволений випадок).
- **Усі timestamps — `timestamptz`**; `revision BIGINT` на versioned-ресурсах (`sources`,
  `source_routes`, `source_cursors`, `worker_pools`, `origin_rate_buckets`).
- **Жодного domain JSONB (R-27):** JSONB лише `crawl_jobs.args` (bounded control extension,
  CHECK `octet_length(args::text) <= 8192`) і `audit_log.before_state/after_state`
  (before/after snapshot для impact preview §13). Перевірено тестами і в unit, і в integration.
- **Indexes картки (R-32):** `crawl_jobs(status, not_before, priority, job_id)`; unique
  `crawl_jobs(idempotency_key)`; `crawl_jobs(lease_expires_at) WHERE status='leased'`;
  `origin_rate_permits(origin, lease_expires_at)`; `worker_instances(role, status,
  last_heartbeat_at)`; `audit_log(created_at)` на партиційованій таблиці (успадковується
  кожною партицією). Плюс FR-002: unique `crawl_runs(source_id) WHERE status='running' AND
  kind='full'`.
- **`audit_log` партиційований** RANGE по `created_at` (PK `(audit_id, created_at)`) і
  **append-only**: тригер `audit_log_append_only` кидає `insufficient_privilege` на
  UPDATE/DELETE, плюс runtime-ролі не мають цих GRANT. Партиції створює
  `partitions.ensure_month_partitions` (не міграція); Alembic ігнорує child-таблиці через
  `include_name`. INSERT у місяць без партиції дає зрозумілу помилку PostgreSQL
  («no partition of relation … found for row») — обраний варіант задокументовано.
- `env.py` працює у двох режимах: власний async engine з DSN env (CLI `alembic`) і спільне
  sync-з'єднання з `config.attributes["connection"]` (програмний виклик, транзакція викликача).

### Репозиторії (`src/collector/persistence/postgres/repositories/**`)

Спільний контракт: перший аргумент `AsyncSession`, без `commit()` всередині, час — параметр
`now` (детерміновані тести), помилки — `errors.*`, кожна функція документує transaction
boundary.

- **queue (§7.2):** `enqueue` (`INSERT … ON CONFLICT DO NOTHING` за `idempotency_key` + SELECT →
  повторний enqueue повертає той самий job); `claim(job_types, worker, lease_seconds, limit)` —
  `SELECT … FOR UPDATE SKIP LOCKED` за `status IN (pending, retry) AND not_before <= now`,
  priority DESC, з інкрементом `attempt` і lease; `heartbeat`/`complete`/`retry` — лише власнику
  lease; `retry` — експоненційний backoff з jitter (`BackoffPolicy`), а при `attempt >=
  max_attempts` → `quarantined` + `dead_letters(max_attempts)` в одній транзакції; `quarantine`
  (операторський, без lease) → `dead_letters(quarantine)`; `recover_expired_leases` повертає
  прострочені у `pending` зі збереженням `attempt`; `list_dead_letters` — keyset (§15).
- **crawl_runs (FR-002):** `start_run` через `ON CONFLICT … DO NOTHING` на partial unique index
  → `ConflictError` **без** зіпсованої транзакції викликача; `finish_run` валідовує перехід.
- **limiter (§7.6, R-53):** `acquire_permit` під `SELECT … FOR UPDATE` на bucket: перевірка
  `blocked_until` → refill токенів від `last_refill_at` (стан refill зберігається і при
  відмові) → підрахунок live permits (`released_at IS NULL AND lease_expires_at > now`) → видача
  лише коли доступні **і** token, **і** slot. Rate token списується лише при видачі (відмова
  через concurrency не «спалює» rate). `release_permit`/`expire_permits` ідемпотентні;
  `block_origin` не скорочує довший блок. `PermitDecision` повертає причину і `retry_after`.
- **pools (§7.6):** `upsert_pool` з optimistic revision; `register_instance`/`heartbeat_instance`
  (stale → ready, stopped не оживає) / `mark_ready|draining|stopped` за таблицею переходів;
  `mark_stale_instances` за heartbeat age; `observed_capacity` — heartbeat-derived
  replicas/slots/min pool revision; `request_scale` — **одна транзакція**: desired state
  (revision+1) + supersede активних команд + `append_audit` + insert `scale_commands`, з
  ідемпотентністю за ключем і `cli_command` для Compose; `transition_scale_command` валідовує
  `SCALE_COMMAND_TRANSITIONS`, а `applied` додатково вимагає збігу observed capacity і
  підтвердженої pool revision.
- **sources:** створення джерела, `set_source_state`, immutable `add_policy_version`
  (`version = max+1` + `current_policy_version_id` в одній транзакції), `upsert_route`,
  `set_route_state` (circuit breaker), `upsert_cursor` — усе з optimistic revision.
- **audit (§13):** `append_audit` (best-effort ідемпотентність за ключем — партиційована таблиця
  не дає глобального unique без partition key).

### CLI і ролі

- `collector db migrate [--check] [--partitions-ahead N]` — `alembic upgrade head` через
  програмний API на спільному з'єднанні + місячні партиції в тій самій транзакції, далі
  `alembic check`; `--check` нічого не змінює і дає exit 1 при drift. DSN — `COLLECTOR_POSTGRES_DSN`
  або `COLLECTOR_POSTGRES_DSN_FILE` (secret-файл має пріоритет); у виводі DSN завжди без пароля.
- `collector db roles [--sql PATH]` — ідемпотентно застосовує
  `src/collector/persistence/postgres/sql/roles.sql`: 8 NOLOGIN group-ролей §13, ownership
  об'єктів → `collector_migrate`, мінімальні GRANT (таблиця відповідності — у самому SQL).
  Login-користувачі створюються оператором як члени ролей — паролів у репозиторії немає.
- `deploy/compose/postgres/init/01-roles.sql` — лише створення ролей для dev-кластера
  (`docker-entrypoint-initdb.d`), бо GRANT потребує таблиць після `db migrate`.

### Тести і CI

- `tests/integration/postgres/**` (36 тестів, маркер `integration`): фікстура піднімає
  `postgres:18` через testcontainers (host примусово `127.0.0.1` — лише loopback, як вимагає
  pytest-socket allow-hosts у `tests/conftest.py`) **або** використовує зовнішній
  `COLLECTOR_TEST_POSTGRES_ADMIN_DSN` (шлях CI із service container). Docker недоступний → skip з
  явним повідомленням; `COLLECTOR_TEST_REQUIRE_DOCKER=1` перетворює skip на fail. Template DB
  (`alembic upgrade head` + партиції + ролі) будується раз, кожен тест отримує свіжу
  `CREATE DATABASE … TEMPLATE`.
- `tests/unit/persistence/postgres/**` (31 тест, без БД): DSN/secret-файл, партиції, backoff і
  таблиці переходів, інваріанти metadata (R-27/R-32, timestamptz, revision), CLI без БД.
- `.github/workflows/ci.yml` — новий job `integration-postgres`: service container
  `postgres:18@sha256:86c951e0…` (pinned digest), кроки `alembic upgrade head && alembic check`,
  `collector db migrate --check && collector db roles`,
  `uv run pytest -m integration tests/integration/postgres`.

### Детермінізм concurrency-тестів

Обидва concurrency-тести перевіряють **результат**, не таймінг:

- 4 claimers × 100 jobs: кожен claimer у власній session крутить `claim(limit=3)` до порожньої
  відповіді; перевіряється, що мультимножина claimed job_id дорівнює множині створених (кожен
  рівно один раз), сума = 100 і всі 100 jobs у статусі `leased`. Немає sleep/таймаутів —
  інваріант тримає `FOR UPDATE SKIP LOCKED`, а не планувальник.
- 8 acquirers на один origin: усі 8 стартують через `asyncio.gather`, кожен у власній
  транзакції; при `max_concurrency=1` рівно 1 `granted` і 7 відмов `concurrency` (плюс варіант
  із `max_concurrency=3` → рівно 3). Rate-обмеження (0.2 rps) перевіряється окремим тестом з
  **симульованим годинником** (101 виклик з кроком 0.1 с на відрізку 10 с → рівно 3 видачі),
  тож тест не залежить від реального часу і не чекає 10 секунд.

## Команди та вивід

```text
$ uv sync --frozen
Checked 60 packages in 8ms

$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
145 files already formatted
Success: no issues found in 60 source files
exit=0
```

```text
$ uv run alembic upgrade head && uv run alembic check       # чиста БД `verify`
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_control_queue, WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.schemas
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.tables
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.types
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.constraints
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.defaults
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.comments
No new upgrade operations detected.
exit=0
```

```text
$ uv run collector db migrate && uv run collector db migrate --check && uv run collector db roles
migrated postgresql+asyncpg://collector:***@127.0.0.1:55432/verify: 0001_control_queue -> 0001_control_queue
partition created: audit_log_y2026m09
partition created: audit_log_y2026m10
partition created: audit_log_y2026m11
partition created: audit_log_y2026m12
No new upgrade operations detected.
schema up to date: revision=0001_control_queue
roles applied to postgresql+asyncpg://collector:***@127.0.0.1:55432/verify from roles.sql: collector_migrate, collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro
exit=0
```

```text
$ uv run pytest -m "not live"
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
541 passed, 1 skipped, 6 warnings in 224.89s (0:03:44)
```

```text
$ uv run pytest -m integration tests/integration/postgres      # testcontainers
....................................                                     [100%]
36 passed in 111.12s (0:01:51)

$ COLLECTOR_TEST_POSTGRES_ADMIN_DSN=postgresql://collector:***@127.0.0.1:55432/postgres \
  COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres
....................................                                     [100%]
36 passed in 99.24s (0:01:39)
```

```text
$ docker exec … psql -U collector -c "\dt"          # після db migrate + db roles
 public | alembic_version        | table             | collector_migrate
 public | audit_log              | partitioned table | collector_migrate
 public | audit_log_y2026m09..12 | table             | collector_migrate
 public | crawl_jobs             | table             | collector_migrate
 public | crawl_runs             | table             | collector_migrate
 public | dead_letters           | table             | collector_migrate
 public | origin_rate_buckets    | table             | collector_migrate
 public | origin_rate_permits    | table             | collector_migrate
 public | scale_commands         | table             | collector_migrate
 public | source_cursors         | table             | collector_migrate
 public | source_policy_versions | table             | collector_migrate
 public | source_routes          | table             | collector_migrate
 public | sources                | table             | collector_migrate
 public | worker_instances       | table             | collector_migrate
 public | worker_pools           | table             | collector_migrate
```

Кількість тестів: **541 passed, 1 skipped** у `-m "not live"` (з них 36 integration + 31 нових
unit від WP-01A; решта — WP-00/WP-01C).

## Acceptance PR1 → де перевірено

| Пункт картки | Тест / доказ |
|---|---|
| clean DB → upgrade → check → downgrade | `test_migrations.py::test_upgrade_check_downgrade_cycle_on_clean_database` |
| 4 claimers × 100 jobs, кожен рівно раз | `test_queue.py::test_four_parallel_claimers_claim_each_job_exactly_once` |
| heartbeat чужого lease відхиляється | `test_queue.py::test_heartbeat_by_non_owner_is_rejected` |
| expired lease → claim іншим worker | `test_queue.py::test_expired_lease_is_recovered_and_reclaimed_by_another_worker` |
| повторний enqueue не дублює | `test_queue.py::test_enqueue_with_same_idempotency_key_returns_existing_job` |
| max_attempts → dead letter + `quarantined` | `test_queue.py::test_retry_backoff_then_max_attempts_quarantines_with_dead_letter` |
| 8 acquirers, concurrency 1 → рівно 1 slot | `test_limiter.py::test_eight_parallel_acquirers_get_exactly_one_slot_with_concurrency_one` (+ варіант з 3) |
| 0.2 rps за 10 с ≤ 3 | `test_limiter.py::test_rate_limit_0_2_rps_grants_at_most_three_in_ten_seconds` (рівно 3) |
| expired permit відновлює slot; release ідемпотентний | `test_limiter.py::test_expired_permit_restores_slot_and_release_is_idempotent` |
| `block_origin` блокує до `until` | `test_limiter.py::test_block_origin_denies_until_deadline` |
| stale revision відхиляється | `test_pools.py::test_stale_revision_is_rejected`, `test_control_plane.py::test_source_state_uses_optimistic_revision` |
| недозволений перехід scale command | `test_pools.py::test_scale_command_disallowed_transition_is_rejected`, `…::test_applied_requires_heartbeat_derived_capacity_to_match` |
| `collector_api_ro` INSERT → permission denied | `test_roles.py::test_read_only_roles_cannot_insert` (+ `…_fetcher_can_enqueue_but_translation_cannot`, `…_runtime_roles_have_no_ddl_and_no_audit_update`) |
| FR-002: один running full run | `test_control_plane.py::test_only_one_running_full_crawl_run_per_source` |
| жодного JSONB payload (R-27) | `test_metadata.py::test_no_domain_jsonb_and_all_timestamps_are_timestamptz`, `test_migrations.py::test_models_match_card_contracts` |
| ролі й GRANT задокументовані | `src/collector/persistence/postgres/sql/roles.sql` (таблиця ролей у шапці), `deploy/compose/postgres/init/README.md` |

## Виправлення після gate 2

Вердикт gate 2 — `pass` (`docs/plan/reports/WP-01A/testing-pr1.md`; тестувальник додав 35 тестів
у `1cab61e`). Закрито чотири знахідки; L-3 підтверджено оркестратором як owner-рішення.

### L-1 — `request_scale`/`upsert_pool` кидали сирий `IntegrityError` замість `errors.*`

Додано `errors.InvalidValueError` (аргумент порушує інваріант ресурсу) і перевірку **до першого
запису**:

- `PoolDesiredState.validate()` — `min_replicas >= 0`, `max >= min`, `desired` у діапазоні,
  `desired_concurrency >= 1`, `mode` з `POOL_MODES`; викликається з `upsert_pool`;
- `request_scale` — `requested_replicas >= 0` і `requested_concurrency >= 1` до будь-якого
  запиту, а після row lock на pool ще й `requested_replicas` у межах `[min_replicas,
  max_replicas]` (межі відомі лише після читання pool).

CHECK-константи у схемі не чіпались — вони лишаються другим рубежем для прямої зміни рядка в
обхід репозиторію. Практичний наслідок: транзакція викликача більше не «мертва» після
невалідного виклику (порушений CHECK ламав її повністю), і в тесті це зафіксовано явно — після
трьох відхилених `request_scale` та сама транзакція успішно виконує валідний.

Тести: `test_pools.py::test_invalid_scale_values_raise_domain_error_without_writes` (перевіряє
також, що не з'явилось ні команди, ні audit-запису, ні зміни revision),
`test_pools.py::test_invalid_pool_desired_state_is_rejected_before_write`,
`test_policies.py::test_pool_desired_state_validation_rejects_invalid_values` (unit, 5 варіантів)
і `…_accepts_scale_to_zero` (scale-to-zero лишається дозволеним).

### L-2 — ідемпотентність `enqueue` залежить від READ COMMITTED

Docstring `enqueue` тепер явно вимагає READ COMMITTED і пояснює механізм. Під час написання
тесту з'ясувалась **точніша** поведінка, ніж передбачала знахідка: у `REPEATABLE READ` падає не
наступний `SELECT`, а сам `INSERT … ON CONFLICT DO NOTHING` — PostgreSQL кидає `could not
serialize access due to concurrent update`. Тобто режим відмови гучний, а не тихий, і дубля не
виникає; docstring описує саме його. Повідомлення `NotFoundError` у залишковій гілці теж
переписано так, щоб воно вказувало на isolation level.

Тест: `test_queue.py::test_enqueue_outside_read_committed_fails_loudly_without_duplicating`
(REPEATABLE READ → `DBAPIError`; далі READ COMMITTED на тому самому ключі ідемпотентний, у
таблиці рівно один рядок). `docs/persistence/postgres.md` за карткою створюється у PR3 — вимогу
до isolation level перенести туди разом із рештою transaction boundaries.

### I-2 — index під `ORDER BY` у `claim`: **додано index** (рішення з виміром)

Обрано не «зафіксувати свідомий вибір», а додати index, бо вимір показав не деградацію, а обвал
плану. На 400 000 pending jobs (PostgreSQL 18, той самий запит `claim`, `LIMIT 3`):

```text
-- лише обов'язковий index картки (status, not_before, priority, job_id)
 Limit (actual time=328.878..328.888 rows=3.00 loops=1)
   ->  LockRows
         ->  Sort  Sort Key: priority DESC, not_before, job_id
               Sort Method: external merge  Disk: 18800kB
               ->  Seq Scan on crawl_jobs (rows=400000)
 Execution Time: 331.638 ms
```

Причина: `status IN ('pending','retry')` на **провідній** колонці не дає PostgreSQL читати index
у порядку `priority DESC` — доводиться сортувати всю чергу. Тому status винесено у предикат
partial index-у (міграція `0002_claim_index`):

```sql
CREATE INDEX ix_crawl_jobs_claimable_order ON crawl_jobs (priority DESC, not_before, job_id)
    WHERE status IN ('pending', 'retry');
```

```text
 Limit (actual time=0.133..0.163 rows=3.00 loops=1)
   ->  LockRows
         ->  Index Scan using ix_crawl_jobs_claimable_order on crawl_jobs
               Index Cond: (not_before <= now())
 Execution Time: 0.182 ms
```

331 мс → 0.18 мс (≈1800×). Обов'язковий index картки **лишається** (R-32: операторські вибірки і
фільтри за `(status, not_before)`). Ціна нового index-у — запис у ще один btree на найгарячішій
таблиці; вона обмежена тим, що index partial: рядок зникає з нього щойно job переходить у
`leased/succeeded/quarantined`, тож розмір тримається на рівні глибини черги, а не історії (на
тому ж наборі — 24 МБ проти 29 МБ у повного index-у при 400 k pending).

Предикат будується з `CLAIMABLE_JOB_STATUSES` (константа `CLAIMABLE_PREDICATE`), тож index і
`claim` не можуть розійтися; це зафіксовано unit-тестом
`test_metadata.py::test_claimable_predicate_matches_statuses_used_by_claim` і перевіркою порядку
колонок index-у в `test_mandatory_indexes_present`.

### I-1 — mutation gap: `SKIP LOCKED` тепер має власний тест

`test_queue.py::test_claim_uses_skip_locked_and_does_not_block_on_rows_locked_by_another_claimer`
перевіряє дві незалежні половини:

1. **текст SQL** — через `before_cursor_execute` збирається фактичний statement claim-у, і кожен
   `FOR UPDATE` у ньому має містити `SKIP LOCKED`;
2. **поведінку** — перший claimer тримає відкриту транзакцію із залоченим рядком, другий під
   `SET LOCAL lock_timeout = '3s'` має одразу отримати **інші** jobs, а не чекати.

Обидві половини перевірено мутацією `.with_for_update(skip_locked=True)` → `.with_for_update()`:
перша дає `AssertionError` на тексті SQL, друга (з тимчасово вимкненою першою) —
`LockNotAvailableError: canceling statement due to lock timeout`. Знахідку I-1 закрито: втрата
`SKIP LOCKED` тепер червоніє assertion-ом, а не лише зростанням латентності.

### Супутнє (не знахідка)

Тести більше не зашивають номер head-ревізії: `migrations.head_revision()` читає його зі script
directory, і `test_migrations.py`, `test_adversarial.py`, `test_cli_db.py` користуються ним —
інакше кожна нова міграція (як `0002`) ламала б чужі тести.

### Команди після виправлень

```text
$ uv sync --frozen
Checked 60 packages in 4ms

$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
150 files already formatted
Success: no issues found in 60 source files
exit=0

$ uv run alembic upgrade head && uv run alembic check          # чиста БД `gate2`
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_control_queue, WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.
INFO  [alembic.runtime.migration] Running upgrade 0001_control_queue -> 0002_claim_index, WP-01A PR1 (gate 2, I-2): partial index під hot path `claim` (§7.2).
No new upgrade operations detected.
exit=0

$ uv run collector db migrate && uv run collector db migrate --check && uv run collector db roles
migrated postgresql+asyncpg://collector:***@127.0.0.1:55433/gate2: 0002_claim_index -> 0002_claim_index
partition created: audit_log_y2026m09
partition created: audit_log_y2026m10
partition created: audit_log_y2026m11
partition created: audit_log_y2026m12
No new upgrade operations detected.
schema up to date: revision=0002_claim_index
roles applied to postgresql+asyncpg://collector:***@127.0.0.1:55433/gate2 from roles.sql: collector_migrate, collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro
exit=0

$ uv run pytest -m "not live"
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
587 passed, 1 skipped, 6 warnings in 73.90s (0:01:13)

$ uv run pytest -m integration tests/integration/postgres            # testcontainers
75 passed in 72.68s (0:01:12)

$ COLLECTOR_TEST_POSTGRES_ADMIN_DSN=postgresql://collector:***@127.0.0.1:55433/postgres \
  COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres
75 passed in 67.88s (0:01:07)
```

`downgrade base → upgrade head → check` пройдено на обох ревізіях без drift. Тестів після
gate 2: **587 passed, 1 skipped** у `-m "not live"`, з них 75 integration (36 мої + 35
тестувальника + 4 нові за знахідками) і 38 unit persistence.

### Статус знахідок gate 2

| # | Знахідка | Статус |
|---|---|---|
| L-1 | сирий `IntegrityError` з `request_scale` | fixed (`InvalidValueError` + 4 тести) |
| L-2 | ідемпотентність `enqueue` вимагає READ COMMITTED | fixed (docstring + тест; уточнено режим відмови) |
| L-3 | зміни у трьох тестових файлах WP-00 | resolved — owner-рішення оркестратора, зафіксовано у `docs/plan/deps/WP-01A-to-WP-00.md` |
| I-1 | втрату `SKIP LOCKED` не ловив жоден assertion | fixed (тест із двох половин; обидві перевірені мутацією) |
| I-2 | index не обслуговує `ORDER BY` у `claim` | fixed (міграція `0002_claim_index`, вимір 331 мс → 0.18 мс) |
| I-3 | heartbeat у межах простроченого lease | accepted — контракт зафіксовано тестом тестувальника |
| I-4 | ідемпотентність `audit_log` — best effort | accepted — дія ідемпотентна через unique `scale_commands.idempotency_key` |

## Відповіді на код-рев'ю

Вердикт gate 3 — `changes_requested` (`docs/plan/reports/WP-01A/code-review-pr1.md`: 1 high,
5 medium, 9 low, 4 informational). Нижче — відповідь на кожну знахідку. Коміт із виправленнями:
`fix(wp-01a): code review findings (high DSN, partitions UTC+default, drain heartbeat, enum
drift test, block_origin refill)`.

| # | Знахідка | Статус | Що зроблено / обґрунтування |
|---|---|---|---|
| H-1 | integration-тест падає з `TypeError` на беспарольному admin-DSN (конфігурація власного CI job) | **fixed** | `_assert_no_password_in_output` більше не порівнює `None` з рядком: при `trust`-auth маскувати нічого й тест це визнає явно. Щоб гарантія «пароль не в логах» не зникла саме там, де потрібна, додано `test_db_migrate_never_prints_password_even_when_dsn_has_one` — підставляє синтетичний пароль у той самий DSN і перевіряє, що у виводі його немає, є маркер `***` і немає traceback. Увесь набір прогнано з DSN **без** пароля (103 passed), з паролем (103) і через testcontainers (103). |
| M-1 | межі партицій залежать від `TimeZone` сесії DDL (overlap або діра) | **fixed** | `MonthPartition.create_sql` формує `timestamptz`-межі з явним `+00`. Тести: `test_month_partition_bounds_are_utc_regardless_of_session_timezone` (UTC / Europe/Kyiv / America/Los_Angeles — межі в `pg_class` однакові) і `test_partitions_created_from_different_timezones_neither_overlap_nor_leave_gaps` (сусідні місяці з різних сесій; рядки `2034-03-31T22:00Z` і `2034-04-01T00:00Z` потрапляють у правильні партиції). Виправлено **до PR2**, як і просив рев'юер, бо helper успадкують `fetches`/`raw_objects`/`change_events`/`outbox_events`. |
| M-2 | heartbeat «оживляє» `draining` instance у `ready` | **fixed** | Намір drain винесено в окрему колонку `worker_instances.drain_requested_at` (міграція `0003`): `heartbeat_instance` повертає `stale → draining`, якщо drain запитаний, і лише інакше в `ready`. Скасувати drain може тільки явний `mark_ready` (він і чистить колонку); для цього дозволено перехід `draining → ready` — це зняття drain-барʼєра для survivors (§7.6). Тест `test_heartbeat_does_not_resurrect_draining_instance` відтворює сценарій рев'ю разом із перевіркою `observed_capacity`. |
| M-3 | `alembic check` не бачить розходження CHECK-ів і предиката partial index із контрактами | **fixed** | Новий `tests/integration/postgres/test_schema_contract.py` (15 тестів): читає `pg_get_constraintdef` для 12 CHECK-констрейнтів і `pg_indexes.indexdef` для `ix_crawl_jobs_claimable_order` **з живої БД** і порівнює множини значень із `collector.contracts.enums`/`WorkerRole`/константами коду; додатково INSERT кожного значення `SourceState` (саме цей розрив рев'юер відтворив) і мутаційний тест `test_drifted_check_constraint_is_detected`, який підміняє CHECK у транзакції й перевіряє, що порівняння червоніє. |
| M-4 | `block_origin` не скидає refill → повний burst після зняття 429 | **fixed** | `block_origin` виставляє `available_tokens = 0` і `last_refill_at = until`, тож після зняття блоку видача починається «з нуля» за звичайним refill. Тест `test_block_origin_resets_refill_so_there_is_no_burst_after_unblock`; два наявні тести (мій і тестувальника) оновлено під новий контракт — у момент `until` дозволу ще немає, він зʼявляється після refill. |
| M-5 | `audit_log` без DEFAULT-партиції: пропущене обслуговування зупиняє audited дії | **fixed** | Міграція `0003` створює `audit_log_default`. Тести: рядок далекого місяця потрапляє в DEFAULT (`test_audit_log_default_partition_accepts_rows_without_monthly_partition`) і чистий `alembic upgrade head` без жодного `db migrate` дозволяє писати audit (`test_fresh_upgrade_head_without_maintenance_can_write_audit`). Додано `partitions.default_partition_row_count` як джерело метрики «партиції відстають» із TODO(WP-12) і описом процедури перенесення рядків (поки вони в DEFAULT, місячну партицію того самого періоду створити не можна). Контракт «зрозуміла помилка замість auto-create» для таблиць без DEFAULT збережено окремим тестом. |
| L-1 | `request_scale` не ідемпотентна під конкуренцією | **fixed** | Після невдалого захоплення pool з `expected_revision` ключ ідемпотентності перечитується — конкурент, що вже створив команду, дає повтор, а не `StaleRevisionError`. Тест `test_request_scale_stays_idempotent_when_pool_revision_moved` (два паралельні виклики → один `command_id`, один audit, `revision=2`). |
| L-2 | сирі `IntegrityError` повз типізовані помилки (`upsert_pool` create, `create_source`) | **fixed** | Обидві гілки перевіряють наявність рядка до вставки й кидають `ConflictError`; транзакція викликача лишається живою. Тест `test_creating_existing_pool_raises_conflict_not_integrity_error`. |
| L-3 | `collector db roles` віддає traceback замість типізованої помилки | **fixed** | `apply_roles` транслює помилки asyncpg у `DBAPIError`, а CLI `_run_async` додатково розпізнає їх за модулем (asyncpg без `py.typed`) — це покрило й помилки connect/auth, які SQLAlchemy не обгортає. Тести: `test_db_roles_translates_driver_errors_without_traceback` і `test_postgres_error_predicate_covers_driver_and_sqlalchemy_errors` (сторонні винятки, як `ValueError`, не ковтаються). |
| L-4 | `db migrate --check` не є read-only | **fixed (формулювання)** | Docstring `migrate_database` і help `--check` тепер кажуть прямо: схема не змінюється, але команда потребує тих самих прав, що й міграції (не read-only роль), бо `MigrationContext` створює `alembic_version` у транзакції, яка відкочується. Поведінку не змінював: робити `--check` доступним для `collector_api_ro` означало б окремий шлях без `MigrationContext`. |
| L-5 | `release_permit` не перевіряє власника | **fixed** | Доданий опційний `owner_instance` додає предикат власника; sweeper (власника не знає) працює як раніше. Тест `test_release_permit_with_owner_does_not_free_foreign_slot`. Параметр опційний навмисно — WP-01D передаватиме його у воркерах. |
| L-6 | jitter поверх cap → фактичний максимум `maximum*(1+ratio)` | **fixed** | Cap застосовується після jitter. Тест `test_backoff_never_exceeds_maximum_even_with_jitter` (40 attempt-ів). |
| L-7 | `upsert_cursor` перезаписує курсор без монотонної перевірки | **accepted (wp-implementer WP-01A, 2026-09-22)** | Напрямок відкату безпечний (повторний обхід, не пропуск), а правильна семантика монотонності залежить від типу курсора: ETag/Last-Modified не впорядковані, page token не порівнюваний, watermark — порівнюваний. Вибір належить власнику discovery (WP-01D): або `expected_revision`, або `GREATEST` для часових курсорів. Винесу рішення у PR2 разом із рештою cursor/watermark-семантики і передам вимогу власнику discovery (WP-01D). |
| L-8 | `enqueue` мовчки повертає термінальний job для повторно використаного ключа | **fixed (docstring)** | Docstring `enqueue` явно попереджає: ключ — це ідентичність job, тож без дискримінатора вікна (§9.3 п.3 `planned_at_bucket`) джерело після першого успішного обходу більше не фетчиться; статус повернутого job перевіряє викликач. Поведінку не змінював — «той самий job, без дубля» є контрактом картки, а рішення «перезапустити роботу» належить планувальнику (WP-01D). |
| L-9 | `set_instance_status` не ідемпотентна | **fixed** | Повтор того самого статусу — no-op. Тест `test_instance_status_transitions_are_idempotent` (повторні `mark_ready`/`mark_draining`/`mark_stopped`; `drain_requested_at` не «оновлюється» повтором). |
| I-1 | дві реалізації lease; queue-варіант варто параметризувати до PR2 | **accepted (wp-implementer WP-01A, 2026-09-22)** | Погоджуюсь по суті, але параметризація черги — зміна публічного API репозиторію, яку не можна зробити «наосліп» до появи справжнього другого споживача: `projection_tasks` мають власні поля (`entity_uuid`, `projection_version`, unique artifact key) і власну транзакцію ack. Виконаю на початку PR2 — першим кроком, до написання `claim_projection_tasks`, щоб копії не зʼявилося. |
| I-2 | дрібне дублювання (`_raise_stale_or_missing*`, `EXPECTED_TABLES`/`ALLOWED_JSONB` у двох тестах) | **accepted (wp-implementer WP-01A, 2026-09-22)** | Два хелпери відрізняються таблицею, типом ключа і текстом повідомлення; спільна generic-версія на два виклики додала б параметризацію замість шести рядків. Константи в тестах дублюються навмисно: unit-перевірка метаданих і integration-перевірка живої схеми мають лишатись незалежними джерелами істини (спільна константа зробила б обидва тести зеленими при одній помилці). Перегляну, коли PR2 додасть третього споживача. |
| I-3 | тривалість транзакції викликача нічим не обмежена | **accepted (wp-implementer WP-01A, 2026-09-22)** | Правильне місце для запобіжника — воркер (WP-01D): `statement_timeout`/`idle_in_transaction_session_timeout` на runtime-ролях і заборона мережевих викликів усередині транзакції. Репозиторій не може цього гарантувати, не забравши в викликача право писати результат у тій самій транзакції (саме це потрібно для outbox у PR2). Вимогу передам у картку WP-01D; docstring кожної операції вже містить явне «commit одразу». |
| I-4 | обидва index-и на `crawl_jobs` виправдані (підтвердження виміру gate 2) | **not applicable** | Підтвердження без дії: рев'юер незалежно відтворив `Index Scan using ix_crawl_jobs_claimable_order` і збіг предиката з `CLAIMABLE_JOB_STATUSES`; ризик розходження закритий у M-3. |

### Команди після виправлень gate 3

```text
$ uv sync --frozen
Checked 60 packages in 5ms

$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
153 files already formatted
Success: no issues found in 60 source files
exit=0

$ uv run alembic upgrade head && uv run alembic check          # чиста БД `gate3`
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_control_queue, …
INFO  [alembic.runtime.migration] Running upgrade 0001_control_queue -> 0002_claim_index, …
INFO  [alembic.runtime.migration] Running upgrade 0002_claim_index -> 0003_default_partition, …
No new upgrade operations detected.
exit=0
# downgrade base (3 кроки) → upgrade head (3 кроки) → check: No new upgrade operations detected.

$ uv run collector db migrate && uv run collector db migrate --check && uv run collector db roles
migrated postgresql+asyncpg://collector:***@127.0.0.1:55434/gate3: 0003_default_partition -> 0003_default_partition
partition created: audit_log_y2026m09 … audit_log_y2026m12
schema up to date: revision=0003_default_partition
roles applied to …/gate3 from roles.sql: collector_migrate, collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro
exit=0

$ uv run pytest -m "not live"
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
621 passed, 1 skipped, 6 warnings in 261.39s (0:04:21)

$ COLLECTOR_TEST_POSTGRES_ADMIN_DSN=postgresql://collector_ci@127.0.0.1:55435/postgres \
  COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres
103 passed in 237.02s (0:03:57)          # DSN БЕЗ пароля — точна конфігурація CI job (H-1)

$ COLLECTOR_TEST_POSTGRES_ADMIN_DSN=postgresql://collector:***@127.0.0.1:55434/postgres \
  COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres
103 passed in 255.83s (0:04:15)          # DSN з паролем

$ uv run pytest -m integration tests/integration/postgres    # testcontainers
103 passed in 205.66s (0:03:25)

$ uv run pre-commit run --all-files
… усі 11 hooks Passed
```

Тестів після gate 3: **621 passed, 1 skipped** у `-m "not live"` — з них 103 integration
(було 75) і 44 unit persistence. Розподіл integration за файлами: `test_adversarial.py` 18,
`test_role_connections.py` 17, `test_schema_contract.py` 15 (новий), `test_migrations.py` 11,
`test_pools.py` 11, `test_limiter.py` 9, `test_queue.py` 9, `test_roles.py` 5,
`test_cli_db.py` 4, `test_control_plane.py` 4. Три шляхи integration (без пароля, з паролем, testcontainers) — 0 failed.

### Статус знахідок gate 3

| Severity | Усього | fixed | accepted | not applicable |
|---|---:|---:|---:|---:|
| high | 1 | 1 | 0 | 0 |
| medium | 5 | 5 | 0 | 0 |
| low | 9 | 7 | 2 | 0 |
| informational | 4 | 0 | 3 | 1 |

## Що не перевірено

- **CI job `integration-postgres` не запускався** — гілка не push-иться (правило 7 ролі). Job
  написано за шляхом «service container + `COLLECTOR_TEST_POSTGRES_ADMIN_DSN`», і саме цей шлях
  локально прогнано окремо (36 passed, вивід вище), тож у CI відрізняються лише runner і мережа.
- **`alembic upgrade --sql` (offline режим)** реалізовано в `env.py`, але не покрито тестом.
- **Продуктивність під навантаженням** (§15: 100 jobs/s, 1 млн pending) не вимірювалась — це
  критерії переходу на брокер, не acceptance PR1.
- **Поведінка при розсинхронізованому годиннику** хостів: репозиторії свідомо порівнюють lease
  за часом застосунку (детерміновані тести), припущення NTP задокументоване в `clock.py`.
  Тесту на дрейф немає.
- **Партиціонування `fetches`/`raw_objects`/`change_events`/`outbox_events`** — ці таблиці
  з'являються у PR2; helper уже загальний (`PARTITIONED_TABLES`).
- `docs/persistence/postgres.md` і `docs/runbooks/migrations.md` — за карткою це PR3/етап 5.

## Ризики

1. **`request_scale` і `claim` тримають row locks** до commit викликача. Якщо WP-01D покладе в
   ту саму транзакцію довгу роботу (HTTP-запит), це серіалізує pool/чергу. Пом'якшення:
   docstring кожної функції явно каже «commit одразу»; при появі workers варто додати
   `statement_timeout`/`idle_in_transaction_session_timeout` для runtime-ролей (WP-01D/WP-12).
2. **`audit_log` без глобального unique на `idempotency_key`** (партиційована таблиця вимагала б
   ключа з `created_at`): одночасні повтори того самого запиту теоретично дадуть два записи
   журналу. Для журналу це нешкідливо, але `scale_commands.idempotency_key` — справжній unique,
   тож дублювання дії не відбувається.
3. **Ідемпотентність `enqueue` без оновлення полів**: повторний enqueue з іншими `args`/priority
   поверне **старий** job без змін. Це навмисно (ключ = ідентичність job), але викликач, який
   очікує «update on conflict», отримає несподіванку — задокументовано в docstring.
4. **testcontainers піднімає Ryuk** (`testcontainers/ryuk:0.8.1`) — ще один образ, який CI/dev
   тягне з Docker Hub. У CI обраний шлях без testcontainers (service container), тож ризик
   стосується лише локальних прогонів; `TESTCONTAINERS_RYUK_DISABLED=true` вимикає його.
5. ~~**`SET ROLE`-тести ролей** перевіряють GRANT, але не реальні login-користувачі.~~
   **Закрито на gate 2:** `tests/integration/postgres/test_role_connections.py` (тестувальник)
   перевіряє всі 8 ролей через окремі login-з'єднання, включно з матрицею
   `has_table_privilege` по всіх 13 таблицях.

## Як вимкнути або відкотити

- Схема: `collector db migrate` не викликається — жоден інший компонент PR1 ще не працює;
  у dev відкат `uv run alembic downgrade base` (реалізовано) або `docker compose down -v`;
  у production — forward-fix міграція (forward-only, картка «Rollback/disable»).
- Ролі: `collector db roles` ідемпотентний і лише додає GRANT; повний відкат — `DROP ROLE`
  8 ролей після перепризначення ownership (`REASSIGN OWNED BY collector_migrate TO …`).
- CI: видалити job `integration-postgres` з `.github/workflows/ci.yml` — інші jobs незалежні.
- Код: `revert` шести комітів PR1; CLI-команди повертаються у стан стабів WP-00.

## Dependency-запити

- `docs/plan/deps/WP-01A-to-WP-00.md` — (1) `db migrate` більше не стаб і група `db` має
  `roles`, тому змінено owned-тести WP-00 `tests/unit/test_cli.py`,
  `tests/unit/test_cli_adversarial.py` і `tests/unit/test_foundation_config.py`
  (`FORBIDDEN_FOUNDATION_DEPS` забороняв `sqlalchemy`/`alembic`, які картка WP-01A прямо
  вимагає) — зміни зроблені в branch за прецедентом WP-01C, потребують підтвердження owner;
  (2) для WP-00 PR2 — сервіс `postgres` у `docker-compose.yml` із тим самим digest і mount
  `deploy/compose/postgres/init`; (3) `alembic.ini` + `migrations/` в образі або
  `COLLECTOR_ALEMBIC_INI`.
- Змін shared contracts (`src/collector/contracts/**`) **не потрібно**: усі осі стану, UUIDv7,
  `SourceIdentity` і ключі ідемпотентності використані як є.
