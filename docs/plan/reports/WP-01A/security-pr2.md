# WP-01A PR2 — security-рев'ю (§13)

Branch `wp/01a-2-artifacts-projection` @ `e395b24`, diff `main...HEAD`. Звіт збережено оркестратором із відповіді рев'юера (у рев'юера немає Write).

**Вердикт: approve** — critical/high немає; S-1, S-2 (medium) закрити до merge.

## Знахідки

| # | Severity | file:line | Суть | Рекомендація |
|---|---|---|---|---|
| S-1 | medium | `sql/roles.sql:133` | Табличний INSERT на `entity_index` для `collector_parser`: можна вставити рядок одразу з `confirmed_projection_version = projection_version = N`, `confirmed_at`, довільним `mongo_*` — «підтверджена» версія без ack. Тригер 0005 лише на UPDATE. Відтворено: `SET ROLE collector_parser; INSERT … (7, 7)` → `INSERT 0 1`. | Column-level `GRANT INSERT (entity_uuid, domain, entity_kind, source_id, source_item_id, identity_hash, canonical_url, created_at, updated_at)`; у `upsert_entity` (`repositories/entities.py:78-80`) не передавати версії/`mongo_collection` явно; або BEFORE INSERT тригер (`confirmed_projection_version = 0 AND confirmed_at IS NULL`). Негативний тест. |
| S-2 | medium | `sql/roles.sql:126` | Табличний UPDATE на `normalized_artifacts` для parser: можна переписати `object_key`/`sha256`/`raw_sha256`/`size_bytes` закомічених artifacts. | `REVOKE UPDATE …; GRANT UPDATE (parse_attempt_id, updated_at)`. |
| S-3 | low | `sql/roles.sql:108` | `collector_scheduler` має повний UPDATE на `outbox_events` (зокрема `topic`, `payload_bytes`) — можна переписати payload або перевести internal у domain; аналогічно `projection_tasks`/`artifact_upload_claims`. | Column-level UPDATE за фактичними колонками. |
| S-4 | low | `roles.py:199-205`, `:226-236` | `apply_logins`/`verify_runtime_login` перевіряють лише `rolsuper` і членство в `collector_migrate`; не перевіряють членство в ролях з `rolsuper`/`rolcreaterole`/`rolbypassrls`, privileged `pg_*`, allowlist `RUNTIME_ROLES`. | Додати перевірки в обидві функції. |
| S-5 | low | `roles.py:211-214`, `cli.py:499` | При падінні `ALTER ROLE` `str(DBAPIError)` містить SQL із SCRAM verifier → stderr/CI-логи. | Ловити `DBAPIError` → `RoleLoginError(f"{role}: ALTER ROLE failed: {sqlstate}")`. |
| S-6 | low | `repositories/outbox.py:41` | `fetch_unpublished(topics=None)` fail-open — віддає всі топіки; parser може вставити `topic='internal'` з `event_type='domain.changed'`. | Default `topics=(DOMAIN_TOPIC,)` або обов'язковий аргумент; маршрутизація за `topic`. |
| I-1 | info | `deps/WP-01A-to-WP-01D.md`, `WP-01A-to-WP-00.md` §4 | Runtime досі на superuser DSN міграцій; `verify_runtime_login` ніде не викликається. | Не блокує PR2; **блокує будь-яке non-dev розгортання** до виконання dependency-запитів. Записати в ledger. |
| I-2 | info | cluster defaults | Runtime-ролі мають `CONNECT`/`TEMP` на всі БД (PUBLIC); ролі кластерні; `FORCE ROW LEVEL SECURITY` не ввімкнено. | `REVOKE CONNECT, TEMP ON DATABASE … FROM PUBLIC` (WP-00 init). |

## Що перевірено

- `roles.sql`: GRANT звірено з фактичними INSERT/UPDATE репозиторіїв; `alembic_version` лише `collector_migrate`; `pg_default_acl` порожній; SECURITY DEFINER функцій немає; `CREATE` на `public` у runtime немає.
- RLS `outbox_events`: parser `topic='domain'` → відмова; `topic='internal'` проходить (див. S-6).
- Тригер 0005: NOT NULL колонки, спрацьовує для всіх ролей на UPDATE.
- `--with-login`: секрети з файлів до з'єднання, не в argv/env; `__repr__` маскує; SCRAM-SHA-256 (16-байтна salt, 4096 ітерацій); SQL injection неможлива (фіксований `RUNTIME_ROLES`); атомарність; вимагаються всі 7 секретів.
- `gitleaks git --log-opts=main..HEAD`: 8 commits, no leaks found.
- SSRF/body limits/XML/GUI — поза diff; `trivy`/`pip-audit` не встановлені (нових залежностей немає).
- Слід: тимчасова БД `sec_review_pr2` створена й видалена; кластерні ролі не змінювались.
