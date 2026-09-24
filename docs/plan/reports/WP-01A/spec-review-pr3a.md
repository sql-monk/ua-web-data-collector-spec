# WP-01A PR3a — пострев'ю за ТЗ (етап 4)

| Поле | Значення |
|---|---|
| Під-PR | `wp/01a-3a-queue-outbox-preflight` |
| Контракт | `TECHNICAL_SPECIFICATION.md` v1.4, `REVIEW.md`, картка WP-01A PR3a |
| Вхідні gates | `testing-pr3a.md`, `code-review-pr3a.md` |
| Вердикт | **accept** (`missing=0`; CI лишається умовою merge) |

## 1. Acceptance PR3a

| # | Вимога | Доказ | Статус |
|---|---|---|---|
| 1 | Queue/projection `not_before`, defer без спалювання attempt | `queue.py`, `projection.py`; `test_queue_defer.py`, adversarial claim/defer tests | evidenced |
| 2 | Outbox N-2 рахує кожну видачу і паркує poison row | migration `0006`; `outbox.fetch_unpublished`; delivery/concurrency/crash tests | evidenced |
| 3 | Purge published domain та terminal+ack internal, keyset без `OFFSET` | `outbox.purge_published`; purge boundary/concurrency tests | evidenced |
| 4 | Fetch preflight одним запитом за source UUID + route ID | `sources.get_fetch_preflight`; paused/circuit/missing/LOGIN tests | evidenced |
| 5 | Validators з останнього 200/206, 304 ігнорується, індекс застосовний | generated URL hash + partial index; validator та EXPLAIN tests | evidenced |
| 6 | Atomic route failure counter; threshold → circuit + audit | `sources.record_route_failure`; rollback/concurrency/threshold tests | evidenced |
| 7 | Ack fencing за owner у тій самій транзакції | `projection.acknowledge_projection`; stale/missing/recovered lease tests | evidenced |
| 8 | Retry budget query за source/time window | `artifacts.count_retries_since`; partition/timezone boundary tests | evidenced |
| 9 | Reconciler stale/completeness queries, keyset, без `OFFSET` | `reconciliation.py`; stale/quarantine/watermark matrix tests | evidenced |
| 10 | Мінімальні GRANT-и і негативні перевірки чужих ролей | `sql/roles.sql`; `test_pr3a_roles.py`, migration/roles adversarial tests | evidenced |
| AC-1 | Локальні тести зелені без прихованого skip | testing report: 255 PostgreSQL + 29 adversarial; skip guard; після gate 3 — 26 targeted | evidenced |
| AC-2 | `alembic check`, upgrade/downgrade/upgrade | implementation/testing reports, migration adversarial test | evidenced |
| AC-3 | `docs/persistence/postgres.md` доповнено | схема, операції, N-2, purge, preflight, fencing, reconciler, roles | evidenced |
| AC-4 | Dependency §7 | PG-частина реалізована; файл прямо лишає повне `resolved` до WP-01B PR3 | evidenced for PR3a / transferred consumer |
| AC-5 | CI `integration-postgres` green | PR ще не відкрито | partial — обов'язкова умова merge |

## 2. Відповідність ТЗ

| Вимога | Доказ | Статус |
|---|---|---|
| §7.2 queue: lease, attempt, `not_before`, idempotency, dead letter | PR1 контракт збережено; PR3a додає defer/retry lower bound і regression tests | evidenced |
| §7.3 кроки 2/4: transactional outbox та ack після Mongo commit | N-2/purge не публікують `projection.command`; fencing не змінює idempotent ack без owner | evidenced |
| §7.3 крок 5 / FR-022: знаходити незавершені tasks і drift | `list_stale_projection_tasks`, `projection_completeness`; `settled` відповідає ТЗ | evidenced (Mongo-side repair — WP-01B PR3) |
| §9.1/§9.5: operational metadata, outbox, exact event bytes | additive migration; payload у PostgreSQL не додано | evidenced |
| §9.6 / FR-024: UTC-only | `require_aware_utc`; naive/offset regression tests | evidenced |
| §10 п.4–6 / FR-004: preflight, conditional validators, retry budget, circuit signal | repository APIs і tests; мережеве застосування — WP-02 PR2 | evidenced for PG contract / transferred consumer |
| §10 п.13: outbox at-least-once, bounded poison handling | visibility lease + `delivery_attempts`; consumer dedup contract не змінено | evidenced |
| §13 / FR-013: per-component DB least privilege, audit | column-level fetcher UPDATE, projector read-only reconciliation, negative LOGIN tests, secret scan hook | evidenced |
| §15: keyset, bounded batch | purge/stale/quarantine queries мають `after` + `limit`, без `OFFSET` | evidenced |

