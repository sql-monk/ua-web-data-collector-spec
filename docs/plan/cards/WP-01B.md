# Картка WP-01B — MongoDB domain foundation

| Поле | Значення |
|---|---|
| Owner | wp-implementer — **єдиний owner Mongo validators/index migrations** (`migrations/mongo/**`) до кінця проєкту (§17.1); WP-07/WP-09 та інші подають зміни Mongo-схеми dependency-запитом `docs/plan/deps/<WP>-to-WP-01B.md` |
| Branch | чотири послідовні PR: `wp/01b-1-mongo-schema`, `wp/01b-2-projector`, `wp/01b-3-ack-reconciler`, `wp/01b-4-compaction` |
| Worktree | `.worktrees/wp-01b` |
| Залежить від | WP-00 (PR1–PR4) `merged`; WP-01C `merged 1f2fbc8`; WP-01A PR1 `merged 758c68c`, PR2 `merged 43ee69f` — достатньо для PR1–PR2; **WP-01A PR3 `pending`** — потрібен для PR3 (reconciler-запити SR-4, лічильник доставок outbox N-2) і PR4 (`retention_pins`, `compaction_runs`, `version_archive_index`); WP-01D PR1 `merged f87df17`, PR1b `merged de517cf`; для PR3 — dependency WP-01B→WP-01D (queue backend `projection_tasks`, розділ «Dependency-запити») |
| Розблоковує | WP-07, WP-09 (domain `core` + validators), WP-11A (exact-version read, archive locator), WP-12 (projection/drift/compaction метрики, compactor у lifecycle), WP-14 (E2E offline §16.1 п.4) |
| Розмір | XL → чотири PR одного owner; кожен PR ≤ ~800 рядків **продуктивного** коду (без тестів, fixtures, docs). Якщо PR3 перевищує межу — ділиться на PR3a (handler+ack+publisher) і PR3b (reconciler+restore) тим самим owner |
| Розділи ТЗ | §7.3 кроки 3–5, §7.4, §8 (MongoDB 8.0 RS, PyMongo Async, concerns, retry транзакцій), §9.2, §9.3 п.5, §9.5, §9.6 (late arrival у version history), §9.7, §13 (Mongo credentials за компонентами; PG-роль `collector_projector`), §14.1 (projection/drift/compaction лічильники), §15 (per-task transaction, `_id` cursor), §16.1 п.3, п.11, §16.3, FR-020—FR-022, FR-025, Додаток C «Узгодженість двох БД», «Керована історія» |
| Рівні тестів §16.1 | 3 Integration (PostgreSQL 18 + MongoDB 8.0 replica set + object store), 11 Compaction, 1 Unit |
| Регресії REVIEW.md | R-24 (polyglot: domain payload лише в Mongo), R-25 (CAS, `3,1,2`), R-26 (Mongo receipt ≠ PG acknowledgement), R-31 (одна Mongo-транзакція на task; batch лише dispatch), R-34 (primary/majority/snapshot, retry transient/unknown commit, межа single-member RS), R-36 (`entity_projection_versions` для кожної task), R-37 (byte-equivalent event після crash), R-40 (Mongo — serving projection, не canonical), R-44 (retention pins, 90 днів hot, Parquet, rollback window), R-50 (locator до delete) |
| Q-питання | Q-010 (single-member RS локально; ADR у етапі 5), Q-005 (unchanged projection records 90 днів hot → verified Parquet) |

## Scope

Mongo-частина межі PostgreSQL/MongoDB (§7.3): versioned `$jsonSchema` validators і всі обов'язкові indexes §9.2 як forward-only Mongo-міграції; async-репозиторії над PyMongo Async API; Mongo Projector (одна Mongo-транзакція на task: exact version record + CAS current + optional observation + `applied_projection_receipt` з готовими event bytes); інтеграція projector-а в runtime WP-01D і PG-acknowledgement через репозиторій WP-01A; reconciler (FR-022) і DR-rebuild/restore (§7.4); outbox publisher loop з лічильником доставок (N-2); compaction/archive locator (§9.7). Окремі Mongo-користувачі за компонентами (§13).

## Out of scope

- Контракти даних (`src/collector/contracts/**`, `schemas/**`) — лише споживання; нові моделі → dependency-запит до WP-01C.
- PostgreSQL-схема, репозиторії, GRANT (`sql/roles.sql` фактично лежить у `src/collector/persistence/postgres/sql/roles.sql`) — WP-01A; потреби → dependency-запит.
- Worker runtime (claim/lease/heartbeat/drain, реєстр handler-ів, scheduler tick) — WP-01D; WP-01B пише лише доменні handler-и.
- Доменні `core`/`attributes` vehicle/catalog, reviews/questions/contacts як типізовані моделі — WP-07/WP-09 (через WP-01C і WP-01B); read API `409 projection_inconsistent` і export — WP-11A; dashboards/алерти — WP-12; S3 upload claim протокол записувача — WP-02.

## Input / output contracts (DoR §4.1)

