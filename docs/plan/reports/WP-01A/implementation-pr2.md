# WP-01A PR2 — звіт реалізації (`wp/01a-2-artifacts-projection`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR2 «artifacts, upload claims, projection tasks/acks, outboxes, entity index» |
| Branch / worktree | `wp/01a-2-artifacts-projection` / `.worktrees/wp-01a` |
| Картка | `docs/plan/cards/WP-01A.md` — «Спільні вимоги» + «PR2» |
| Dependency, які закриває | `docs/plan/deps/WP-01D-to-WP-01A.md` §2 (LOGIN-ролі + per-role DSN), §3/§5 (`queue.release`), §4 (`command_timeout`) |
| Розділи ТЗ | §5.5, §7.3 (кроки 2, 4, 5), §9.1 (рядки PR2), §9.3, §9.5, §10 п.5, п.8, п.10, §13, §15, §18; REVIEW.md R-27, R-36, R-38/R-41, R-42 |
| Середовище | Windows 11, uv, CPython 3.13.9, PostgreSQL 18 у Docker (`postgres:18@sha256:86c951e0…`, контейнер `wp01a-pg`, loopback-порт 55433) |
| Commits | `c5f6f70` WIP (попередня сесія), `96c1a40` fix схеми/репозиторіїв, `753d0d1` LOGIN-ролі + GRANT PR2, `e5b047d` тести PR2, `2d06588` dependency-відповіді, + фінальний коміт (re-attach `fetches_default`, §5 dependency WP-01D, цей звіт) |

## Що зроблено

Стартовий стан — WIP-коміт `c5f6f70`: міграція 0004, моделі й більшість репозиторіїв були
написані, але жодна перевірка не запускалась. Інвентаризація проти картки:

