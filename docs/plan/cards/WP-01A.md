# Картка WP-01A — PostgreSQL foundation

| Поле | Значення |
|---|---|
| Owner | wp-implementer (єдиний owner SQL migrations до кінця проєкту — інші WP подають dependency-запити) |
| Branch | `wp/01a-postgres` (послідовні PR: `wp/01a-1-control-queue`, `wp/01a-2-artifacts-projection`; PR3 розбито на `wp/01a-3a-queue-outbox-preflight`, `wp/01a-3b-news-translation`, `wp/01a-3c-retention-compaction`, `wp/01a-3d-matching-release-capacity` — рішення оркестратора 2026-09-24) |
| Worktree | `.worktrees/wp-01a` |
| Залежить від | WP-00 PR1 `merged`, WP-01C `merged` |
| Розблоковує | WP-01B, WP-01D, WP-02, WP-03, WP-04, WP-07, WP-09, WP-11A |
| Розмір | L → PR1, PR2, PR3a–PR3d одного owner |
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
- Великі таблиці (`fetches`, `audit_log`) — declarative partitioning по місяцях (`fetched_at`/`created_at`) + helper для створення наступних партицій (maintenance WP-12 викликає). `raw_objects`, `change_events`, `outbox_events` — свідомо непартиціоновані заради глобального unique (`sha256`/`event_id`), за умовами ADR-0007 (`docs/decisions/0007-event-tables-global-unique-over-partitioning.md`).

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

- **LOGIN-ролі per component + per-role DSN (§13, dependency `docs/plan/deps/WP-01D-to-WP-01A.md`,
  рантайм-доказ у `docs/plan/reports/WP-01D/testing-pr1.md` F1 — блокер pilot):** ролі §13 у PR1
  створені `NOLOGIN`, тому всі 8 runtime-процесів WP-01D ходять у PostgreSQL під superuser-роллю
  `collector`, яка ще й виконує міграції. PR2 має: зробити `collector_scheduler`, `collector_fetcher`,
  `collector_parser`, `collector_projector`, `collector_translation`, `collector_api_ro`,
  `collector_export_ro` LOGIN-ролями з паролями з Docker secrets (генерація — `init-secrets.sh`,
  dependency-запит до WP-00, якщо потрібні нові secret-файли); додати `collector db roles --with-login`
  або окрему команду, ідемпотентну до повторного запуску; дати кожному сервісу власний DSN
  (`COLLECTOR_POSTGRES_DSN_FILE` per service); тест — кожна роль може рівно те, що їй потрібно
  (`collector_fetcher` не читає `news_*`, `collector_api_ro` не пише), і жоден runtime-сервіс не
  використовує `collector_migrate`. Після цього тест-вартовий WP-01D (`xfail` на появу LOGIN-ролі)
  має стати зеленим — узгодь з owner WP-01D одним dependency-повідомленням.
- **`queue.release(job_id, owner)` (dependency WP-01D, F2):** повернення lease без інкременту
  `attempt` і без переходу в `quarantined` — для планового drain. Поточний обхід WP-01D покладається
  на `recover_expired_leases`, що затримує повторний claim на TTL.
