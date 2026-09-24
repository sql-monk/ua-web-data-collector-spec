# WP-01B PR1 — звіт реалізатора (`wp/01b-1-mongo-schema`)

Картка: `docs/plan/cards/WP-01B.md`, розділ «PR1», «Передумови», «Рішення оркестратора» (п.5, п.8),
«Спільні вимоги». База: `main` @ `626b7e4`. Статус приймання визначає gate.

## Що зроблено

| Вимога PR1 | Реалізація | Тести |
|---|---|---|
| 1. Міграційний раннер | `src/collector/persistence/mongo/migrations.py`: forward-only модулі `migrations/mongo/NNNN_<name>.py` з ідемпотентним `upgrade(db)`, службова collection `schema_migrations` (`_id`, `name`, `checksum`, `applied_at`); checksum = SHA-256 модуля + asset-каталогу `NNNN_<name>/` (CRLF → LF); змінений/зниклий застосований модуль → `MigrationDriftError` до будь-якого запису; downgrade немає | `tests/unit/persistence/mongo/test_migrations.py`; integration `test_modified_or_missing_applied_migration_is_drift`, `test_rerun_of_interrupted_migration_is_idempotent`, `test_ensure_twice_gives_identical_state` |
| 2. 11 collections §9.2 | `schema.DOMAIN_COLLECTIONS`; створює `0001_collections_validators_warn` (стандартні collections, без time-series/sharding) | `test_all_collections_are_standard_and_indexes_match_manifest` |
| 3. Validators, цикл warn → error | Генератор `validators.py` (snapshot → `$jsonSchema`: `$ref` інлайн, рекурсія → `{}`, `uuid`/`base64url` → `binData`, `date-time` → `date`, `integer` → `int/long`, невідоме ключове слово → `ValueError`). Validators **заморожені** asset-ами `migrations/mongo/0001_collections_validators_warn/{current_document,applied_projection_receipt}.json`; `0001` ставить `warn`, `0002_validators_error` перемикає в `error` усі або жодну collection і відмовляє (`InvalidDocumentsError`) при `count({$nor:[validator]}) > 0`. Лише 4 `*_current` і `applied_projection_receipts` (рішення п.8); решта 6 collections — без validator, перша міграція PR2 | unit `test_validators.py` (мапінг, `$ref`, recursion, помилки, «останній asset == snapshot»); integration `test_validators_only_for_collections_with_snapshots`, `test_valid_documents_pass_error_validators`, `test_invalid_document_warn_accepts_error_rejects_and_switch_refuses` |
| 4. Indexes §9.2 | `schema.INDEX_MANIFEST` — єдине джерело (явні імена); `ensure_indexes` створює відсутні одним `createIndexes` на collection, **звітує** зайві з `$indexStats.accesses.ops` (не видаляє), конфлікт імені/ключів → `IndexConflictError` (exit 1). Без wildcard. Додатково `ix_committed_cursor {committed_at, _id}` на receipts для keyset §15 | unit `test_schema_and_users.py::test_manifest_*`; integration `…match_manifest`, `test_duplicate_natural_key_and_task_id_raise_duplicate_key`, `test_extra_index_is_reported_not_dropped`, `test_conflicting_index_fails_ensure`, `test_indexes_before_validators_still_converge` |
| 5. CLI | `collector db ensure-mongo [--validators] [--indexes] [--users [--secrets-dir DIR]]` (тіло команди в `cli.py` + `persistence/mongo/admin.py`): після RS — міграції, indexes, користувачі; drift/невалідні документи/конфлікт/помилка драйвера → exit 1 без секретів і traceback; `--users` читає секрети **до** з'єднання | unit `test_cli_ensure_mongo.py`, змінений `tests/unit/test_cli_compose_commands.py::test_db_ensure_mongo_validators_indexes_run_after_init`; integration `test_cli_ensure_mongo.py` (двічі → той самий стан, drift → 1, `--users` двічі, відсутній секрет → нуль змін) |
| 6. Mongo-користувачі §13 | `users.py`: custom roles у `admin` — `collector_projector` (`find/insert/update` на 11 collections + `listCollections`), `collector_compactor` (+ `remove` лише на `entity_projection_versions`), `collector_api_ro`/`collector_export_ro` (`find`); `createRole/updateRole`, `createUser/updateUser` (ідемпотентно, ротація пароля). Паролі з `mongo_uri_<component>` (перевірка користувача й `authSource=admin`); для scheduler/fetcher/parser користувачів немає | unit `test_schema_and_users.py::test_role_privileges_*`, `test_credential_from_uri_*`, `test_load_user_credentials_all_or_nothing`; integration `test_users.py` (projector не може `delete`/`dropCollection`/`createIndex`/`collMod`/читати `schema_migrations`; compactor видаляє лише version records; api_ro/export_ro не пишуть; ротація пароля) |
| 7. Репозиторії | `client.py` (`MongoSettings.from_env`: `COLLECTOR_MONGO_URI[_FILE]`, `COLLECTOR_MONGO_DATABASE`; `create_client` — primary, `majority`/`majority`, `retryWrites`, `uuidRepresentation=standard`, `tz_aware`, явні таймаути; `transaction_options` — snapshot/majority/primary/`maxCommitTimeMS`); `repositories.py`: `get_current`, `get_exact_version` (hot), `get_receipt`, `list_receipts` (keyset `(committed_at, _id)`, без `skip`), `receipt_to_document`/`receipt_from_document` (`_id = projection_task_id`); сесія — від викликача | unit `test_client_and_repositories.py`; integration `test_repositories.py` (tz-aware/UUID, event bytes byte-equal, читання в транзакції, keyset на межі сторінки з однаковим `committed_at`, page size 1/2/3/7) |
| Вартовий проти skip | `tests/integration/mongo/conftest.py`: `_skip_or_fail` (fail при `COLLECTOR_TEST_REQUIRE_DOCKER=1`), session-hook (`skipped/xfail` або < `MIN_COLLECTED_TESTS=20` вибраних → exit 1); CI job `integration-mongo` з `-rs` | unit `test_ci_integration_guard.py`; ручна перевірка hook-а нижче (exit 1 при 2 вибраних тестах) |
| Linux-паритет | Mongo RS з member host `127.0.0.1:27017` усередині контейнера, клієнти — mapped port + `directConnection=true`; session-фікстура не відкриває з'єднань (готовність — лениво в першому тесті, бо до `allow_hosts` першого тесту на Linux діє `--disable-socket`) | прогін у Linux-контейнері нижче |

