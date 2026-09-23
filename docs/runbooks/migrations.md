# Runbook: PostgreSQL міграції (WP-01A)

Операційні дії з Alembic-міграціями PostgreSQL-схеми (§9.1, §14.2 ТЗ). Стосується таблиць,
описаних у [`docs/persistence/postgres.md`](../persistence/postgres.md) — PR1 (control plane,
job queue, limiter, worker pools, audit log) і PR2 (fetch/raw lineage, upload claims,
projection tasks/acks, outbox, entity index); команди й контракт нижче не зміняться з появою
таблиць PR3, лише зросте кількість ревізій.

Схема forward-only (картка WP-01A, «Rollback/disable»): у production відкат — це нова
forward-fix міграція, не `downgrade`. `downgrade` реалізований лише там, де це безпечно
(наразі — усі три ревізії PR1, бо кожна ще не мала конкуруючих даних); для нових ревізій,
де `downgrade` небезпечний, він кидає `NotImplementedError` з поясненням замість тихого
псування даних.

## 1. Застосування міграцій у dev

Передумова — запущений PostgreSQL 18 (в WP-00 Docker Compose — сервіс `postgres`, профіль
`core`; поза Compose — будь-який PostgreSQL 18 з DSN у `COLLECTOR_POSTGRES_DSN`).

```bash
export COLLECTOR_POSTGRES_DSN=postgresql://collector:<password>@127.0.0.1:5432/collector
# або: export COLLECTOR_POSTGRES_DSN_FILE=/run/secrets/postgres_dsn (пріоритетний, Docker secret)

uv run collector db migrate      # alembic upgrade head + місячні партиції (audit_log, fetches) на 3 місяці наперед
uv run collector db roles        # ідемпотентно застосовує sql/roles.sql (ПІСЛЯ migrate)
uv run collector db roles --with-login [--secrets-dir DIR]   # PR2: вмикає LOGIN + SCRAM-пароль для 7 runtime-ролей з DSN-секретів
```