- **Транзакційний audit для control plane (§13, знахідка S-2 пострев'ю PR1):** операції, для
  яких у PR1 audit лишився обов'язком викликача, отримують `append_audit` усередині репозиторію,
  у тій самій транзакції (патерн `request_scale`): `set_source_state`, `add_policy_version`,
  `upsert_route`/`set_route_state`, `upsert_cursor`, `block_origin`, `quarantine(owner=None)`,
  `upsert_pool` поза scale-командою. Обов'язкові `actor`/`reason`; тест — mutating-операція без
  audit-запису неможлива.
- **Upload claim (§10 п.5, R-38/R-41):** `acquire_upload_claim(object_key, owner, lease)` — unique object key; reacquire атомарно збільшує `claim_generation` і змінює owner; `commit_reference(object_key, generation, sha256, size, uri)` виконується лише з предикатом `claim_generation = :generation AND lease_expires_at > now() AND owner = :owner`, інакше `StaleClaimError`; `list_orphan_candidates(grace)` для sweeper (WP-02/12) — лише без DB reference і без живого claim.
- **Parse/normalized (§7.3 крок 2, §10 п.8):** одна транзакція `record_parse_result(parse_attempt, normalized_artifact_ref, entity_uuid, target_collection)` → `parse_attempts` + `normalized_artifacts` + атомарна видача `projection_version` під advisory lock `pg_advisory_xact_lock(hashtext(entity_uuid))` (або `SELECT ... FOR UPDATE` на `entity_index`) + `projection_tasks` + `outbox_events(projection.command)`. Unique `(entity_uuid, projection_version)` і unique `parse_key` — ідентичність parse-кроку (ADR-0007 D-2): повтор того самого parse-кроку → той самий task, без дубля; той самий artifact у новому fetch → нова версія, той самий рядок `normalized_artifacts`.
- **Projection queue:** `claim_projection_tasks` / heartbeat / complete аналогічно crawl queue; index `projection_tasks(status, not_before, priority, task_id)`.
- **Acknowledgement (§7.3 крок 4, §9.5):** одна транзакція `acknowledge_projection(task_id, receipt: AppliedProjectionReceipt)`: insert `projection_acknowledgements` (PK task_id — повторний ack ідемпотентний, повертає існуючий), `entity_index.confirmed_projection_version = GREATEST(existing, receipt.projection_version)` (ніколи не зменшується), task → `succeeded`, і лише якщо `applied_to_current AND state_changed` — `change_events` + `outbox_events(domain.changed)` із bytes/hash з receipt (`bytea`, без reserialization). Unique `event_id`.
- **Outbox publisher API:** `fetch_unpublished(limit)` за index `outbox_events(published_at, available_at, event_id)`, `mark_published`, `mark_failed(attempts, error, backoff)`.
- **Entity index:** upsert за unique source identity; `get_confirmed_version(entity_uuid)`; keyset pagination `list_entities(domain, after=(confirmed_projection_version, entity_uuid), limit)` за index `entity_index(domain, confirmed_projection_version, entity_uuid)`.

### Тести

- upload claim: stale generation не може commit; expired lease → reacquire іншим owner → старий commit падає; concurrent 2 producers одного key → рівно один reference;
- projection version: 3 паралельні `record_parse_result` для однієї сутності → версії 1,2,3 без дірок/дублів; ідемпотентність — за `parse_key` (ідентичність parse-кроку: `fetch_id`, `raw_sha256`, `parser_version`, `entity_uuid`, `target_collection`), не за вмістом artifact (ADR-0007, D-2): повторний виклик для того самого parse-кроку → той самий task; той самий artifact, отриманий у новому fetch (новий `fetch_id`), → нова версія, той самий рядок `normalized_artifacts` (дедуп за `object_key`/`sha256` окремо від `parse_key`);
- ack: доставка версій у порядку `3,1,2` (receipts з відповідними `applied_to_current`) → `confirmed_projection_version = 3` увесь час не зменшується; повторний ack ідемпотентний; `domain.changed` лише для `applied_to_current AND state_changed`; bytes у `outbox_events` побайтово дорівнюють receipt bytes;
- crash-вікно: ack «між кроками» (симуляція: транзакція відкочена після insert ack) → жодних часткових записів;
- partitioning: insert у `fetches` з датою наступного місяця без партиції — обрано: рядок приймає DEFAULT-партицію (`fetches_default`), а не помилка й не авто-створення; сигнал «партиції відстають» — `default_partition_row_count` (метрика WP-12); helper (`ensure_month_partitions`) створює партиції на N місяців наперед і ідемпотентний.

### Acceptance PR2

Тести вище зелені; `alembic check`; CI зелений; репозиторії задокументовані docstrings з transaction boundary.

---

## PR3 — news, translations, matching, releases, retention, capacity + передумови хвилі 1

**Розбиття (рішення оркестратора, 2026-09-24).** Початковий PR3 (`wp/01a-3-news-matching-release`)
разом із dependency-запитами чернеток WP-01B, WP-02 і WP-04 значно перевищує ~800 рядків
продуктивного коду, тому ділиться на чотири послідовні PR того самого owner-а (worktree
`.worktrees/wp-01a`, кожен — від свіжого `main` після merge попереднього). Порядок — за
критичним шляхом хвилі 1: першим іде те, що потрібне WP-01D PR1c, WP-02 PR2 і WP-01B PR3.

| PR | Branch | Зміст | Розблоковує | Залежить від |
|---|---|---|---|---|
| PR3a | `wp/01a-3a-queue-outbox-preflight` | queue/projection `not_before` і defer, outbox N-2 + purge internal, fencing ack, preflight, validators conditional GET, лічильник збоїв route, денний retry budget, SR-4 reconciler | WP-01D PR1c (merge), WP-02 PR2, WP-01B PR3 | PR2 `merged` — **стартує одразу** (U-3) |
| PR3b | `wp/01a-3b-news-translation` | `news_*`, `translation_segments` (TM), бюджет перекладу, `upsert_article_version` + translation jobs, `quality_results` | WP-04 PR2 | PR3a, WP-01C PR2 |
| PR3c | `wp/01a-3c-retention-compaction` | `retention_pins`, `compaction_runs`, `version_archive_index`, GRANT для compactor під `collector_projector` | WP-01B PR4 | PR3a (порядок з PR3b оркестратор може поміняти, якщо WP-01B PR4 готовий раніше за WP-04 PR2) |
| PR3d | `wp/01a-3d-matching-release-capacity` | matching/resolution, releases, capacity, exports, контроль обсягу `change_events` | WP-07, WP-09, WP-11A, WP-12 (не хвиля 1) | PR3c |

Якщо окремий під-PR усе одно перевищує межу — ділиться далі (`PR3a1`/`PR3a2`) без зміни
порядку. Спільні вимоги PR1–PR2 (Alembic forward-only, `alembic check`, docstrings з transaction
boundary, ролі §13, тести на LOGIN-ролях) діють для кожного під-PR.

### PR3a — `wp/01a-3a-queue-outbox-preflight`

Джерела: чернетки `WP-01B.md` (N-2, purge internal, SR-4), `WP-02.md` (запит до WP-01A п.1–5),
`WP-04.md` (запит до WP-01A п.2в), `WP-01D.md` PR1c.

#### Вимоги

1. **Queue `not_before` (WP-02 п.5, WP-01D PR1c п.1–2):** `queue.retry(..., not_before=None)` —
   явна нижня межа `not_before` (якщо задано — використовується `max(now + backoff,
   not_before)` або саме `not_before`, коли викликач передав уже обчислену табличну затримку;
   семантику зафіксувати в docstring); `queue.release(job_id, owner, *, not_before=None)` —
   defer без спалювання спроби (поточна компенсація `attempt = GREATEST(attempt - 1, 0)`, без
   полів помилки, без dead letter). Ті самі параметри для `retry_projection_task` і
   `release_projection_task`.
2. **Outbox N-2, варіант 2 (рішення оркестратора WP-01B N-2):** колонка
   `outbox_events.delivery_attempts` (int, default 0); `fetch_unpublished` у **тій самій**
   lease-транзакції інкрементує `delivery_attempts` кожного виданого рядка і паркує
   (`parked_at = now`, `last_error_code = "delivery_attempts_exhausted"`) рядки, що досягли межі
   (`max_delivery_attempts`, параметр з default = `DEFAULT_MAX_PUBLISH_ATTEMPTS`), — такі рядки
   не видаються; crash/OOM publisher-а до `mark_failed` не обходить межу. `mark_failed` не
   подвоює лічильник (окремо `attempts` помилок і `delivery_attempts` видач). Column UPDATE
   `delivery_attempts` для `collector_scheduler` (publisher).
3. **`purge_published` чистить і `topic='internal'` (рішення оркестратора WP-01B п.3):**
   internal-рядки `projection.command` ніколи не публікуються (fail-closed, ADR-0007), тому
   видаляються за іншою ознакою — відповідний `projection_tasks` у термінальному стані з ack
   (`succeeded` і є `projection_acknowledgements`) і рядок старший за поріг; рядок, чий task
   не acknowledged або `quarantined`, **не** видаляється. Batch-обмеження `limit`, keyset за
   `event_id`. Виклик — maintenance WP-12.
4. **Preflight-читання за UUID (WP-02 п.1):** `sources.get_fetch_preflight(source_uuid,
   route_id) -> FetchPreflight(source_state, policy: PolicySnapshot (чинна версія),
   route_state, route_revision, route_kind)` одним запитом; `None`/`NotFoundError` для
   відсутнього; доступно `collector_fetcher` (SELECT уже є — перевірити тестом під LOGIN-роллю).
5. **Validators conditional GET (WP-02 п.2):** `artifacts.latest_validators(source_uuid,
   normalized_url) -> Validators(etag, last_modified, fetched_at) | None` — з останнього
   `fetches` з `outcome=success` і `http_status IN (200, 206)` (304 не оновлює validators);
   індекс на партиціонованій `fetches` під цей запит (наприклад `(source_id, url_hash,
   fetched_at DESC)` з `url_hash = sha256(normalized_url)`, якщо `requested_url` задовгий для
   btree) — план запиту без seq scan по всіх партиціях (EXPLAIN у звіті).
6. **Лічильник збоїв route (WP-02 п.3):** `sources.record_route_failure(route_id, actor,
   reason, threshold) -> RouteState` (атомарний інкремент `source_routes.consecutive_failures`;
   при досягненні порогу — `circuit_open` + audit у тій самій транзакції) і
   `reset_route_failures(route_id)` після успіху; GRANT для `collector_fetcher` лише на ці
   колонки.
7. **Fencing ack (WP-01D PR1c п.5):** `acknowledge_projection(..., owner=None)` — якщо `owner`
   задано, у тій самій транзакції перевіряється lease власника (`_lock_owned`), інакше
   `LeaseNotOwnedError`; без `owner` — поточна поведінка (reconciler, рішення WP-01B п.1).
8. **Денний retry budget (WP-02 п.4, §10):** `artifacts.count_retries_since(source_uuid,
   since) -> int` — агрегат за `ix_fetches_source_id_fetched_at` (кількість `fetches` з
   `outcome=retryable` за вікно); межа і реакція — у WP-02.
9. **Reconciler §7.3 крок 5 (spec-review PR2, SR-4)** — перенесено з початкового PR3: «незавершені
   `projection_tasks`» (claimable/leased довше порогу) і «повнота cursor» (найстаріший
   непідтверджений task/outbox row за entity або джерело; quarantined — окремо, не
   блокує лічильник drift, але блокує «повноту»). Keyset, без `OFFSET`. SELECT для
   `collector_projector` (reconciler працює під ним).
10. **GRANT-перевірка** для рішень оркестратора: reconciler під `collector_projector` —
    SELECT на `projection_tasks`, `projection_acknowledgements`, `entity_index`,
    `outbox_events` (без UPDATE публікаційних колонок); scheduler — INSERT `crawl_jobs` для
    задач `projection.reconcile`/`projection.compact` (є, тест).

#### Тести

- queue: `release(not_before=+10 хв)` → `pending`, `attempt` без змін, `not_before` = задане,
  job не claim-иться раніше; `retry(not_before=+2 год)` → `not_before ≥ +2 год`; те саме для
  projection tasks.
- outbox: publisher «помирає» після `fetch_unpublished` (транзакція закомічена, `mark_failed`
  не викликано) N разів → рядок `parked`, решта рядків видаються (немає head-of-line blocking);
  `mark_failed` + повторна видача не рахують одну спробу двічі; `topic='internal'` не видається.
- purge: internal-рядок з acknowledged task і старший за поріг → видалено; з
  неacknowledged/quarantined task → лишився; `domain` неопублікований → лишився.
- preflight: paused джерело/`circuit_open` route/відсутній UUID; під LOGIN-роллю
  `collector_fetcher`.
- validators: 200 з ETag → повертається; пізніший 304 → повертаються ті самі; пізніший 200 з
  новим ETag → новий; EXPLAIN без seq scan партицій.
- route failures: поріг 3 → третій виклик переводить у `circuit_open`, audit-рядок є; `reset`
  обнуляє; конкурентні інкременти не губляться.
- ack fencing: чужий `owner` → `LeaseNotOwnedError`, жодного запису; без `owner` — як раніше.
- SR-4 запити: task leased довше порогу видно; quarantined блокує «повноту cursor».
- ролі: кожна нова операція під своєю LOGIN-роллю проходить, під чужою — permission denied.

#### Acceptance PR3a

Тести вище зелені локально і в CI (`integration-postgres`); `alembic check`; `docs/persistence/
postgres.md` доповнено; `docs/plan/deps/WP-01A-to-WP-01D.md` §7 (N-2) → resolved разом із
WP-01B PR3.

### PR3b — `wp/01a-3b-news-translation`

Джерела: початковий PR3 (news, translations), чернетка `WP-04.md` (запит до WP-01A п.2а–2ґ,
О-1, О-3, О-7). Залежить від WP-01C PR2 (`NewsTranslation`, `TranslationStatus`,
`NewsVersionCreatedEvent`).

#### Таблиці

`news_articles`, `news_article_versions`, `news_translations`, `translation_segments`,
`translation_budget_periods` (нова, рішення оркестратора WP-04 O-1), `quality_results` — за
§9.1 і контрактами WP-01C (`NewsTranslation` §5.4, `SourceTime`).

#### Вимоги

1. **News (§10 п.8, FR-016, R-20):** `upsert_article_version(source identity, content_hash,
   original/cleaned artifact refs, metadata, source time, content_access, *,
   translation_jobs: Sequence[NewJob])` — нова version лише при зміні `content_hash`;
   `body_original_*` nullable для `metadata_only`; **одна транзакція** включає lineage, outbox
   `news.version_created` (payload — контракт WP-01C PR2) **і** INSERT translation jobs у
   `crawl_jobs` з ідемпотентними ключами (рішення оркестратора WP-04 O-3: jobs ставляться тут,
   список обчислює викликач чистою функцією WP-04 `plan_translation_jobs`; репозиторій не
   імпортує `collector.translation`). Без нової version — jobs не вставляються.
2. **GRANT для викликача:** `upsert_article_version` викликає parser-роль (WP-05) →
   `collector_parser` отримує INSERT на `news_articles`, `news_article_versions`, `crawl_jobs`
   (лише для translation jobs — обмеження `job_type LIKE 'translation.%'` через RLS-політику
   або окрему SECURITY DEFINER-функцію; обрати й обґрунтувати) і `outbox_events`
   (`topic='domain'`/`news.version_created`). **`collector_translation` INSERT на `crawl_jobs`
   не отримує** (вартовий WP-04 PR2 «не може INSERT у `crawl_jobs`» зберігається).
3. **Translation segments / TM (§9.3 п.7, FR-017, WP-04 п.2а):** `tm_key CHAR(64)` unique +
   п'ять компонентів окремими колонками (`source_language`, `target_language`,
   `normalized_segment_hash`, `provider`+`model_version`, `glossary_version`) + перекладений
   текст сегмента + `created_at`; `get_segments(tm_keys)` batch і `put_segments(entries)`
   `ON CONFLICT (tm_key) DO NOTHING`. Функція обчислення ключа — у WP-04
   (`collector.translation.memory`, рішення О-2), колонка зберігає готовий hex.
