# WP-01A PR2 — повторне код-рев'ю після виправлень gate 3 (етап 3')

| Поле | Значення |
|---|---|
| Гілка / коміт | `wp/01a-2-artifacts-projection` @ `bad6a25`, diff `e395b24..HEAD` |
| Попередні звіти | `code-review-pr2.md`, `security-pr2.md` (обидва на `e395b24`) |
| Звіт реалізації | `implementation-pr2.md`, розділ «Fixes after gate 3» |
| Вердикт | **approve** — 0 critical, 0 high, 0 medium, 5 low |

Звіт збережено оркестратором із відповіді рев'юера (у рев'юера немає Write). CR-1, CR-2, CR-3, S-1, S-2, S-3, S-4 відтворено scratch-тестами на `wp01a-pg` (PG 18) поза репозиторієм; кожну runtime-операцію репозиторіїв виконано під `SET LOCAL ROLE <component>`. Тимчасовий template видалено, ролі `r2_*` — у відкоченій транзакції; `collector_*` лишились `NOLOGIN`.

## Статус знахідок попереднього рев'ю

| # | Статус | Доказ |
|---|---|---|
| CR-1 | закрито | A(fetch 10)→B(11)→A(12): версії 1 2 3, третій виклик `created=True` з перевикористанням artifact v1; повтор fetch 12 → той самий task; ті самі bytes під іншим `object_key` → v4 без `IntegrityError`; 6 конкурентних викликів → рівно 1,2,3; під `collector_parser` пройшло. Див. N-1. |
| CR-2 | закрито | publisher 2 у `T0+59s` → 0 рядків, у `T0+60s` → 1. |
| CR-3 | закрито | після 10 `mark_failed` — `parked_at`, `list_parked` = 1; `delay_for(1025/5000/10**6)` без `OverflowError`. Див. N-2. |
| CR-4 | закрито (docs + тест) | протокол sweeper-а acquire → delete → release коректний; ланцюжок під `collector_scheduler` пройшов. |
| CR-5 | закрито | `GREATEST(attempt - 1, 0)` у `queue.release` і `release_projection_task`. |
| CR-6 / S-2 | закрито | parser: `UPDATE normalized_artifacts SET object_key/uri` → denied, `parse_attempt_id` → дозволено. |
| CR-7 | закрито | коментар/docstring 0004 відповідають поведінці. |
| CR-8 | закрито | `require_audit_context` у `quarantine(owner=None)`. |
| CR-9 | закрито | `ON CONFLICT … DO UPDATE … WHERE IS DISTINCT FROM`; без зміни — без audit. |
| CR-10 | закрито | outcome валідується; `record_parse_failure` під parser-ом пройшла. |
| S-1 | закрито | parser: INSERT `entity_index` з версіями/`confirmed_at` → denied, identity-колонки → allowed; `upsert_entity` ідемпотентна. |
| S-3 | закрито для scheduler | `topic`, `parse_key`, `attempt`, `committed_at` → denied; усі операції scheduler-а пройшли. Projector — див. N-4. |
| S-4 | закрито частково | транзитивне членство (`r2_leaf → r2_mid → r2_top(BYPASSRLS)`) видно; allowlist є. Прогалини — N-5. |
| S-5 | закрито | `RoleLoginError` лише роль + SQLSTATE, `from None`. |
| S-6 | закрито | default `topics=(DOMAIN_TOPIC,)`, порожній набір → `ValueError`. Див. N-3. |

## Нові знахідки

| # | Severity | file:line | Суть і сценарій | Рекомендація | Verdict |
|---|---|---|---|---|---|
| N-1 | low | `repositories/projection.py:778-830` (`_artifact_for`); `repositories/artifacts.py:388` | Ті самі bytes під іншим `object_key` K2 → перевикористовується рядок K1; claim K2 `committed` без reference, orphan-запит відкидає `committed` → об'єкт K2 лишається в store назавжди; `result.artifact.object_key` ≠ переданому ключу. | Зафіксувати в контракті WP-02: ключ normalized artifact — функція `(sha256, entity_uuid)`; або включити committed claims без reference в orphan-кандидати. | CONFIRMED |
| N-2 | low | `repositories/outbox.py:50` | Visibility lease не рахує доставок: publisher, що падає на poison-події до `mark_failed`, крутить її нескінченно, межа CR-3 не спрацьовує. | Лічильник доставок у `fetch_unpublished` або паркування за ним; або перенести в publisher loop WP-01D. | PLAUSIBLE |
| N-3 | low | `repositories/outbox.py:247` (`oldest_unpublished_age`) | Не фільтрує `topic`/`parked_at` (на відміну від `count_backlog`); internal `projection.command` ніколи не публікуються стандартним шляхом → вік росте безмежно, алерт `outbox_backlog` (§14.2) горить постійно. | Параметр `topic` (типово `domain`), узгоджена з `count_backlog` семантика щодо parked. | PLAUSIBLE |
| N-4 | low | `sql/roles.sql:162` | Projector має табличний UPDATE на `projection_tasks`, включно з `parse_key`/`parse_attempt_id`/`projection_version`/`artifact_id`; переписаний `parse_key` ламає ідемпотентність. Відтворено. | Column-level UPDATE (`status`, `lease_*`, `leased_at`, `not_before`, `attempt`, `finished_at`, `last_error_*`, `updated_at`). | CONFIRMED |
| N-5 | low | `roles.py:185` (`PRIVILEGED_BUILTIN_ROLES`), `:268` (`verify_runtime_login`) | Не ловить членство в `pg_read_all_data`/`pg_maintain` і в ролях з `rolcreatedb`/`rolreplication`; `verify_runtime_login` не перевіряє власні `rolcreatedb`/`rolreplication`. Відтворено. | Додати ці ролі й атрибути в обидві перевірки. | CONFIRMED |

## Вердикт

**approve.** N-4, N-5 — закрити в цьому PR. N-1, N-3 — вимоги до контракту WP-02 і publisher/метрик WP-01D (або закрити тут). N-2 — можна перенести в WP-01D.

## Що перевірено окремо (без знахідок)

- `parse_key` обчислюється під row lock `entity_index`; unique + `_existing_parse_result` відкидають недетермінований повтор як `ConflictError`; нових гонок немає.
- `projection_tasks.parse_attempt_id`: FK `SET NULL`, заповнюється в тій самій транзакції.
- `parked_at`: partial index збігається з предикатом `fetch_unpublished`; `unpark` скидає `attempts` і пише audit у тій самій транзакції.
- Column grants vs фактичні запити: усі runtime-операції parser/projector/scheduler пройшли під своїми ролями; `FOR UPDATE` під column-level UPDATE працює.
- Регресій викликачів немає; `mypy src` — чисто; цільові integration/unit набори на `wp01a-pg` — 134 passed.
