# Dependency-запит: WP-01A → WP-00 (CLI-контракт §16.2: `db migrate` реалізовано, `db roles` додано)

| Поле | Значення |
|---|---|
| Від | WP-01A PR1 (`wp/01a-1-control-queue`) |
| До | WP-00 (owner `tests/unit/test_cli.py`, `tests/unit/test_cli_adversarial.py`, `docker-compose.yml`/`Dockerfile` PR2) |
| Файли | `tests/unit/test_cli.py`, `tests/unit/test_cli_adversarial.py`, `tests/unit/test_foundation_config.py`, а після rebase на WP-00 PR2 ще `tests/unit/test_cli_compose_commands.py`, `tests/unit/test_health_adversarial.py`, `tests/unit/test_compose_config.py` (змінено в branch WP-01A за прецедентом WP-01C), `docker-compose.yml` (запит), `Dockerfile` (запит) |
| Стан | п.1 — **resolved**: підтверджено оркестратором на gate 2 як owner-рішення (звіт `docs/plan/reports/WP-01A/testing-pr1.md`, знахідка L-3); п.2–3 — open, для WP-00 PR2; п.4 (WP-01A PR2: DSN-секрети per component) — open |

## 1. CLI-контракт: `db migrate` більше не стаб, група `db` має підкоманду `roles`

Картка WP-01A закріплює за WP-01A `src/collector/cli.py` «лише реалізація `db migrate` замість
стаба + нова `db roles`». Наслідок для owned-тестів WP-00:

- `tests/unit/test_cli.py::STUBS` і `tests/unit/test_cli_adversarial.py::STUB_ARGV` містили
  `["db", "migrate"]` як стаб з owner `WP-01A` — після реалізації команда без DSN завершується
  кодом 1 з `postgres config: ...` (не `not implemented`), тому запис прибрано з обох списків;
- `test_group_help_lists_subcommands`/`test_group_command_set_is_exact` вимагали точний набір
  `{"ensure-mongo", "migrate"}` для групи `db` — додано `"roles"`.

Також `tests/unit/test_foundation_config.py::FORBIDDEN_FOUNDATION_DEPS` забороняв `sqlalchemy`
і `alembic` у `pyproject.toml`, тоді як картка WP-01A прямо додає `sqlalchemy[asyncio]`,
`asyncpg`, `alembic` (runtime) і `testcontainers` (dev) — обидва пакети прибрано зі списку
(решта заборон — scrapy/httpx/pymongo/fastapi — лишається).

Зміни мінімальні (5 рядків + коментарі з посиланням на цей файл) і зроблені прямо у branch
WP-01A за прецедентом `docs/plan/deps/WP-01C-to-WP-00.md` (п.1–2 «resolved у branch за
рішенням оркестратора»), щоб `uv run pytest -m "not live"` лишався зеленим.

### Доповнення після rebase на WP-00 PR2 (ті самі підстави, п.1)

WP-00 PR2 додав власні тести навколо стаба `db migrate` (TCP-проба + рядок
«no migrations yet; owner WP-01A») і навколо порожнього каталогу init-скриптів. Після того як
WP-01A PR1 замінив тіло команди на Alembic, три тести описували вже неіснуючий контракт:

| Тест | Був | Став |
|---|---|---|
| `test_cli_compose_commands.py::test_db_migrate_exits_0_with_owner_message_when_postgres_reachable` | exit 0 + `no migrations yet; owner WP-01A` | `…::test_db_migrate_requires_dsn_and_is_no_longer_a_tcp_stub` — без DSN exit 1 і явне повідомлення, без stub-рядка |
| `test_cli_compose_commands.py::test_db_migrate_exits_1_when_postgres_unreachable` | недоступність через monkeypatch `check_postgres` | реальний закритий порт у DSN → exit 1, порожній stdout, `postgres error` у stderr, без traceback |
| `test_health_adversarial.py::test_db_migrate_never_prints_stub_line_nor_reads_secrets` | exit 0 + `owner WP-01A` | відсутній secret-файл DSN → exit 1 з назвою env, без stub-рядка і без traceback |
| `test_compose_config.py::test_postgres_init_scripts_are_mounted_read_only_for_wp_01a` | `init/` не містить `*.sql` (WP-00 їх не копіює) | `init/` містить рівно `01-roles.sql`, і у виконуваному SQL немає GRANT/паролів/DDL таблиць — тобто перевіряється саме те, що туди не можна класти |

