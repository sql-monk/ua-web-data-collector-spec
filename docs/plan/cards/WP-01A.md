# Картка WP-01A — PostgreSQL foundation

| Поле | Значення |
|---|---|
| Owner | wp-implementer (єдиний owner SQL migrations до кінця проєкту — інші WP подають dependency-запити) |
| Branch | `wp/01a-postgres` (три послідовні PR: `wp/01a-1-control-queue`, `wp/01a-2-artifacts-projection`, `wp/01a-3-news-matching-release`) |
| Worktree | `.worktrees/wp-01a` |
| Залежить від | WP-00 PR1 `merged`, WP-01C `merged` |
| Розблоковує | WP-01B, WP-01D, WP-02, WP-03, WP-04, WP-07, WP-09, WP-11A |
| Розмір | L → три PR одного owner |
| Розділи ТЗ | §7.2, §7.3 (кроки 2, 4, 5), §7.4 (PITR), §9.1 (усі таблиці + indexes + partitioning), §9.3, §9.5, §10 п.5, п.8, п.10, §13 (ролі БД), §15 (keyset pagination), §16.1 п.3 |
| Рівні тестів §16.1 | 3 Integration (PostgreSQL з нуля), 1 Unit (repository logic без БД, де можливо) |
| Регресії REVIEW.md | R-24, R-27 (без payload JSONB), R-28 (точні storage контракти), R-32 (compound indexes), R-36 (`entity_projection_versions` обов'язковий — PG-частина: `projection_tasks` unique), R-38/R-41 (upload claim lease + `claim_generation` fencing), R-43 (temporal columns), R-53 (origin permits canonical у PG) |
| Q-питання | — (Q-005 retention — таблиці без політики видалення; Q-010 не стосується) |

## Scope

Єдине ownership PostgreSQL-схеми: Alembic-міграції для всіх таблиць §9.1, SQLAlchemy 2 моделі/репозиторії з транзакційними операціями, які інші WP викликають через типізовані інтерфейси: job queue claim/lease/heartbeat/complete/retry/dead-letter, origin rate permits, upload claims, artifact pointers, projection tasks/acks з монотонною `projection_version`, outboxes, entity index, news/translation tables, matching/resolution tables, release/pin/compaction/capacity/audit tables. Окремі DB roles за компонентами. Integration-тести з нуля на реальному PostgreSQL 18 у Docker.

## Out of scope

Бізнес-логіка workers (WP-01D/02/03/04), Mongo (WP-01B), API endpoints (WP-11A), фактичний parser/projector потік (WP-01B/02), матчинг-алгоритми (WP-07/09). Контракти даних — лише споживання `collector.contracts`; зміна контракту → dependency-запит до WP-01C.

## Owned files

`migrations/postgres/**` (Alembic env + versions), `alembic.ini`, `src/collector/persistence/postgres/**`, `src/collector/cli.py` (лише реалізація `db migrate` замість стаба + нова `db roles` для створення ролей), `tests/integration/postgres/**`, `tests/unit/persistence/postgres/**`, `tests/fixtures/postgres/**`, `deploy/compose/postgres/**` (init SQL для ролей у dev), `.github/workflows/ci.yml` (лише додавання job `integration-postgres` із service container), `pyproject.toml` (додати `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, dev: `testcontainers` або використання compose service — обрати; алфавітно), `docs/plan/reports/WP-01A/**`, `docs/plan/deps/WP-01A-to-*.md`.

Forbidden: `src/collector/contracts/**`, `schemas/**`, `docker-compose.yml` (PR2 WP-00 у роботі — потреба у зміні → dependency-запит), `migrations/mongo/**`.

## Спільні вимоги для всіх PR

- SQLAlchemy 2.x (async, `asyncpg`), Alembic forward-only; кожна міграція має `upgrade`, `downgrade` лише де безпечно (інакше `raise NotImplementedError` з поясненням); `alembic upgrade head` з порожньої БД → `alembic check` без drift проти моделей.
- Усі PK — UUID (UUIDv7 із контрактів генерується застосунком; `gen_random_uuid()` як DB default лише для audit/log таблиць). Гроші `amount_minor BIGINT + currency CHAR(3)`; усі timestamps `timestamptz` UTC; source-time колонки nullable + `source_timezone_raw`, `source_time_precision`, `source_time_inferred`, `source_locale_raw` (контракт `SourceTime`).
- Жодного domain payload JSONB для catalog/vehicle (R-27): лише URI/hash/size/schema/version/lineage. JSONB дозволений лише для bounded news/control extensions і явно перелічених полів.
- Optimistic `revision` на versioned resources (sources, policies, worker_pools).
- Repository API: типізовані async-функції над `AsyncSession`, без ORM-магії у викликачів; кожна операція документує transaction boundary. Жодних SQL-рядків у інших WP — лише через ці репозиторії.
- Ролі БД (§13): `collector_migrate` (owner, лише міграції), `collector_scheduler`, `collector_fetcher`, `collector_parser`, `collector_projector`, `collector_translation`, `collector_api_ro`, `collector_export_ro` з мінімальними GRANT на таблиці/послідовності; тест, що `collector_parser` не може писати у `news_translations`, а `collector_api_ro` не може `INSERT`.
- Integration-тести: маркер `integration`, loopback дозволений; PostgreSQL 18 у Docker (compose service `postgres` з PR2 або `testcontainers`); кожен тест — на чистій схемі (`alembic upgrade head` у template DB + `CREATE DATABASE ... TEMPLATE`), без мережі за межі loopback.
- Великі таблиці (`fetches`, `raw_objects`, `change_events`, `outbox_events`, `audit_log`) — declarative partitioning по місяцях (`fetched_at`/`created_at`) + helper для створення наступних партицій (maintenance WP-12 викликає).

---

## PR1 — `wp/01a-1-control-queue`: control plane, job queue, origin limiter, worker pools

### Таблиці

`sources`, `source_policy_versions`, `source_routes`, `source_cursors`, `crawl_runs`, `crawl_jobs`, `origin_rate_buckets`, `origin_rate_permits`, `worker_pools`, `worker_instances`, `scale_commands`, `dead_letters`, `audit_log` — за §9.1 з усіма полями з опису; enum-колонки використовують значення `SourceState`/`RouteState` з контрактів (перевірка через CHECK або PG enum — обрати CHECK для еволюції).

### Репозиторії та операції

- **Queue (§7.2):** `enqueue(job)` з unique idempotency key (повторний enqueue → той самий job, без дубля); `claim(job_types, worker, lease_seconds, limit)` через `FOR UPDATE SKIP LOCKED` з урахуванням `status='pending' AND not_before <= now()` і priority; `heartbeat(job_id, owner)` продовжує lease лише власнику; `complete/retry(backoff, jitter)/quarantine`; `recover_expired_leases()` повертає jobs із простроченим lease у `pending` (attempt зберігається); `max_attempts` → `dead_letters` запис.
- **Crawl runs (FR-002):** `start_run(source_id, kind)` не дозволяє два несумісні повні обходи одного джерела (unique partial index на `(source_id) WHERE status='running' AND kind='full'`).
- **Origin limiter (§7.6, FR-033, R-53):** `acquire_permit(origin, owner, lease)` атомарно (одна транзакція, row lock на bucket): перевіряє `blocked_until`, поповнює токени за `refill_rate` від `last_refill_at`, видає rate token і concurrency slot лише якщо обидва доступні; `release_permit(permit_id)` ідемпотентний; `expire_permits()` повертає прострочені slots; `block_origin(origin, until)` для 429/`Retry-After`. Rate tokens і concurrency обліковуються окремо.
- **Worker pools (§7.6):** CRUD desired state з `revision`; `register_instance/heartbeat/mark_draining/mark_stopped`; `stale` detection за heartbeat age; `scale_commands` insert в одній транзакції з оновленням desired state; станові переходи `requested→draining→awaiting_manual_apply|applying→applied|failed|superseded` (валідація у репозиторії).
- **Audit:** `append_audit(actor, action, resource, before, after, request_id, idempotency_key)`; append-only (тригер або відсутність UPDATE grant).

### Indexes (обов'язкові)

`crawl_jobs(status, not_before, priority, job_id)`; unique `crawl_jobs(idempotency_key)`; `crawl_jobs(lease_expires_at) WHERE status='leased'`; `origin_rate_permits(origin, lease_expires_at)`; `worker_instances(role, status, last_heartbeat_at)`; `audit_log(created_at)` партиційно.

### Тести (integration)

- clean DB → `alembic upgrade head` → `alembic check` → `downgrade` там, де реалізовано;
- queue: 4 паралельні claimers × 100 jobs → кожен job claimed рівно один раз; heartbeat чужого lease відхиляється; expired lease → повторний claim іншим worker; повторний enqueue з тим самим ключем не дублює; після `max_attempts` — dead letter і статус `quarantined`;
- limiter: 8 паралельних `acquire_permit` на origin із concurrency 1 і 0.2 rps → рівно 1 активний slot, сумарна видача за 10 с ≤ 3 (rate), expired permit відновлює slot; `block_origin` блокує до `until`;
- worker pools: stale revision відхиляється; `scale_command` недозволений перехід відхиляється;
- ролі: `collector_api_ro` INSERT → permission denied.

### Команди перевірки

```bash
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run mypy src
uv run alembic upgrade head && uv run alembic check
uv run pytest -m "not live"
uv run pytest -m integration tests/integration/postgres
```

### Acceptance PR1

Усі команди зелені локально та в CI job `integration-postgres` (service container `postgres:18` pinned digest); тести вище зелені; `alembic check` без drift; жодного JSONB payload; ролі й GRANT задокументовані.

---

## PR2 — `wp/01a-2-artifacts-projection`: artifacts, upload claims, projection tasks/acks, outboxes, entity index

### Таблиці

`fetches`, `raw_objects`, `parse_attempts`, `artifact_upload_claims`, `normalized_artifacts`, `projection_tasks`, `projection_acknowledgements`, `entity_index`, `change_events`, `outbox_events` — за §9.1.

### Операції

- **Транзакційний audit для control plane (§13, знахідка S-2 пострев'ю PR1):** операції, для
  яких у PR1 audit лишився обов'язком викликача, отримують `append_audit` усередині репозиторію,
  у тій самій транзакції (патерн `request_scale`): `set_source_state`, `add_policy_version`,
  `upsert_route`/`set_route_state`, `upsert_cursor`, `block_origin`, `quarantine(owner=None)`,
  `upsert_pool` поза scale-командою. Обов'язкові `actor`/`reason`; тест — mutating-операція без
  audit-запису неможлива.
- **Upload claim (§10 п.5, R-38/R-41):** `acquire_upload_claim(object_key, owner, lease)` — unique object key; reacquire атомарно збільшує `claim_generation` і змінює owner; `commit_reference(object_key, generation, sha256, size, uri)` виконується лише з предикатом `claim_generation = :generation AND lease_expires_at > now() AND owner = :owner`, інакше `StaleClaimError`; `list_orphan_candidates(grace)` для sweeper (WP-02/12) — лише без DB reference і без живого claim.
- **Parse/normalized (§7.3 крок 2, §10 п.8):** одна транзакція `record_parse_result(parse_attempt, normalized_artifact_ref, entity_uuid, target_collection)` → `parse_attempts` + `normalized_artifacts` + атомарна видача `projection_version` під advisory lock `pg_advisory_xact_lock(hashtext(entity_uuid))` (або `SELECT ... FOR UPDATE` на `entity_index`) + `projection_tasks` + `outbox_events(projection.command)`. Unique `(entity_uuid, projection_version)` і unique artifact projection key (повторний виклик для того самого artifact → той самий task, без дубля).
- **Projection queue:** `claim_projection_tasks` / heartbeat / complete аналогічно crawl queue; index `projection_tasks(status, not_before, priority, task_id)`.
- **Acknowledgement (§7.3 крок 4, §9.5):** одна транзакція `acknowledge_projection(task_id, receipt: AppliedProjectionReceipt)`: insert `projection_acknowledgements` (PK task_id — повторний ack ідемпотентний, повертає існуючий), `entity_index.confirmed_projection_version = GREATEST(existing, receipt.projection_version)` (ніколи не зменшується), task → `succeeded`, і лише якщо `applied_to_current AND state_changed` — `change_events` + `outbox_events(domain.changed)` із bytes/hash з receipt (`bytea`, без reserialization). Unique `event_id`.
- **Outbox publisher API:** `fetch_unpublished(limit)` за index `outbox_events(published_at, available_at, event_id)`, `mark_published`, `mark_failed(attempts, error, backoff)`.
- **Entity index:** upsert за unique source identity; `get_confirmed_version(entity_uuid)`; keyset pagination `list_entities(domain, after=(confirmed_projection_version, entity_uuid), limit)` за index `entity_index(domain, confirmed_projection_version, entity_uuid)`.

### Тести

- upload claim: stale generation не може commit; expired lease → reacquire іншим owner → старий commit падає; concurrent 2 producers одного key → рівно один reference;
- projection version: 3 паралельні `record_parse_result` для однієї сутності → версії 1,2,3 без дірок/дублів; повторний виклик для того самого artifact → той самий task;
- ack: доставка версій у порядку `3,1,2` (receipts з відповідними `applied_to_current`) → `confirmed_projection_version = 3` увесь час не зменшується; повторний ack ідемпотентний; `domain.changed` лише для `applied_to_current AND state_changed`; bytes у `outbox_events` побайтово дорівнюють receipt bytes;
- crash-вікно: ack «між кроками» (симуляція: транзакція відкочена після insert ack) → жодних часткових записів;
- partitioning: insert у `fetches` з датою наступного місяця без партиції → зрозуміла помилка або авто-створення (обрати, задокументувати); helper створює партиції на N місяців наперед.

### Acceptance PR2

Тести вище зелені; `alembic check`; CI зелений; репозиторії задокументовані docstrings з transaction boundary.

---

## PR3 — `wp/01a-3-news-matching-release`: news, translations, matching, releases, retention, capacity

### Таблиці

`news_articles`, `news_article_versions`, `news_translations`, `translation_segments`, `entity_aliases`, `match_candidates`, `entity_resolution_decisions`, `dataset_releases`, `release_parts`, `retention_pins`, `compaction_runs`, `version_archive_index`, `capacity_snapshots`, `exports`, `quality_results` — за §9.1; поля з контрактів WP-01C (`ResolutionDecision`, `ReleaseManifest`, `ReleasePart`, `NewsTranslation` §5.4, `SourceTime`).

### Операції

- **News (§10 п.8, FR-016, R-20):** `upsert_article_version(source identity, content_hash, original/cleaned artifact refs, metadata, source time, content_access)` — нова version лише при зміні `content_hash`; `body_original_*` nullable для `metadata_only`; транзакція включає lineage + outbox `news.version_created` (для WP-04 translation job).
- **Translations (§9.3 п.7, FR-017):** `create_translation_job(article_version_id, target='uk', provider, model, glossary)` з unique idempotency key; `translation_segments` з ключем TM (source lang, target lang, normalized segment hash, provider/model, glossary version); `record_translation_version` immutable; `status` enum включно з `not_required`, `translation_failed`.
- **Matching (§9.8):** append-only `entity_resolution_decisions` з `decision_version`, `supersedes_decision_id` FK; `match_candidates` upsert зі score/evidence; `entity_aliases` як materialized projection (перебудова — WP-07/09 через `replace_aliases_for_group` у транзакції).
- **Releases (§9.9):** `create_release(draft)`, переходи стану за `RELEASE_TRANSITIONS` контракту (перевірка у репозиторії), `published` immutable (UPDATE тригер/CHECK або repo guard + тест), `release_parts` з hash/row counts; `supersede`.
- **Retention (§9.7):** `retention_pins` з owner/reason/scope/expiry; `compaction_runs` стани `marked → dry_run → archived → verified → deleted → rolled_back`; `version_archive_index` locator `(entity_uuid, projection_version) → part URI/row-group/hash` — insert у тій самій транзакції, що переводить run у `verified`.
- **Capacity (§15.1):** `capacity_snapshots` append-only з класами/actual/forecast/unit cost/confidence/owner.
- **Quality/exports:** мінімальні таблиці зі статусами; `quality_results` пов'язані з `crawl_runs`.

### Тести

news: та сама стаття з тим самим hash → без нової версії; зміна hash → нова версія, стара незмінна; `metadata_only` без body проходить; translation job idempotent; release: недозволений перехід відхиляється, `UPDATE` published → відхилено; pin блокує compaction candidate (query helper); `version_archive_index` атомарно з `verified`; ролі: `collector_translation` не пише у `crawl_jobs`.

### Acceptance PR3 (закриває WP-01A)

§17.2: «control/news schemas, jobs, artifact pointers, projection tasks/acks, outboxes, entity index/lineage, release/pin/capacity tables; clean SQL integration green». Плюс: `docs/persistence/postgres.md` (ER-огляд, transaction boundaries, ролі, partitioning, як додати міграцію), runbook `docs/runbooks/migrations.md`.

## Rollback/disable

До появи workers — revert PR; міграції forward-only, тому для відкату схеми у dev — `down -v` stateful volume; у production — новий forward-fix migration.

## Docs (етап 5)

`docs/persistence/postgres.md`, `docs/runbooks/migrations.md`, ADR-0005 «PostgreSQL queue + outbox замість брокера» (§7.2 з критеріями переходу), docstrings репозиторіїв.
