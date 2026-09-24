# WP-01D PR1c — code review (етап 3)

Гілка `wp/01d-1c-handler-plumbing`, diff від актуального `main` після merge WP-01A PR3a.

## Знахідки

| # | Severity | Місце | Сценарій відмови | Рішення |
|---|---|---|---|---|
| CR-1 | medium | `src/collector/workers/scheduler.py`, `_lease_held` / `run.finally` | `asyncio.wait_for` скасовує доменний тік під час `ctx.lease_is_ours()`. `shield` залишав запит `AdvisoryLease.is_held()` працювати, але вихід з `async with _lease_check` одразу відпускав lock. Наступна перевірка або `lease.release()` могла одночасно використати те саме retained asyncpg connection і отримати `another operation is in progress`; у гіршому випадку shutdown пошкоджував стан lease. | **fixed** у `c0190c7`: внутрішня task тримає lock до фактичного завершення; callback забирає її результат і відпускає lock після cancellation caller-а; shutdown `release()` використовує той самий lock. Додано adversarial-тест, що скасовує перший check і доводить `max_active == 1`. |

Після виправлення critical/high/medium відкритих знахідок немає.

## Перевірені інваріанти

- `TaskResult`: несумісні поля відхиляються; усі `not_before` timezone-aware; `defer` не має
  error text/output і не може створити dead letter.
- Retry: таблиця handler-а індексується поточною 1-based attempt, jitter обмежений;
  handler lower bound не скорочує schedule, а конфігурована стеля не дозволяє ховати job
  надовго.
- Registry: лише відсутність самого майбутнього доменного модуля дає Noop; зламана залежність,
  import-time exception, відсутня фабрика та неправильний контракт валять boot.
- Multi-backend runtime: спільна concurrency, round-robin після успішного claim, heartbeat,
  report і drain прив'язані до backend конкретної task.
- Projection: тип output перевіряється до complete; ack, confirmed version, task status і
  `domain.changed` лишаються в одній report-транзакції; stale owner відсікається repository
  fencing.
- Scheduler: кожен тік ізольований, lazy import не тягне вимкнений publisher, ownership
  перевіряється перед доменним тіком, а server-side check-и не конкурують на одному connection.
- Cancellation не перехоплюється загальними `except Exception`, тому task/runtime shutdown
  зберігає семантику `CancelledError`.

## Прогони після виправлення

```text
uv run pytest tests/unit/workers -q
162 passed in 2.23s

COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -q -m integration \
  tests/integration/scaling/test_scheduler_ticks.py
4 passed in 10.03s

uv run ruff check src/collector/workers/scheduler.py \
  tests/unit/workers/test_runtime_plumbing_adversarial.py
All checks passed!

uv run mypy src
Success: no issues found in 102 source files
```

## Вердикт

**approve after fix** — CR-1 виправлена й має regression-тест; відкритих блокерів немає.
