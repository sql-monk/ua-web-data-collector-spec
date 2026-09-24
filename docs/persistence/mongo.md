# MongoDB domain store

MongoDB зберігає serving projection каталогів і автомобілів. PostgreSQL лишається canonical
control plane для задач, lineage, acknowledgements та outbox, а S3/MinIO — для immutable raw і
normalized artifacts. MongoDB не використовується для scheduler state і не є єдиним джерелом,
з якого можна відновити domain state.

## Топологія та підключення

Локальний MVP використовує MongoDB 8.0 як single-member replica set `rs0`, бо транзакції
потребують replica set. Це дає transaction semantics, але не високу доступність: перед HA
production потрібні щонайменше три data-bearing members у різних failure domains, backup і
перевірений restore.

Runtime-клієнт створюється через `collector.persistence.mongo.client.create_client`:

- primary reads, `readConcern=majority`, `writeConcern=majority`;
- `retryReads=true`, `retryWrites=true`;
- projection-транзакції — `snapshot` + `majority`, bounded commit timeout;
- UUID — BSON Binary subtype 4 (`uuidRepresentation=standard`), datetime — UTC BSON date;
- URI читається з `COLLECTOR_MONGO_URI[_FILE]`, ім'я БД — з URI або
  `COLLECTOR_MONGO_DATABASE`.

Один `AsyncMongoClient` створюється на процес. URI й паролі не логуються.

## Collections та validators

Маніфест `collector.persistence.mongo.schema.DOMAIN_COLLECTIONS` містить 11 стандартних
collections §9.2. Time-series і sharding у v1 не використовуються.

PR1 встановлює `$jsonSchema` validators для чотирьох `*_current` collections і
`applied_projection_receipts`, бо лише для них уже були contract snapshots. Validators решти
collections додає PR2 разом з їхніми контрактами. Для current documents корінь відкритий для
доменних полів наступних WP, але базові поля, включно з явним `schema_version`, обов'язкові.

JSON Schema перетворюється на BSON schema явно: UUID/base64url → `binData`, date-time → `date`,
integer → `int|long`, локальні `$ref` інлайняться. Невідомі формати або ключові слова дають
помилку, а не мовчки втрачають обмеження.

Validator assets заморожені в `migrations/mongo/<version_name>/`. Зміна contract snapshot не
переписує застосовану міграцію: потрібна нова forward-міграція. Цикл застосування:

1. створити collection і validator з `validationAction=warn`;
2. перевірити всі документи;
3. окремою міграцією перевести всі цільові collections у `error` або не змінити жодної.

## Forward-only міграції

Команда:

```text
uv run collector db ensure-mongo --validators --indexes --users
```

Модулі `migrations/mongo/NNNN_name.py` мають ідемпотентну `upgrade(db)`. Collection
`schema_migrations` зберігає версію, ім'я, SHA-256 модуля разом з assets і час застосування.
Змінена або зникла вже застосована міграція зупиняє запуск до будь-яких нових записів.
Downgrade не підтримується; відкат schema/validator робиться новою forward-міграцією.

Як додати міграцію:

1. взяти наступний чотиризначний номер і створити `NNNN_name.py`;
2. зробити `upgrade(db)` безпечною для повтору після часткового збою;
3. додати frozen assets у сусідній каталог `NNNN_name/`, якщо вони потрібні;
4. додати unit та replica-set integration tests для першого і повторного запуску, drift і
   часткового збою;
5. не редагувати застосовані модулі чи assets.

## Індекси та pagination

`INDEX_MANIFEST` — єдине джерело керованих індексів. `ensure-mongo --indexes` створює
відсутні, приймає лише повний семантичний збіг (keys, unique і відсутність неочікуваних
sparse/partial/collation/TTL/hidden options), конфлікт завершує помилкою. Зайві індекси не
видаляються автоматично: команда показує їх разом з `$indexStats.accesses.ops` для окремого
операторського рішення.

Списки receipts використовують keyset cursor `(committed_at, _id)` та compound index
`ix_committed_cursor`; `skip/OFFSET` не застосовується.

## Репозиторії та межі транзакцій

PR1 надає `get_current`, hot-path `get_exact_version`, `get_receipt` і `list_receipts`.
Функції не відкривають і не комітять транзакцію: session передає викликач. PR2 додає одну
Mongo-транзакцію на одну projection task; archive fallback для exact version належить PR4.

Receipt має `_id = projection_task_id`. `event_bytes` лишаються BSON Binary без
пересеріалізації. BSON date має мілісекундну точність, тому projector фіксує `committed_at` до
мілісекунд і при replay читає збережений receipt.

## Ролі та секрети

`ensure-mongo --users` читає всі URI-секрети до підключення й застосовує ролі атомарно щодо
вхідної конфігурації:

| Користувач | Доступ |
|---|---|
| `collector_projector` | find/insert/update у domain collections, без delete/DDL |
| `collector_compactor` | як projector; remove лише в `entity_projection_versions` |
| `collector_api_ro` | find-only |
| `collector_export_ro` | find-only |

Scheduler, fetcher і parser не мають Mongo credentials. Root credential монтується лише в
one-shot `ensure-mongo`. Паролі беруться з `mongo_uri_<component>` з `authSource=admin`;
повторний запуск оновлює роль і пароль, не розширюючи права.

## Експлуатація та відкат

Clean-host startup типово запускає повну schema/users фазу
(`COLLECTOR_ENSURE_MONGO_SCHEMA=1`). Значення `0` — лише явний recovery override, який
ініціалізує replica set без validators/indexes/users.

Якщо schema step падає, не видаляйте volume: виправте причину й повторіть one-shot. Для
validator regression додайте forward-міграцію з `validationAction=warn`; для конфлікту index
спочатку перевірте ключі, unique та semantic options і прийміть окреме рішення про rebuild.
Production rollback не робиться через видалення collections або `schema_migrations`.

Операційні метрики наступних PR: schema drift/ensure failure, projection retry, receipt без
acknowledgement та `cross_store_drift_total`. Backup/restore і reconciler описуються в PR3.
