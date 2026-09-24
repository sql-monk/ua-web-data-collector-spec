# WP-01B PR1 — документація

Вердикт: **PASS**.

- Додано `docs/persistence/mongo.md`: topology, concerns, collections, validators, migrations,
  indexes, repositories, roles, secrets, startup і forward rollback.
- Додано ADR-0010 про Mongo serving projection та single-member local topology/Q-010.
- Clean-host і Compose docs оновлено: повна Mongo schema/users фаза типово ввімкнена;
  `COLLECTOR_ENSURE_MONGO_SCHEMA=0` — лише recovery override.
- Dependency `WP-01B-to-WP-00` позначена resolved після WP-00 PR5.
- Implementation report доповнений актуальними gate evidence; traceability — PR1 доказами.

Перевірка: `pre-commit run --all-files` (включно з markdownlint) має бути зеленою перед push;
GitHub link check — CI gate перед merge.
