# WP-00 PR5 — documentation gate

Дата: 2026-09-24.

## Оновлено

- `deploy/compose/README.md`: формати MinIO/Mongo credentials, service mapping, rotation,
  translation placeholder і `ensure-mongo` switch.
- `docs/runbooks/clean-host-start.md`: генерація secrets, startup, MinIO validation і recovery.
- `docs/plan/deps/WP-00-to-WP-01D.md`: dependency п.1–2 позначено resolved.
- `implementation-pr5.md`: актуальні рішення, evidence, ризики й rollback.

## Перевірка

- Команди в документації звірені з актуальним Compose та живим стеком.
- Імена buckets, secrets, env і services звірені unit-тестами.
- Translation чітко позначений як disabled без operator-supplied credential.

## Вердикт

**pass**. Новий ADR не потрібен: рішення не змінює архітектурний напрям ADR-0002.
