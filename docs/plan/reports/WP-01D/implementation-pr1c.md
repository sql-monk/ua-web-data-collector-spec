# WP-01D PR1c — implementation report

| Поле | Значення |
|---|---|
| Branch | `wp/01d-1c-handler-plumbing` |
| Dependency | WP-01A PR3a merged у `main` (`9322fc7`) |
| Scope | runtime plumbing для handler-ів і scheduler ticks хвилі 1 |
| Статус | ready for PR |

## Реалізовано

1. `TaskResult` підтримує `retryable(..., not_before=)`, `deferred(until, error_code)` і
   `success(output=...)`. `RetrySchedule` задає табличні затримки з обмеженим jitter.
2. `WorkerRuntime` обчислює retry lower bound, clamp-ить надмірний defer, повертає deferred
   job через release без спалювання attempt і працює з кількома `HandlerBinding` у спільному
   pool слотів із round-robin claim.
3. `HandlerContext` передає фабриці той самий session factory, instance id/owner, clock та env.
   Lazy registry відрізняє ще не створений доменний модуль від зламаного імпорту: лише перший
   випадок дозволяє `NoopHandler`.
4. Додано `QueueBackend`, `CrawlJobsBackend` і `ProjectionTasksBackend`. Projection receipt
   ack-иться з owner fencing у тій самій report-транзакції; неправильний output карантиниться.
5. Scheduler складає maintenance із доменними lazy ticks, має окремі interval/timeout,
   ізолює відмови й перевіряє advisory lease перед неідемпотентною роботою.
6. Після code review усунуто cancellation race server-side lease check: shielded запит утримує
   lock до фактичного завершення, а shutdown release серіалізовано тим самим lock.
7. `docs/workers.md` §5–§6 описує API handler-а, backend bindings, projection ack та контракт
   scheduler tick-а.

## Dependency PR3a

Після merge PR #12 гілку синхронізовано з `main`. Тимчасові `_Pr3a*` Protocol/cast та всі
`NEEDS_PR3A` xfail-маркери видалено. Виклики й тести працюють безпосередньо з merged API:

- `queue.retry/release(..., not_before=...)`;
- `retry/release_projection_task(..., not_before=...)`;
- `acknowledge_projection(..., owner=...)`.

## Верифікація

```text
ruff check / format: pass
mypy src: 102 source files, pass
unit workers: 162 passed
focused PR1c integration: 25 passed
full integration/scaling: 95 passed
full `pytest -m "not live"`: 3607 passed, 23 expected skips, 9 warnings in 18:27
pre-commit (all files): all hooks passed
```

`COLLECTOR_TEST_REQUIRE_DOCKER=1` був увімкнений: недоступний PostgreSQL/Docker став би
failure. 23 skips — окремий GUI/e2e стек, якого цей PR не змінює, та один Windows-only
network test. Незалежний звіт з mutation-check: `docs/plan/reports/WP-01D/testing-pr1c.md`.
Code review і виправлення CR-1:
`docs/plan/reports/WP-01D/code-review-pr1c.md`.

## Ризики й межі

- Реальні доменні модулі WP-01B/WP-02/WP-04 ще не входять до цього PR; registry перевірено
  синтетичними модулями, а відсутні модулі свідомо дають видимий Noop fallback.
- PR1c не змінює Compose, образи, міграції, ролі чи схему БД; end-to-end compose deployment
  належить відповідним WP.
- Projection quarantine фенситься через heartbeat + quarantine в одній транзакції, бо
  repository operator API поки не приймає owner напряму.
- Scheduler tick-и лишаються зобов'язаними бути ідемпотентними: advisory lease звужує, але не
  може математично усунути overlap при втраті connection.

## Rollback

`COLLECTOR_WORKER_PLACEHOLDER=1` обходить registry та лишає placeholder-поведінку. Publisher
вимкнений, доки не встановлено `COLLECTOR_OUTBOX_PUBLISHER_ENABLED`. Повний rollback — revert
комітів PR1c; міграцій і незворотних змін даних немає.
