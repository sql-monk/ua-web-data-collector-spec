# WP-01A PR2 — код-рев'ю (етап 3)

| Поле | Значення |
|---|---|
| Гілка / коміт | `wp/01a-2-artifacts-projection` @ `e395b24`, diff `main...HEAD` |
| Картка | `docs/plan/cards/WP-01A.md` («Спільні вимоги» + PR2) |
| Звіт тестування | `docs/plan/reports/WP-01A/testing-pr2.md` (F-1..F-3 виправлено в `e395b24`) |
| Вердикт | **changes_requested** (1 high, 3 medium, 6 low) |

Звіт збережено оркестратором із відповіді рев'юера (у рев'юера немає Write). CR-1, CR-2, CR-3, CR-5 відтворено scratch-тестами на `wp01a-pg` (PG 18) поза репозиторієм; решта — читанням коду.

## Знахідки

| # | Severity | Де | Суть | Verdict |
|---|---|---|---|---|
| CR-1 | high (spec-mismatch) | `repositories/projection.py:156-165`; міграція 0004 `:414-418`, `:491-495` | Ідемпотентність `record_parse_result` за вмістом (`object_key` / `(sha256, entity_uuid)`): стан A→B→A з byte-identical artifact повертає task v1 (`created=False`), нова `projection_version` не видається, Mongo лишається на B мовчки. Інший `object_key` з тими самими bytes → сирий `IntegrityError` на `uq_normalized_artifacts_sha256_entity_uuid`; `uq_projection_tasks_artifact_id_target_collection` не дає перевикористати artifact. Відтворено: `A1 1 B 2 A2 1 False`, потім `INTEGRITY IntegrityError`. | CONFIRMED |
| CR-2 | medium | `repositories/outbox.py:37-69` (docstring `:14-15`, `:51-53`); тест `test_outbox_entities.py:78-95` | `fetch_unpublished` без lease/visibility timeout: після commit паралельний publisher отримує ті самі рядки → N-кратна публікація. Тест покриває лише одночасні транзакції. | CONFIRMED |
| CR-3 | medium | `repositories/outbox.py:96-126`; `repositories/queue.py:83-88` | `mark_failed` без межі спроб і parked-стану; `BackoffPolicy.delay_for(1026)` → `OverflowError` (cap після піднесення до степеня) → hot loop. | CONFIRMED |
| CR-4 | medium | `repositories/artifacts.py:399-425` | `list_orphan_candidates` без generation/fencing-протоколу: sweeper може видалити ключ, який щойно reacquire-нув новий producer. | PLAUSIBLE |
| CR-5 | low | `repositories/queue.py:339-404`; `repositories/projection.py:341-363` | `release` не відкочує інкремент `attempt` від claim — плановий drain «спалює» спробу (`max_attempts=2` → quarantined після однієї помилки). Запит WP-01D буквально виконано; пропозиція `GREATEST(attempt - 1, 0)`. | CONFIRMED |
| CR-6 | low | `sql/roles.sql:126` | parser має табличний UPDATE на `normalized_artifacts`, потрібен лише `parse_attempt_id`. | CONFIRMED |
| CR-7 | low | міграція 0004 `:676-681` | downgrade зберігає лише `fetches_default`; місячні партиції втрачаються; повторний upgrade з рядками в DEFAULT ламає `ensure_month_partitions`. Коментар «дані не губляться» вводить в оману. Лише dev. | CONFIRMED |
| CR-8 | low | `repositories/queue.py:307` | `quarantine(owner=None)` перевіряє `not actor`, а не `require_audit_context` — `actor="  "` проходить. | CONFIRMED |
| CR-9 | low | `repositories/sources.py:359-439` | `upsert_cursor` пише audit навіть без зміни значення — шум в `audit_log`. | CONFIRMED |
| CR-10 | low (spec-mismatch) | `repositories/projection.py:74-118` | `record_parse_result` не валідує `attempt.outcome`; немає шляху записати failed/skipped attempt без artifact. | CONFIRMED |

## Вердикт

**changes_requested** через CR-1. CR-2, CR-3 — виправити в цьому PR; CR-4 — щонайменше задокументувати обов'язковий fencing-протокол sweeper-а.

## Що перевірено окремо (без знахідок)

- Видача `projection_version`: `SELECT … FOR UPDATE` на `entity_index`, rollback без дірок, deadlock parser↔ack неможливий.
- Ack: lock task до INSERT, `GREATEST` в одному UPDATE, `domain.changed` лише за умовою, bytes без reserialization; F-3 коректний.
- Upload claim fencing: один UPDATE з предикатом owner+generation+status+lease (строга межа); reacquire під row lock.
- Міграція 0005 (тригер монотонності) і `roles.sql` (F-1 REVOKE→column GRANT, F-2 RLS) — ідемпотентні, достатні для repo-запитів.
- `db roles --with-login`: секрети до з'єднання, all-or-nothing, SCRAM verifier, `NOSUPERUSER … NOBYPASSRLS`, відмова для членів `collector_migrate`, маскування пароля.
- Audit у тій самій транзакції; сумісність із викликами WP-01D.
- Типізація без `type: ignore`; timestamptz/UTC; без domain JSONB.
