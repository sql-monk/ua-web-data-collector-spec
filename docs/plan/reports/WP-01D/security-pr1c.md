# WP-01D PR1c — security review

Обсяг: lazy imports, handler input/output, PostgreSQL queue adapters, scheduler advisory lease,
logs/errors. PR не змінює API/GUI/Docker/secrets/network fetch policy.

## Перевірено

- Доменний module path походить зі статичної allowlist, не з job/env/user input.
- Відсутність самого майбутнього модуля відрізняється від відсутньої вкладеної залежності;
  broken import валить boot, а не запускає привілейований silent Noop.
- `HandlerContext` повторно використовує session factory runtime LOGIN-ролі; нових engine/DSN,
  superuser fallback чи секретів немає.
- У логи не потрапляють `Task.args`; error text редагує credentials у URL та типові secret
  query params. Clamp warning містить лише job id і timestamps.
- Projection output типізується/валідується; ack і event bytes не серіалізуються повторно в
  runtime; stale owner блокується repository fencing до запису ack.
- Quarantine projection task спочатку перевіряє owner heartbeat під row lock у тій самій
  транзакції, тому операторський API не може змінити task іншого worker-а через цей adapter.
- Scheduler lease checks і release не виконуються паралельно на retained connection після
  cancellation (CR-1 fixed), що усуває fail-open/невизначений стан singleton-а.

## Знахідки

Critical/high/medium/low відкритих security-знахідок немає.

Залишковий операційний ризик: redaction — страховка, не універсальний DLP; доменні handler-и
не повинні вкладати секрети у довільний текст винятків. Це явно задокументовано в
`handlers.py`/`docs/workers.md`.

## Вердикт

**approve**.