| Напрям | Контракт @ версія | Де |
|---|---|---|
| Input | `projection.command@1.0` (`ProjectionCommand`) | `src/collector/contracts/projection.py`, `schemas/events/projection_command.v1.json` |
| Input | `NormalizedArtifactRef@1.0` + bytes normalized artifact з object store | `src/collector/contracts/artifacts.py`, `schemas/common/normalized_artifact_ref.v1.json` |
| Input | **normalized projection payload — контракту немає** (що саме лежить в artifact: `entity_kind`, `source`, `identity_hash`, `core`, `attributes`, `latest_state`, `time`, observation-поля) | dependency WP-01B→WP-01C (блокер PR2) |
| Input | PG-репозиторії WP-01A PR2: `projection.claim_projection_tasks`/`heartbeat_projection_task`/`retry_projection_task`/`release_projection_task`/`quarantine_projection_task`/`recover_expired_projection_leases`/`acknowledge_projection`/`get_projection_task`/`get_acknowledgement`; `entities.get_confirmed_version`/`list_entities`/`set_mongo_document`; `artifacts.get_normalized_artifact`; `outbox.fetch_unpublished`/`mark_published`/`mark_failed`/`list_parked`/`unpark` | `src/collector/persistence/postgres/repositories/**`, `docs/persistence/postgres.md` §3 |
| Output (Mongo) | current document: `current_document_base@1.0`, `schema_version: 1` (int, ADR-0004) | `src/collector/contracts/current.py`, `schemas/mongo/current_document_base.v1.json` |
| Output (Mongo) | `applied_projection_receipt@1.0` (`AppliedProjectionReceipt`) | `schemas/mongo/applied_projection_receipt.v1.json` |
| Output (Mongo) | `entity_projection_version`, offer/listing observation, seller/contact observation, review/question — **snapshot-ів немає** | dependency WP-01B→WP-01C |
| Output (PG, через WP-01A) | `projection_acknowledgement@1.0` (`ProjectionAcknowledgement.from_receipt`), `domain.changed` bytes з receipt | `acknowledge_projection` — без повторної серіалізації (ADR-0003) |
| Output (event) | `domain_changed_event@1.0` через `encode_event` (canonical bytes, SHA-256, ліміт 256 KiB → `event_artifact`) | `src/collector/contracts/events.py`, ADR-0003 |
| Output (Mongo schema) | версіоновані validators/indexes `migrations/mongo/NNNN_<name>.py` + службова collection застосованих міграцій | створюється в цьому WP |

## Fixtures і provenance

- Існують (read-only, owner WP-01C): `tests/fixtures/contracts/documents/{projection_command,applied_projection_receipt,current_document_base,domain_changed_event,projection_acknowledgement}.v1.0.json`, `tests/fixtures/contracts/factories.py`.
- Створюються в цьому WP: `tests/fixtures/mongo/**` — **синтетичні** normalized payloads для `catalog_item`, `catalog_offer`, `vehicle_listing`, `seller` (кілька версій однієї сутності з/без зміни state hash; payload >256 KiB для шляху `event_artifact`; невалідний payload для quarantine). Provenance: згенеровано вручну/фабрикою з контрактів WP-01C, без даних реальних джерел і без реальних контактів (§18: публічні контакти — лише в domain collections). Кожен файл — поле/коментар `provenance: synthetic, WP-01B`.

## Owned files

`migrations/mongo/**` (створюється), `src/collector/persistence/mongo/**`, `src/collector/workers/projector.py`, `src/collector/workers/reconciler.py`, `src/collector/workers/compactor.py` (створюються в цьому WP; лише доменні handler-и за контрактом `TaskHandler`), `src/collector/workers/publisher.py` (створюється в цьому WP — **Відкрите для оркестратора:** підтвердити, що publisher loop N-2 як доменний модуль дозволено класти в `src/collector/workers/`, owner якого WP-01D; альтернатива — `src/collector/persistence/mongo/`-незалежний `src/collector/outbox/`), `src/collector/cli.py` (лише тіло `db ensure-mongo`: гілка `--validators/--indexes` замість `not_implemented("WP-01B")`; прапорець `--users` — див. PR1), `tests/integration/mongo/**`, `tests/unit/persistence/mongo/**`, нові файли `tests/unit/workers/test_{projector,reconciler,compactor,publisher}*.py`, `tests/fixtures/mongo/**`, `.github/workflows/ci.yml` (лише новий job `integration-mongo`), `pyproject.toml` + `uv.lock` (лише додавання залежностей, алфавітно), `docs/plan/reports/WP-01B/**`, `docs/plan/deps/WP-01B-to-*.md`; етап 5 — `docs/persistence/mongo.md`, `docs/runbooks/{reconcile,restore-mongo,compaction}.md`, ADR.

Forbidden: `src/collector/contracts/**`, `schemas/**`, `tests/contract/**`, `tests/fixtures/contracts/**` (WP-01C); `migrations/postgres/**`, `src/collector/persistence/postgres/**` включно з `sql/roles.sql`, `alembic.ini`, `tests/integration/postgres/**` (WP-01A); `src/collector/workers/{runtime,handlers,roles,scheduler,config,session,advisory,liveness,login,signals}.py`, `src/collector/orchestration/**`, `tests/integration/scaling/**`, наявні `tests/unit/workers/**` (WP-01D); `docker-compose.yml`, `deploy/**`, `Dockerfile*`, `tests/unit/test_compose_config*.py` (WP-00/WP-01D — зміни лише dependency-запитом); `web/**`.

## Як projector-worker підключається до runtime WP-01D

Факти з коду: `WorkerRuntime._claim`/`_report` (`src/collector/workers/runtime.py`) жорстко працюють із `crawl_jobs` через `queue_repo.claim/complete/retry/quarantine/release`; `Task` (`handlers.py`) — знімок `crawl_jobs`-рядка; реєстр `HANDLER_FACTORIES[WorkerRole.PROJECTOR]` наповнюється «при імпорті модуля ролі», але жоден код не імпортує доменні модулі; дефолтний тік scheduler-а (`scheduler.make_maintenance_tick`) не викликає `recover_expired_projection_leases`. Projector працює з **`projection_tasks`**, а завершення task робить `acknowledge_projection` (task → `succeeded` у тій самій PG-транзакції, що ack). Тому:

1. WP-01B реалізує `ProjectorHandler(TaskHandler)` у `src/collector/workers/projector.py`: `job_types` — `target_collection`-и, `check_ready()` — Mongo `hello` на primary + наявність validators поточної версії, `handle(task)` → `TaskResult` з receipt; **handler не пише статус task і не робить ack сам** (правило 2 `docs/workers.md` §5).
2. Потрібне від WP-01D (dependency-запит, блокер PR3): (а) queue backend для ролі `projector` над `projection_tasks` (claim/heartbeat/release/retry/quarantine через функції WP-01A); (б) завершення з результатом: runtime у своїй report-транзакції викликає `acknowledge_projection(session, task_id, receipt)` замість `queue.complete`; (в) мапінг роль → модуль handler-а (lazy import у `resolve_handler`), щоб `HANDLER_FACTORIES[WorkerRole.PROJECTOR] = ...` реально виконувався; (г) реєстр доменних тіків scheduler/maintenance (для `recover_expired_projection_leases` і publisher-а). Форма API — рішення WP-01D.
3. Реєстрація в WP-01B: `HANDLER_FACTORIES[WorkerRole.PROJECTOR] = lambda role: ProjectorHandler(...)` у `collector.workers.projector`; Mongo-клієнт і artifact reader — з env/Docker secrets, один `AsyncMongoClient` на процес.

---

## Dependency-запити, які подає WP-01B

| Файл | До | Що | Блокує |
|---|---|---|---|
| `docs/plan/deps/WP-01B-to-WP-01C.md` | WP-01C | моделі + snapshots: `EntityProjectionVersion` (§9.2), `ObservationRecord` (offer/listing; `ObservationReason` уже є), normalized projection payload (input projector-а); мінімальні `SellerContactObservation`, `ReviewQuestion` (§9.2 таблиця) або явна згода, що їх додають WP-07/WP-09 | PR1 (validators цих collections), PR2 |
| `docs/plan/deps/WP-01B-to-WP-01D.md` | WP-01D | п.(а)–(г) розділу вище | PR3 |
| `docs/plan/deps/WP-01B-to-WP-01A.md` | WP-01A (PR3) | (1) N-2: `delivery_attempts` у `fetch_unpublished` + паркування в тій самій lease-транзакції (див. PR3); (2) підтвердження SR-4-запитів reconciler-а; (3) GRANT для ролі, під якою працюють reconciler/compactor (див. «Відкрите»); (4) доля `topic='internal'` рядків `projection.command`, які ніхто не публікує | PR3, PR4 |
| `docs/plan/deps/WP-01B-to-WP-00.md` | WP-00 (+ WP-01D для `projector-worker`) | секрети `mongo_uri_<component>` в `init-secrets.sh`; `ensure-mongo` command `collector db ensure-mongo --validators --indexes [--users]`; `projector-worker`: Mongo-секрет, `depends_on: minio`, мережа до object store; тест-вартовий compose, що scheduler/fetcher не монтують Mongo-секретів (§13) | PR1 (compose), PR3 |

**Відкрите для оркестратора:** подати `WP-01B-to-WP-01C.md` і `WP-01B-to-WP-01D.md` одразу при старті PR1 (а не на PR2/PR3), бо обидва — на критичному шляху.

## Спільні вимоги для всіх PR

- **Mongo-клієнт (§8, §9.5, R-34):** `pymongo.AsyncMongoClient` (є в `pymongo>=4.15`), `readPreference=primary`, `readConcern=majority`, `writeConcern=majority`, транзакції `snapshot`, `retryWrites=true`, `uuidRepresentation=standard`, `tz_aware=True`, явні `serverSelectionTimeoutMS`/`socketTimeoutMS`/`maxTimeMS`. UUID зберігаються як BSON Binary subtype 4, datetime — BSON date UTC (рішення WP-01B, фіксується в `docs/persistence/mongo.md`; validators генеруються з snapshot-ів `schemas/mongo/*.json` з перетворенням `string/uuid` → `binData`, `date-time` → `date`, `$ref` інлайниться).
- **Межа транзакції:** одна task/entity — одна Mongo-транзакція; batch дозволений лише для читання/dispatch; жодного unordered bulk через межі task (R-31, §15). Транзакція bounded за часом (< `transactionLifetimeLimitSeconds`) і за розміром документа (bounded snapshot, без unbounded arrays, §9.2).
- **Retry (§8):** `TransientTransactionError` → повторити транзакцію цілком; `UnknownTransactionCommitResult` → повторити commit; після вичерпання спроб — перевірка receipt за `projection_task_id` (idempotency key) і лише потім retryable-помилка.
- **Seams для fault injection:** точки `after_mongo_commit`, `before_pg_ack`, `after_archive_put`, `after_pg_verified`, `mid_delete` передаються залежністю (DI), не monkeypatch глобалів — тестер має доводити crash-вікна детерміновано.
- **Логи й метрики (§13, §14.1):** без публічних контактів, URL і item ID у логах/labels; лічильники `projection_tasks_total{domain,status}`, `projection_replays_total`, `cross_store_drift_total`, `compaction_candidates/archived/deleted/pinned` — лише в коді, експорт WP-12.
- **Тест проти мовчазного skip (урок HANDOFF §6, 23 тести):** (1) `tests/integration/mongo/conftest.py`: Mongo недоступний → `skip` локально, але `pytest.fail` при `COLLECTOR_TEST_REQUIRE_DOCKER=1` (патерн `_skip_or_fail` з `tests/integration/postgres/conftest.py`); (2) session-hook у тому ж conftest: при `COLLECTOR_TEST_REQUIRE_DOCKER=1` будь-який `skipped`/`xfail` у `tests/integration/mongo/**` і кількість зібраних тестів менша за зафіксований мінімум PR-у → ненульовий exit; (3) unit-вартовий `tests/unit/persistence/mongo/test_ci_integration_guard.py` парсить `.github/workflows/ci.yml` і перевіряє, що job `integration-mongo` існує, має `COLLECTOR_TEST_REQUIRE_DOCKER: "1"`, адресу Mongo і крок `pytest -m integration tests/integration/mongo`; (4) CI-крок запускається з `-rs`. Тестер доводить мутаціями: прибрати env з job → вартовий червоний; зупинити Mongo в job → fail, не skip; додати `pytest.mark.skip` на один тест → session-hook червоний.
- **Linux-паритет (урок HANDOFF §6):** gate 2 не `pass` без прогону `pytest -m integration tests/integration/mongo` на Linux (CI job або Linux-контейнер) з дослівним виводом у `testing-pr<N>.md`. Відомі пастки: `ProactorEventLoop` на Windows, `pytest-socket` на Linux блокує все, крім loopback — Mongo RS у тестах має оголошувати member host `127.0.0.1:<port>` (або `directConnection=true`), інакше драйвер ходить на `mongo:27017` і тест висне/падає лише в CI; бюджет таймаутів на 2-ядерному runner-і.
- **Тестовий Mongo:** той самий digest, що в compose (`mongo:8.0@sha256:4968f22d0c6c10ef29952f3e807f62872ba22b3312f25803564fbfc08255efc2`), `--replSet rs0`, **лише в тестах** `--setParameter enableTestCommands=1` (для `configureFailPoint failCommand`); локально — `testcontainers`, у CI — `docker run` у кроці job (service container не приймає аргументи `mongod`), адреса через `COLLECTOR_TEST_MONGO_URI`. Кожен тест — власна БД (`collector_test_<uuid>`), мережа лише loopback.
- **Інтеграція з PG у тестах:** фікстури БД WP-01A (template DB) використовуються через імпорт/перевикористання патерну, без редагування `tests/integration/postgres/**`; ролі — реальні LOGIN-ролі (`collector_projector`), як у `test_role_logins.py`, де це можливо.

