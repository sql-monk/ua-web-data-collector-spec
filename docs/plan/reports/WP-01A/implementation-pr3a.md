# WP-01A PR3a — implementation (`wp/01a-3a-queue-outbox-preflight`)

Картка: `docs/plan/cards/WP-01A.md`, розділ «PR3a». Worktree `.worktrees/wp-01a-3a`, база —
`main` `626b7e4`. Споживачі: WP-01D PR1c (`not_before`, defer, fencing ack, projection
backend), WP-02 PR2 (preflight, validators, лічильник збоїв route, retry budget), WP-01B PR3
(N-2, purge internal, SR-4, fencing ack), WP-04 PR2 (defer).

## Що зроблено

| п. картки | Реалізація | Тести |
|---|---|---|
| 1 `not_before`/defer | `queue.retry(..., not_before=None)`, `queue.release(..., not_before=None)`, `projection.retry_projection_task(..., not_before=None)`, `projection.release_projection_task(..., not_before=None)`; спільні `queue.clamp_not_before`, `queue.next_attempt_at` | `test_queue_defer.py` (10), unit `test_queue_timing.py` (3) |
| 2 outbox N-2 | колонка `outbox_events.delivery_attempts` (міграція `0006`); `fetch_unpublished(..., max_delivery_attempts=DEFAULT_MAX_PUBLISH_ATTEMPTS)` рахує видачі й паркує вичерпані рядки в тій самій lease-транзакції (один `UPDATE … CASE`); `mark_failed` лічильник видач не чіпає; `unpark` скидає обидва; column UPDATE `delivery_attempts` для `collector_scheduler` | `test_outbox_delivery.py` (6 з 10) |
| 3 `purge_published` + internal | нова `outbox.purge_published(older_than, after, limit)` → `PurgeResult(deleted, last_event_id)`: опубліковані `domain`, старші за поріг, і `internal`, чия task `succeeded` і має ack; keyset за `event_id`, `SKIP LOCKED`; `GRANT DELETE ON outbox_events TO collector_scheduler`. Наслідок: повтор того самого parse-кроку після purge повертає наявну task з `ParseResult.outbox_event=None` (раніше впав би `ConflictError`) | `test_outbox_delivery.py` (4 з 10) |
| 4 preflight | `sources.get_fetch_preflight(source_uuid, route_id) -> FetchPreflight \| None` — один SELECT (`sources` ⋈ `source_routes` ⟕ `source_policy_versions` за `current_policy_version_id`) | `test_fetch_preflight.py` (4); під LOGIN `collector_fetcher` — `test_pr3a_roles.py` |
| 5 validators | `artifacts.latest_validators(source_uuid, normalized_url) -> Validators \| None`; stored generated `fetches.requested_url_md5 = md5(requested_url)` + partial index `ix_fetches_validators (source_id, requested_url_md5, fetched_at) WHERE outcome='success' AND http_status IN (200,206)` | `test_fetch_preflight.py` (3, включно з EXPLAIN) |
| 6 лічильник збоїв route | `sources.record_route_failure(route_id, *, actor, reason, threshold, circuit_open_for=None) -> RouteState` (атомарний інкремент; на порозі `circuit_open` + `revision+1` + audit `source_route.circuit_open` в одній транзакції; лише з `healthy`/`degraded`), `sources.reset_route_failures(route_id) -> RouteState` (стан не змінює); fetcher: `REVOKE UPDATE` + column UPDATE лише лічильника/стану | `test_fetch_preflight.py` (3, включно з 8×5 конкурентних інкрементів), `test_pr3a_roles.py` (3) |
| 7 fencing ack | `projection.acknowledge_projection(..., owner=None)`: з `owner` — `_lock_owned` до будь-якого запису, інакше `LeaseNotOwnedError` | `test_ack_fencing_reconcile.py` (4) |
| 8 retry budget | `artifacts.count_retries_since(source_uuid, since) -> int` (`outcome='retryable'`, `fetched_at >= since`) | `test_fetch_preflight.py` (1), `test_pr3a_roles.py` |
| 9 SR-4 reconciler | новий модуль `repositories/reconciliation.py`: `list_stale_projection_tasks`, `list_quarantined_projection_tasks`, `projection_completeness -> ProjectionCompleteness` (keyset за `task_id`, без `OFFSET`) | `test_ack_fencing_reconcile.py` (3) |
| 10 GRANT | `sql/roles.sql`: scheduler — UPDATE `delivery_attempts`, DELETE `outbox_events`; fetcher — column UPDATE `source_routes`; reconciler під `collector_projector` — лише SELECT (UPDATE `published_at`/`parked_at`/`delivery_attempts` → permission denied); scheduler — INSERT `crawl_jobs` для `projection.reconcile`/`projection.compact` | `test_pr3a_roles.py` (8) |
| — skip-вартовий | unit `test_integration_suite_guard.py`: кожен модуль `tests/integration/postgres/test_*.py` має `pytestmark = pytest.mark.integration` і не має власних `skip`/`skipif`/`importorskip`; conftest тримає `REQUIRE_DOCKER` → fail; CI-job `integration-postgres` має прапорець і команду | 27 параметризованих |

