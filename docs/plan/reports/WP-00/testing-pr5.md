# WP-00 PR5 — незалежне тестування

Дата: 2026-09-24. Обсяг: `main...wp/00-5-object-store-secrets`.

## Результат

**pass**. Контракт PR5 підтверджено на unit, compose і живому Docker-рівнях.

## Виконані перевірки

- `tests/unit/test_secrets_object_store.py`: 62 passed; додані adversarial-перевірки
  source pin/metadata для `mc` та валідності `ensure-minio.sh` у `/bin/sh`.
- Пов'язані compose/secrets/worker тести: 178 passed.
- `docker compose config --quiet`: pass.
- Чисте створення secret-файлів і повторний запуск: значення не перезаписані.
- `docker compose --profile core --profile workers up -d --wait`: усі довгоживучі сервіси
  healthy, `migrate-postgres`, `ensure-mongo`, `ensure-minio` — exit 0.
- Повторний `up -d --wait`: pass, one-shots знову exit 0.
- Реальна матриця MinIO: fetcher не може Delete `raw` або Put `normalized`; maintenance може
  Delete `raw`.
- Високоентропійні значення secret-файлів не знайдені у container env/command і compose logs.
- Повний `COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m "not live" -q -x`:
  **3674 passed, 23 skipped, 8 warnings** за 18:05. Skip-и: GUI-профіль не піднятий у цьому
  локальному прогоні, два runtime-enforcement guards і один Windows network-block test; PR5
  вони не стосуються.

## Покриття acceptance

| Пункт | Результат |
|---|---|
| Buckets/users/policies створюються ідемпотентно | pass |
| Per-component MinIO/Mongo credentials і точні mounts | pass |
| Root credentials ізольовані | pass |
| Provider credential порожній, translation disabled | pass |
| Compose clean-host стартує | pass |
| `mongo_uri_api_ro` insert denied | deferred до WP-01B PR1 (`--users` ще відсутній) |

Перший повний локальний прогін показав один невідтворений збій близько 15% і був перерваний
до summary. Повторний прогін з `-x` пройшов повністю; остаточним Linux-підтвердженням є PR CI.

## PR CI

PR #14: **6/6 green** — python, web, PostgreSQL integration, Docker clean-host
build/SBOM/trivy/up/e2e, pre-commit і gitleaks. Docker job завершився за 5:11.