4. **Translation versions:** `record_translation_version(...)` immutable, `INSERT … ON CONFLICT
   DO NOTHING` за `translation_idempotency_key` (рівно одна version при паралельних head/body,
   рішення О-4); `status` — `TranslationStatus` з контракту (включно з `not_required`,
   `translation_failed`); `create_translation_job` — через `upsert_article_version` (п.1).
5. **Бюджет перекладу (О-1, варіант (а)):** `translation_budget_periods(period_start,
   budget_class fresh|backfill, limit_chars NULL = не налаштовано, reserved_chars,
   consumed_chars, revision)`; `reserve(period, class, chars) -> Reservation | BudgetExhausted`
   під row lock (патерн `origin_rate_buckets`), `commit(reservation, actual)`,
   `release(reservation)`; `limit_chars IS NULL` для `backfill` → завжди `BudgetExhausted`
   (Q-008: backfill paused без явного ліміту).
6. **`quality_results` (О-7 — прийнято пропозицію чернетки):** мінімальна таблиця зі статусами,
   тип `translation_rubric` для `translation_quality_result` (§12.3), пов'язана з `crawl_runs`
   або джерелом; до появи API звіти можуть лишатися файлами
   `docs/plan/reports/<WP>/translation-qa.md`.
7. **GRANT `collector_translation` (WP-04 п.2г):** SELECT `news_article_versions`/`news_articles`,
   SELECT/INSERT `translation_segments`, INSERT `news_translations`, SELECT/UPDATE
   `translation_budget_periods`, INSERT `quality_results`; нічого з `crawl_jobs`, крім наявних
   SELECT/UPDATE.