Розбіжність «cursor acknowledged або quarantined» (§7.3) проти картки «quarantined блокує
повноту» вирішено двома явними предикатами: `settled` для дослівної семантики ТЗ і `complete`
для операційного закриття cursor за карткою. Це не прихована зміна контракту; споживач WP-01B
має обрати предикат відповідно до операції.

## 3. Definition of Done §18

| Пункт | Статус | Коментар |
|---|---|---|
| Один WP, без сторонніх змін | evidenced | diff у межах PostgreSQL owner + reports/dependency/docs acceptance |
| Formatter/lint/types/tests | evidenced | ruff, format, mypy, pre-commit; PostgreSQL і adversarial тести |
| Migration + compatibility evidence | evidenced | `0006`, existing-data upgrade, downgrade/upgrade, metadata tests |
| Temporal/replay evidence | evidenced | UTC boundary tests; outbox crash, ack/reconcile replay cases |
| Adapter artifacts | not applicable | адаптер не додається |
| Документація, метрики, runbook | partial | PostgreSQL docs/docstrings оновлені; metrics/maintenance invocation — WP-12 |
| Secret scan / contacts hygiene | evidenced | pre-commit secret scan; секретів/контактних fixtures у diff немає |
| Reviewer findings triaged | evidenced | testing F-1..F-4 та code review CR-1..CR-3 закриті/пояснені |
| Merge лише після CI/review, SHA у ledger | partial | виконується інтеграційним етапом після цього gate |

## 4. Регресії `REVIEW.md`

| ID | Перевірка | Статус |
|---|---|---|
| R-24 / R-36 | cross-store projection лишається task/receipt/ack; unique version/task не послаблено | preserved |
| R-27 | нових domain payload JSONB немає; лише лічильник, hash та operational metadata | preserved |
| R-28 | API, стани, межі транзакцій і семантика часу зафіксовані в docstrings/docs/tests | preserved |
| R-32 | validator lookup має compound partial index; queue/outbox indexes не послаблено | preserved |
| R-38 / R-41 | upload claim generation/lease код не змінено; regression suite зелена | preserved |
| R-43 | UTC-only застосовано до нових temporal parameters | strengthened |
| R-53 | global origin permits не змінено; preflight не обходить limiter | preserved |

## 5. Traceability Додатка C

- «Керований збір» — FR-004: conditional validators, retry budget, route circuit state.
- «Узгодженість двох БД» — FR-020–FR-023, §7.3: fencing ack, stale/completeness queries,
  безпечне очищення internal outbox лише після acknowledgement.
- «Часова коректність» — FR-024, §9.6: aware UTC validation і timezone boundary tests.
- «Технічна безпека» — FR-013, §13: least-privilege role tests та audit переходу circuit.
- «Незалежна реалізація» — §17/§18: картка, окремі implementation/testing/review reports,
  migration compatibility evidence.

## 6. Залежності та відкриті рішення

- Нового рішення поза ТЗ або ADR не прийнято; Q-001—Q-014 не блокують цей під-PR.
- Publisher loop і Mongo reconciler споживають API у WP-01B PR3; до того dependency §7
  коректно лишається «PG-частина реалізована, resolved після WP-01B PR3».
- WP-02 PR2 споживає preflight/validators/retry budget/route counter.
- WP-01D PR1c споживає defer/not-before/fencing API.

## Вердикт

**ACCEPT.** Відсутніх вимог або відкритих critical/high findings немає. Єдина зовнішня умова
злиття — зелений CI відкритого PR; consumer-side інтеграції лишаються у власних WP і не є
прихованим боргом PR3a.