Допоміжне: LOGIN-фікстури (`Logins`, `logins`, `role_engine`, `write_role_secrets`,
`reset_role_logins`) перенесено з `test_role_logins.py` у `tests/integration/postgres/conftest.py`
(паролі — `secrets.token_hex` у рантаймі, жодного secret у fixtures). У нових запитах —
`Row._tuple()` (deprecation SQLAlchemy 2.0.19). `test_metadata.py` доповнено (обов'язковий
індекс `fetches`, generated column, CHECK `delivery_attempts`). `docs/plan/deps/WP-01A-to-WP-01D.md`
— статус §7 (N-2) і §8 (вказівник на API для PR1c).

Коміти:

```text
5afe47d docs(wp-01a): deps WP-01A-to-WP-01D §7 N-2 implemented in PR3a, PR1c API pointer
cdd1d2c test(wp-01a): fencing ack, SR-4 reconciler queries, PR3a grants under LOGIN roles, skip guard
18a1168 test(wp-01a): fetch preflight, validators with EXPLAIN, route failure counter, retry budget
0297b0c test(wp-01a): queue/projection defer and not_before, outbox delivery counter and purge
00139ac feat(wp-01a): queue/projection not_before and defer, outbox delivery counter and purge, fencing ack
```

Обсяг продуктивного коду (`src/` + `migrations/`, разом із docstrings): +852 / −43 — на межі
~800 рядків; близько половини — docstrings (transaction boundary, семантика). На PR3a1/PR3a2 не
ділив: зміни зв'язані однією міграцією `0006` і GRANT-ами.

## Нові публічні API (фіксовані для WP-01D PR1c, WP-02 PR2, WP-01B PR3)

Усі функції `async`, перший аргумент `AsyncSession`, commit робить викликач.

