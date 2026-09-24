# WP-00 PR5 — spec review

Дата: 2026-09-24. Джерела: картка WP-00 PR5, §13 ТЗ, dependency-контракти WP-01B/WP-01D.

## Матриця приймання

| Вимога | Статус |
|---|---|
| 5 buckets і 6 MinIO roles з точною матрицею прав | met |
| 4 Mongo per-component URI | met |
| Translation credential placeholder, default disabled | met |
| Exact mounts і startup ordering | met |
| Ідемпотентні `ensure-minio`/`ensure-mongo` | met |
| Secret values відсутні в env/inspect/logs | met |
| Clean-host compose config і runtime | met |
| `api_ro` insert denial | deferred за дозволом картки: потребує WP-01B `--users` |
| Linux CI | met — PR #14, 6/6 green, включно з Docker clean-host job |

Forbidden areas `src/**`, migrations, root Dockerfile і CI workflow не змінювались.

## Вердикт

**accept**. Невиконаних блокуючих вимог немає.
