# WP-01A PR3a — звіт документування (етап 5)

| Поле | Значення |
|---|---|
| Під-PR | `wp/01a-3a-queue-outbox-preflight` |
| Вхід | картка PR3a, implementation/testing/code/security/spec reports |
| Вердикт | **done** |

## Оновлена документація

- `docs/persistence/postgres.md` розширено з PR1–PR2 до PR1–PR3a: нові колонки/індекси,
  queue/projection defer, outbox N-2 та purge, preflight/validators, route counter, retry
  budget, fencing ack, reconciler queries, UTC-вимоги й column-level grants.
- У transaction-boundary таблицях прямо зафіксовано, де тримаються row locks, хто робить
  commit, чому `fetch_unpublished == []` не доводить порожній backlog і коли internal outbox
  можна видаляти.
- `docs/plan/deps/WP-01A-to-WP-01D.md` фіксує API для PR1c та статус N-2: PostgreSQL-частина
  готова, повне закриття — після publisher loop у WP-01B PR3.
- Публічні repository API мають docstrings із семантикою параметрів, транзакційною межею,
  fencing/lease поведінкою та типовими помилками.
- Окремі звіти `testing-pr3a.md`, `code-review-pr3a.md`, `security-pr3a.md` і
  `spec-review-pr3a.md` зберігають відтворювані докази gates.

## Рішення про додаткові артефакти

- Новий ADR не потрібний: PR3a реалізує рішення картки й ADR-0007, не приймаючи нового
  архітектурного default поза ТЗ.
- Новий runbook не потрібний: schema rollout/rollback уже покритий
  `docs/runbooks/migrations.md`; виклик purge/reconcile належить runtime WP-01B/WP-12.
- Каталог `docs/observability` ще не створений. Нові сигнали (parked/delivery attempts,
  circuit state, reconciliation drift) описані в persistence docs; метрики й alerts належать
  WP-12 та не створюються тут без runtime producer-а.

## Перевірка

```text
$ uv run pre-commit run --all-files
... ruff check, ruff format, secret scan, markdownlint-cli2 ... Passed
```

Зовнішніх URL у нових/оновлених документах немає, тому окремий network link-check не додає
покриття.