```python
# collector.persistence.postgres.repositories.queue
async def retry(session, job_id: UUID, owner: str, *, error_code: str,
                error_message: str | None = None, policy: BackoffPolicy | None = None,
                rng: random.Random | None = None, not_before: datetime | None = None,
                now: datetime | None = None) -> CrawlJob
async def release(session, job_id: UUID, owner: str, *, not_before: datetime | None = None,
                  now: datetime | None = None) -> CrawlJob
def clamp_not_before(not_before: datetime | None, now: datetime) -> datetime
def next_attempt_at(now: datetime, attempt: int, *, policy: BackoffPolicy | None = None,
                    rng: random.Random | None = None,
                    not_before: datetime | None = None) -> datetime

# collector.persistence.postgres.repositories.projection
async def retry_projection_task(session, task_id: UUID, owner: str, *, error_code: str,
                                error_message: str | None = None,
                                policy: BackoffPolicy | None = None,
                                not_before: datetime | None = None,
                                now: datetime | None = None) -> ProjectionTask
async def release_projection_task(session, task_id: UUID, owner: str, *,
                                  not_before: datetime | None = None,
                                  now: datetime | None = None) -> ProjectionTask
async def acknowledge_projection(session, task_id: UUID, receipt: AppliedProjectionReceipt, *,
                                 event: DomainChangedEvent | None = None,
                                 owner: str | None = None,
                                 now: datetime | None = None) -> AcknowledgeResult
# ParseResult.outbox_event: OutboxEvent | None  (None — лише повтор після purge_published)

# collector.persistence.postgres.repositories.outbox
DELIVERY_ATTEMPTS_EXHAUSTED = "delivery_attempts_exhausted"
async def fetch_unpublished(session, *, topics: Sequence[str] = ("domain",), limit: int = 100,
                            visibility_seconds: int = 60,
                            max_delivery_attempts: int = DEFAULT_MAX_PUBLISH_ATTEMPTS,
                            now: datetime | None = None) -> list[OutboxEvent]
class PurgeResult:  # frozen dataclass
    deleted: int
    last_event_id: UUID | None
async def purge_published(session, *, older_than: timedelta, after: UUID | None = None,
                          limit: int = 1000, now: datetime | None = None) -> PurgeResult

# collector.persistence.postgres.repositories.sources
class FetchPreflight:  # frozen dataclass
    source_pk: UUID; source_id: str; source_state: SourceState
    policy: PolicySnapshot | None; policy_version: int | None
    route_id: UUID; route_state: RouteState; route_revision: int; route_kind: str
    circuit_open_until: datetime | None
async def get_fetch_preflight(session, source_uuid: UUID,
                              route_id: UUID) -> FetchPreflight | None
async def record_route_failure(session, route_id: UUID, *, actor: str, reason: str,
                               threshold: int, circuit_open_for: timedelta | None = None,
                               request_id: str | None = None,
                               now: datetime | None = None) -> RouteState
async def reset_route_failures(session, route_id: UUID, *,
                               now: datetime | None = None) -> RouteState

# collector.persistence.postgres.repositories.artifacts
class Validators:  # frozen dataclass
    etag: str | None; last_modified: str | None; fetched_at: datetime
async def latest_validators(session, source_uuid: UUID,
                            normalized_url: str) -> Validators | None
async def count_retries_since(session, source_uuid: UUID, since: datetime) -> int

# collector.persistence.postgres.repositories.reconciliation (новий модуль)
OPEN_TASK_STATUSES = ("pending", "retry", "leased")
async def list_stale_projection_tasks(session, *, older_than: timedelta,
                                      after: UUID | None = None, limit: int = 100,
                                      now: datetime | None = None) -> list[ProjectionTask]
async def list_quarantined_projection_tasks(session, *, source_id: str | None = None,
                                            after: UUID | None = None,
                                            limit: int = 100) -> list[ProjectionTask]
class ProjectionCompleteness:  # frozen dataclass
    open_tasks: int; oldest_open_task_created_at: datetime | None
    quarantined_tasks: int; oldest_quarantined_task_created_at: datetime | None
    unpublished_domain_events: int
    oldest_unpublished_domain_event_created_at: datetime | None
    settled: bool   # property: open_tasks == 0 (§7.3 крок 5 дослівно)
    complete: bool  # property: open_tasks == 0 and quarantined_tasks == 0 (картка п.9)
async def projection_completeness(session, *, source_id: str | None = None,
                                  entity_uuid: UUID | None = None,
                                  created_before: datetime | None = None
                                  ) -> ProjectionCompleteness
```

Семантика, на яку спирається PR1c/WP-02:

- `retry(not_before=…)` → наступна спроба **рівно** `max(not_before, now)`, `policy` не
  застосовується: runtime передає вже обчислене `max(now + schedule.delay(attempt),
  retry_after)`, і дефолтний `BackoffPolicy` (30 с × 2) не перекриває таблицю §10.
  `not_before=None` → `now + policy.delay_for(attempt)` як у PR1. `max_attempts` спрацьовує
  незалежно (карантин + dead letter).