Після першого прогону CI на PR #3 до цього переліку додався ще один рядок того самого
класу: `test_cli_compose_commands.py::test_db_migrate_exits_1_when_postgres_unreachable`
зʼєднувався з реальним закритим портом `127.0.0.1:1`, що на POSIX блокує `pytest-socket`
(на Windows loopback дозволений) — відмову тепер підставляють у `asyncpg.connect`, без socket.
Інваріант тесту незмінний: exit 1, порожній stdout, `postgres error` у stderr, без traceback.

Жодну перевірку не послаблено: кожен тест зберіг свій інваріант (немає stub-рядка, немає
traceback, секрети не витікають, у initdb немає GRANT/паролів) і лише перевів його на реальну
поведінку команди. Монтування `./deploy/compose/postgres/init:/docker-entrypoint-initdb.d:ro`
і `COLLECTOR_POSTGRES_DSN_FILE` для one-shot `migrate-postgres` WP-00 PR2 уже зробив — п.2
цього запиту закритий.

**Відкрите для WP-00:** `docker-compose.yml` (forbidden для WP-01A) у коментарі до
`migrate-postgres` уже передбачає «+ collector db roles після merge WP-01A PR1» — команду
one-shot варто змінити на `collector db migrate && collector db roles`, інакше GRANT для нових
таблиць доведеться застосовувати вручну.

**Стан п.1 — `resolved`.** Незалежне тестування (gate 2) винесло ці зміни окремою знахідкою
L-3 («процесна, не технічна: жоден тест не послаблено — навпаки, додано покриття
`db migrate`/`db roles`»), і оркестратор підтвердив їх як owner-рішення. Додаткових дій від
WP-00 за цим пунктом не потрібно.

Поведінка нових команд, яку WP-00 може перевіряти у своїх контрактних тестах:

| Команда | Без DSN | Успіх |
|---|---|---|
| `collector db migrate [--check] [--partitions-ahead N]` | exit 1, stderr `postgres config: не задано COLLECTOR_POSTGRES_DSN (або COLLECTOR_POSTGRES_DSN_FILE)` | exit 0, stdout `migrated <dsn без пароля>: <rev> -> <rev>` + `partition created: ...`; `--check` → `schema up to date: revision=...` або exit 1 `schema drift` |
| `collector db roles [--sql PATH]` | те саме | exit 0, stdout `roles applied to <dsn>: collector_migrate, ...` |

## 2. `docker-compose.yml` (WP-00 PR2): сервіс `postgres` і init-скрипти

- image `postgres:18@sha256:86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae`
  (той самий digest, що в CI job `integration-postgres` і в testcontainers-фікстурі);
- mount `./deploy/compose/postgres/init:/docker-entrypoint-initdb.d:ro` — створює NOLOGIN
  group-ролі §13 при першому старті (`deploy/compose/postgres/init/01-roles.sql`);
- env для застосунку: `COLLECTOR_POSTGRES_DSN` або (краще) `COLLECTOR_POSTGRES_DSN_FILE`
  з Docker secret; one-shot `migrate-postgres` = `collector db migrate && collector db roles`;
- для локальних integration-тестів проти compose-сервісу: `COLLECTOR_TEST_POSTGRES_ADMIN_DSN`
  (superuser DSN) замість testcontainers.

## 3. `Dockerfile` (WP-00 PR2): `alembic.ini` і `migrations/` в image

`collector db migrate` шукає `alembic.ini` через env `COLLECTOR_ALEMBIC_INI`, інакше вгору від
cwd/пакета. Image має або `COPY alembic.ini migrations/ /app/` (з `WORKDIR /app`), або
`ENV COLLECTOR_ALEMBIC_INI=/app/alembic.ini` з відповідним `COPY`; `migrations/postgres`
резолвиться відносно каталогу `alembic.ini`.

## 4. PR2 (`wp/01a-2-artifacts-projection`): DSN-секрети per component і `db roles --with-login` — open

