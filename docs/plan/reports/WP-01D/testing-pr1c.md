# WP-01D PR1c — незалежне тестування (етап 2)

| Поле | Значення |
|---|---|
| Branch | `wp/01d-1c-handler-plumbing` після merge `main` з WP-01A PR3a |
| Контракт | `docs/plan/cards/WP-01D.md`, розділ PR1c |
| Рівні §16.1 | 1 Unit, 3 Integration, 15 Scaling |
| Вердикт | **pass** |

## Відновлена точка роботи

Реалізація була готова, але 9 integration-тестів мали `xfail(strict=True)` до merge WP-01A
PR3a, а два незалежні adversarial-модулі лишилися незакоміченими. Після merge PR #12:

- гілку синхронізовано з `main` без конфліктів;
- тимчасові `_Pr3a*` Protocol/cast видалено, repository API викликаються напряму;
- 9 початкових і 3 adversarial `NEEDS_PR3A` маркери видалено;
- обидва adversarial-модулі збережено окремим test-комітом `88bed77`.

## Результати

```text
$ uv run ruff check src/collector/workers tests/unit/workers tests/integration/scaling
All checks passed!
$ uv run ruff format --check src/collector/workers tests/unit/workers tests/integration/scaling
33 files already formatted
$ uv run mypy src
Success: no issues found in 102 source files
$ uv run pytest -q tests/unit/workers
162 passed in 2.23s
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -q -m integration \
    tests/integration/scaling/test_handler_plumbing.py \
    tests/integration/scaling/test_handler_plumbing_adversarial.py \
    tests/integration/scaling/test_projection_backend.py \
    tests/integration/scaling/test_scheduler_ticks.py
25 passed in 99.90s
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -q -m integration tests/integration/scaling
95 passed in 383.38s (0:06:23)
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -q -m "not live"
3607 passed, 23 skipped, 9 warnings in 1107.97s (0:18:27)
```

`COLLECTOR_TEST_REQUIRE_DOCKER=1` перетворює недоступний Docker/PostgreSQL на failure, а не
skip. У PR1c integration/scaling після синхронізації немає xfail або skip. 23 skips повного
набору — зовнішній GUI/e2e стек і один Windows-only network test, не PR1c.

## Acceptance → доказ

| Вимога PR1c | Доказ | Результат |
|---|---|---|
| `defer` не спалює attempt, не пише error/dead letter, включно з останньою спробою | `test_handler_plumbing.py`, `test_handler_plumbing_adversarial.py`, unit runtime plumbing | pass |
| Retry schedule §10 + lower bound/ceiling | unit deterministic jitter/boundary tests; PostgreSQL exact-delay tests | pass |
| Lazy import: відсутній модуль → Noop, зламаний → boot failure | `test_registry.py`, adversarial registry isolation tests | pass |
| Factory отримує runtime sessions/instance id | unit registry + PostgreSQL handler plumbing | pass |
| Кілька backend-ів ділять slots і не голодують | unit round-robin + projection integration | pass |
| Projection ack у report transaction, stale owner fenced | projection backend rollback/takeover/duplicate/foreign-receipt tests | pass |
| Scheduler ticks lazy, isolated, lease-aware | `test_scheduler_ticks.py`, adversarial unit tick failure/timeout tests | pass |
| LOGIN roles і scaling regressions | повний `tests/integration/scaling`: 95/95 | pass |

Окремо після code-review виправлення scheduler-а:

```text
$ uv run pytest -q tests/integration/scaling/test_scheduler_ticks.py
4 passed in 10.03s
```

## Mutation-check

Тимчасова мутація `ProjectionTasksBackend.complete`: `owner=owner` → `owner=None` вимкнула
fencing ack. Тест
`test_lost_lease_blocks_the_ack_and_the_next_owner_acks_exactly_once` став червоним:

```text
assert (first.runtime.lost_leases, first.runtime.report_failures) == (1, 0)
E assert (0, 0) == (1, 0)
1 failed, 9 deselected in 15.34s
```

Мутацію повернуто через patch; `git diff` після повернення порожній. Це доводить, що тест
перевіряє саме owner fencing, а не лише фінальний happy path.

Також виконано три мутації, прямо названі в acceptance картки:

1. `CrawlJobsBackend.defer`: `queue.release` замінено на `queue.retry`. Обидва тести
   `test_deferred_job_keeps_its_attempt_and_writes_no_error` і
   `test_five_defers_in_a_row_never_dead_letter_a_job_with_four_attempts` стали червоними:
   перший побачив `('retry', 1)` замість `('pending', 0)`, другий зупинився після четвертої
   спроби. Мутацію повернуто.
2. `ProjectionTasksBackend.complete`: додано передчасний `session.commit()` одразу після ack,
   тобто ack винесено з атомарної report-транзакції. Тест
   `test_fault_between_ack_and_commit_leaves_no_partial_rows` став червоним: task лишився
   `succeeded`, хоча fault-seam підняв виняток. Мутацію повернуто.
3. `registry.import_optional`: будь-який `ModuleNotFoundError` тимчасово трактувався як
   «доменного модуля ще немає». Два варіанти
   `test_broken_role_module_fails_boot_instead_of_a_silent_noop` стали червоними — runtime
   мовчки вибрав `NoopHandler` при відсутній внутрішній/сторонній залежності. Мутацію повернуто.

Після трьох мутацій `git diff` порожній.

## Додане adversarial-покриття

- `test_runtime_plumbing_adversarial.py`: last-attempt defer, past/future clamp, deterministic
  retry jitter, binding routing, broken-module isolation, output quarantine/redaction,
  scheduler tick isolation, cancellation гонка shielded lease-check на retained connection.
- `test_handler_plumbing_adversarial.py`: defer на єдиній спробі, duplicate/stale/foreign
  projection ack, rollback без часткових рядків, invalid crawl output → quarantine/dead letter.

## Висновок

**PASS.** Залежність PR3a більше не прихована xfail-маркерами; усі acceptance-сценарії PR1c
виконуються напряму, повний scaling-набір зелений, усі три обов'язкові мутації та додаткова
owner-fencing мутація виявляються тестами.
