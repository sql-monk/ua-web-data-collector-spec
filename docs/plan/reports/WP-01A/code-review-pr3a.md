# WP-01A PR3a — код-рев'ю (етап 3)

| Поле | Значення |
|---|---|
| Гілка | `wp/01a-3a-queue-outbox-preflight`, diff `main...HEAD` |
| Картка | `docs/plan/cards/WP-01A.md`, розділ «PR3a» |
| Вхід рев'ю | implementation через `41bf97a`, незалежні тести `91cd19f`, testing report `91aa4c8` |
| Вердикт | **approved after fixes** (3/3 findings fixed) |

Перевірено транзакційні межі queue/outbox/projection, `SKIP LOCKED`, fencing, N-2,
ідемпотентність, keyset pagination, route counter/circuit transition, UTC-контракти,
PostgreSQL roles та downgrade/upgrade наслідки. Знахідки нижче виправлено без послаблення
незалежних тестів.

## Знахідки та рішення

| ID | Severity | Файл:рядок до виправлення | Claim / failure scenario | Verdict | Рішення |
|---|---|---|---|---|---|
| CR-1 | medium | `repositories/reconciliation.py:63` | `older_than < 0` переносив cutoff у майбутнє, тому свіжі pending/leased tasks помилково ставали «застарілими» й могли бути передані reconciler-у для зайвого repair. | CONFIRMED | Від'ємне значення відхиляється до SQL; integration regression test. |
| CR-2 | medium | `repositories/sources.py:497` | `circuit_open_for <= 0` переводив route у `circuit_open`, але з уже спливлим `circuit_open_until`; downstream preflight міг одразу дозволити повторний fetch, нівелюючи circuit breaker. | CONFIRMED | Не-`None` duration мусить бути додатним; тести для нуля і від'ємного значення. |
| CR-3 | low | `repositories/queue.py:114` | Експортований `next_attempt_at` перевіряв naive `now` лише коли задано `not_before`; звичайна backoff-гілка повертала naive datetime всупереч §9.6 UTC-only. | CONFIRMED | `now` завжди проходить `require_aware_utc`; unit regression test. |

## Re-review після виправлень

- CR-1: `list_stale_projection_tasks` узгоджено з `outbox.purge_published`: нульовий поріг
  дозволено, від'ємний — `ValueError` до звернення до БД.
- CR-2: `None` лишається підтримуваним безстроковим circuit; додатна тривалість зберігає
  попередню поведінку; помилкові значення не інкрементують counter і не пишуть audit.
- CR-3: aware offset нормалізується до UTC, naive відхиляється в обох гілках helper-а.

Цільовий прогін після виправлень:

```text
$ uv run pytest -q tests/unit/persistence/postgres/test_queue_timing.py \
    tests/integration/postgres/test_ack_fencing_reconcile.py \
    tests/integration/postgres/test_fetch_preflight.py
..........................                                               [100%]
26 passed in 24.06s
```

## Перевірено без знахідок

- Queue/projection defer не спалює attempt; retry/quarantine атомарні з dead letter.
- Ack fencing серіалізується row lock-ом; stale owner не може записати після recover/reclaim.
- `fetch_unpublished` рахує delivery issuance у lease-транзакції, не подвоює failure counter,
  паркує poison rows без head-of-line blocking; `[]` при непорожньому backlog задокументовано.
- Purge видаляє internal event лише після terminal task + acknowledgement; неопублікований
  domain і quarantined/unacknowledged internal зберігаються.
- Route failure increment не губиться при конкуренції; audit і state transition атомарні.
- Validators запит захищений від MD5 collision повторним порівнянням URL та має partial index.
- Нові grants мінімальні для заявлених ролей; негативні LOGIN-role тести присутні.
- Немає нових `type: ignore`, domain payload у PostgreSQL чи необмеженого retry loop.

## Вердикт

**APPROVED.** Усі три знахідки виправлено й покрито регресійними тестами. Блокуючих
коректнісних, race/idempotency або error-handling проблем у diff після re-review не лишилося.
