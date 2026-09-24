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
161 passed in 9.31s
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -q -m integration \
    tests/integration/scaling/test_handler_plumbing.py \
    tests/integration/scaling/test_handler_plumbing_adversarial.py \
    tests/integration/scaling/test_projection_backend.py \
    tests/integration/scaling/test_scheduler_ticks.py
25 passed in 99.90s
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -q -m integration tests/integration/scaling
95 passed in 383.38s (0:06:23)
```

`COLLECTOR_TEST_REQUIRE_DOCKER=1` перетворює недоступний Docker/PostgreSQL на failure, а не
skip. У PR1c integration/scaling після синхронізації немає xfail або skip.

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

## Додане adversarial-покриття

- `test_runtime_plumbing_adversarial.py`: last-attempt defer, past/future clamp, deterministic
  retry jitter, binding routing, broken-module isolation, output quarantine/redaction,
  scheduler tick isolation.
- `test_handler_plumbing_adversarial.py`: defer на єдиній спробі, duplicate/stale/foreign
  projection ack, rollback без часткових рядків, invalid crawl output → quarantine/dead letter.

## Висновок

**PASS.** Залежність PR3a більше не прихована xfail-маркерами; усі acceptance-сценарії PR1c
виконуються напряму, повний scaling-набір зелений, mutation-check чутливий до fencing defect.
