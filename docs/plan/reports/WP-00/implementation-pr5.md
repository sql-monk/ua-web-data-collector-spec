# WP-00 PR5 — implementation (`wp/00-5-object-store-secrets`)

Дата: 2026-09-24. Картка: `docs/plan/cards/WP-00.md`, PR5.

## Результат

- `ensure-minio` ідемпотентно створює buckets `raw`, `normalized`, `archive`, `translated`,
  `events`, шість користувачів та їхні least-privilege policies.
- MinIO server і `mc` збираються з зафіксованих upstream commits в одному
  `collector-minio` image. One-shot працює від uid 10001, з read-only rootfs, `cap_drop: ALL`,
  `no-new-privileges` і tmpfs для конфігурації `mc`.
- `init-secrets.sh` генерує шість MinIO credentials і чотири окремі Mongo URI, не
  перезаписує наявні значення та серіалізує паралельні запуски.
- `google_translation_credentials` створюється порожнім і монтується тільки в
  `translation-worker`; провайдер типово `disabled`. Реалізація перекладу не входить у PR5.
- Runtime-сервіси монтують лише credentials своїх компонентів і чекають на успішний
  `ensure-minio`. Root credentials доступні лише серверу та відповідному one-shot.
- `ensure-mongo` має fail-closed перемикач для майбутньої команди
  `--validators --indexes --users`; default лишається `0`, доки WP-01B не додасть `--users`.
- Оновлено `deploy/compose/README.md`, clean-host runbook і dependency-контракт з WP-01D.

## Ключові рішення

- MinIO credential — два рядки: `access_key=collector-<component>` і
  `secret_key=<40 hex>`.
- Секрет не передається в argv: root доступ задається всередині one-shot через
  `MC_HOST_collector`, а ключ користувача передається `mc admin user add` через stdin.
- `ensure-minio.sh` виконується системним `/bin/sh` Alpine image; синтаксис перевіряється
  окремим тестом і живим запуском контейнера.
- Кожний MinIO-споживач має `depends_on: ensure-minio: service_completed_successfully`.

## Перевірка acceptance

| Вимога | Доказ |
|---|---|
| Точна матриця MinIO permissions | unit-тести policy JSON; живі `mc` операції: fetcher Delete `raw` denied, fetcher Put `normalized` denied, maintenance Delete `raw` allowed |
| Ідемпотентні one-shots | два послідовні `docker compose ... up -d --wait`; `ensure-minio`, `ensure-mongo`, `migrate-postgres` завершилися 0 обидва рази |
| Розділення credentials | unit/adversarial compose-тести та перевірка mounts живого стеку |
| Немає витоку значень | високоентропійні частини згенерованих секретів не знайдені у container env/command або `docker compose logs` |
| Clean-host compose | `docker compose config --quiet`; повний core+workers стек healthy |
| Provider translation вимкнений | порожній credential, `COLLECTOR_TRANSLATION_PROVIDER=disabled` за замовчуванням |

`mongo_uri_api_ro` insert-denial свідомо не перевіряється в цьому PR: користувачів створює
`--users` з WP-01B PR1. Вартовий змусить увімкнути схему після появи цієї CLI-опції.

## Тести

- `uv run pytest tests/unit/test_secrets_object_store.py -q` — 62 passed.
- Пов'язані тести compose/secrets/workers — 178 passed.
- `docker compose config --quiet` — pass.
- Clean-host core+workers, повторний `up`, permission matrix і leak-check — pass.
- Повний `pytest -m "not live"`: 3674 passed, 23 skipped; CI фіксується у `testing-pr5.md`.

## Ризики й межі

- S3 `HeadObject` авторизується як `GetObject`; maintenance тому технічно може читати об'єкт.
- Fetcher може повторно записати content-addressed key; immutability/object lock належать
  WP-12/WP-13.
- Шифрування raw bucket і централізований audit видалень також поза PR5.
- Локальні secret-файли мають прийняті в ADR-0002 dev-права; production secret store —
  окремий deployment concern.

## Відкат

Revert PR не змінює схему БД. Дані buckets не видаляються. Користувачі й policies, вже
створені в MinIO, можна прибрати вручну через `mc admin`; Mongo schema/users лишаються
вимкненими default-перемикачем. Переклад лишається вимкненим.

Dependency `docs/plan/deps/WP-00-to-WP-01D.md` п.1–2 закрито 2026-09-24.