- `release(not_before=…)` = defer: `pending`, `attempt = GREATEST(attempt - 1, 0)`, поля
  помилки не змінюються, dead letter немає; минуле → `now`. Верхній clamp (24 год) — runtime.
- `acknowledge_projection(owner=…)`: чужий/втрачений lease або вже `succeeded` task →
  `LeaseNotOwnedError` без жодного запису; прострочений, але не відновлений lease власника —
  дозволено (як `queue.complete`). `owner=None` — поведінка PR2 (reconciler).
- `fetch_unpublished` може повернути менше за `limit` (запарковані в цьому батчі рядки не
  повертаються; наступний виклик бере наступні події).
- `latest_validators`: пізніший 200 без `ETag`/`Last-Modified` дає `Validators(None, None, t)`,
  а не старіші значення; порівнюється з `fetches.requested_url` (fetch пише туди нормалізований
  URL).
- `get_fetch_preflight` → `None` і для відсутнього джерела/route, і для route чужого джерела.

## Команди та вивід

Хост Windows 11, Docker Desktop; хост одночасно навантажували ще 4 агенти (4–5 паралельних
PostgreSQL/Mongo testcontainers), звідси тривалість прогонів. Кирилиця в консолі Windows
відображається як `�` (кодова сторінка консолі), на вміст не впливає.

```text
$ uv sync --frozen
Checked 66 packages in 10ms
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
279 files already formatted
$ uv run mypy src
Success: no issues found in 79 source files
```

`collector db migrate --check` / `alembic check` — окремий одноразовий контейнер `postgres:18`
(той самий pinned digest), порожня БД `wp3a_alembic`, DSN через `COLLECTOR_POSTGRES_DSN`
(пароль замасковано):

```text
$ uv run collector db migrate --check   # порожня БД
Target database is not up to date.
schema drift: ����� ����������� �� �������
exit=1
$ uv run collector db migrate
No new upgrade operations detected.
migrated postgresql+asyncpg://admin3a:***@127.0.0.1:32817/wp3a_alembic: empty -> 0006_queue_outbox_preflight
partition created: audit_log_y2026m09
partition created: audit_log_y2026m10
partition created: audit_log_y2026m11
partition created: audit_log_y2026m12
partition created: fetches_y2026m09
partition created: fetches_y2026m10
partition created: fetches_y2026m11
partition created: fetches_y2026m12
exit=0
$ uv run collector db migrate --check
No new upgrade operations detected.
schema up to date: revision=0006_queue_outbox_preflight
exit=0
$ uv run alembic upgrade head && uv run alembic check
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
...
No new upgrade operations detected.
exit=0
$ uv run collector db roles
roles applied to postgresql+asyncpg://admin3a:***@127.0.0.1:32817/wp3a_alembic from roles.sql: collector_migrate, collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro
exit=0
```

Upgrade поверх наявних даних і downgrade-цикл `0006` (БД `wp3a_up`: `0005` → INSERT у
`fetches` → head):

```text
INFO  [alembic.runtime.migration] Running upgrade 0005_entity_version_guard -> 0006_queue_outbox_preflight, ...
$ SELECT tableoid::regclass, requested_url_md5 = md5(requested_url) FROM fetches
fetches_default|t
$ alembic downgrade 0005_entity_version_guard && alembic upgrade head
INFO  [alembic.runtime.migration] Running downgrade 0006_queue_outbox_preflight -> 0005_entity_version_guard, ...
INFO  [alembic.runtime.migration] Running upgrade 0005_entity_version_guard -> 0006_queue_outbox_preflight, ...
No new upgrade operations detected.
```

EXPLAIN запиту `latest_validators` (з `test_fetch_preflight.py::
test_validators_query_uses_partial_index_without_seq_scan`, 3 місяці × 20 URL, `ANALYZE`,
`SET LOCAL enable_seqscan = off` — доводить застосовність індексу в кожній партиції; на
малих партиціях планувальник інакше обрав би seq scan за вартістю):

