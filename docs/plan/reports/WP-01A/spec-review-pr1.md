# WP-01A PR1 — пострев'ю за ТЗ (`wp/01a-1-control-queue`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR1 «control plane, job queue, origin limiter, worker pools» |
| Branch / worktree | `wp/01a-1-control-queue` / `.worktrees/wp-01a` |
| Рев'юваний commit | `b08eee5` (`git diff main...HEAD` — 59 файлів, +9207 / −10) |
| Картка | `docs/plan/cards/WP-01A.md` — «Спільні вимоги» (8 bullet-ів) + розділ «PR1» (таблиці, репозиторії/операції, indexes, тести, Acceptance) |
| Розділи ТЗ | §7.2, §7.6, §9.1 (рядки PR1 + обов'язкові indexes + партиціонування), §9.3 п.3, §13, §15, §16.1 п.3, §16.3 (дотичні), §17.2, §18, §20, Додаток C; FR-001, FR-002, FR-009, FR-031—FR-033 |
| REVIEW.md | R-53 (критичний), R-28, R-32 (названі у завданні); дотично R-24/R-27, R-43 |
| Вхідні звіти | `implementation-pr1.md` (базовий + «Виправлення після gate 2» + «Відповіді на код-рев'ю»), `testing-pr1.md` (`pass`), `code-review-pr1.md` (`changes_requested`: 1 high, 5 medium, 9 low, 4 informational) |
| Рев'юер | wp-spec-reviewer, read-only; записи — лише цей файл і `docs/acceptance/traceability.md` |

## Вердикт

**`accept`** — `missing`: 0; `partial`: 9 (усі — або межі під-PR1/картки (таблиці й docs, віднесені до PR2/PR3), або етапи конвеєра поза етапом 4 (push/CI/merge), або елементи §16.3, які потребують workers з WP-01D/WP-02); знахідок critical/high цього пострев'ю: 0.

Єдина `high` знахідка попереднього етапу (H-1, CI-джоб червоний на власній беспарольній конфігурації) має статус `fixed` і підтверджена в коді: `tests/integration/postgres/test_cli_db.py:35-41` більше не порівнює `None` з рядком, а гарантія «пароль не в логах» перенесена в окремий тест `test_db_migrate_never_prints_password_even_when_dsn_has_one:85-107`, який підставляє синтетичний пароль. Усі 5 medium і 7 з 9 low — `fixed`, перевірено в коді поіменно (розділ 6); 2 low і 3 informational — `accepted (wp-implementer WP-01A, 2026-09-22)` з аргументом і адресою (PR2 / WP-01D); 1 informational — `not applicable`.

Обсяг приймання: **лише PR1** рядка WP-01A у §17.2. Artifact pointers, projection tasks/acks, outboxes, entity index (PR2), news/matching/release/retention/capacity (PR3), а також `docs/persistence/postgres.md`, `docs/runbooks/migrations.md` і ADR-0005 (етап 5 картки) цим вердиктом **не** закриваються і позначені `not applicable (PR2/PR3)`.

## Власна верифікація (Windows 11, uv 0.12.13, CPython 3.13, Docker 29.8.0, HEAD `b08eee5`)

```text
$ uv run ruff check .                    → All checks passed!                       exit=0
$ uv run ruff format --check .           → 153 files already formatted              exit=0
$ uv run mypy src                        → Success: no issues found in 60 source files  exit=0
$ uv run pytest tests/unit/persistence -q → 44 passed in 8.01s                       exit=0
$ uv run pytest -m integration tests/integration/postgres -q   (testcontainers, postgres:18@sha256:86c951e0…)
                                          → 103 passed in 494.36s (0:08:14)          exit=0
```

Статичні зонди (read-only, фактичний результат):

- `git diff main...HEAD --stat` — жодного файла поза owned-переліком картки, крім трьох тестів WP-00 (approved dependency, розділ 7) і `uv.lock`/`pyproject.toml`, які картка прямо дозволяє;
- `Base.metadata.tables` (через `tests/unit/persistence/postgres/test_metadata.py::test_pr1_tables_are_registered`) — рівно 13 таблиць PR1; **жодної таблиці PR2/PR3** не з'явилось;
- `grep -rn "offset|OFFSET" src/collector/persistence/` — 0 збігів у SQL (єдиний збіг — `utcoffset()` у `clock.py:23`), §15 дотримано;
- `grep -rn "collector_migrate" src/` — лише `roles.py` і `sql/roles.sql` (§13 «migration role не використовується runtime-процесами»; те саме перевіряє `test_role_connections.py:225`);
- `grep -n` по `migrations/postgres/versions/20260922_0001_control_queue.py` — усі шість обов'язкових indexes картки, `uq_crawl_runs_running_full` (:438), `postgresql_partition_by="RANGE (created_at)"` (:69) і тригер `audit_log_append_only` (:81-82) присутні в самій міграції, а не лише в моделях.

---

## 1. Acceptance: картка WP-01A → доказ → статус

### 1.1. «Спільні вимоги» (кожен bullet — окремий рядок)

| # | Вимога картки | Доказ | Статус |
|---|---|---|---|
| С-1 | SQLAlchemy 2.x async/`asyncpg`, Alembic forward-only; кожна міграція має `upgrade`, `downgrade` лише де безпечно; `alembic upgrade head` з порожньої БД → `alembic check` без drift | `pyproject.toml` (`sqlalchemy[asyncio]`, `asyncpg`, `alembic`, dev `testcontainers`); `migrations/postgres/env.py` (два режими: власний async engine і спільне з'єднання викликача); три ревізії `0001`/`0002`/`0003` — у кожної реалізовано і `upgrade`, і `downgrade` (`0003:263-267` навіть `DETACH` замість `DROP`, щоб не втратити рядки DEFAULT-партиції); `test_migrations.py::test_upgrade_check_downgrade_cycle_on_clean_database`; `test_adversarial.py::test_upgrade_head_twice_in_a_row_is_idempotent`; вивід `No new upgrade operations detected` у `implementation-pr1.md:434-440` і `testing-pr1.md` §1; мутація M0 (`testing-pr1.md` §5) доводить, що `alembic check` **червоніє** на drift | evidenced |
| С-2 | Усі PK — UUID (UUIDv7 із застосунку; `gen_random_uuid()` лише для audit/log) | `models/base.py:46` (`UuidPk`, значення від `collector.contracts.new_entity_id`); `models/audit.py:35-37` — єдиний `server_default=text("gen_random_uuid()")` у схемі; natural PK `worker_pools.role` і `origin_rate_buckets.origin` — не відхилення, а дослівний текст §9.1 («pool role», «PK normalized origin») | evidenced |
| С-3 | Гроші `amount_minor BIGINT + currency CHAR(3)` | у PR1 немає грошових колонок (catalog/vehicle/news — PR3) | not applicable (аргумент: жодної monetary-таблиці у scope PR1) |
| С-4 | Усі timestamps `timestamptz` UTC | `models/base.py:39-43` (`type_annotation_map`), `test_metadata.py::test_no_domain_jsonb_and_all_timestamps_are_timestamptz` (обхід усіх колонок усіх таблиць); `clock.py:26-33` відхиляє naive datetime | evidenced |
| С-5 | Source-time колонки nullable + `source_timezone_raw`/`source_time_precision`/`source_time_inferred`/`source_locale_raw` (контракт `SourceTime`) | у PR1 немає source-time колонок: control plane оперує лише system time (`started_at`, `fetched_at` — PR2, `news_*` — PR3) | not applicable (аргумент: доменні часові осі з'являються з PR2/PR3) |
| С-6 | Жодного domain payload JSONB (R-27); JSONB лише для bounded control/news extensions | `test_metadata.py:29-32` — allowlist рівно з трьох пар (`crawl_jobs.args`, `audit_log.before_state`, `audit_log.after_state`); `models/queue.py:96` — CHECK `octet_length(args::text) <= 8192`; `test_migrations.py::test_models_match_card_contracts` повторює перевірку на **живій** схемі | evidenced |
| С-7 | Optimistic `revision` на versioned resources (sources, policies, worker_pools) | `models/base.py:62` (`Revision BIGINT`); `test_metadata.py::test_revision_columns_on_versioned_resources`; `repositories/sources.py:89` (`set_source_state` з `expected_revision`), `:118` (`add_policy_version` — `FOR UPDATE` + revision+1, immutable `version = max+1`), `repositories/pools.py:99`/`:347`; `test_control_plane.py::test_source_state_uses_optimistic_revision`, `test_pools.py::test_stale_revision_is_rejected`, `test_adversarial.py::test_scale_command_with_stale_revision_leaves_no_partial_writes` | evidenced |
| С-8 | Repository API: типізовані async-функції над `AsyncSession`, без ORM-магії у викликачів; кожна операція документує transaction boundary; жодних SQL-рядків в інших WP | усі 6 модулів `repositories/**` — `async def f(session: AsyncSession, ...)`, module-docstring «Transaction boundaries» + per-function рядок; `errors.py` — 7 типізованих винятків замість `sqlalchemy.exc.*`; сирий SQL лише у `sql/roles.sql` і DDL-helper `partitions.py` (з allowlist `PARTITIONED_TABLES` і `_IDENT`-валідацією) | evidenced |
| С-9 | Ролі БД §13 (8 ролей) з мінімальними GRANT; тест, що `collector_parser` не може писати у `news_translations`, а `collector_api_ro` не може `INSERT` | `sql/roles.sql:22-93` (створення 8 NOLOGIN group-ролей + пооб'єктні GRANT + ownership → `collector_migrate`); `roles.py:110-120`; `test_roles.py` (4) і `test_role_connections.py` (17 — окремі **login-з'єднання**, матриця `has_table_privilege` по всіх 13 таблицях, 7 runtime-ролей × UPDATE/DELETE `audit_log`); `collector_api_ro` INSERT → `test_roles.py::test_read_only_roles_cannot_insert`. `news_translations` у PR1 не існує — еквівалент за scope: `test_role_connections.py::test_parser_cannot_write_audit_log_but_keeps_its_queue` | partial (PG-частина PR1 — evidenced; дослівний тест `collector_parser` → `news_translations` можливий лише з PR3) |
| С-10 | Integration-тести: маркер `integration`, лише loopback; PostgreSQL 18 у Docker; кожен тест на чистій схемі (`alembic upgrade head` у template DB + `CREATE DATABASE … TEMPLATE`) | `tests/integration/postgres/conftest.py:1-12, 41-43` (pinned `postgres:18@sha256:86c951e0…`, host примусово `127.0.0.1`, `TEMPLATE_DB = "collector_template"`, `DROP DATABASE … WITH (FORCE)` після кожного тесту); `COLLECTOR_TEST_REQUIRE_DOCKER=1` перетворює skip на fail (у CI виставлено — `ci.yml:89`) | evidenced |
| С-11 | Великі таблиці (`fetches`, `raw_objects`, `change_events`, `outbox_events`, `audit_log`) — declarative partitioning по місяцях + helper для наступних партицій | у PR1 з цього переліку існує лише `audit_log`: `models/audit.py:32` (`RANGE (created_at)`), `0001:69`, PK `(audit_id, created_at)`; helper `partitions.ensure_month_partitions` уже generic (`PARTITIONED_TABLES`), викликається з `db migrate` і призначений maintenance WP-12; `test_migrations.py::test_month_partitions_are_created_and_idempotent`, `::test_month_partition_bounds_are_utc_regardless_of_session_timezone`, `::test_partitions_created_from_different_timezones_neither_overlap_nor_leave_gaps` | evidenced для scope PR1 (решта чотирьох таблиць — PR2) |

### 1.2. PR1 «Таблиці» — §9.1 поле за полем (13 таблиць)

Контракт §9.1 наведено рядками таблиці ТЗ; перевірено проти `models/**` і проти **живої** схеми (`test_migrations.py::test_models_match_card_contracts`, `test_schema_contract.py` — 15 тестів читають `pg_get_constraintdef`/`pg_indexes.indexdef`).

| Таблиця | Контракт §9.1 | Реалізація | Статус |
|---|---|---|---|
| `sources` | UUID PK; canonical `source_id`; version/status; `created_at/updated_at`; optimistic version | `control.py:44-66`: `id` UuidPk, `UniqueConstraint(source_id)`, `state` (CHECK зі `SourceState`), `state_reason`, `current_policy_version_id`, `revision`, `updated_by`, `created_at/updated_at`; плюс `domain` (CHECK `DataDomain`), `country` | evidenced |
| `source_policy_versions` | version/status; immutable snapshot | `control.py:69-90`: `UniqueConstraint(source_id, version)`, explicit-колонки лімітера/розкладу (`requests_per_second`, `max_concurrency`, `burst_tokens`, `crawl_interval_seconds`, `browser_allowed`, `robots_policy`), `manifest_sha256`/`manifest_uri` замість JSONB-копії маніфесту (R-27), `effective_from`, `created_by/created_at`; `updated_at` свідомо відсутній — рядок immutable | evidenced |
| `source_routes` | status; optimistic version | `control.py:93-120`: `UniqueConstraint(source_id, route_kind, route_key)`, `state` (CHECK `RouteState`), circuit-breaker (`consecutive_failures`, `circuit_open_until`, `last_success_at/last_failure_at`), `revision`, timestamps, `ix_source_routes_state(state, circuit_open_until)` | evidenced |
| `source_cursors` | cursor payload; optimistic version | `control.py:123-140`: `UniqueConstraint(source_id, cursor_kind, cursor_key)`, `cursor_value` TEXT (opaque — page token/ETag/watermark), `cursor_at`, `revision`, timestamps | evidenced |
| `crawl_runs` | UUID PK/FK; status; timestamps/error code | `queue.py:58-87`: `kind` (CHECK), `status` (CHECK), `started_by/started_at/finished_at/error_code`, FK на `sources`/`source_policy_versions`; FR-002 partial unique `uq_crawl_runs_running_full` | evidenced |
| `crawl_jobs` | `job_type`, `status` (`pending/leased/succeeded/retry/quarantined`), priority, idempotency key, `attempt/max_attempts`, `not_before`, `lease_owner/lease_expires_at`, timestamps/error code | `queue.py:90-144`: **усі** перелічені поля дослівно; `JOB_STATUSES` (`:47`) — рівно ті п'ять значень; CHECK `attempts`, CHECK `lease_consistent` ((`status='leased'`) ⇔ (owner і expiry не NULL)) — інваріант, якого §9.1 не вимагає, але який робить lease-контракт нерозривним | evidenced |
| `origin_rate_buckets` | PK normalized origin; token/refill/concurrency budget; available tokens; `blocked_until`; revision/time | `limiter.py:31-51`: `origin` PK, `capacity_tokens`/`refill_per_second`/`max_concurrency`, `available_tokens`, `last_refill_at`, `blocked_until`/`block_reason`, `revision`, timestamps; CHECK `positive_rate`, `tokens_range` | evidenced |
| `origin_rate_permits` | UUID; origin; owner instance/job; acquired/lease expiry/released time; atomic acquire/idempotent return/expiry recovery | `limiter.py:54-76`: `permit_id`, FK `origin`, `owner_instance`, `job_id`, `acquired_at`, `lease_expires_at`, `released_at`, `release_reason` (CHECK + CHECK `release_consistent`); атомарність/ідемпотентність — розділ 1.3 | evidenced |
| `worker_pools` | pool role, desired/**current** replicas+concurrency, min/max/mode/revision | `pools.py:72-98`: `role` PK (CHECK `WorkerRole`), `desired_replicas/desired_concurrency`, `min/max_replicas`, `mode` (CHECK), `resource_profile`, `revision`, `updated_by/update_reason`. **Current не зберігається** — виводиться з heartbeat (`repositories/pools.py:317 observed_capacity`). Це не розходження: §7.6 прямо каже «`applied` дозволений лише коли **heartbeat-derived** current replicas/concurrency відповідають desired revision», і `transition_scale_command` (`:490-510`) саме так і робить | evidenced (з інтерпретацією — знахідка S-3, informational) |
| `worker_instances` | instance boot ID/status/heartbeat/version/leases | `pools.py:101-137`: `instance_id` (boot UUID), `role`, `status` (`starting/ready/draining/stopped/stale` — дослівно §7.6), deployment/container/hostname metadata, `version`, `slots_total/slots_active/active_leases`, `pool_revision`, `drain_requested_at`, `started_at/last_heartbeat_at/stopped_at` | evidenced |
| `scale_commands` | idempotency key, expected pool revision, requested values, status/result, actor/reason, audit link | `pools.py:140-168`: `UniqueConstraint(idempotency_key)`, `expected_pool_revision`/`applied_pool_revision`, `requested_replicas/requested_concurrency`, `status` (CHECK з 7 станів §7.6), `cli_command` (exact CLI для Compose mode — §7.5), `result`, `actor`, `reason`, `audit_id`+`audit_created_at` (без FK на партиційований `audit_log` — свідомо, щоб drop старої партиції не ламав команди) | evidenced |
| `dead_letters` | status; actor/reason; timestamps | `queue.py:147-167`: FK `job_id`, `job_type`, `reason` (CHECK `max_attempts` \| `quarantine`), `attempt`, `error_code/error_message`, `created_at`, `resolved_at/resolved_by/resolution`; indexes `job_id`, `created_at` | evidenced |
| `audit_log` | actor/reason; timestamps | `audit.py:26-48`: PK `(audit_id, created_at)` (партиційна вимога), `actor`, `action`, `resource_type/resource_id`, `before_state/after_state` (JSONB control extension для impact preview §13), `request_id`, `idempotency_key`; RANGE-партиціонування + append-only тригер | evidenced |

**Scope-перевірка:** `test_metadata.py::test_pr1_tables_are_registered` фіксує множину таблиць рівно як 13 — таблиці PR2 (`fetches`, `raw_objects`, `parse_attempts`, `artifact_upload_claims`, `normalized_artifacts`, `projection_tasks`, `projection_acknowledgements`, `entity_index`, `change_events`, `outbox_events`) і PR3 у схемі **відсутні**. PR не вийшов за межі scope.

### 1.3. PR1 «Репозиторії та операції» (кожна операція — рядок)

| Операція картки | Реалізація (file:line) | Тест | Статус |
|---|---|---|---|
| Queue: `enqueue(job)` з unique idempotency key (повторний → той самий job, без дубля) | `repositories/queue.py:86-142` (`INSERT … ON CONFLICT DO NOTHING` за `uq_crawl_jobs_idempotency_key` + `SELECT`; docstring фіксує вимогу READ COMMITTED) | `test_queue.py::test_enqueue_with_same_idempotency_key_returns_existing_job`; `test_adversarial.py::test_concurrent_enqueue_of_same_key_yields_one_row_without_unique_violation` (6 сесій); `test_queue.py::test_enqueue_outside_read_committed_fails_loudly_without_duplicating` | evidenced |
| Queue: `claim(job_types, worker, lease_seconds, limit)` через `FOR UPDATE SKIP LOCKED`, `status='pending' AND not_before <= now()`, priority | `queue.py:145-190` (`.with_for_update(skip_locked=True)` :172; `status IN ('pending','retry')` — `retry` як claimable задокументовано в `models/queue.py:1-15`; `ORDER BY priority DESC, not_before, job_id`; `attempt+1`, lease у момент claim) | `test_queue.py::test_four_parallel_claimers_claim_each_job_exactly_once` (4×100); `::test_claim_uses_skip_locked_and_does_not_block_on_rows_locked_by_another_claimer` (SQL-текст + поведінка з `lock_timeout`); `::test_claim_respects_priority_not_before_and_job_type`; `test_adversarial.py::test_priority_wins_for_identical_not_before`, `::test_claim_with_empty_job_types_claims_nothing`; мутація M1b (`testing-pr1.md` §5) — без row lock job захоплюється двічі | evidenced |
| Queue: `heartbeat(job_id, owner)` продовжує lease лише власнику | `queue.py:193-213` + предикат `_owned` (`:353-358`) | `test_queue.py::test_heartbeat_by_non_owner_is_rejected`; `test_adversarial.py::test_heartbeat_after_lease_expiry_contract`; мутація M2 — 6 тестів червоніють | evidenced |
| Queue: `complete` | `queue.py:216-238` (лише власник; `finished_at`, lease очищено) | `test_queue.py::test_operator_quarantine_and_complete_are_terminal`; `test_adversarial.py::test_complete_by_foreign_worker_and_double_complete_are_rejected` | evidenced |
| Queue: `retry(backoff, jitter)` | `queue.py:241-275` + `BackoffPolicy` (`:64-83`; cap застосовано **після** jitter — L-6) | `test_queue.py::test_retry_backoff_then_max_attempts_quarantines_with_dead_letter`; `test_policies.py::test_backoff_is_exponential_capped_and_jittered_deterministically`, `::test_backoff_never_exceeds_maximum_even_with_jitter` | evidenced |
| Queue: `quarantine` | `queue.py:278-303` (операторський варіант `owner=None` через `_lock_any`, термінальні статуси відхиляються) | `test_queue.py::test_operator_quarantine_and_complete_are_terminal` | evidenced |
| Queue: `recover_expired_leases()` повертає у `pending`, `attempt` зберігається | `queue.py:306-326` (`SKIP LOCKED`, щоб не чекати на рядки, що саме heartbeat-яться) | `test_queue.py::test_expired_lease_is_recovered_and_reclaimed_by_another_worker` (перевіряє збереження `attempt`) | evidenced |
| Queue: `max_attempts` → запис у `dead_letters` | `queue.py:383-412` (`_quarantine_locked` — статус і dead letter в **одній** транзакції, `reason='max_attempts'`) | `test_queue.py::test_retry_backoff_then_max_attempts_quarantines_with_dead_letter`; `test_adversarial.py::test_retry_after_max_attempts_writes_exactly_one_dead_letter` (6 повторів → рівно один рядок) | evidenced |
| Crawl runs (FR-002): `start_run` не дозволяє два несумісні повні обходи (unique partial index) | `repositories/crawl_runs.py:100-138` (`ON CONFLICT … DO NOTHING` з `index_where` → `ConflictError` **без** зіпсованої транзакції); index `models/queue.py:64-69` | `test_control_plane.py::test_only_one_running_full_crawl_run_per_source`; `test_adversarial.py::test_parallel_start_full_run_grants_exactly_one` (5 паралельних → 1/4) | evidenced |
| Limiter: `acquire_permit` атомарно (одна транзакція, row lock на bucket): `blocked_until` → refill → rate token **і** concurrency slot | `repositories/limiter.py:98-194` (`SELECT … FOR UPDATE` :119; порядок перевірок 1-4 у module-docstring; токен списується лише при видачі) | `test_limiter.py::test_eight_parallel_acquirers_get_exactly_one_slot_with_concurrency_one`, `::test_eight_parallel_acquirers_with_concurrency_three`, `::test_rate_limit_0_2_rps_grants_at_most_three_in_ten_seconds`; `test_adversarial.py::test_long_pause_refill_never_exceeds_burst_capacity`, `::test_two_origins_do_not_block_each_other` | evidenced |
| Limiter: `release_permit` ідемпотентний | `limiter.py:197-224` (предикат `released_at IS NULL`; опційний `owner_instance` — L-5) | `test_limiter.py::test_expired_permit_restores_slot_and_release_is_idempotent`, `::test_release_permit_with_owner_does_not_free_foreign_slot`; `test_adversarial.py::test_double_release_is_idempotent_and_frees_slot_once`, `::test_release_of_unknown_permit_is_false_not_error`; мутація M3 | evidenced |
| Limiter: `expire_permits()` повертає прострочені slots | `limiter.py:227-244`; додатково прострочений permit не рахується live ще до sweep (`:143-145`) | `test_adversarial.py::test_expiry_frees_concurrency_slot_even_after_rate_token_spent` | evidenced |
| Limiter: `block_origin(origin, until)` для 429/`Retry-After` | `limiter.py:247-280` (не скорочує довший блок; скидає `available_tokens=0`, `last_refill_at=until` — M-4) | `test_limiter.py::test_block_origin_denies_until_deadline`, `::test_block_origin_resets_refill_so_there_is_no_burst_after_unblock`; `test_adversarial.py::test_block_origin_with_live_permits_denies_new_but_keeps_existing`; мутація M5 | evidenced |
| Limiter: rate tokens і concurrency обліковуються **окремо** | `limiter.py:149-176` — відмова через concurrency не списує токен; `PermitDecision.reason ∈ {blocked, rate, concurrency}` | `test_adversarial.py::test_expiry_frees_concurrency_slot_even_after_rate_token_spent` (зворотний випадок) | evidenced |
| Pools: CRUD desired state з `revision` | `repositories/pools.py:99-166` (`upsert_pool`; `PoolDesiredState.validate()` до першого запису; повторне створення → `ConflictError`) | `test_pools.py::test_stale_revision_is_rejected`, `::test_invalid_pool_desired_state_is_rejected_before_write`, `::test_creating_existing_pool_raises_conflict_not_integrity_error` | evidenced |
| Pools: `register_instance` / `heartbeat` / `mark_draining` / `mark_stopped` | `pools.py:169-293` (+ `mark_ready` як єдиний спосіб зняти drain); `INSTANCE_TRANSITIONS:44-53` | `test_pools.py::test_instance_lifecycle_and_stale_detection`, `::test_heartbeat_does_not_resurrect_draining_instance`, `::test_instance_status_transitions_are_idempotent`; `test_adversarial.py::test_heartbeat_from_stopped_instance_is_rejected_and_state_unchanged` | evidenced |
| Pools: `stale` detection за heartbeat age | `pools.py:296-314` (`mark_stale_instances`, TTL 60 с за замовчуванням) | `test_pools.py::test_instance_lifecycle_and_stale_detection` | evidenced |
| Pools: `scale_commands` insert в **одній** транзакції з оновленням desired state | `pools.py:347-464` — pool `FOR UPDATE` + revision+1, supersede активних команд, `append_audit`, insert команди; усе без внутрішнього commit | `test_pools.py::test_request_scale_is_one_transaction_and_idempotent`, `::test_request_scale_stays_idempotent_when_pool_revision_moved`; `test_adversarial.py::test_scale_command_with_stale_revision_leaves_no_partial_writes` (ні команди, ні audit, ні зміни desired state) | evidenced |
| Pools: станові переходи `requested→draining→awaiting_manual_apply\|applying→applied\|failed\|superseded` (валідація у репозиторії) | `models/pools.py:57-69` (`SCALE_COMMAND_TRANSITIONS` — дослівно §7.6), `repositories/pools.py:467-516` (`transition_scale_command`; `applied` додатково вимагає збігу heartbeat-derived capacity і підтвердженої `applied_pool_revision`) | `test_pools.py::test_scale_command_disallowed_transition_is_rejected`, `::test_applied_requires_heartbeat_derived_capacity_to_match`; `test_adversarial.py::test_scale_command_terminal_and_skipping_transitions_are_rejected`; `test_policies.py::test_scale_command_transition_table_matches_card` (unit); `test_schema_contract.py` — CHECK у **живій** БД дорівнює `SCALE_COMMAND_STATUSES` | evidenced |
| Audit: `append_audit(actor, action, resource, before, after, request_id, idempotency_key)` | `repositories/audit.py:20-63` — усі сім параметрів присутні | `test_pools.py::test_request_scale_is_one_transaction_and_idempotent` (audit у тій самій транзакції) | evidenced |
| Audit: append-only (тригер або відсутність UPDATE grant) | **обидва**: тригер `audit_log_append_only` (`0001:37-46, 81-82`) і відсутність UPDATE/DELETE GRANT (`roles.sql:74`) | `test_migrations.py::test_audit_log_is_append_only`; `test_role_connections.py::test_no_runtime_role_can_update_or_delete_audit_log` (×7 ролей), `::test_audit_log_append_only_trigger_applies_to_owner_too` | evidenced |

### 1.4. PR1 «Indexes (обов'язкові)»

Перевірено тричі незалежно: метадані моделей (`test_metadata.py::test_mandatory_indexes_present`), `pg_indexes` живої БД (`testing-pr1.md` §1 — дослівний вивід `psql`), і текст міграції (`0001`).

| Index картки | Де | Статус |
|---|---|---|
| `crawl_jobs(status, not_before, priority, job_id)` | `models/queue.py:102-104`, `0001:565` | evidenced |
| unique `crawl_jobs(idempotency_key)` | `models/queue.py:93`, `0001:554` | evidenced |
| `crawl_jobs(lease_expires_at) WHERE status='leased'` | `models/queue.py:116-120`, `0001:557` | evidenced |
| `origin_rate_permits(origin, lease_expires_at)` | `models/limiter.py:61`, `0001:640` | evidenced |
| `worker_instances(role, status, last_heartbeat_at)` | `models/pools.py:108-113`, `0001:382` | evidenced |
| `audit_log(created_at)` партиційно | `models/audit.py:29`, `0001:71` (`ON ONLY public.audit_log` — успадковується кожною партицією) | evidenced |
| FR-002 unique `crawl_runs(source_id) WHERE status='running' AND kind='full'` | `models/queue.py:64-69`, `0001:438` | evidenced |
| §9.1 «обов'язкові operational indexes»: `crawl_jobs(status, not_before, priority, job_id)` | той самий; `projection_tasks`/`outbox_events`/`entity_index` — таблиці PR2 | evidenced для PR1; решта — not applicable (PR2) |

### 1.5. PR1 «Тести (integration)»

| Сценарій картки | Тест | Статус |
|---|---|---|
| clean DB → `alembic upgrade head` → `alembic check` → `downgrade` | `test_migrations.py::test_upgrade_check_downgrade_cycle_on_clean_database` | evidenced |
| queue: 4 паралельні claimers × 100 jobs → кожен job рівно один раз | `test_queue.py::test_four_parallel_claimers_claim_each_job_exactly_once` | evidenced |
| heartbeat чужого lease відхиляється | `test_queue.py::test_heartbeat_by_non_owner_is_rejected` | evidenced |
| expired lease → повторний claim іншим worker | `test_queue.py::test_expired_lease_is_recovered_and_reclaimed_by_another_worker` | evidenced |
| повторний enqueue з тим самим ключем не дублює | `test_queue.py::test_enqueue_with_same_idempotency_key_returns_existing_job` + adversarial-варіант | evidenced |
| після `max_attempts` — dead letter і статус `quarantined` | `test_queue.py::test_retry_backoff_then_max_attempts_quarantines_with_dead_letter` | evidenced |
| limiter: 8 паралельних `acquire_permit`, concurrency 1 → рівно 1 активний slot | `test_limiter.py::test_eight_parallel_acquirers_get_exactly_one_slot_with_concurrency_one` (+ варіант із 3) | evidenced |
| сумарна видача за 10 с ≤ 3 при 0.2 rps | `test_limiter.py::test_rate_limit_0_2_rps_grants_at_most_three_in_ten_seconds` (симульований годинник: 101 виклик із кроком 0.1 с → рівно 3) | evidenced |
| expired permit відновлює slot | `test_limiter.py::test_expired_permit_restores_slot_and_release_is_idempotent` | evidenced |
| `block_origin` блокує до `until` | `test_limiter.py::test_block_origin_denies_until_deadline` | evidenced |
| worker pools: stale revision відхиляється | `test_pools.py::test_stale_revision_is_rejected` | evidenced |
| `scale_command` недозволений перехід відхиляється | `test_pools.py::test_scale_command_disallowed_transition_is_rejected` | evidenced |
| ролі: `collector_api_ro` INSERT → permission denied | `test_roles.py::test_read_only_roles_cannot_insert` (+ 17 тестів через реальні login-з'єднання) | evidenced |

### 1.6. «Acceptance PR1»

| Пункт | Доказ | Статус |
|---|---|---|
| Усі команди перевірки зелені **локально** | `implementation-pr1.md:424-466` (після gate 3: ruff/format/mypy зелені, `alembic upgrade head && alembic check` — `No new upgrade operations detected`, `621 passed, 1 skipped` у `-m "not live"`, 103 integration × 3 конфігурації, `pre-commit` 11 hooks); незалежно підтверджено `testing-pr1.md` §1 і моїм прогоном (розділ «Власна верифікація») | evidenced |
| Усі команди зелені **в CI job `integration-postgres`** (service container `postgres:18` pinned digest) | Job написано і сконфігуровано (`ci.yml:65-115`: pinned digest `sha256:86c951e0…`, `COLLECTOR_TEST_REQUIRE_DOCKER=1`, три кроки картки), але **жодного разу не виконувався** — гілка не push-иться на етапі 4 конвеєра. H-1 (єдиний дефект, що робив джоб червоним саме на цій конфігурації) закрито і перевірено локально на беспарольному DSN (`103 passed`) | partial (виконання CI — merge-gate оркестратора, поза етапом 4) |
| Тести вище зелені | розділи 1.5, 1.3 | evidenced |
| `alembic check` без drift | розділ 1.1 С-1; мутація M0 доводить чутливість | evidenced |
| Жодного JSONB payload | розділ 1.1 С-6 | evidenced |
| Ролі й GRANT задокументовані | `sql/roles.sql:8-20` (таблиця «роль → компонент → права»), `deploy/compose/postgres/init/README.md` | evidenced |

### 1.7. §17.2 (рядок WP-01A) і дотичні пункти §16.3

| Пункт | Доказ | Статус |
|---|---|---|
| §17.2 WP-01A: «єдине ownership SQL migrations» | `migrations/postgres/**` + `alembic.ini` належать лише WP-01A; `docs/plan/deps/WP-01A-to-WP-00.md` — процедура для чужих файлів; жодних `migrations/mongo/**` | evidenced |
| §17.2 WP-01A: «control schemas, jobs» | розділи 1.2–1.4 | evidenced (PR1-частина) |
| §17.2 WP-01A: «news schemas, artifact pointers, projection tasks/acks, outboxes, entity index/lineage, release/pin/capacity tables» | у схемі відсутні | not applicable (PR2/PR3 за карткою) |
| §17.2 WP-01A: «clean SQL integration green» | `conftest.py` template DB з нуля + 103 integration passed | evidenced (PR1-частина) |
| §16.3 «повторний parse або projection тієї самої raw відповіді не створює дублікати» | PR2 | not applicable |
| §16.3 «відновлення після kill worker … продемонстровано» | PG-частина: `recover_expired_leases` + `expire_permits` + `mark_stale_instances` з тестами (`test_expired_lease_is_recovered_and_reclaimed_by_another_worker`, `test_expiry_frees_concurrency_slot_even_after_rate_token_spent`, `test_instance_lifecycle_and_stale_detection`) | partial (демонстрація на живих workers — WP-01D/WP-12) |
| §16.3 «один source pause зупиняє нові запити не пізніше 60 секунд» | PG-частина: `sources.state` (`SourceState.PAUSED`) + `set_source_state` з optimistic revision; `source_routes.state`; примусове зупинення запитів — fetcher/scheduler | partial (FR-009 як стан — evidenced; 60-секундний SLO — WP-02/WP-01D) |
| §16.3 «scale `fetch 1→4→1` … scale-down під активним job … kill replica відновлюється після lease expiry» | PG-частина: `scale_commands` (7 станів, одна транзакція з desired state), `drain_requested_at`, `observed_capacity`, lease recovery | partial (сам scale/drain/kill — WP-01D, §16.1 п.15) |
| §16.3 «Compose mode повертає audited scale command; GUI/API не мають Docker socket» | `request_scale(orchestrator='compose')` → `cli_command` + `audit_id/audit_created_at` у тій самій транзакції (`pools.py:429-460`); Docker socket у PR1 не використовується взагалі | evidenced (PG-частина) |

---

## 2. DoD §18 — усі дев'ять пунктів

| # | Пункт §18 | Доказ | Статус |
|---|---|---|---|
| 1 | Реалізація відповідає одному issue/WP і не містить сторонніх змін | `git diff main...HEAD --stat` — усі 59 файлів у owned-переліку картки, крім трьох тестів WP-00 (approved dependency `docs/plan/deps/WP-01A-to-WP-00.md`, п.1 `resolved` рішенням оркестратора) і `pyproject.toml`/`uv.lock`, які картка прямо дозволяє; жодної таблиці PR2/PR3 (розділ 1.2) | evidenced |
| 2 | Formatter, lint, types, unit/contract/integration tests пройшли | власний прогін: ruff `All checks passed!`, format `153 files already formatted`, mypy `Success: no issues found in 60 source files`, `tests/unit/persistence` 44 passed, integration — розділ «Власна верифікація»; звіт реалізації — `621 passed, 1 skipped` | evidenced |
| 3 | Зміна схеми має migration і compatibility evidence | три Alembic-ревізії з `upgrade`/`downgrade`; `alembic check` без drift на чистій БД; мутація M0 доводить чутливість перевірки; `test_schema_contract.py` (15) звіряє CHECK-и і предикат partial index у **живій** БД зі значеннями `collector.contracts.enums` — закриває клас розходжень, якого `alembic check` не бачить | evidenced |
| 4 | Зміна timestamp/matching/release contract має temporal/replay/reproducibility evidence | `src/collector/contracts/**` не змінювався (звіт реалізації, «Dependency-запити»: «Змін shared contracts не потрібно»); PR1 лише споживає контракти | not applicable (аргумент: жоден із трьох контрактів не змінено) |
| 5 | Новий адаптер має manifest/fixtures/golden/coverage/live smoke | адаптерів у PR1 немає | not applicable |
| 6 | Документація, метрики й runbook оновлені | є: docstring-и репозиторіїв із transaction boundary (вимога картки), таблиця ролей у `sql/roles.sql:8-20`, `deploy/compose/postgres/init/README.md`, три звіти етапів, dependency-запит. Немає (і за карткою не мало бути в PR1): `docs/persistence/postgres.md`, `docs/runbooks/migrations.md`, ADR-0005 «PostgreSQL queue + outbox замість брокера» з критеріями переходу §7.2 — усе це картка відносить до PR3 / етапу 5. Метрика `default_partition_row_count` існує як функція з `TODO(WP-12)`, але ще не опублікована у §14.1 | partial (межа картки: docs — PR3/етап 5) |
| 7 | Secret scan чистий; контакти лише в domain collections | `pre-commit run --all-files` — 11 hooks Passed, включно з `gitleaks`/`gitleaks-history` (`implementation-pr1.md:464-465`); у `sql/roles.sql` і `deploy/compose/postgres/init/01-roles.sql` — лише `NOLOGIN` ролі без паролів (перевірено читанням); DSN у виводі CLI завжди через `redacted_dsn`, є окремий тест `test_db_migrate_never_prints_password_even_when_dsn_has_one`; публічних контактів у PR1 немає взагалі | evidenced |
| 8 | Reviewers' findings позначені `fixed`, `accepted with owner/date` або `not applicable` з аргументом | gate 3 (`code-review-pr1.md`): 1 high + 5 medium + 7 low — `fixed` (усі перевірено в коді, розділ 6); 2 low + 3 informational — `accepted (wp-implementer WP-01A, 2026-09-22)` з аргументом і адресою; 1 informational — `not applicable`. gate 2 (`testing-pr1.md`): L-1/L-2/I-1/I-2 — `fixed`, L-3 — `resolved`; **I-3 та I-4 позначені просто `accepted` без owner/дати** (`implementation-pr1.md:390-391`) — знахідка S-1 (low) | partial (2 рядки accepted без owner/дати) |
| 9 | PR злитий лише після CI та required review; commit SHA і release evidence зафіксовані | commit SHA зафіксовані у звітах (`b08eee5`, попередні — у шапці `implementation-pr1.md`); CI ніколи не запускався (гілка не push-иться на етапі 4); merge — крок оркестратора | partial (етап конвеєра після пострев'ю) |

---

## 3. Додаток C — рядки, які покриває WP-01A PR1

| Ціль Додатка C | Що саме покрито PR1 | Доказ | Статус |
|---|---|---|---|
| Керований збір (FR-001, FR-002, FR-004, §3) | FR-001: реєстр джерел як дані — `sources`/`source_policy_versions`/`source_routes`/`source_cursors` з `SourceState`/`RouteState`, лімітами (`requests_per_second`, `max_concurrency`, `burst_tokens`, `crawl_interval_seconds`), `robots_policy`, `manifest_sha256`, розкладом і optimistic revision — зміна без коду ядра. FR-002: `start_run` + partial unique. FR-004 (частина «per-origin rate limit»): canonical PG-лімітер | `models/control.py:44-140`, `repositories/sources.py`, `repositories/crawl_runs.py:100`, `repositories/limiter.py`; `test_control_plane.py` (4), `test_limiter.py` (9), `test_adversarial.py` (12 лімітер/runs) | partial (FR-001 як storage/state — evidenced; enforcement розкладу, robots і conditional GET — WP-02/WP-03) |
| Docker і масштабування (FR-030—FR-033, §7.5—§7.6) | FR-031/FR-032: `worker_pools`/`worker_instances`/`scale_commands` з окремими desired і heartbeat-derived current, drain-наміром і 7 станами scale command; Compose mode → `awaiting_manual_apply` + exact CLI. FR-033/R-53: глобальний token bucket + concurrency за normalized origin | `models/pools.py`, `repositories/pools.py`, `repositories/limiter.py`; `test_pools.py` (11), `test_limiter.py` (9), `test_adversarial.py` | partial (PG-контракти — evidenced; replica/drain/kill/rate тести на живому стеку — WP-01D, §16.1 п.15) |
| Експлуатація (FR-009, §14) | FR-009 як стан: pause/resume/disable через `sources.state` + optimistic revision + reason/actor; dead letters як таблиця з keyset-переглядом і полями resolution | `repositories/sources.py:89-115`, `models/queue.py:147-167`, `repositories/queue.py:333-350` | partial (bounded backfill, replay dead-letter і dashboards — WP-11A/WP-12) |
| Технічна безпека (FR-013, §13) | Розділення credentials БД за компонентами: 8 ролей, пооб'єктні GRANT, `collector_migrate` не в runtime, append-only audit, DSN лише з env/secret-файла і ніколи в логах | `sql/roles.sql`, `roles.py`, `config.py:41-65`; `test_roles.py` (4), `test_role_connections.py` (17), `test_cli_db.py` (4) | evidenced (для §13 «облікові дані БД розділені за компонентами» і «migration role не використовується runtime-процесами») |
| Узгодженість двох БД (FR-020—FR-023, §7.3—§7.4) | §7.3 крок 2 (`parse_attempt` + pointer + `projection_version` + task + outbox) — PR2 | — | not applicable (PR2) |
| Незалежна реалізація (§17, §18) | Єдиний owner PostgreSQL-міграцій; dependency-запит замість правок чужої схеми; три етапи конвеєра (реалізація → тестування → код-рев'ю) із власними звітами і статусами знахідок; CI-job як контракт | `docs/plan/deps/WP-01A-to-WP-00.md`, `docs/plan/reports/WP-01A/*`, `.github/workflows/ci.yml:65-115` | evidenced |

---

## 4. Регресії REVIEW.md — де саме втілено в коді/тестах

| R (REVIEW.md) | Втілення в **коді** | Втілення в **тестах** | Статус |
|---|---|---|---|
| **R-53** (критичний) — горизонтальне масштабування могло множити request rate до джерела; fix: canonical PostgreSQL leased origin permits, expiry recovery, aggregate multi-replica test, pool capacity не обходить source policy | `repositories/limiter.py` цілком: єдине джерело істини — рядок `origin_rate_buckets` під `SELECT … FOR UPDATE` (`:119`), **жодного локального кешу токенів** (незалежно підтверджено код-рев'ю, розділ «Limiter (R-53)»); rate і concurrency обліковуються окремо (`:149-176`); `release_permit`/`expire_permits` ідемпотентні (`:197-244`); `block_origin` (`:247-280`). Конфіг bucket-а походить із policy джерела (`ensure_bucket`, `:51-91`), а не з кількості реплік | `test_limiter.py::test_eight_parallel_acquirers_get_exactly_one_slot_with_concurrency_one` і `…_with_concurrency_three` — **aggregate multi-replica**: 8 незалежних сесій/транзакцій через `asyncio.gather`; `::test_rate_limit_0_2_rps_grants_at_most_three_in_ten_seconds`; `::test_expired_permit_restores_slot_and_release_is_idempotent` (expiry recovery); `test_adversarial.py::test_expiry_frees_concurrency_slot_even_after_rate_token_spent`, `::test_long_pause_refill_never_exceeds_burst_capacity`, `::test_two_origins_do_not_block_each_other`; мутації M3, M5 (`testing-pr1.md` §5) | evidenced |
| **R-28** — незалежним WP бракувало точних storage/event контрактів; fix: ключі, стани, versions, leases, timestamps, обов'язкові indexes | Ключі: `uq_crawl_jobs_idempotency_key`, `uq_scale_commands_idempotency_key`, `uq_crawl_runs_running_full`, `uq_source_routes_*`, `uq_source_cursors_*`, `uq_source_policy_versions_source_id_version`. Стани: CHECK-и, згенеровані **з самих контрактних enum** (`models/base.py:66 enum_check`), тож розбіжність із `collector.contracts.enums` неможлива в моделях. Versions: `Revision BIGINT` + `expected_revision` у кожній мутації. Leases: `lease_owner`/`lease_expires_at`/`leased_at` + CHECK `lease_consistent`; permits — окремий рядок із `acquired_at`/`lease_expires_at`/`released_at`/`release_reason` + CHECK `release_consistent`. Timestamps: усі `timestamptz`. Типізовані помилки `errors.py` як частина контракту для інших WP | `test_metadata.py::test_enum_checks_use_shared_contract_values`, `::test_pr1_tables_are_registered`, `::test_revision_columns_on_versioned_resources`; **головне** — `test_schema_contract.py` (15 тестів, доданий за M-3): читає `pg_get_constraintdef` для 12 CHECK-ів і `pg_indexes.indexdef` для claim-index із **живої** БД і звіряє з `SourceState`/`RouteState`/`DataDomain`/`WorkerRole`/константами коду, вставляє кожне контрактне значення і має мутаційний `::test_drifted_check_constraint_is_detected` | evidenced |
| **R-32** — для history та operational queue бракувало compound indexes; fix: PostgreSQL status/not-before/priority/publish lookup | `models/queue.py:102-104` (`status, not_before, priority, job_id` — дослівно §9.1/картка), `:109-115` (`ix_crawl_jobs_claimable_order` — partial `(priority DESC, not_before, job_id) WHERE status IN ('pending','retry')`, міграція `0002`), `:116-120` (lease_expires_at partial), `:121`; `models/limiter.py:61`; `models/pools.py:108-113`, `:148`; `models/control.py:99`; `models/audit.py:29-31`; `models/queue.py:70` (`crawl_runs(source_id, started_at)`) | `test_metadata.py::test_mandatory_indexes_present` (включно з перевіркою **порядку** колонок claim-index і збігу предиката з `CLAIMABLE_JOB_STATUSES`), `::test_claimable_predicate_matches_statuses_used_by_claim`; `test_schema_contract.py::test_claim_index_predicate_in_database_matches_claimable_statuses`; вимір у `implementation-pr1.md:277-302` (Seq Scan + external merge 331 мс → Index Scan 0.18 мс на 400 000 pending), незалежно відтворений код-рев'ю (I-4, `EXPLAIN` на 200 000) | evidenced |
| R-24 / R-27 (дотично) — жодного domain payload у PostgreSQL | allowlist із трьох JSONB-колонок + CHECK на розмір `args` | `test_metadata.py::test_no_domain_jsonb_and_all_timestamps_are_timestamptz`, `test_migrations.py::test_models_match_card_contracts` | evidenced |
| R-43 (дотично) — розділення source/system time | у PR1 немає source-time колонок; `clock.py` забороняє naive datetime | `test_policies.py::test_resolve_now_requires_aware_utc` | not applicable (PR3) |
| R-36 / R-38 / R-41 | `projection_tasks`, `artifact_upload_claims` | — | not applicable (PR2) |

---

## 5. Q-питання §20, від яких залежить WP

| Q | Питання / safe default ТЗ | Як використано в PR1 | Статус |
|---|---|---|---|
| Q-005 | Retention raw/history? Default: raw/changed history безстроково з cold tier | Жодна таблиця PR1 не має політики видалення: DELETE-шляхів немає в жодному репозиторії; `audit_log` партиційований по місяцях, тож майбутній drop/archive партицій можливий без зміни схеми; `dead_letters` має `resolved_at/resolved_by/resolution` замість видалення | evidenced (safe default використано; retention-політика — WP-12) |
| Q-013 | Production deployment mode? Default: Compose для single-host, Swarm для automatic replicas | `request_scale(orchestrator=...)` — **default `"compose"`** (`pools.py:357`): формує exact `cli_command` і команда очікує `awaiting_manual_apply`; `"swarm"` лишає `cli_command=None` для контролера. Обидва шляхи є в `SCALE_COMMAND_TRANSITIONS` | evidenced (safe default + конфігурований) |
| Q-014 | Увімкнути worker autoscale? Default: ні, manual | `worker_pools.mode` — `server_default="manual"` (`models/pools.py:90`), `POOL_MODES = ("manual", "autoscale")`; жодної autoscale-логіки в PR1 | evidenced (safe default + конфігурований) |
| Q-006 | Інфраструктурний бюджет/SLO | не впливає на схему PR1 | not applicable |
| Q-010 | Production topology MongoDB | WP-01B | not applicable (картка прямо це зазначає) |

---

## 6. Перевірка статусів знахідок попередніх етапів (кожен `fixed` — у коді)

### Gate 3 (`code-review-pr1.md`, вердикт `changes_requested`)

| # | Заявлено | Перевірка рев'юером ТЗ | Підтверджено |
|---|---|---|---|
| H-1 | fixed | `test_cli_db.py:35-41` — гілка `password is None` замість порівняння з `None`; додано `:85-107 test_db_migrate_never_prints_password_even_when_dsn_has_one` із синтетичним паролем і перевіркою `***`/відсутності traceback | так |
| M-1 | fixed | `partitions.py:61-79` — межі `FOR VALUES FROM ('… 00:00:00+00')`; тести `test_month_partition_bounds_are_utc_regardless_of_session_timezone`, `test_partitions_created_from_different_timezones_neither_overlap_nor_leave_gaps` присутні | так |
| M-2 | fixed | нова колонка `worker_instances.drain_requested_at` (`models/pools.py:132`, міграція `0003:257-260`); `heartbeat_instance:230-231` — `stale → draining`, якщо drain запитаний; `set_instance_status:266-269` — `mark_draining` ставить, `mark_ready` знімає; `INSTANCE_TRANSITIONS:50` дозволяє `draining → ready` як зняття барʼєра (§7.6 «survivors відновлюють claim»); тест `test_heartbeat_does_not_resurrect_draining_instance` | так |
| M-3 | fixed | новий `tests/integration/postgres/test_schema_contract.py` (15 тестів, 12 CHECK-констрейнтів + предикат index-у з живої БД + мутаційний тест) | так |
| M-4 | fixed | `limiter.py:272-278` — `available_tokens = 0`, `last_refill_at = until`, `revision += 1`; тест `test_block_origin_resets_refill_so_there_is_no_burst_after_unblock` | так |
| M-5 | fixed | міграція `0003:256` створює `audit_log_default`; `partitions.py:82-115` (`default_partition_name`, `default_partition_sql`, `default_partition_row_count` з TODO(WP-12)); тести `test_audit_log_default_partition_accepts_rows_without_monthly_partition`, `test_fresh_upgrade_head_without_maintenance_can_write_audit`; контракт «зрозуміла помилка замість auto-create» збережено окремим тестом `test_partitioned_table_without_default_still_fails_clearly` | так |
| L-1 | fixed | `pools.py:388-394` — після невдалого захоплення pool ключ ідемпотентності перечитується; тест `test_request_scale_stays_idempotent_when_pool_revision_moved` | так |
| L-2 | fixed | `pools.py:119-121` (`ConflictError` перед вставкою pool); `sources.py` `create_source` — той самий підхід (`ConflictError` в `errors.py:196`); тест `test_creating_existing_pool_raises_conflict_not_integrity_error` | так |
| L-3 | fixed | `roles.py:144-150` — трансляція asyncpg-помилок у `DBAPIError`; `cli.py` `_is_postgres_error` розпізнає їх за модулем; тести `test_db_roles_translates_driver_errors_without_traceback`, `test_postgres_error_predicate_covers_driver_and_sqlalchemy_errors` | так |
| L-4 | fixed (формулювання) | `ops.py:172-176` і `cli.py` help `--check` прямо кажуть «потребує тих самих прав, що й міграції (не read-only роль)» | так |
| L-5 | fixed | `limiter.py:201, 221-222` — опційний `owner_instance` як додатковий предикат; тест `test_release_permit_with_owner_does_not_free_foreign_slot` | так |
| L-6 | fixed | `queue.py:78-83` — `min(delay + jitter, maximum)`; тест `test_backoff_never_exceeds_maximum_even_with_jitter` | так |
| L-7 | accepted (wp-implementer WP-01A, 2026-09-22) | owner і дата вказані; аргумент (напрямок відкату безпечний; семантика монотонності залежить від типу курсора) і адреса (PR2 + вимога власнику discovery WP-01D) наведені | прийнятно |
| L-8 | fixed (docstring) | `queue.py:90-105` — явне попередження про дискримінатор вікна (§9.3 п.3 `planned_at_bucket`) | так |
| L-9 | fixed | `pools.py:258-259` — повтор того самого статусу no-op; тест `test_instance_status_transitions_are_idempotent` | так |
| I-1, I-2, I-3 | accepted (wp-implementer WP-01A, 2026-09-22) | owner/дата, аргумент і конкретна адреса виконання (початок PR2 / картка WP-01D) присутні | прийнятно |
| I-4 | not applicable | підтвердження виміру без дії; ризик розходження закритий у M-3 | прийнятно |

### Gate 2 (`testing-pr1.md`, вердикт `pass`)

L-1, L-2, I-1, I-2 — `fixed` (перевірено: `InvalidValueError` + `PoolDesiredState.validate`, docstring READ COMMITTED + тест, тест `SKIP LOCKED` із двох половин, міграція `0002`). L-3 — `resolved` (owner-рішення оркестратора + dependency-запит). **I-3 та I-4 — `accepted` без owner/дати** → знахідка S-1.

---

## 7. Оцінка відхилень і додатків реалізатора

| Що | Оцінка |
|---|---|
| Міграція `0002_claim_index` понад початковий план | **Прийнято.** Не замінює обов'язковий index картки (обидва лишаються і мають різне призначення — це незалежно підтверджено код-рев'ю I-4); рішення підкріплене виміром (331 мс → 0.18 мс), предикат будується з `CLAIMABLE_JOB_STATUSES`, тож не може розійтися з `claim`. ТЗ не порушено: §9.1 вимагає **наявності** `crawl_jobs(status, not_before, priority, job_id)`, а не відсутності інших index-ів. ADR не потрібен |
| Міграція `0003` (DEFAULT-партиція `audit_log`) | **Прийнято.** Закриває підтверджений дефект M-5, за якого чистий `alembic upgrade head` (команда перевірки картки і перший крок CI) робив `append_audit` неможливим, а отже зупиняв **усі** audited дії control plane. Не суперечить §9.1 (партиціонування по місяцях збережено) і не скасовує обраний контракт «зрозуміла помилка замість auto-create» — він лишився під тестом для таблиць без DEFAULT. ТЗ змінювати не потрібно |
| Нова колонка `worker_instances.drain_requested_at` | **Прийнято.** §7.6 вимагає role-wide drain barrier, який переживає перезапуск claim-у («survivors відновлюють claim після підтвердження new revision»). Зберігати намір drain у тому самому полі, що й спостережуваний стан, було дефектом (M-2). §9.1 перелічує мінімальний контракт `worker_instances`, додаткова колонка його не порушує |
| Зміни у трьох тестових файлах WP-00 | **Прийнято.** Перевірено `git diff` дослівно: 5 змістовних рядків — `["db","migrate"]` прибрано зі списків стабів, `"roles"` додано до очікуваного набору групи `db`, `sqlalchemy`/`alembic` прибрано з `FORBIDDEN_FOUNDATION_DEPS`. Жоден тест не послаблено (набір команд §16.2 і перевірка стабів решти WP лишились), кожна зміна має коментар із посиланням на dependency-запит, п.1 запиту — `resolved` рішенням оркестратора. Це саме та процедура, яку вимагає §17.1 |
| Ієрархія `errors.*` | **Прийнято.** Картка вимагає, щоб інші WP працювали «через типізовані інтерфейси» і не писали SQL; без доменних винятків викликач мусив би ловити `sqlalchemy.exc.*`, тобто знати деталі реалізації. Сім класів відповідають реальним режимам відмови (`StaleRevisionError` ↔ §7.6 stale GUI action, `LeaseNotOwnedError` ↔ §7.2 lease, `ConflictError` ↔ FR-002, `InvalidTransitionError` ↔ §7.6 states) |
| Вихід за межі scope PR1 | **Не виявлено.** У схемі рівно 13 таблиць PR1 (зафіксовано тестом); жодного файла з `migrations/mongo/**`, `src/collector/contracts/**`, `schemas/**`, `docker-compose.yml` — тобто forbidden-перелік картки дотримано |
| `worker_pools` без колонок current replicas/concurrency | **Прийнято з зауваженням** (знахідка S-3). §7.6 прямо називає current «heartbeat-derived», і `observed_capacity`/`transition_scale_command` це реалізують; але §9.1 у рядку `worker_pools` пише «desired/current» поруч, тож інтерпретацію варто зафіксувати одним рядком у звіті/ADR, щоб WP-01D і WP-11A не шукали неіснуючих колонок |

---

## 8. Знахідки пострев'ю

Жодної `critical`/`high`. Жодна не блокує вердикт.

**S-1 (low, DoD §18 п.8) — дві знахідки gate 2 позначені `accepted` без owner/дати.**
`docs/plan/reports/WP-01A/implementation-pr1.md:390-391` — I-3 («heartbeat у межах простроченого lease») і I-4 («ідемпотентність `audit_log` — best effort») мають статус `accepted` з аргументом, але без `owner/дата`, тоді як DoD §18 вимагає саме `accepted with owner/date`, і в таблиці gate 3 цей формат витриманий. Виправлення — два рядки: `accepted (wp-implementer WP-01A, 2026-09-22)`.

**S-2 (informational, §13/FR-036) — audit-запис гарантований лише для scale-команд.**
`repositories/pools.py:429-440` пише `audit_log` у тій самій транзакції, що й зміну desired state, тож scale-дію без сліду створити неможливо. Для решти mutating-операцій control plane (`sources.py:89 set_source_state` — це pause/resume/disable з FR-009, `:170 upsert_route`, `:217 set_route_state`, `:253 upsert_cursor`) audit лишається обов'язком викликача; це задокументовано (`repositories/audit.py:3-5` прямо називає «source state» прикладом), і картка PR1 вимагає лише наявності `append_audit`. Рекомендація власнику: у PR2 або в `docs/persistence/postgres.md` зафіксувати перелік операцій, для яких викликач (WP-11A) **зобов'язаний** додати `append_audit`, інакше §13 «audit log усіх mutating actions» триматиметься лише на дисципліні API-шару.

**S-3 (informational, §9.1) — інтерпретація «desired/current» у `worker_pools` не зафіксована у звіті.**
Див. розділ 7, останній рядок. Один рядок у звіті реалізації або в `docs/persistence/postgres.md` (PR3).

**S-4 (informational, стійкість міграцій) — `0003` імпортує runtime-модуль.**
`migrations/postgres/versions/20260922_0003_default_partition.py:244-247` викликає `partitions.default_partition_sql`/`default_partition_name`. Історична міграція має бути замороженим знімком DDL: якщо в PR2 зміниться формат імені DEFAULT-партиції, зміниться і сенс уже застосованої ревізії `0003`. Ризик зараз мінімальний (обидві функції — чисті рядкові шаблони з валідацією ідентифікатора), але `0001` і `0002` цієї залежності не мають. Рекомендація: у PR2 інлайнити SQL у нові ревізії.

**Пропозицій ADR або змін ТЗ немає.** Жодне відхилення реалізатора не потребує правки §7.2/§7.6/§9.1: усі вони або доповнюють мінімальний контракт, або реалізують те, що §7.6 формулює текстом (heartbeat-derived current, drain barrier).

---

## 9. Підсумок

| Блок | evidenced | partial | missing | not applicable |
|---|---:|---:|---:|---:|
| 1. Acceptance (картка + §17.2 + §16.3) | 55 | 6 | 0 | 6 |
| 2. DoD §18 | 4 | 3 | 0 | 2 |
| 3. Додаток C | 2 | 3 | 0 | 1 |
| 4. REVIEW.md (R-53, R-28, R-32 + дотичні) | 4 | 0 | 0 | 2 |
| 5. Q §20 | 3 | 0 | 0 | 2 |

`missing` — 0. Усі `partial` пояснені межами під-PR1 (таблиці й docs, віднесені карткою до PR2/PR3), етапами конвеєра після пострев'ю (push → CI → merge) або компонентами, яких у проєкті ще немає (workers WP-01D/WP-02). Знахідок `critical`/`high` цього етапу — 0; єдина `high` попереднього етапу — `fixed` і перевірена в коді.

**Вердикт: `accept`.**
