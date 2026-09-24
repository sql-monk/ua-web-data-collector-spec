# WP-01B PR1 — код-рев'ю

| Поле | Значення |
|---|---|
| Diff | `main...wp/01b-1-mongo-schema` |
| Фокус | коректність, idempotency, drift, transaction boundaries, pagination, errors |
| Вердикт | **APPROVED after fixes** |

## Знахідки та виправлення

| ID | Severity | Claim / failure scenario | Рішення |
|---|---|---|---|
| CR-1 | high | Current document без `schema_version` проходив validator всупереч §9.2; serving store міг містити документ без версії схеми. | Поле примусово required у recipe й frozen asset; integration regression test. |
| CR-2 | high | Index з тими самими keys/name/unique, але sparse/partial/collation приймався як еквівалентний; uniqueness domain могла бути слабшою за manifest. | Semantic options перевіряються; конфлікт fail-closed; adversarial + mutation test. |

## Перевірено без нових знахідок

- Drift перевіряється до нового запису; interrupted migration повторюється ідемпотентно.
- `warn → error` спершу перевіряє всі цільові collections і лише потім виконує `collMod`.
- Зайві indexes не видаляються, конфлікти не перебудовуються автоматично.
- Receipt mapping не пересеріалізує event bytes; keyset має унікальний `_id` tie-breaker.
- Transaction/session ownership лишається у викликача; PR1 не створює прихованих commit-ів.
- CLI читає весь users input до з'єднання; очікувані schema/index/user errors дають exit 1.
- Немає unbounded retry, `skip/OFFSET`, ODM-магії чи Mongo payload у PostgreSQL.

Блокуючих коректнісних або race/idempotency проблем після виправлень не знайдено.