Fixtures: `tests/fixtures/mongo/mongo_factories.py` — синтетичні current documents і receipts
(`provenance: synthetic, WP-01B`), будуються через контракти WP-01C; без реальних даних і контактів.
Нормалізовані payloads/версії для PR2 не створювались (їх контракт — WP-01C PR2).

Прийняті рішення (для рев'ю):

1. **Корінь validator-а current collections відкритий** (`additionalProperties` прибрано лише на
   корені, вкладені блоки лишаються закритими): доменні поля WP-07/WP-09 (напр. `catalog_item_id`
   для index §9.2) інакше відхилялися б. Receipts — закриті повністю + `_id: binData`.
2. **Validators — заморожені asset-и**, а не генерація під час `ensure-mongo`: застосована міграція
   не змінюється разом зі snapshot-ом; unit-тест вимагає, щоб останній asset дорівнював snapshot-у,
   тобто зміна snapshot-а WP-01C = нова Mongo-міграція. Бонус: image не потребує `schemas/`.
3. **Indexes — декларативний маніфест** (card п.4 «маніфест — єдине джерело»), міграції — лише
   collections/validators. Видалення index-а — свідомо (звіт `extra`), не автоматично.
4. **Schema admin — sync PyMongo** (one-shot CLI), репозиторії — `AsyncMongoClient` (§8).
5. **PK receipt-а `_id = projection_task_id`** (UUID) + окремий unique `ux_projection_task`, як
   вимагає картка; `committed_at` зберігається з точністю BSON date (мс).

## Команди та вивід

Середовище: Windows 11, Docker 29.8.0, Python 3.13.9 (uv). Compose-стек не піднімався (рішення
оркестратора: хост спільний) — `ensure-mongo` перевірено проти тестового mongod (testcontainers /
`docker run`).

```text
$ uv sync --frozen
Checked 66 packages in 7ms
exit=0

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
292 files already formatted

$ uv run mypy src
Success: no issues found in 85 source files

$ uv run collector contracts export --check
schemas up to date: schemas
```

`uv run pytest -m integration tests/integration/mongo -rs` (Windows, testcontainers, той самий
digest `mongo:8.0@sha256:4968f22d…`):

```text
collected 27 items

tests\integration\mongo\test_cli_ensure_mongo.py ....                    [ 14%]
tests\integration\mongo\test_repositories.py .......                     [ 40%]
tests\integration\mongo\test_schema_migrations.py ...........            [ 81%]
tests\integration\mongo\test_users.py .....                              [100%]

======================= 27 passed in 422.29s (0:07:02) ========================
exit=0
```

Linux-паритет (контейнер `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`, `--network container:`
mongod із тими самими аргументами, що в CI job; `COLLECTOR_TEST_MONGO_URI=mongodb://127.0.0.1:27017/?directConnection=true`,
`COLLECTOR_TEST_REQUIRE_DOCKER=1`, повне `--disable-socket` pytest-socket):

```text
platform linux -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
plugins: socket-0.8.1, anyio-4.15.1, asyncio-1.4.0, respx-0.23.1
collected 27 items

tests/integration/mongo/test_cli_ensure_mongo.py ....                    [ 14%]
tests/integration/mongo/test_repositories.py .......                     [ 40%]
tests/integration/mongo/test_schema_migrations.py ...........            [ 81%]
tests/integration/mongo/test_users.py .....                              [100%]

============================= 27 passed in 19.70s ==============================
exit=0
```

Session-hook вартового (Windows, `COLLECTOR_TEST_REQUIRE_DOCKER=1`, свідомо вибрано 2 тести з 27):

```text
collected 27 items / 25 deselected / 2 selected
tests\integration\mongo\test_users.py ..                                 [100%]
MONGO GUARD: зібрано 2 integration-тестів Mongo, мінімум — 20 (тест зник або не зібрався)
====================== 2 passed, 25 deselected in 38.04s ======================
exit=1
```

`uv run pytest -m "not live" -rs` (Windows; включно з integration postgres/scaling/mongo через
testcontainers):

```text
NOTLIVE_PLACEHOLDER
```

`uv run pre-commit run --all-files`:

```text
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
exit=0
```

`uv run pre-commit run --hook-stage manual gitleaks-history` — 2 знахідки, **обидві не з цього
branch** (спільний git-репозиторій, скан бачить коміти інших worktree):
`9d9f1de…:tests/fixtures/contracts/documents/news_translation.v1.0.json:generic-api-key:28`
(WP-01C PR2) і `19dfb23…:tests/fixtures/translation/tm_key_golden.json:generic-api-key:12`
(branch WP-04). Коміти WP-01B знахідок не мають. Передано оркестратору як інформацію.

## Що не перевірено

- **CI job `integration-mongo` на GitHub** — не запускався (push заборонено). Замінник — прогін
  у Linux-контейнері з тими самими аргументами mongod і env, що в job (вище). Крок `docker run`
  з `MONGO_INITDB_ROOT_PASSWORD_FILE` і `mongosh`-очікуванням виконано лише логічно-еквівалентно
  (локально пароль передавався через env контейнера).
- **Compose one-shot `ensure-mongo --validators --indexes --users`** — not testable offline у
  цьому PR: `docker-compose.yml`/секрети `mongo_uri_*` — WP-00 PR5 (dependency-запит
  `docs/plan/deps/WP-01B-to-WP-00.md`); compose-стек не піднімався за рішенням оркестратора.
  CLI-контракт покрито integration-тестом через `CliRunner` проти справжнього RS.
- Мутаційні перевірки вартового (прибрати env з job, зупинити Mongo в job, `pytest.mark.skip` на
  тест) — робота тестувальника gate 2; hook вручну доведено на недоборі тестів (вище).
- `uv run pytest -m integration tests/integration/postgres tests/integration/scaling` окремо не
  запускався — входить у `pytest -m "not live"` вище.

## Ризики

| Ризик | Оцінка / мітигація |
|---|---|
| Відкритий корінь validator-а current collections пропускає довільні доменні поля | Свідоме рішення до контрактів WP-07/WP-09; закривається їхньою міграцією (новий asset з доменною схемою) |
| Назви `catalog_item_id`/`seller_id`/`parent_item_id`/`content_version`/`published_at` — дослівно з §9.2; снапшоти WP-01C PR2 (`git show wp/01c-2-…`) їх уже використовують, окрім `catalog_item_id` (ще не в жодному контракті) | Відкрите питання картки (WP-07/WP-09); зміна = новий маніфест + звіт `extra` для старого index-а |
| `committed_at` у BSON має точність мс — receipt, створений з мікросекундами, після читання не byte-equal | Задокументовано в `repositories.py`; вимога до PR2: фіксувати `committed_at` з точністю мс |
| Keyset `list_receipts` за `committed_at` може пропустити receipt, закомічений пізніше з меншим `committed_at` (довга транзакція) | Для PR3 reconciler-а: курсор з запасом/перекриттям вікна або курсор за PG-tasks; у PR1 — лише читання |
| Продуктивний код ≈ 866 рядків без docstrings/коментарів у `persistence/mongo` + `migrations` і ≈ 100 у `cli.py` — трохи понад орієнтир ~800 | Без абстракцій «на майбутнє»; основний обсяг — раннер і генератор validators |
| Локальний прогін integration Mongo на Windows повільний (~7 хв: ~40–100 мс на round-trip через Docker Desktop, DDL на RS) | На Linux 19.7 с; CI job timeout 20 хв |
| Ролі в `admin` прив'язані до однієї domain-БД (`COLLECTOR_MONGO_DATABASE`) | URI компонентів мають вказувати ту саму БД — зафіксовано в deps-запиті до WP-00 |
| Гонка двох паралельних `ensure-mongo --validators` | one-shot у compose; повторний запуск ідемпотентний, дубль запису `schema_migrations` дасть DuplicateKey → exit 1, повтор — no-op |

## Як вимкнути або відкотити

- Revert комітів PR1: CLI повертається до `not implemented: owned by WP-01B` для
  `--validators/--indexes`; уже застосований стан Mongo лишається (collections не видаляються).
- Validators на живій БД: відкат лише **новою forward-міграцією** з `validationAction: warn`
  (`ensure_collection(..., action="warn")`); downgrade не підтримується. Dev — `docker compose
  down -v` (volume `mongo-data`).
- Зайві indexes не видаляються автоматично; `--indexes` лише звітує.
- Користувачі: `dropUser`/`dropRole` для `collector_{projector,compactor,api_ro,export_ro}` під
  root; повторний `--users` відновлює.

## Dependency-запити

- `docs/plan/deps/WP-01B-to-WP-00.md`: (1) зміна owned-тесту WP-00
  `test_cli_compose_commands.py` (стаб → реальна поведінка, зроблено в branch за прецедентом
  WP-01A); (2) контракт CLI для compose one-shot `ensure-mongo --validators --indexes --users`
  у WP-00 PR5 (секрети `mongo_uri_*`, `COLLECTOR_MONGO_DATABASE`, `COLLECTOR_MONGO_USER_SECRETS_DIR`).
- До WP-01C/WP-01D/WP-01A — нових запитів немає (передумови PR2/PR3 уже в їхніх картках).

## Коміти

```text
17caca3 feat(wp-01b): forward-only Mongo migrations, frozen $jsonSchema validators, index manifest §9.2
8b972b7 feat(wp-01b): async repositories, component Mongo users, ensure-mongo --validators --indexes --users
877617b test(wp-01b): Mongo schema/users/repositories integration and unit tests, skip guard
046c4d4 ci(wp-01b): integration-mongo job with pinned replica set
b317419 fix(wp-01b): cp1251-safe ensure-mongo help; dependency request to WP-00
```