| Пункт картки PR2 | Стан у WIP | Що зроблено в цій сесії |
|---|---|---|
| Таблиці §9.1 PR2 + міграція `0004` | написано, не перевірено | `upgrade head` з порожньої БД + `alembic check` без drift; `raw_objects` отримала UUID PK (`raw_object_id`) + unique `sha256` — картка вимагає UUID PK для всіх таблиць, крім natural keys PR1; повторний upgrade після downgrade приєднує від'єднану `fetches_default` назад (раніше `CREATE … IF NOT EXISTS` мовчки лишав `fetches` без DEFAULT) |
| Upload claim з fencing | написано | виправлено типізацію; прибрано `delete_expired_claims` (видалення рядка claim ховало б orphan-об'єкт від sweeper-а); тести |
| `record_parse_result` + монотонна `projection_version` | написано | `populate_existing` на заблокованому рядку `entity_index` (після очікування lock лічильник має бути свіжий, не з identity map); тести паралельності |
| Projection queue | написано | тести claim/heartbeat/release/retry/quarantine/recover |
| `acknowledge_projection` | написано | виправлено mypy; тести 3,1,2 / ідемпотентність / bytes / crash-вікно |
| Outbox publisher API | написано | тести, у т.ч. 2 паралельні publisher-и з `SKIP LOCKED` |
| Entity index + keyset | написано | keyset через row comparison `(v, uuid) > (:v, :uuid)`; тест пагінації |
| Транзакційний audit control plane | частково (`block_origin` посилався на неіснуючі `actor`/`request_id`) | `block_origin(actor=…, request_id=…)`; `require_audit_context` — порожні `actor`/`reason` → `InvalidValueError` до першого запису; `upsert_cursor` аудитує **кожен** виклик (кожен змінює `revision`); `upsert_pool` читає RETURNING без кешу identity map; тести |
| `queue.release` | написано | тести |
| LOGIN-ролі + per-role DSN | не було | `collector db roles --with-login`, `roles.apply_logins`, `verify_runtime_login`, GRANT PR2 у `roles.sql`; тести |

### Схема (`migrations/postgres/versions/20260923_0004_artifacts_projection.py`, `models/**`)

- Нові таблиці: `fetches` (RANGE по `fetched_at` + DEFAULT-партиція), `raw_objects`,
  `parse_attempts`, `artifact_upload_claims`, `normalized_artifacts`, `projection_tasks`,
  `projection_acknowledgements`, `entity_index`, `change_events`, `outbox_events`.
- Обов'язкові indexes §9.1: `projection_tasks(status, not_before, priority, task_id)`,
  `outbox_events(published_at, available_at, event_id)`,
  `entity_index(domain, confirmed_projection_version, entity_uuid)`; плюс partial indexes під
  hot paths (`ix_projection_tasks_claimable_order`, `ix_outbox_events_unpublished`).
- Unique-ключі ідемпотентності: `projection_tasks(entity_uuid, projection_version)`,
  `projection_tasks(artifact_id, target_collection)`, `artifact_upload_claims(object_key)`,
  `normalized_artifacts(object_key)`, `raw_objects(sha256)`, `outbox_events(event_id)`,
  `change_events(event_id)`, `entity_index(source_id, source_item_id)`, PK
  `projection_acknowledgements(task_id)`.
- R-27: жодного JSONB; `bytea` лише для готових event bytes (`outbox_events.payload_bytes`,
  `change_events.event_bytes`) з CHECK `octet_length ≤ 262144` (тест
  `test_pr2_tables_carry_no_payload_beyond_bounded_event_bytes`).
- Lineage переживає видалення raw object: `raw_sha256`/`raw_uri` у `fetches`,
  `parse_attempts`, `normalized_artifacts` — колонки без FK.
- **Партиціонування — свідоме відхилення від «Спільних вимог».** Партиціоновано `fetches`.
  `raw_objects`, `change_events`, `outbox_events` лишилися непартиціонованими: їхній головний
  інваріант — глобальний unique (`sha256` §9.3 п.4; `event_id` §7.3/§9.1), а PostgreSQL не
  тримає unique без partition key у ключі, тож місячні партиції зробили б дедуплікацію
  помісячною. Обґрунтування й розглянуті альтернативи — docstring `models/outbox.py` і
  `partitions.py`. Питання для spec-review (див. «Ризики»).
- Рішення картки «insert у місяць без партиції → помилка чи авто-створення»: **ні те, ні те** —
  рядок приймає `fetches_default`, сигнал — `default_partition_row_count` (метрика WP-12);
  `ensure_month_partitions` створює партиції `audit_log` і `fetches` на N місяців уперед.

### Репозиторії (`src/collector/persistence/postgres/repositories/**`)

Усі функції мають docstring із transaction boundary (межа — викликач; `acquire_upload_claim`,
`commit_reference`, `claim_projection_tasks`, `fetch_unpublished` — короткі окремі транзакції).

- `artifacts`: `record_fetch`, `record_raw_object` (дедуплікація за `sha256`, lineage першої
  появи не переписується), `acquire_upload_claim` (reacquire атомарно `claim_generation + 1`
  і новий owner; живий чужий lease → `StaleClaimError`; committed ключ не перебирається),
  `commit_reference` (один UPDATE з предикатом `owner + generation + status='leased' +
  lease_expires_at > now`; повтор тим самим sha256 ідемпотентний), `release_claim`,
  `expire_claims`, `list_orphan_candidates(grace)` (без DB reference — ні claim `committed`,
  ні `raw_objects`/`normalized_artifacts` — і без живого claim, lease сплив > grace тому).
- `projection`: `record_parse_result` (одна транзакція: row lock `entity_index … FOR UPDATE` →
  `normalized_artifacts ON CONFLICT (object_key) DO NOTHING` → `parse_attempts` →
  `projection_version + 1` → `projection_tasks` → `outbox_events(projection.command,
  topic=internal, event_id=task_id)`; повтор для того самого artifact → той самий task,
  `created=False`); `claim_projection_tasks` / `heartbeat_projection_task` /
  `retry_projection_task` / `release_projection_task` / `quarantine_projection_task` /
  `recover_expired_projection_leases`; `acknowledge_projection` (одна транзакція: ack
  `ON CONFLICT (task_id) DO NOTHING` → `GREATEST` для confirmed version → task `succeeded` →
  лише для `applied_to_current AND state_changed` `change_events` + `outbox_events(domain)` з
  bytes receipt без reserialization).
- `outbox`: `fetch_unpublished(limit, topics, lock)` (`FOR UPDATE SKIP LOCKED`, порядок index
  §9.1), `mark_published` (ідемпотентний), `mark_failed` (attempts + backoff
  `BackoffPolicy`), `get_event`, `count_backlog`, `oldest_unpublished_age`.
- `entities`: `upsert_entity` (unique source identity, лічильники не чіпає),
  `get_confirmed_version`, `find_entity`, `set_mongo_document`, `list_entities(domain,
  after=(confirmed_projection_version, entity_uuid), limit)`.
- `queue.release(job_id, owner)`: `leased → pending`, `not_before = now`, `attempt` не
  змінюється, `last_error_*` не пишуться, dead letter не створюється.
- Audit усередині репозиторію, та сама транзакція: `sources.set_source_state`,
  `add_policy_version`, `upsert_route` (лише при створенні), `set_route_state`, `upsert_cursor`
  (кожен виклик), `limiter.block_origin` (лише коли блок справді подовжено),
  `queue.quarantine(owner=None)`, `pools.upsert_pool` (create і update).

### Ролі БД і CLI

- `sql/roles.sql` — GRANT для таблиць PR2 per component (§13 «parser пише лише pointer/task/
  outbox»; projector — claim/ack/confirmed version/change_events/outbox; scheduler — outbox
  publisher, recover projection leases, expire claims; `api_ro`/`export_ro` — SELECT усього) і
  спільна база worker-ролей із запиту WP-01D §2.3 (`crawl_jobs` SELECT/UPDATE, `dead_letters`
  і `worker_pools` SELECT/INSERT, `worker_instances` SELECT/INSERT/UPDATE, `audit_log` лише
  INSERT). Скрипт і далі не містить LOGIN/паролів.
- `roles.py`: `apply_logins` — `ALTER ROLE <runtime-роль> WITH LOGIN NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT PASSWORD '<SCRAM-SHA-256 verifier>'` (verifier
  рахується в Python за RFC 5802/7677, відкритий пароль на сервер не йде); пароль — з DSN-секрету
  `postgres_dsn_<component>` (усі сім обов'язкові; користувач у DSN = роль; роль-член
  `collector_migrate` → відмова); `verify_runtime_login(conn)` для старту runtime WP-01D.
- CLI: `collector db roles [--sql PATH] [--with-login [--secrets-dir DIR]]`; секрети читаються
  до з'єднання з БД; помилки секретів — exit 1 без DSN/пароля у виводі.
- `create_engine(..., command_timeout=...)` (зміна WP-01D у файлі WP-01A) — підтверджено без змін.

### Тести

| Acceptance-пункт картки PR2 | Тест |
|---|---|
| upload claim: stale generation не може commit | `test_upload_claims.py::test_stale_generation_cannot_commit` |
| expired lease → reacquire іншим owner → старий commit падає | те саме + `::test_expired_lease_blocks_commit_even_for_the_owner` |
| concurrent 2 producers одного key → рівно один reference | `::test_two_concurrent_producers_of_one_key_create_exactly_one_reference` |
| `list_orphan_candidates` лише без reference і живого claim | `::test_orphan_candidates_exclude_references_and_live_claims` |
| 3 паралельні `record_parse_result` → 1,2,3 без дірок/дублів | `test_projection.py::test_parallel_record_parse_result_issues_versions_without_gaps_or_duplicates` |
| повтор для того самого artifact → той самий task | `::test_repeated_record_for_same_artifact_returns_same_task` |
| ack 3,1,2 → confirmed = 3 і не зменшується | `::test_out_of_order_acks_never_lower_confirmed_version` |
| повторний ack ідемпотентний | `::test_repeated_ack_is_idempotent_and_emits_no_second_event` |
| `domain.changed` лише для applied AND changed | `::test_out_of_order_acks_…`, `::test_applied_without_state_change_emits_no_domain_event` |
| bytes в outbox побайтово = bytes receipt | `::test_out_of_order_acks_…` (`payload_bytes == receipt.event_bytes`, так само `change_events`) |
| crash-вікно → жодних часткових записів | `::test_crash_between_ack_steps_leaves_no_partial_rows` |
| projection queue claim/heartbeat/complete | `::test_projection_queue_claim_heartbeat_retry_release_and_recover`, `::test_retry_on_last_attempt_quarantines_projection_task` |
| outbox publisher API | `test_outbox_entities.py` (3 тести outbox) |
| entity index upsert + keyset | `test_outbox_entities.py::test_upsert_entity_…`, `::test_list_entities_keyset_pagination_is_complete_and_stable` |
| partitioning: місяць без партиції; helper на N місяців | `test_fetch_partitions.py` (3 тести), `test_migrations.py::test_month_partitions_are_created_and_idempotent` |
| LOGIN-ролі, кожна може рівно те, що їй потрібно | `test_role_logins.py` (6 тестів: реальні репозиторні операції fetcher/parser/projector/scheduler під своєю роллю; fetcher не читає projection/outbox/entity index/`news_*`; parser не пише ack/не публікує; projector не пише artifact pointers; api/export_ro не пишуть) |
| жоден runtime-сервіс не використовує `collector_migrate` | `test_role_logins.py::test_every_runtime_role_logs_in_with_its_own_dsn_and_no_migrate_rights`, `::test_runtime_role_that_is_member_of_migrate_is_refused`, `test_role_connections.py::test_migrate_role_is_not_referenced_by_runtime_code` |
| `db roles --with-login` ідемпотентно, без витоку паролів | `test_cli_db.py::test_db_roles_with_login_…` (2 тести), unit `tests/unit/persistence/postgres/test_role_logins.py` (5 тестів) |
| `queue.release` | `test_queue_release.py` (2 тести) |
| mutating-операція без audit неможлива | `test_control_plane_audit.py` (13 тестів: сигнатура, по одному audit-рядку, rollback, порожні actor/reason) |
| `alembic check` без drift; CHECK-значення = контракти | `test_migrations.py` (+ `test_fetches_default_partition_survives_downgrade_and_is_reattached`), `test_schema_contract.py` (+9 CHECK PR2, predicate projection claim index), unit `test_metadata.py` |

PR1-тести оновлено під нові сигнатури (`actor`/`reason`); `test_role_connections.py::test_parser_cannot_write_audit_log_but_keeps_its_queue`
замінено на `test_parser_appends_audit_but_cannot_read_or_rewrite_it_and_keeps_its_queue` —
parser тепер має рівно INSERT на `audit_log` (bootstrap `upsert_pool` пише audit), але не
SELECT/UPDATE/DELETE.

## Команди та вивід

DSN у виводі нижче — з паролем, заміненим на `***` (одноразовий пароль локального
тестового контейнера). Admin-DSN для тестів: `COLLECTOR_TEST_POSTGRES_ADMIN_DSN=postgresql://collector_test_admin:***@127.0.0.1:55433/postgres`.

```text
$ uv sync --frozen
Checked 66 packages in 7ms
exit=0
$ uv run ruff check .
All checks passed!
exit=0
$ uv run ruff format --check .
236 files already formatted
exit=0
$ uv run mypy src
Success: no issues found in 77 source files
exit=0
```

Порожня БД `alembic_check` (`DROP DATABASE … WITH (FORCE)` + `CREATE DATABASE`),
`COLLECTOR_POSTGRES_DSN=postgresql://collector_test_admin:***@127.0.0.1:55433/alembic_check`:

```text
$ uv run alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_control_queue, WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.
INFO  [alembic.runtime.migration] Running upgrade 0001_control_queue -> 0002_claim_index, WP-01A PR1 (gate 2, I-2): partial index під hot path `claim` (§7.2).
INFO  [alembic.runtime.migration] Running upgrade 0002_claim_index -> 0003_default_partition, WP-01A PR1 (gate 3): DEFAULT-партиція `audit_log` (M-5) і намір drain (M-2).
INFO  [alembic.runtime.migration] Running upgrade 0003_default_partition -> 0004_artifacts_projection, WP-01A PR2: artifacts, upload claims, projection tasks/acks, outboxes, entity index.
exit=0
$ uv run alembic check
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
$ uv run pytest -m integration tests/integration/postgres
============================= test session starts =============================
platform win32 -- Python 3.13.9, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\repos\webscraper\.worktrees\wp-01a
configfile: pyproject.toml
plugins: anyio-4.15.1, asyncio-1.4.0, socket-0.8.1, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collected 165 items

tests\integration\postgres\test_adversarial.py ..................        [ 10%]
tests\integration\postgres\test_cli_db.py ......                         [ 14%]
tests\integration\postgres\test_control_plane.py ....                    [ 16%]
tests\integration\postgres\test_control_plane_audit.py .............     [ 24%]
tests\integration\postgres\test_fetch_partitions.py ...                  [ 26%]
tests\integration\postgres\test_limiter.py .........                     [ 32%]
tests\integration\postgres\test_migrations.py .............              [ 40%]
tests\integration\postgres\test_outbox_entities.py .....                 [ 43%]
tests\integration\postgres\test_pools.py ...........                     [ 49%]
tests\integration\postgres\test_projection.py ..........                 [ 55%]
tests\integration\postgres\test_queue.py .........                       [ 61%]
tests\integration\postgres\test_queue_release.py ..                      [ 62%]
tests\integration\postgres\test_role_connections.py .................    [ 72%]
tests\integration\postgres\test_role_logins.py ......                    [ 76%]
tests\integration\postgres\test_roles.py .....                           [ 79%]
tests\integration\postgres\test_schema_contract.py ..................... [ 92%]
....                                                                     [ 94%]
tests\integration\postgres\test_upload_claims.py .........               [100%]

======================= 165 passed in 232.33s (0:03:52) =======================
exit=0
```

`uv run pytest -m "not live"` (963–964 тести; включно з PG-integration через той самий
admin-DSN і `tests/integration/scaling/**` WP-01D). Прогонів було п'ять: перший — повністю
зелений, у кожному з чотирьох наступних падав **один** тест WP-01D, у трьох різних варіантах
(див. «Ризики»). Останній прогін — на фінальному коді (після re-attach `fetches_default`,
+1 тест):

```text
# прогін 1 (після встановлення тестів PR2, той самий код src/ і tests/, що в HEAD)
940 passed, 23 skipped, 8 warnings in 363.18s (0:06:03)
```

```text
# прогін 5 — фінальний код (дослівно: заголовок + рядки підсумку)
$ uv run pytest -m "not live"
============================= test session starts =============================
platform win32 -- Python 3.13.9, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\repos\webscraper\.worktrees\wp-01a
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0, socket-0.8.1, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collected 964 items
...
FAILED tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising
====== 1 failed, 940 passed, 23 skipped, 8 warnings in 381.87s (0:06:21) ======
exit=1
```

```text
# прогони 2–4 (дослівно: заголовок + рядки підсумку)
$ uv run pytest -m "not live"
============================= test session starts =============================
platform win32 -- Python 3.13.9, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\repos\webscraper\.worktrees\wp-01a
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0, socket-0.8.1, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collected 963 items
...
FAILED tests/integration/scaling/test_worker_runtime_adversarial.py::test_drain_barrier_must_be_set_on_every_instance_of_the_role
====== 1 failed, 939 passed, 23 skipped, 8 warnings in 362.64s (0:06:02) ======
exit=1
...
FAILED tests/integration/scaling/test_worker_runtime_adversarial.py::test_instance_marked_stopped_claims_nothing_and_exits
====== 1 failed, 939 passed, 23 skipped, 8 warnings in 352.89s (0:05:52) ======
exit=1
...
FAILED tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising
====== 1 failed, 939 passed, 23 skipped, 8 warnings in 363.44s (0:06:03) ======
exit=1
```

Ключові рядки падінь (дослівно):

```text
E               Failed: стан не настав за 15.0 с: решта ролі claim-ить далі
tests\integration\scaling\conftest.py:148: Failed

E       AssertionError: зупинений instance не бере tasks
E       assert [UUID('01a0ce...fb34a026fd1')] == []
tests\integration\scaling\test_worker_runtime_adversarial.py:331: AssertionError
2026-09-23 15:32:50 [info     ] worker.drain_barrier           active=True instance_id=01a0ce41-208f-7755-868a-199a3bcc4a52 role=fetch
2026-09-23 15:32:50 [info     ] worker.claimed                 active_tasks=1 count=1 instance_id=01a0ce41-208f-7755-868a-199a3bcc4a52 role=fetch

E       AssertionError: heartbeat справді зависав, а не падав із винятком
E       assert 0 >= 1
```

Перевірка, що нестабільність не внесена PR2:

```text
# ізольований повтор test_drain_barrier_must_be_set_on_every_instance_of_the_role, 5 разів
1 passed in 4.58s
1 passed in 7.55s
1 passed in 4.03s
1 passed in 4.81s
1 passed in 4.74s

# uv run pytest tests/integration/scaling на HEAD PR2, 2 рази
1 failed, 32 passed in 81.18s (0:01:21)
33 passed in 79.42s (0:01:19)

# те саме на базовому коміті 88323ab (git checkout 88323ab -- src tests/integration/scaling
# tests/integration/postgres/conftest.py; після прогону — git checkout HEAD -- src tests), 3 рази
33 passed in 92.02s (0:01:32)
FAILED tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising
1 failed, 32 passed in 84.86s (0:01:24)
33 passed in 37.27s
```

Markdownlint змінених документів:

```text
$ uv run pre-commit run markdownlint-cli2 --files docs/plan/deps/WP-01A-to-WP-01D.md docs/plan/deps/WP-01A-to-WP-00.md deploy/compose/postgres/init/README.md
markdownlint-cli2........................................................Passed
```

## Що не перевірено

- **CI job `integration-postgres`** (trust-auth service container) — PR не відкривався, CI не
  запускався. Тести LOGIN-ролей написані під обидва режими auth (`trust` → «not permitted to
  log in», `scram` → «password authentication failed»), але в `trust` фактично не прогнані.
- **Реальний Compose-стек з per-role DSN** — `init-secrets.sh`/`docker-compose.yml` не належать
  WP-01A (dependency WP-00 §4, WP-01D). `collector db roles --with-login` перевірено через
  CliRunner і прямі LOGIN-з'єднання до тестової БД, не в контейнері `migrate-postgres`.
- **`news_*` для тесту «fetcher не читає `news_*`»** — таблиць ще немає (PR3). Тест перевіряє
  всі таблиці з префіксом `news_` через `has_table_privilege`, тож стане змістовним автоматично
  в PR3; зараз він вакуумний.
- Навантажувальні характеристики (latency `record_parse_result` під row lock при гарячих
  сутностях, розмір `outbox_events` без партицій) — не вимірювались: картка цього не вимагає,
  offline-бенчмарку немає.
- `create_source`/`ensure_bucket` лишилися без audit усередині репозиторію — їх немає в
  переліку картки PR2 (S-2); поведінка PR1 незмінна.

## Ризики

1. **Партиціонування `raw_objects`/`change_events`/`outbox_events` не зроблено** (відхилення від
   «Спільних вимог» заради глобального unique). Ризик — ріст `outbox_events` без retention;
   пом'якшення — publisher/maintenance (WP-12) видаляє або архівує опубліковані рядки;
   `change_events` — archival за `created_at`. Потребує рішення spec-review/оркестратора.
2. **Тест-вартовий WP-01D лишився зеленим**, хоча LOGIN-ролі тепер існують (вмикаються з
   Python, SQL-файли ролей містять лише `NOLOGIN`). Поки WP-01D не переведе сервіси на
   `postgres_dsn_<component>`, runtime і далі ходить superuser-роллю — це вже відоме
   відхилення §13, але вартовий більше не сигналить про нього. Оформлено явним повідомленням
   `docs/plan/deps/WP-01A-to-WP-01D.md` §1; тест WP-01D не редагувався.
3. **Нестабільні тести WP-01D** (`tests/integration/scaling/**`) під навантаженням хоста:
   по одному падінню (3 різні тести) у 4 з 5 повних прогонів; той самий клас збою відтворюється на базовому
   коміті `88323ab`. Імовірна причина для двох drain-тестів — TOCTOU у `_claim_loop`
   (claim-транзакція, що стартувала до барʼєра, бере job). Передано WP-01D (§5 dependency-файлу).
   Для gate: `pytest -m "not live"` може бути червоним не через PR2.
4. **Зміни сигнатур**: `sources.set_source_state(reason: str)` (було `str | None`),
   обов'язковий `actor` у `limiter.block_origin`, обов'язкові `actor`/`reason` у
   `add_policy_version`/`upsert_route`/`set_route_state`/`upsert_cursor`. У `src/` цих функцій
   ніхто, крім тестів WP-01A, не викликає; WP-01D викликає лише сумісні `upsert_pool` і
   `quarantine(owner=…)`.
5. `upsert_cursor` пише audit на кожен discovery-тік — `audit_log` росте пропорційно частоті
   обходу. Це прямо вимога картки (S-2); ретеншн `audit_log` — WP-12/Q-005.
6. Worker-ролі отримали `audit_log` INSERT уже в PR2 (bootstrap `upsert_pool`), а
   `projector`/`translation` — `crawl_jobs` SELECT/UPDATE (runtime WP-01D claim-ить із
   `crawl_jobs` для всіх ролей). Якщо PR3 винесе translation jobs в окрему таблицю, GRANT
   варто звузити.
7. SCRAM verifier рахується без SASLprep, тому паролі обмежено printable ASCII (hex з
   `init-secrets.sh` підходить; інше — явна відмова `RoleLoginError`, не тихий збій логіну).

## Як вимкнути або відкотити

- До злиття/появи workers PR2 — revert PR. Схема: `alembic downgrade 0003_default_partition`
  (downgrade `0004` реалізовано: DROP нових таблиць; `fetches_default` перед DROP від'єднується
  і лишається окремою таблицею з даними). У production — forward-fix міграцією (картка
  «Rollback/disable»).
- LOGIN-ролі: `collector db roles` без `--with-login` їх не вимикає; вимкнути вручну —
  `ALTER ROLE collector_<component> WITH NOLOGIN PASSWORD NULL` для семи ролей (так робить
  teardown тестів). Runtime без per-role DSN продовжує працювати на `postgres_dsn` як у PR1.
- Audit у репозиторіях вимкнути прапорцем не можна (це інваріант §13); відкат — revert
  відповідних змін `sources.py`/`limiter.py`/`pools.py`/`queue.py`.

## Dependency-запити

- `docs/plan/deps/WP-01A-to-WP-00.md` §4 (open): сім секретів `postgres_dsn_<component>` у
  `init-secrets.sh` (+ `.example`), one-shot `migrate-postgres` = `collector db migrate &&
  collector db roles --with-login` з усіма сімома секретами, runtime-сервіси монтують свій DSN.
- `docs/plan/deps/WP-01A-to-WP-01D.md` (new): §1 LOGIN-ролі готові — перевести
  `scheduler`/`worker-*` на per-role DSN, замінити тест-вартовий, викликати
  `verify_runtime_login`; мапінг `WorkerRole` → роль БД (відкрите: `export`); §2 `queue.release`
  готовий; §3 `command_timeout` підтверджено; §4 зміни сигнатур; §5 нестабільні тести scaling.
- Відповідь на `docs/plan/deps/WP-01D-to-WP-01A.md` §2–§5 — у `WP-01A-to-WP-01D.md` (файл
  WP-01D не редагувався).