#### Тести

news: та сама стаття з тим самим hash → без нової версії і без jobs; зміна hash → нова версія,
стара незмінна, jobs вставлено в тій самій транзакції (відкат транзакції → ні version, ні jobs,
ні outbox); `metadata_only` без body проходить; повторний `upsert_article_version` → jobs не
дублюються; TM: `put_segments` двічі той самий ключ → один рядок; translation version: два
паралельні `record_translation_version` → один рядок; бюджет: паралельні `reserve` двох сесій
не перевищують `limit_chars`; `backfill` без ліміту → `BudgetExhausted`; ролі:
`collector_translation` не пише у `crawl_jobs`, `collector_parser` не пише в
`news_translations`, `collector_parser` не може вставити job не-translation типу.

### PR3c — `wp/01a-3c-retention-compaction`

Джерела: початковий PR3 (retention), чернетка `WP-01B.md` (PR4, рішення оркестратора п.1 —
compactor під `collector_projector`).

#### Таблиці та операції

- `retention_pins` з owner/reason/scope/expiry + query helper «чи pinned версія/артефакт»
  (викликається повторно безпосередньо перед delete — pin race).
- `compaction_runs` — стани `marked → dry_run → archived → verified → deleted → rolled_back`
  (+ `failed`), переходи в репозиторії, `actor`/`reason`, audit.