`--with-login` — окрема, необов'язкова дія: без неї `db roles` поводиться як у PR1 (лише
GRANT, ролі лишаються `NOLOGIN`). Усі сім секретів `postgres_dsn_<component>` обов'язкові —
без будь-якого з них команда завершується exit 1 без жодної зміни в БД. Деталі механізму —
[`docs/persistence/postgres.md` §7.2](../persistence/postgres.md#72-login-scram-verifier-db-roles---with-login).

`db migrate` виконує `alembic upgrade head` і `ensure_month_partitions` **в одній транзакції**
(DDL PostgreSQL транзакційний) — обидва або застосовуються разом, або жодне. Вивід:

```text
migrated postgresql+asyncpg://collector:***@127.0.0.1:5432/collector: 0001_control_queue -> 0003_default_partition
partition created: audit_log_y2026m09
partition created: audit_log_y2026m10
partition created: audit_log_y2026m11
partition created: audit_log_y2026m12
```

DSN у виводі завжди без пароля (`settings.redacted_dsn`); `--partitions-ahead N` міняє
горизонт (за замовчуванням 3 місяці). `collector db roles` обов'язково виконується **після**
кожного `db migrate`, що додає нову таблицю або GRANT у `sql/roles.sql` — GRANT потребує вже
існуючих об'єктів.

### Порядок при першому старті кластера

1. Стартує `postgres` (init-скрипти `deploy/compose/postgres/init/01-roles.sql` створюють
   NOLOGIN group-ролі §13 ще до появи таблиць — `collector db roles` посилається на них).
2. `collector db migrate` — створює схему і партиції.
3. `collector db roles` — GRANT на щойно створені таблиці.

## 2. One-shot `migrate-postgres` (Docker Compose)

WP-00 `docker-compose.yml` (профіль `core`) визначає one-shot-сервіс `migrate-postgres`, що
запускається перед `api`/workers і має завершитись кодом 0:

```yaml
migrate-postgres:
  command: ["collector", "db", "migrate"]   # + collector db roles після merge WP-01A PR1
  environment:
    COLLECTOR_POSTGRES_DSN_FILE: /run/secrets/postgres_dsn
```

**Важливо (operationally unverified у цьому PR):** на момент написання документа сервіс на
`main` ще викликає лише `collector db migrate` — додавання `collector db roles` до цього
one-shot зафіксовано як відкритий пункт 2 dependency-запиту
[`docs/plan/deps/WP-01A-to-WP-00.md`](../plan/deps/WP-01A-to-WP-00.md) і виконується власником
`docker-compose.yml` (WP-00) після merge WP-01A PR1. До того часу в dev-стеку `collector db
roles` після `docker compose up` треба запускати вручну (розділ 1) або через `docker compose
run --rm migrate-postgres collector db roles`. Сам `collector db migrate` (реалізація Alembic)
і `collector db roles` перевірені й зелені — розділ «Команди перевірки» картки WP-01A, звіт
`docs/plan/reports/WP-01A/implementation-pr1.md`.

DSN для one-shot іде через `COLLECTOR_POSTGRES_DSN_FILE` (Docker secret) — окремо від DSN
runtime-ролей, бо міграційна роль (`collector_migrate`) за §13 не використовується
runtime-процесами.

## 3. `collector db migrate --check` — виявлення drift

```bash
uv run collector db migrate --check
```

- exit 0, `schema up to date: revision=<rev>` — схема відповідає моделям, нічого не мінялось;
- exit 1, рядки drift + `schema drift: схема відрізняється від моделей` — або БД не на head,
  або хтось змінив схему в обхід Alembic.

**`--check` нічого не лишає у схемі, але не є read-only за правами**: `alembic check`
конфігурує `MigrationContext`, який на порожній БД намагається створити `alembic_version`
(усе відкочується разом із з'єднанням, тож жодного постійного сліду немає) — але це означає,
що команда потребує `CREATE` на схемі `public`, тобто тих самих прав, що й міграції, а не
моніторингової read-only ролі (`collector_api_ro`). Використовуйте DSN міграційної ролі
(`collector_migrate`) і для `--check`.

`--check` — саме той крок, який виконує CI job `integration-postgres`
(`.github/workflows/ci.yml`) на `postgres:18` service container з pinned digest.

### Що робити при drift

1. **БД не на head** (найчастіше — забутий `collector db migrate` після pull нової ревізії):
   `uv run collector db migrate` без `--check` — застосує відсутні ревізії.
2. **Схема змінена в обхід Alembic** (ручний DDL, застарілий дамп, ручне виправлення
   incident-у): не редагувати таблицю напряму ще раз. Порівняти `drift`-рядки з моделями
   (`src/collector/persistence/postgres/models/**`), і або (а) написати forward-fix
   міграцію, що приводить схему до стану моделей (якщо ручна зміна була помилкою), або
   (б) якщо ручна зміна мала бути постійною — спершу оновити модель, згенерувати від неї
   міграцію, і лише тоді застосувати. Ніколи не редагувати вже застосовану ревізію
   (forward-only, розділ 5).
3. **CHECK-констрейнт або predicate partial index підмінено напряму** — `alembic check` це
   **не побачить** (autogenerate не порівнює текст CHECK/`WHERE`); джерело істини —
   `tests/integration/postgres/test_schema_contract.py`, що читає живу схему через
   `pg_get_constraintdef`/`pg_indexes.indexdef` і звіряє з `collector.contracts.enums`. Якщо
   підозра саме на це — прогнати цей набір тестів проти підозрюваної БД, не лише `alembic
   check`.

## 4. Що робити при неуспішній міграції

DDL у PostgreSQL транзакційний, і `collector db migrate` виконує `upgrade head` + партиції в
одній транзакції — при помилці **весь крок відкочується**, часткового стану не лишається
(перевірено `test_migrations.py::test_upgrade_head_twice_in_a_row_is_idempotent` і власним
прогоном `downgrade base → upgrade head` у трьох gate-ах PR1). Дії:

1. Прочитати помилку в stderr — `collector db migrate` перекладає помилки PostgreSQL/asyncpg
   (`postgres error: …`, exit 1) без traceback; DSN у повідомленні завжди без пароля.
2. Найчастіші причини: недостатні права DSN (потрібна роль з `CREATE` на `public`, тобто член
   `collector_migrate` або superuser у dev — не `collector_api_ro`), зайнятий lock (конкурентний
   `db migrate` з іншого процесу — повторити після завершення), синтаксична/логічна помилка в
   новій ревізії (виправити файл ревізії, **не** попередні застосовані).
3. Повторний `collector db migrate` безпечний (`alembic upgrade head` ідемпотентний на
   успішно застосованих ревізіях — `test_adversarial.py::
   test_upgrade_head_twice_in_a_row_is_idempotent`).

## 5. Forward-only політика і forward-fix замість downgrade

- У production `downgrade` **не використовується**, навіть коли реалізований: наступна
  ревізія завжди рухає схему вперед. Якщо застосована міграція виявилась помилковою —
  написати нову ревізію, яка виправляє стан (додає відсутню колонку/index, ослаблює або
  замінює CHECK, переносить дані), а не намагатися «відкотити» попередню.
- Для ревізій, де `downgrade` небезпечний (втрата даних, незворотна конверсія типу) —
  реалізація **не пишеться**: `def downgrade() -> None: raise NotImplementedError(...)` з
  описом, чому і що робити замість цього (forward-fix). Ревізії PR1 (`0001`–`0003`) мають
  повний `downgrade`, бо кожна застосовувалась до появи даних, які могли б конфліктувати;
  наступні PR оцінюють це для кожної нової ревізії окремо.
- `scale_commands.audit_id`/`audit_created_at` навмисно без FK на `audit_log` — це дозволяє
  maintenance-циклу видаляти старі партиції `audit_log` (retention, WP-12) без forward-fix
  міграції, що чіпає `scale_commands`.

## 6. Відкат схеми в dev

Лише для локальної розробки/тестування, ніколи в production:

```bash
uv run alembic downgrade base   # реалізовано для 0001-0003; послідовно розгортає всі ревізії
```

або, якщо потрібен повний скид стану (включно з даними та volume):

```bash
docker compose down -v   # видаляє stateful volume postgres; наступний up створює порожній кластер
```

Після будь-якого відкату — `collector db migrate && collector db roles`, щоб повернути схему й
ролі до поточного стану.

## 7. Відсутня партиція

- `audit_log` і `fetches` мають DEFAULT-партицію (`audit_log_default` — міграція `0003`;
  `fetches_default` — міграція `0004`) — INSERT у місяць без явної партиції **не падає**,
  рядок осідає в DEFAULT. Ненульова кількість рядків там — сигнал, що maintenance
  (`ensure_month_partitions`, за замовчуванням горизонт 3 місяці) відстає; перевірити:
  `partitions.default_partition_row_count(conn, "audit_log" | "fetches")` (метрика заплановано
  для §14.1/§14.2, `TODO(WP-12)`). Виправлення — запустити `collector db migrate` (створює
  відсутні місячні партиції) і потім перенести рядки з DEFAULT у щойно створену партицію
  окремою maintenance-транзакцією (`BEGIN; CREATE TABLE tmp (LIKE <table>); INSERT INTO tmp
  SELECT * FROM <table>_default WHERE <col> >= … AND <col> < …; DELETE FROM <table>_default
  WHERE …; ALTER TABLE <table> ATTACH PARTITION …; COMMIT` — WP-12 реалізує як окремий job).
- `raw_objects`, `change_events`, `outbox_events` — свідомо непартиційовані (ADR-0007), тому цей
  розділ до них не застосовується.
- Таблиці **без** DEFAULT-партиції (гіпотетичні майбутні партиційовані таблиці, поки для них не
  прийнято те саме рішення, що для `audit_log`/`fetches`) — INSERT у місяць без партиції дає
  зрозумілу помилку PostgreSQL `no partition of relation "…" found for row`; виправлення — те
  саме `collector db migrate --partitions-ahead N` з достатнім горизонтом наперед.

## Команди — короткий довідник

| Дія | Команда |
|---|---|
| Застосувати міграції + партиції | `uv run collector db migrate [--partitions-ahead N]` |
| Перевірити drift без змін (потребує прав міграції) | `uv run collector db migrate --check` |
| Застосувати ролі/GRANT (після migrate) | `uv run collector db roles [--sql PATH]` |
| Увімкнути LOGIN для 7 runtime-ролей (PR2) | `uv run collector db roles --with-login [--secrets-dir DIR]` |
| Пряме `alembic` (той самий контракт, що в CI) | `uv run alembic upgrade head && uv run alembic check` |
| Повний цикл dev-перевірки нової ревізії | `uv run alembic upgrade head && uv run alembic check` → `uv run alembic downgrade base && uv run alembic upgrade head` (де `downgrade` реалізовано) → `uv run pytest -m integration tests/integration/postgres` |
| Відкат схеми в dev (не production) | `uv run alembic downgrade base` або `docker compose down -v` |

## Джерела

- `src/collector/cli.py` (`db migrate`, `db roles`), `src/collector/persistence/postgres/
  {migrations.py,ops.py,partitions.py,roles.py}`.
- `.github/workflows/ci.yml` (job `integration-postgres` — той самий контракт `--check`).
- `docs/plan/deps/WP-01A-to-WP-00.md` (стан one-shot `migrate-postgres`; §4 PR2 — DSN-секрети
  per component для `--with-login`, open).
- `docs/plan/deps/WP-01A-to-WP-01D.md` (перехід runtime-сервісів на per-role DSN, open).
- `docs/persistence/postgres.md` (схема, партиціонування, ролі, LOGIN, RLS).
- `docs/decisions/0007-event-tables-global-unique-over-partitioning.md` (чому `fetches` має
  DEFAULT-партицію, а `raw_objects`/`change_events`/`outbox_events` — ні).
- `docs/plan/cards/WP-01A.md` («Rollback/disable»).