```text
Limit
  ->  Merge Append
        Sort Key: fetches.fetched_at DESC
        ->  Index Scan Backward using fetches_y2026m09_source_id_requested_url_md5_fetched_at_idx on fetches_y2026m09 fetches_1
              Index Cond: ((source_id = '…'::uuid) AND ((requested_url_md5)::text = '0de458db57e315d5dfcdaf3c0be7b4eb'::text))
              Filter: (requested_url = 'https://news.example.test/article/1?n=3'::text)
        ->  Index Scan Backward using fetches_y2026m10_source_id_requested_url_md5_fetched_at_idx on fetches_y2026m10 fetches_2
              Index Cond: (… той самий …)
        ->  Index Scan Backward using fetches_y2026m11_source_id_requested_url_md5_fetched_at_idx on fetches_y2026m11 fetches_3
        ->  Index Scan Backward using fetches_y2026m12_source_id_requested_url_md5_fetched_at_idx on fetches_y2026m12 fetches_4
        ->  Index Scan Backward using fetches_default_source_id_requested_url_md5_fetched_at_idx on fetches_default fetches_5
```

Integration PostgreSQL (testcontainers, pinned `postgres:18`):

```text
$ uv run pytest -m integration tests/integration/postgres
tests\integration\postgres\test_fetch_preflight.py ...........           [ 25%]
tests\integration\postgres\test_limiter.py .........                     [ 28%]
tests\integration\postgres\test_migrations.py .............              [ 33%]
tests\integration\postgres\test_outbox_delivery.py ..........            [ 37%]
tests\integration\postgres\test_outbox_entities.py ........              [ 40%]
tests\integration\postgres\test_pools.py ...........                     [ 45%]
tests\integration\postgres\test_pr2_adversarial.py ................      [ 51%]
tests\integration\postgres\test_pr3a_roles.py ........                   [ 54%]
tests\integration\postgres\test_projection.py ....................       [ 62%]
tests\integration\postgres\test_queue.py .........                       [ 65%]
tests\integration\postgres\test_queue_defer.py ..........                [ 69%]
tests\integration\postgres\test_queue_release.py ..                      [ 70%]
tests\integration\postgres\test_role_connections.py .................    [ 77%]
tests\integration\postgres\test_role_logins.py .................         [ 83%]
tests\integration\postgres\test_roles.py .....                           [ 85%]
tests\integration\postgres\test_schema_contract.py ..................... [ 94%]
....                                                                     [ 95%]
tests\integration\postgres\test_upload_claims.py ...........             [100%]

====================== 255 passed in 3291.95s (0:54:51) =======================
```

(До PR3a — 209 тестів у наборі; +46 нових.)

```text
$ uv run pytest -m "not live"
...
SKIPPED [6] tests\e2e\test_gui_runtime_contract.py:167: gui �� ������� �� http://127.0.0.1:80 ...
... (gui runtime / e2e-enforced — лише в job `docker`; test_network_blocked — Windows)
FAILED tests/integration/scaling/test_scheduler_singleton.py::test_two_schedulers_keep_exactly_one_active_and_lease_is_retaken_after_a_kill
FAILED tests/integration/scaling/test_worker_runtime.py::test_drain_timeout_returns_the_lease_to_the_queue
FAILED tests/integration/scaling/test_worker_runtime.py::test_heartbeat_does_not_extend_a_foreign_lease
FAILED tests/integration/scaling/test_worker_runtime.py::test_role_wide_drain_barrier_stops_claim_without_sigterm
FAILED tests/integration/scaling/test_worker_runtime.py::test_handler_failure_becomes_a_retry_with_backoff
==== 5 failed, 1139 passed, 23 skipped, 10 warnings in 4066.64s (1:07:46) =====
```

П'ять падінь — WP-01D-набір `tests/integration/scaling` (тести з wall-clock таймінгами:
heartbeat/lease/drain), прогін тривав 67 хв під навантаженням хоста. Ізольований повтор тих
самих двох модулів проти одноразового PostgreSQL 18:

```text
$ COLLECTOR_TEST_POSTGRES_ADMIN_DSN=… uv run pytest -m integration tests/integration/scaling/test_worker_runtime.py tests/integration/scaling/test_scheduler_singleton.py
21 passed in 541.65s (0:09:01)
```

Той самий клас нестабільності задокументовано в `docs/plan/deps/WP-01A-to-WP-01D.md` §5
(відтворювався на базовому коміті PR2). Код PR3a змінює лише сигнатури з default-ами, що
зберігають поведінку для викликів без нових параметрів.

Linux-паритет (unit-набір persistence у `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`,
`git archive HEAD`, повне `--disable-socket` Linux):

```text
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 4.59s
```

```text
$ uv run pre-commit run --all-files
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
```

## Що не перевірено

- CI job `integration-postgres` (Linux, service container) — гілку не пушено; локально
  integration виконано на Windows через testcontainers, unit — ще й у Linux-контейнері.
- Integration-набір у Linux-контейнері не проганявся (потребує Docker-in-Docker/мережі хоста).
- EXPLAIN на реальному обсязі `fetches`: тест доводить застосовність індексу (з вимкненим seq
  scan), а не вибір планувальника на мільйонах рядків.
- Документація acceptance доповнена після gate 2: `docs/persistence/postgres.md` охоплює
  `delivery_attempts`/N-2, `purge_published`, `ix_fetches_validators`, column GRANT
  `source_routes`, fencing і модуль `reconciliation`.
- Споживання API — WP-01D PR1c/WP-02 PR2/WP-01B PR3 ще не злиті; сумісність перевірено лише
  сигнатурами й тестами цього PR.

## Ризики

- **Розбіжність картки і ТЗ щодо «повноти cursor».** §7.3 крок 5: cursor опрацьований, коли
  всі tasks «acknowledged або quarantined»; картка п.9: quarantined **блокує** повноту. Дано
  обидва предикати: `ProjectionCompleteness.settled` (ТЗ) і `.complete` (картка). Рішення, який
  використовує reconciler WP-01B, — за оркестратором/spec-reviewer.
- **`not_before` у `retry` скасовує `policy`.** Це свідомий вибір (таблиця §10 коротша за
  дефолтний backoff); викликач, що передає лише `Retry-After`, мусить сам додати табличну
  затримку (`max(...)`), інакше retry буде раніше за backoff.
- **REVOKE табличного UPDATE `source_routes` у `collector_fetcher`.** Будь-який код під
  fetcher, що оновлював інші колонки route напряму, отримає permission denied. У `main` такого
  коду немає (усе через репозиторій); WP-02 PR1 паралельно — лише через репозиторій.
- **`ParseResult.outbox_event` тепер `OutboxEvent | None`.** Зовнішніх викликачів у `src/`
  немає; WP-05 має це врахувати.
- **Stored generated column на партиціонованій `fetches`**: `ALTER TABLE … ADD COLUMN …
  STORED` переписує таблицю — на великій production-таблиці це довгий lock. Зараз таблиця в
  dev порожня/мала; для production це вже forward-only міграція, тож прийнятно лише до
  першого production-розгортання.
- `projection_completeness` за `status <> 'succeeded'` не має спеціального індексу — для
  reconciler-тіку (раз на вікно) прийнятно; при великій кількості відкритих tasks — додати
  partial index.
- Обсяг продуктивного коду на межі ~800 рядків.

## Як вимкнути або відкотити

- Код: `git revert` комітів PR3a; нові параметри мають default-и, тож revert не ламає
  викликачів, які їх ще не передають.
- Схема (dev/test): `alembic downgrade 0005_entity_version_guard` — прибирає
  `ix_fetches_validators`, `fetches.requested_url_md5`, CHECK і `outbox_events.delivery_attempts`
  (похідні дані, джерельні факти не втрачаються; перевірено циклом вище). Production —
  forward-only (новий forward-fix migration), картка «Rollback/disable».