- `version_archive_index` — locator `(entity_uuid, projection_version) → part URI/row-group/
  hash`; insert у **тій самій** транзакції, що переводить run у `verified`.
- GRANT: `collector_projector` — SELECT/INSERT/UPDATE `compaction_runs`, INSERT/SELECT
  `version_archive_index`, SELECT `retention_pins`; `collector_api_ro`/`collector_export_ro` —
  SELECT `version_archive_index` (exact read WP-11A).

#### Тести

pin блокує compaction candidate (query helper); `version_archive_index` атомарно з `verified`
(відкат → ні locator, ні `verified`); недозволений перехід run відхиляється; ролі — compactor
під `collector_projector` проходить, під `collector_scheduler` — permission denied на
`version_archive_index`.

### PR3d — `wp/01a-3d-matching-release-capacity` (закриває WP-01A)

Решта початкового PR3.

#### Таблиці

`entity_aliases`, `match_candidates`, `entity_resolution_decisions`, `dataset_releases`,
`release_parts`, `capacity_snapshots`, `exports` — поля з контрактів WP-01C
(`ResolutionDecision`, `ReleaseManifest`, `ReleasePart`).

#### Операції

- **Matching (§9.8):** append-only `entity_resolution_decisions` з `decision_version`, `supersedes_decision_id` FK; `match_candidates` upsert зі score/evidence; `entity_aliases` як materialized projection (перебудова — WP-07/09 через `replace_aliases_for_group` у транзакції).
- **Releases (§9.9):** `create_release(draft)`, переходи стану за `RELEASE_TRANSITIONS` контракту (перевірка у репозиторії), `published` immutable (UPDATE тригер/CHECK або repo guard + тест), `release_parts` з hash/row counts; `supersede`.
- **Capacity (§15.1):** `capacity_snapshots` append-only з класами/actual/forecast/unit cost/confidence/owner.
- **Exports:** мінімальна таблиця зі статусами.
- **Контроль обсягу `change_events` (ADR-0007, spec-review PR2 r2 SR-8):** запит обсягу/темпу росту `change_events` для порівняння з тригером перегляду ADR-0007 (2× річний прогноз §15); якщо тригер спрацював — міграція на місячні партиції + `change_event_ids(event_id PK)`; виклик і алерт — WP-12.

#### Тести

release: недозволений перехід відхиляється, `UPDATE` published → відхилено; resolution decisions
append-only (UPDATE/DELETE → відмова); `supersedes_decision_id` на неіснуюче рішення → FK error.

### Acceptance PR3 (після PR3d закриває WP-01A)

§17.2: «control/news schemas, jobs, artifact pointers, projection tasks/acks, outboxes, entity index/lineage, release/pin/capacity tables; clean SQL integration green». Плюс: `docs/persistence/postgres.md` (ER-огляд, transaction boundaries, ролі, partitioning, як додати міграцію), runbook `docs/runbooks/migrations.md`. Кожен під-PR PR3a–PR3d має власний acceptance (тести вище зелені, `alembic check`, CI green) і власні звіти `docs/plan/reports/WP-01A/*-pr3<x>.md`.

## Rollback/disable

До появи workers — revert PR; міграції forward-only, тому для відкату схеми у dev — `down -v` stateful volume; у production — новий forward-fix migration.

## Docs (етап 5)

`docs/persistence/postgres.md`, `docs/runbooks/migrations.md`, ADR-0005 «PostgreSQL queue + outbox замість брокера» (§7.2 з критеріями переходу), docstrings репозиторіїв.