Контекст: dependency `docs/plan/deps/WP-01D-to-WP-01A.md` §2 (F1 gate 2 WP-01D — усі runtime-процеси
ходять у PostgreSQL superuser-роллю міграцій). WP-01A PR2 зробив свою частину: команда
`collector db roles --with-login [--secrets-dir DIR]` робить сім runtime-ролей LOGIN-ролями, а
пароль кожної бере з DSN-секрету компонента. Лишилися файли, яких WP-01A торкатися не може
(`deploy/compose/secrets/init-secrets.sh`, `*.example`, `docker-compose.yml` — owner WP-00):

1. **`init-secrets.sh`**: згенерувати сім нових секретів `postgres_dsn_<component>` —
   `scheduler`, `fetcher`, `parser`, `projector`, `translation`, `api_ro`, `export_ro`. Формат —
   той самий, що в `postgres_dsn`, але користувач = роль і **власний** випадковий пароль:

   ```text
   postgresql://collector_<component>:<random_hex_48>@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-collector}
   ```

   Пароль — лише printable ASCII (hex із `random_hex` підходить; інакше `db roles` відмовить:
   verifier рахується без SASLprep). Окремі файли паролів не потрібні: джерело істини — сам DSN,
   `db roles --with-login` читає з нього пароль і ставить ролі SCRAM verifier. Відповідні
   `postgres_dsn_<component>.example` без секретів (плейсхолдер) — для циклу `for example in *.example`.
2. **`docker-compose.yml`, one-shot `migrate-postgres`**: `collector db migrate && collector db
   roles --with-login` і змонтувати йому **всі сім** `postgres_dsn_<component>` (разом із
   `postgres_dsn` міграційної ролі). Каталог секретів типово `/run/secrets`
   (`COLLECTOR_POSTGRES_ROLE_SECRETS_DIR` перевизначає). Без будь-якого з файлів команда
   завершується exit 1 і нічого не змінює — тобто неповний набір секретів видно одразу.
3. **Runtime-сервіси** (`scheduler`, `worker-*`, `api`, експортер): кожен монтує **свій**
   `postgres_dsn_<component>` як `COLLECTOR_POSTGRES_DSN_FILE` замість спільного
   `postgres_dsn`. Це координується з WP-01D (власник worker/scheduler-сервісів і тест-вартового
   `test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire`) — див.
   `docs/plan/deps/WP-01A-to-WP-01D.md`.

Нічого з цього не ламає поточний stack: поки секретів немає, `collector db roles` без
`--with-login` працює як у PR1, а runtime лишається на `postgres_dsn` (відоме відхилення §13,
записане у WP-01D).

## 5. `.gitleaksignore` у корені репозиторію — resolved by orchestrator

Коміт `bad6a25` (WP-01A PR2) містить синтетичне значення в unit-тесті, яке gitleaks
класифікує як `generic-api-key` (SR-1 spec-review PR2). Історію запушеної гілки не
переписуємо. Оркестратор дозволив WP-01A як виняток з owned files створити кореневий
`.gitleaksignore` з fingerprint саме цього finding і коментарем-поясненням; сам тест
переписано, щоб значення будувалося в рантаймі. Від WP-00 дій не потрібно. **Resolved by
orchestrator.**

## 6. `REVOKE CONNECT, TEMP ON DATABASE … FROM PUBLIC` — open (security-pr2.md I-2)

`security-pr2.md` I-2: усі PostgreSQL-ролі за замовчуванням мають `CONNECT`/`TEMP` на будь-яку
БД кластера через членство в `PUBLIC` (кластерний default, не рішення WP-01A). Ролі WP-01A
(`collector_*`) призначені для однієї БД (`COLLECTOR_POSTGRES_DSN`/`postgres_dsn_<component>`),
і сама схема прав (GRANT per table) це не звужує — `CONNECT` лишається доступним ширше, ніж
потрібно.

`sql/roles.sql`/`collector db roles` (owned WP-01A) виконується **після** `collector db
migrate` на вже створеній БД `collector` і не має прав ні створювати інші БД кластера, ні
безпечно виконувати `REVOKE ... FROM PUBLIC` на рівні кластера (це `ALTER DEFAULT PRIVILEGES`
чи `REVOKE` на `pg_database`, зона init-скрипту WP-00, не міграцій WP-01A). Прохання: у
`deploy/compose/postgres/init/**` (WP-00) додати `REVOKE CONNECT, TEMP ON DATABASE <інші БД
кластера, якщо є> FROM PUBLIC` для БД `collector` при першому старті кластера. Не блокує PR2
(severity `info`, `security-pr2.md`).