---

## PR1 — `wp/01b-1-mongo-schema`: collections, validators, indexes, repositories, Mongo-користувачі

### Вимоги

1. **Міграційний раннер** `migrations/mongo/` (створюється): forward-only модулі `NNNN_<name>.py` з `upgrade(db)` (ідемпотентний), службова collection застосованих версій із checksum модуля; повторний запуск — no-op; зміна вже застосованого модуля → помилка (drift). Downgrade не підтримується — відкат лише новою forward-міграцією (послаблення `validationAction`).
2. **Collections §9.2 (усі 11):** `catalog_items_current`, `catalog_offers_current`, `entity_projection_versions`, `catalog_offer_observations`, `product_reviews`, `product_questions`, `vehicle_listings_current`, `vehicle_observations`, `sellers_current`, `contact_observations`, `applied_projection_receipts` — стандартні (не time-series, без sharding).
3. **Validators:** `$jsonSchema` для кожної collection з обов'язкових полів §9.2 (для current — зі snapshot `current_document_base.v1.json`, для receipts — `applied_projection_receipt.v1.json`, для решти — зі snapshot-ів dependency WP-01C; до їх появи — **Відкрите для оркестратора:** дозволити тимчасові validators з таблиці §9.2 у `migrations/mongo/**` з тестом-вартовим «замінити на snapshot, коли він з'явиться»). Цикл §9.2: міграція ставить `validationAction: warn` → перевірка `{$nor: [{$jsonSchema: …}]}` count == 0 → наступна міграція `error`; перемикання в `error` відмовляє, якщо є невалідні документи.
4. **Indexes §9.2 (усі обов'язкові):** unique `{source.source_id, source.source_item_id}` і unique `{entity_uuid}` (для current — `_id`) на кожній current collection; unique `{entity_uuid, projection_version}` і `{projection_task_id}` на `entity_projection_versions`; unique `{projection_task_id}` на observations і `applied_projection_receipts`; unique `{source.source_id, source.source_item_id, content_version}` для reviews/questions; `{entity_uuid, observed_at: -1}`; `{catalog_item_id, last_seen_at: -1}`; `{seller_id, observed_at: -1}`; `{parent_item_id, published_at: -1}`; `{last_seen_at: -1}`. Назви `catalog_item_id`/`seller_id`/`parent_item_id`/`content_version` — дослівно з §9.2 (**Відкрите для оркестратора:** WP-07/WP-09 мають узгодити їх у своїх контрактах). Без wildcard indexes. Маніфест indexes — єдине джерело; `--indexes` створює відсутні й **звітує** зайві (index budget, `$indexStats` review).
5. **CLI:** `collector db ensure-mongo --validators --indexes` (контракт §16.2) реально застосовує міграції після ініціалізації RS; exit 1 з повідомленням без секретів при drift/помилці. Прапорець `--users` (створення Mongo-користувачів) — **припущення**, аналог `db roles --with-login`; **Відкрите для оркестратора:** новий прапорець чи окрема підкоманда.
6. **Mongo-користувачі за компонентами (§13):** custom roles — `collector_projector` (find/insert/update на domain collections і receipts; без `dropCollection`/`createIndex`/`collMod`/`delete`), `collector_compactor` (+ delete лише на `entity_projection_versions`), `collector_api_ro`, `collector_export_ro` (лише find); root лише в `ensure-mongo`. Паролі — з Docker secrets `mongo_uri_<component>` (генерація — dependency WP-00). Scheduler/fetcher/parser Mongo-користувачів не мають.
7. **Репозиторії** (`src/collector/persistence/mongo/`): клієнт-фабрика з concerns; `get_current(entity_uuid)`, `get_exact_version(entity_uuid, projection_version)` (hot-частина §9.5; archive-частина — PR4), `get_receipt(task_id)`, `list_receipts(after=(committed_at, _id), limit)` — стабільний compound sort + `_id` cursor, без `skip` (§15). Транзакційні функції приймають `AsyncClientSession`; межа транзакції — викликач (як у WP-01A).

### Тести

Integration з порожньої БД: `ensure-mongo --validators --indexes` двічі → однаковий стан; усі 11 collections і всі indexes §9.2 існують (порівняння з маніфестом); невалідний документ під `warn` приймається, під `error` відхиляється; перемикання `warn→error` при наявному невалідному документі відмовляє; дубль `(source_id, source_item_id)` і дубль `projection_task_id` → DuplicateKey; зайвий index звітується; змінений застосований модуль міграції → drift-помилка; ролі: `collector_projector` не може `delete`/`dropCollection`/`createIndex`, `collector_api_ro` не може insert; `list_receipts` keyset без дублів/пропусків на межі сторінки. Unit: генерація validator зі snapshot (UUID → `binData`, datetime → `date`, `$ref` інлайн). Вартовий проти мовчазного skip (спільні вимоги).

### Acceptance PR1

Команди зелені локально (Windows) і в CI job `integration-mongo` (Linux); `ensure-mongo --validators --indexes` ідемпотентний; жодного `skipped` у `tests/integration/mongo` при `COLLECTOR_TEST_REQUIRE_DOCKER=1`; dependency-запити WP-01C/WP-01D/WP-00 подано.

---

## PR2 — `wp/01b-2-projector`: projector core і receipts (§7.3 крок 3)

Бібліотечний рівень без реєстрації в runtime (runtime-інтеграція — PR3): `apply_projection(command, payload) -> AppliedProjectionReceipt` у `src/collector/persistence/mongo/` (точна назва модуля — за implementer).

### Вимоги

1. Перевірка input: bytes normalized artifact відповідають `sha256`/`size` з `NormalizedArtifactRef`; payload валідується контрактом (dependency WP-01C); `artifact.entity_uuid == command.entity_uuid` (validator контракту). Невідповідність hash → permanent (quarantine) з кодом; невалідний payload → permanent.
2. **Одна Mongo-транзакція на task:** (а) receipt за `projection_task_id` існує → повернути його без жодного запису (duplicate task, §10 п.9); (б) **завжди** insert `entity_projection_versions` (R-36) з state hash, `state_changed`, previous current version/hash, bounded snapshot або artifact ref, lineage; (в) CAS current: читання current у транзакції; відсутній → insert; `current.projection_version < v` → replace; інакше current не змінюється (`applied_to_current=false`). **Заборонено** `update_one({_id, projection_version: {$lt: v}}, …, upsert=True)`: при новішому current це DuplicateKey, що абортить транзакцію; (г) observation (`changed`/`heartbeat`, §9.3 п.5) лише коли `applied_to_current` і (state hash змінився або сплив heartbeat interval від останньої observation сутності); (д) якщо `applied_to_current AND state_changed` — один раз сформувати `DomainChangedEvent` зі стабільним `event_id`, `encode_event` → `event_bytes` (BSON Binary)/media type/SHA-256; `EventTooLargeError` → payload у immutable artifact (content-addressed), receipt несе `event_artifact`; (е) insert receipt — атомарно з усім вище.
3. Для не-applied task `state_changed=false`, `result_version/result_hash` — поточного current (так вимагає validator `AppliedProjectionReceipt`).
4. `receipt.cluster_time` фіксується при першому commit і далі **завжди читається зі збереженого receipt**, ніколи не береться з поточної сесії під час replay (інакше `acknowledge_projection` поверне `ConflictError` через `_require_same_receipt`). **Відкрите для оркестратора:** джерело значення (operation time insert-у receipt у транзакції) — підтвердити, що це задовольняє поле «Mongo receipt ID/cluster time» §9.1.
5. Retry-політика зі спільних вимог; heartbeat interval конфігурується (**припущення:** 24 год за замовчуванням, `COLLECTOR_PROJECTOR_HEARTBEAT_HOURS`; ТЗ значення не задає — **Відкрите для оркестратора**).
6. Artifact reader — інтерфейс (`Protocol`) з in-memory реалізацією для тестів; реалізація над MinIO — **Відкрите для оркестратора:** хто owner спільного S3-клієнта (природно WP-02 PR2); до рішення WP-01B не тягне власний S3 SDK у PR2.

### Тести (integration, Mongo RS)

- **порядок `3,1,2` → current=3** (R-25, §16.3): три версії однієї сутності в порядку 3,1,2 → current.projection_version = 3 після кожного кроку, три version records, три receipts; receipts 1 і 2 — `applied_to_current=false`, `state_changed=false`; жодного DuplicateKey-аборту;
- паралельні v2 і v3 однієї сутності (різні task) → current=3, обидва version records; write conflict розв'язується retry;
- **`TransientTransactionError`**: `configureFailPoint failCommand` з `errorLabels: ["TransientTransactionError"]` на insert version record (times: 1) → транзакція повторена цілком, рівно один version record/receipt/observation;
- **`UnknownTransactionCommitResult`**, два випадки: (а) failpoint на `commitTransaction` до виконання → commit повторено, один receipt; (б) seam «commit застосовано, клієнт бачить unknown» → повторний commit/перевірка receipt → рівно один receipt, одна observation;
- duplicate task (той самий `task_id` двічі, у т.ч. паралельно двома викликами — модель подвійного виконання після fencing, ADR-0006) → один receipt, другий виклик повертає той самий receipt byte-equal;
- **receipt з event bytes**: `event_bytes` у Mongo побайтово == `encode_event(event).event_bytes`, `event_sha256 == sha256(event_bytes)`; повторний виклик не перекодовує; >256 KiB → `event_artifact`, `event_bytes` відсутні;
- observation: без зміни state hash і до спливу heartbeat — observation немає; після спливу (контрольований годинник) — `heartbeat`; зміна hash — `changed`;
- R-31: дві task різних сутностей, друга падає permanent → перша закомічена, друга не лишила жодного документа;
- hash mismatch artifact / невалідний payload → permanent-помилка, жодного запису в Mongo.

### Acceptance PR2

Тести вище зелені локально і в CI (Linux); жодного `skipped`; кожен adversarial-тест доведено тестером як червоний на мутації (прибраний CAS, upsert з `$lt`, перекодування event при replay, cluster_time із сесії).

---

## PR3 — `wp/01b-3-ack-reconciler`: runtime-інтеграція, PG ack, reconciler, publisher, restore (§7.3 кроки 4–5, §7.4)

Передумови: dependency WP-01B→WP-01D `resolved`; WP-01A PR3 (SR-4-запити; N-2 варіант 2 нижче) `merged` — або рішення оркестратора про fallback.

### Вимоги

1. **Handler:** `ProjectorHandler` (`src/collector/workers/projector.py`) — читає artifact, викликає `apply_projection`, повертає receipt runtime-у; ack (`acknowledge_projection`: ack + `GREATEST` confirmed version + task `succeeded` + `domain.changed` лише для `applied_to_current AND state_changed`, bytes з receipt) виконується в report-транзакції runtime-у за механізмом dependency WP-01D. Помилки: Mongo недоступний → retryable; hash/payload → `PermanentTaskError`.
2. **Lease recovery:** `recover_expired_projection_leases` у тіку scheduler/maintenance (роль `collector_scheduler` має column UPDATE на `projection_tasks`) через реєстр тіків WP-01D.
3. **Reconciler (§7.3 крок 5, FR-022)** — `src/collector/workers/reconciler.py`: читання primary + `majority`; виявляє (а) task без Mongo receipt, (б) receipt без PG ack → ack зі **збереженого** receipt (копія bytes, без `encode_event`), (в) ack без відповідного `entity_index` (confirmed < ack version, порожній `mongo_document_id`), (г) version drift (`entity_index.confirmed_projection_version` > Mongo `current.projection_version` або current відсутній) → reprojection з normalized artifact; (д) quarantined tasks — лише звіт; cursor не вважається опрацьованим, доки є неacknowledged і не quarantined tasks (запит WP-01A PR3). Результат — лічильник `cross_store_drift_total` і звіт; ідемпотентний (другий прохід — нуль дій).
4. **DR rebuild/restore (§7.4):** режим replay без повторного ack: після відновлення Mongo зі snapshot-у (watermark) або втрати collections projector перепроєктовує tasks після watermark (або всі) і **порівнює `result_hash`/`result_version` з наявним PG ack** замість нового ack; event bytes не перекодовуються (для наявних подій — з PG `change_events`). **Відкрите для оркестратора:** як reconciler трактує receipt, перебудований після restore, з іншим `cluster_time`, ніж у PG ack (порівнювати без `cluster_time` чи додати ознаку rebuild у контракт через WP-01C).
5. **Outbox publisher loop (N-2, deps `WP-01A-to-WP-01D.md` §7)** — `src/collector/workers/publisher.py`, під `collector_scheduler` (єдина роль з column UPDATE на `outbox_events`), як доменний тік maintenance: `fetch_unpublished` (коротка транзакція) → доставка поза транзакцією → `mark_published`/`mark_failed`. **Обраний варіант — 2 (dependency до WP-01A PR3):** `fetch_unpublished` у тій самій lease-транзакції інкрементує лічильник видач (`delivery_attempts`) і паркує рядок (`parked_at`) при досягненні межі, тож crash/OOM publisher-а до `mark_failed` не обходить межу. Fallback (якщо WP-01A PR3 не встигає; **рішення оркестратора**): варіант 1 у loop — перед ризиковою операцією per-event `mark_failed(error_code="delivery_started")` як передплата спроби, після доставки `mark_published`; недолік — остання спроба паркується до доставки (фактично `max_attempts − 1` доставок), фіксується у «Відомих ризиках». Sink — інтерфейс `EventSink`; **Відкрите для оркестратора:** реальний споживач (SSE WP-11A / webhook / брокер). До рішення publisher **вимкнений за замовчуванням** (`COLLECTOR_OUTBOX_PUBLISHER_ENABLED=0`), щоб не позначати події опублікованими без споживача.

### Тести

- **crash між кроками 3–4 → replay без нової observation** (§7.3, §16.3): seam `after_mongo_commit` вбиває handler (cancel/виняток до ack) → lease спливає → `recover_expired_projection_leases` → інший runtime-instance бере task → receipt знайдено → ack; рівно 1 receipt, 1 version record, 1 observation, 1 `projection_acknowledgements`, 1 `change_events`, 1 `outbox_events`; `outbox_events.payload_bytes` побайтово == `receipt.event_bytes`;
- той самий crash, але ack робить reconciler (без повторного claim) → drift 0; повторний прохід reconciler-а — нуль дій;
- `3,1,2` end-to-end через runtime: `confirmed_projection_version` = 3 після кожного ack, ніколи не зменшується; `domain.changed` лише для v3; кожна task — рівно один receipt і один ack (§16.3);
- повторна projection тієї самої task (at-least-once) → без дублікатів у Mongo і PG (§16.3 «повторний parse або projection…»);
- reconciler: receipt без ack; ack без index; confirmed > Mongo current (видалений current) → reprojection повертає drift до 0; quarantined task блокує «повноту cursor»;
- **restore (§16.3, §7.4):** N tasks → snapshot Mongo з watermark → ще M tasks → втрата Mongo → restore snapshot → replay після watermark у rebuild-режимі → count/hash усіх collections дорівнює стану до втрати, reconciler drift 0, жодного нового ack/event; також повний rebuild з порожньої Mongo;
- Mongo недоступний під час роботи projector-а → task retry, після відновлення — довиконано (частина §16.3 «окремої недоступності MongoDB»);
- **publisher N-2:** sink, що вбиває publisher (BaseException/cancel) на конкретній події → після межі видач подія `parked`, решта подій доставляються (немає head-of-line blocking); звичайна помилка sink → `mark_failed` з backoff; `topic='internal'` ніколи не видається;
- ролі: handler/reconciler працюють під LOGIN-роллю `collector_projector` + Mongo-користувачем `collector_projector`; publisher — під `collector_scheduler` без Mongo-credentials;
- вартовий проти мовчазного skip охоплює нові тести.

### Acceptance PR3

Тести вище зелені локально і в CI (Linux); §16.3 пункти про fault injection після Mongo commit, `3,1,2`, повторну projection і restore — з доказом у `testing-pr3.md`; `tests/integration/postgres/**` і `tests/integration/scaling/**` лишаються зеленими.

---

## PR4 — `wp/01b-4-compaction`: compaction, archive locator, rollback window (§9.7, FR-025)

Передумови: WP-01A PR3 `merged` (`retention_pins`, `compaction_runs`, `version_archive_index`, pin query helper, locator insert атомарно з `verified`).

### Вимоги

1. `src/collector/workers/compactor.py`: mark → dry-run manifest → immutable archive part (partitioned Parquet domain/source/month; залежність `pyarrow` додається в `pyproject.toml`) → row/hash verify читанням назад → **PG-транзакція, що вставляє locators і переводить run у `verified`** → Mongo delete → 30-денний rollback window → artifact sweep.
2. Кандидати: лише `entity_projection_versions` з `state_changed=false` старші 90 днів (Q-005); **ніколи** current/confirmed version, останній успішний snapshot сутності, changed/heartbeat versions, pinned version, artifact із живим lineage reference. Pins перевіряються повторно безпосередньо перед delete (pin race).
3. Exact read (§9.5): `get_exact_version` → hot record, інакше archive locator (PG) → рядок Parquet з перевіркою hash; жодного вікна, де версії немає ні в hot, ні в archive (R-50).
4. Rollback: у межах вікна `rolled_back` відновлює видалені документи з archive part byte/hash-equal; sweep archive/artifact — лише після вікна.
5. Apply — лише з `actor`/`reason` і audit before/after (§13 «compaction apply … impact preview, typed confirmation, reason та audit»); за замовчуванням лише dry-run (`COLLECTOR_COMPACTION_APPLY=0`). Кожен крок ідемпотентний після crash (re-run продовжує з поточного стану run).

### Тести (§16.1 п.11)

- dry-run: детермінований manifest, нуль змін у Mongo/PG/object store;
- **locator публікується до delete**: паралельний reader безперервно читає всі exact versions під час compaction → жодного `not found`; мутація «delete до PG verified» → тест червоний;
- **pin race**: pin, створений між dry-run і delete → версія не видалена, run звітує skipped;
- archive verification failure (зіпсований part/hash) → run `failed`, жодного locator, жодного delete;
- **rollback window**: після delete `rolled_back` відновлює документи з тим самим state hash; після вікна — sweep; до вікна sweep нічого не видаляє;
- crash на кожному seam (`after_archive_put`, `after_pg_verified`, `mid_delete`) → повторний запуск доводить run до кінця без втрат/дублів;
- не видаляються current/confirmed/останній snapshot/changed/pinned;
- restore exact version із Parquet дорівнює hot-версії до compaction (hash).

### Acceptance PR4 (закриває WP-01B)

Тести вище зелені локально і в CI (Linux); acceptance §17.2 нижче виконано повністю; етап 5 — docs.

---

## Команди перевірки (усі PR)

```bash
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run mypy src
uv run pytest -m "not live"
uv run collector contracts export --check
docker compose config --quiet
docker compose --profile core --profile workers up -d --wait
uv run collector db ensure-mongo --validators --indexes
uv run pytest -m integration tests/integration/mongo -rs
uv run pytest -m integration tests/integration/postgres tests/integration/scaling
docker compose down -v
```

`docker compose … up` з `ensure-mongo --validators --indexes` у compose — після resolve dependency WP-00; до того CLI виконується вручну проти піднятого стека.

## Acceptance (§17.2, дослівно)

«єдине ownership Mongo validators/index migrations; replica set, repositories, projector, receipts/reconciler, compaction/archive locator; crash/out-of-order/restore tests green».

Плюс §16.3 (дослівно, у частині WP-01B):

- «повторний parse або projection тієї самої raw відповіді не створює дублікати;» (частина projection — PR2/PR3)
- «fault injection після Mongo commit, але до PostgreSQL acknowledgement, завершується idempotent replay; reconciler повертає drift до нуля;» (PR3)
- «доставка projection versions у порядку `3, 1, 2` залишає current на версії 3; усі tasks мають рівно один receipt/acknowledgement й export читає підтверджену exact version;» (PR2/PR3; export — WP-11A на `get_exact_version`)
- «ізольований restore PostgreSQL/raw/MongoDB за процедурою §7.4 відтворює domain state до зафіксованого watermark, а count/hash reconciliation не знаходить втрат або дублів;» (PR3)
- «compaction dry-run, pin race, concurrent read, archive verification і rollback пройдено; published release після compaction має ті самі part hashes;» (PR4; part hashes release — WP-11A)
- «відновлення після kill worker та окремої недоступності PostgreSQL/MongoDB продемонстровано;» (частина projector — PR3)
- «чистий Docker host підіймає core/workers/gui однією documented командою; migrations/validators завершуються до readiness, restart не втрачає named-volume data;» (validators — PR1 + dependency WP-00)

## Відомі ризики

| Ризик | Статус | Owner |
|---|---|---|
| Runtime WP-01D жорстко прив'язаний до `crawl_jobs` (`queue_repo` у `_claim`/`_report`), handler-модулі ніхто не імпортує, тік не відновлює projection leases — projector не може працювати без змін у чужому коді | open, блокер PR3; dependency WP-01B→WP-01D | WP-01D |
| Немає контракту normalized projection payload і snapshot-ів для version records/observations — validators і projector не мають джерела схеми | open, блокер PR1 (частково) / PR2; dependency WP-01B→WP-01C | WP-01C |
| §13 vs роль maintenance: `docs/workers.md` відносить reconcile/compaction до `maintenance` під `collector_scheduler`, але §13 забороняє scheduler-у Mongo-credentials, а `collector_scheduler` не має INSERT на `projection_acknowledgements` | open — **Відкрите для оркестратора** (пропозиція: reconciler — під `collector_projector` + Mongo `collector_projector`; compactor — окремий Mongo-користувач `collector_compactor` і PG-GRANT на compaction-таблиці через WP-01A PR3) | orchestrator / WP-01A / WP-01D |
| `projection.command` (`topic='internal'`) ніколи не публікується (fail-closed) і не видаляється `purge_published` (лише опубліковані, ADR-0007) → необмежений ріст `outbox_events` | open — dependency WP-01B→WP-01A п.(4) | WP-01A / WP-12 |
| `event_artifact` (>256 KiB) пишеться в object store до Mongo commit; аборт транзакції лишає orphan object | mitigated: content-addressed key + sweeper WP-02/WP-12 (`list_orphan_candidates`); тест у PR2 | WP-01B / WP-12 |
| Receipt `cluster_time` недетермінований між replay і rebuild → хибний drift або `ConflictError` на ack | mitigated вимогою PR2 п.4 і PR3 п.4; рішення про rebuild — відкрите | WP-01B |
| Single-member RS — транзакції є, HA немає (Q-010); `enableTestCommands` лише в тестах | accepted до production; ADR етапу 5 | WP-01B / DevOps |
| Нестабільний `tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising` (ledger) може червонити регресійний прогін PR3 | known, не внесений WP-01B; flaky = знахідка high лише якщо внесена цим WP | WP-01D |
| Fallback N-2 (варіант 1) дає `max_attempts − 1` фактичних доставок | лише якщо оркестратор обере fallback | WP-01B |

## Rollback/disable

- Projector: `worker_pools.desired_replicas = 0` для `projector` (tasks накопичуються в PG, outbox-патерн гарантує довиконання) або `COLLECTOR_WORKER_PLACEHOLDER=1`; revert PR3 повертає роль на `NoopHandler`.
- Validators: відкат лише forward-міграцією `validationAction: warn`; collections не видаляються. Dev — `docker compose down -v` (stateful volume `mongo-data`); production — forward-fix + reprojection з PG/artifacts (§7.4).
- Publisher: вимкнений за замовчуванням (`COLLECTOR_OUTBOX_PUBLISHER_ENABLED=0`).
- Compaction: за замовчуванням лише dry-run (`COLLECTOR_COMPACTION_APPLY=0`); видалене в межах 30 днів відновлюється `rolled_back`.

## Docs (етап 5)

`docs/persistence/mongo.md` (collections, validators warn→error, indexes і budget, BSON mapping UUID/date, transaction boundaries, retry, ролі §13, як додати Mongo-міграцію); `docs/runbooks/reconcile.md`, `docs/runbooks/restore-mongo.md` (watermark, replay, звірка count/hash, RPO/RTO), `docs/runbooks/compaction.md` (dry-run, apply, rollback, sweep); ADR-0008 «MongoDB topology: single-member replica set у MVP» (Q-010); за потреби ADR «Outbox delivery counter / publisher sink» (N-2); рядки traceability (Додаток C «Узгодженість двох БД», «Керована історія») у `docs/acceptance/traceability.md` — spec-reviewer.

## Відкрите для оркестратора (зведення)

1. Розміщення publisher loop N-2 у `src/collector/workers/publisher.py` (owned WP-01D) і реальний sink; до рішення — вимкнений.
2. N-2: підтвердити варіант 2 (dependency до WP-01A PR3) або дозволити fallback варіант 1.
3. Під якими PG/Mongo-ролями працюють reconciler і compactor (конфлікт §13 з `maintenance` під `collector_scheduler`).
4. Тимчасові validators з §9.2 до появи snapshot-ів WP-01C — так/ні.
5. Назви полів domain-indexes (`catalog_item_id`, `seller_id`, `parent_item_id`, `content_version`) — узгодження з WP-07/WP-09.
6. `--users` як новий прапорець `db ensure-mongo` чи окрема підкоманда (§16.2 не перейменовується, лише розширюється).
7. Heartbeat interval observations (припущення 24 год, конфігуроване).
8. Observation лише для `applied_to_current` (припущення; late-arriving старіша версія лишається у version history, §9.6).
9. Джерело `receipt.cluster_time` і трактування rebuild-receipt reconciler-ом.
10. Owner спільного S3-клієнта для читання normalized artifacts і запису archive parts (WP-02 PR2?).
11. Подати dependency-запити до WP-01C і WP-01D одразу при старті PR1.
