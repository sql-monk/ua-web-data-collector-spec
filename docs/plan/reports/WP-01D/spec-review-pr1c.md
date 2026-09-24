# WP-01D PR1c — spec review (етап 4)

Контракт: `docs/plan/cards/WP-01D.md`, PR1c; додатково §7.5–§7.6, §10, §13, §15,
§16.1/§16.3 і регресії R-52/R-53/R-57.

## Acceptance traceability

| Вимога | Реалізація | Доказ | Статус |
|---|---|---|---|
| 1. `not_before`, `defer` без attempt/error/dead letter | `handlers.py`, `runtime.py`, backend `defer → release` | unit + PostgreSQL repeated-defer tests; mutation `release → retry` червона | pass |
| 2. Retry schedule §10 | `RetrySchedule`, `_retry_not_before` | boundary/jitter unit tests, exact-delay PostgreSQL tests, default-backoff regression | pass |
| 3. Lazy role registry | `registry.py` | missing/broken/no-factory/CLI/placeholder tests; silent-Noop mutation червона | pass |
| 4. `HandlerContext` | `handlers.py`, runtime construction | identity test для sessions та registered instance id | pass |
| 5. Projection backend і multi-binding | `backends.py`, `runtime.py` | ack/rollback/stale-owner/retry/defer/quarantine/drain + round-robin tests | pass |
| 6. Scheduler tick registry | `registry.py`, `scheduler.py` | recovery, isolation, singleton overlap, lease-loss і cancellation-race tests | pass |
| Docs §5–§6 | `docs/workers.md` | markdown/pre-commit gate | pass |

## Cross-cutting invariants

- R-52/R-57: backend кожної active task зберігається до heartbeat/report/drain; role-wide drain
  логіка PR1 не змінена, release не перетворено на retry.
- R-53: PR1c не вводить локальний rate limiter і не змінює global permit contract; handler
  отримує стабільний instance owner для permit claims.
- §13: немає нових DSN, секретів або привілейованих з'єднань; фабрика використовує session
  factory runtime LOGIN-ролі. Помилки й логи проходять redaction, task args не логуються.
- §15: нового локального durable state немає; registry/configuration process-local, черги й
  lease лишаються в PostgreSQL.
- PR3a dependency більше не умовна: strict xfail/cast прибрані, full scaling зелений.

## Відхилення та залишкові ризики

Відхилень від картки не знайдено. Доменні реалізації та compose wiring лишаються у власних WP,
що відповідає Out of scope. Виправлена code-review знахідка CR-1 не змінює публічний контракт,
лише гарантує серіалізацію retained advisory connection під cancellation.

## Вердикт

**accept** — усі пункти PR1c простежуються до коду й executable tests; DoD для цього PR
виконано після фінального повного прогону/CI.