- GRANT: повторний `collector db roles` з попередньою версією `roles.sql` **не** повертає
  табличний UPDATE `source_routes` fetcher-у і не забирає DELETE outbox у scheduler — для
  повного відкату потрібні явні `GRANT UPDATE ON source_routes TO collector_fetcher` /
  `REVOKE DELETE ON outbox_events FROM collector_scheduler`.
- N-2 без відкату схеми: `fetch_unpublished(max_delivery_attempts=<велике>)` фактично вимикає
  паркування за видачами; `purge_published` викликається лише maintenance WP-12 (поки не
  викликається нічим).

## Виправлення після gate 2

Джерело: `docs/plan/reports/WP-01A/testing-pr3a.md` (незалежні тести — коміт `91cd19f`, не
послаблювалися).

| Знахідка | Виправлення | Доказ |
|---|---|---|
| F-1 medium — acceptance-документація не охоплювала PR3a | `docs/persistence/postgres.md` оновлено до PR1–PR3a: схема/колонки, transaction boundaries queue/outbox/preflight/reconciler, N-2/purge, fencing, індекс validators, LOGIN/GRANT | `markdownlint-cli2`; звірка з `0006`, repositories і role tests |
| F-2 low — `[]` може означати лише parked candidates, не порожній backlog | Docstring `fetch_unpublished` і PostgreSQL-документація прямо фіксують цю семантику та повторний poll; SQL не ускладнювався циклом у транзакції | adversarial `test_batch_of_only_exhausted_rows_returns_empty_while_backlog_remains` |
| F-3 low — naive datetime оброблявся неоднорідно | Новий `clock.require_aware_utc(value, parameter=…)`; `clamp_not_before`, `count_retries_since`, `projection_completeness(created_before)` відхиляють naive до SQL і нормалізують aware offset до UTC | unit `test_clamp_not_before_rejects_naive_and_normalizes_aware_offsets`; integration `test_count_retries_since_rejects_naive_boundary_before_sql`, `test_projection_completeness_rejects_naive_watermark_before_sql`; наявний adversarial test чотирьох defer/retry API |
| F-4 info — expired, unrecovered lease owner може ack | Код не змінено: це безпечна owner-based семантика, спільна з `queue.complete`; зафіксовано в `acknowledge_projection` і PostgreSQL docs для WP-01D PR1c | `test_ack_with_owner_on_expired_but_unrecovered_lease_is_accepted`; row-lock конкуренція recover/claim |

Після виправлень: `ruff`/format green, mypy strict — 100 source files, 18 unit tests і 20
цільових PostgreSQL integration tests passed. Повний PostgreSQL-набір і всі repository gates
повторюються перед PR після code/spec review.

## Виправлення після gate 3

Джерело: `docs/plan/reports/WP-01A/code-review-pr3a.md`.

| Знахідка | Виправлення | Доказ |
|---|---|---|
| CR-1 medium — від'ємний `older_than` робив свіжі tasks stale | `list_stale_projection_tasks` відхиляє від'ємний поріг до SQL | `test_stale_tasks_rejects_negative_age_before_sql` |
| CR-2 medium — недодатний `circuit_open_for` відкривав уже спливлий circuit | Явна перевірка `circuit_open_for > 0` для не-`None`; `None` зберігає безстроковий circuit | `test_route_failure_does_not_override_unsupported_and_validates_input` (0 та −1 мкс) |
| CR-3 low — `next_attempt_at` допускав naive `now` у backoff-гілці | Helper завжди викликає `require_aware_utc`, незалежно від `not_before` | `test_next_attempt_at_rejects_naive_now_without_explicit_not_before` |

Re-review: **approved**. Цільовий unit + PostgreSQL integration прогін — `26 passed in
24.06s`.

## Dependency-запити

Нових немає. Оновлено відповідь `docs/plan/deps/WP-01A-to-WP-01D.md`: §7 (N-2) — PG-частину
реалізовано, `resolved` разом із WP-01B PR3; §8 — вказівник на API для PR1c.
