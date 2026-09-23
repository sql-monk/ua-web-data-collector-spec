# Dependency-відповідь: WP-01A → WP-01D (LOGIN-ролі, `queue.release`, `command_timeout`)

| Поле | Значення |
|---|---|
| Від | WP-01A PR2 (`wp/01a-2-artifacts-projection`) |
| До | WP-01D (owner `docker-compose.yml` worker/scheduler-сервісів, `src/collector/workers/**`, `tests/unit/test_compose_config*.py`) |
| Відповідь на | `docs/plan/deps/WP-01D-to-WP-01A.md` §2–§5 |
| Стан | §2 — зроблено на боці WP-01A, **потребує дії WP-01D** (per-role DSN + тест-вартовий); §3/§5 — зроблено; §4 — підтверджено |

## 1. §2 LOGIN-ролі per component — зроблено в WP-01A; тест-вартовий **лишився зеленим**, і це треба прибрати вам

Що є після PR2:

- `collector db roles --with-login [--secrets-dir DIR]` (типово `$COLLECTOR_POSTGRES_ROLE_SECRETS_DIR`
  або `/run/secrets`) робить сім runtime-ролей LOGIN-ролями: `collector_scheduler`,
  `collector_fetcher`, `collector_parser`, `collector_projector`, `collector_translation`,
  `collector_api_ro`, `collector_export_ro`. Логін — **сама group-роль** (окремих
  `fetch_01`-користувачів немає), тож DSN компонента має вигляд
  `postgresql://collector_fetcher:<password>@postgres:5432/collector`;
- пароль береться з DSN-секрету `postgres_dsn_<component>` (`scheduler`, `fetcher`, `parser`,
  `projector`, `translation`, `api_ro`, `export_ro` — формат, який ви запропонували в §2.2);
  на сервер іде лише SCRAM-SHA-256 verifier; усі сім секретів обов'язкові (інакше exit 1 без
  змін), користувач у DSN має дорівнювати ролі, runtime-роль — член `collector_migrate` →
  відмова; атрибути явно `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`;
- `collector_migrate` LOGIN не отримує; `collector db roles` без `--with-login` видані логіни
  не вимикає;
- `collector.persistence.postgres.roles.verify_runtime_login(conn)` — один виклик при старті
  worker/scheduler: `RoleLoginError`, якщо процес підключився superuser-ом або членом
  `collector_migrate`. Пропоную викликати його в runtime після переходу на per-role DSN —
  тоді повернення до спільного `postgres_dsn` впаде при старті, а не тихо.

**Тест-вартовий.** `tests/unit/test_compose_config.py::test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire`
сканує лише SQL-файли ролей (`sql/roles.sql`, `deploy/compose/postgres/init/01-roles.sql`).
LOGIN у PR2 вмикається з Python (`roles.apply_logins`), бо пароль мусить прийти з Docker secret
у runtime, а не з файлу в репозиторії, — тож SQL-файли як і раніше містять лише `NOLOGIN`, і
вартовий **не спрацював**. Це не «тихе» виконання: цим повідомленням WP-01A явно сигналить, що
передумова вартового («LOGIN-ролей ще не існує») більше не виконується. Тест WP-01A не
редагував. Прохання до WP-01D одним PR:

1. перевести `scheduler` і `worker-*` на власні `postgres_dsn_<component>` (мапінг нижче) і
   прибрати спільний `postgres_dsn` з runtime-сервісів;
2. замінити вартового на позитивний тест §13 (runtime-сервіси не монтують `postgres_dsn`);
3. (бажано) `verify_runtime_login` при старті runtime.

Генерація секретів і one-shot `migrate-postgres` (`collector db migrate && collector db roles --with-login`)
— запит до WP-00: `docs/plan/deps/WP-01A-to-WP-00.md` §4.

### Мапінг `WorkerRole` → роль БД (пропозиція; рішення за WP-01D)

| `collector worker <role>` / сервіс | Роль БД | Секрет |
|---|---|---|
| `scheduler`, `maintenance` | `collector_scheduler` | `postgres_dsn_scheduler` |
| `discovery`, `fetch`, `browser` | `collector_fetcher` | `postgres_dsn_fetcher` |
| `parse` | `collector_parser` | `postgres_dsn_parser` |
| `projector` | `collector_projector` | `postgres_dsn_projector` |
| `translation` | `collector_translation` | `postgres_dsn_translation` |
| `api` (WP-11A) | `collector_api_ro` | `postgres_dsn_api_ro` |
| `export` | **відкрите питання** | — |

`export` у §13 — read-only (`collector_export_ro`), але worker runtime WP-01D пише
`worker_instances`/`crawl_jobs`/`audit_log` (heartbeat, claim, bootstrap pool). Варіанти:
(а) export-воркер ходить у чергу як `collector_scheduler`, а дані читає окремим
`collector_export_ro`-з'єднанням; (б) окрема роль `collector_exporter` (запит до WP-01A на PR3).
WP-01A не обирав за вас.

### §2.3 Мінімальні права runtime — підтверджено й розширено

`sql/roles.sql` PR2 дає кожній worker-ролі (`fetcher`, `parser`, `projector`, `translation`)
спільну базу: `crawl_jobs` SELECT/UPDATE, `dead_letters` SELECT/INSERT, `worker_pools`
SELECT/INSERT (bootstrap), `worker_instances` SELECT/INSERT/UPDATE, `audit_log` **лише INSERT**.
`audit_log` INSERT потрібен уже в PR2 (а не в PR3, як у запиті): `upsert_pool` (bootstrap pool
воркером) тепер пише audit у своїй транзакції (знахідка S-2 пострев'ю PR1). INSERT у
`crawl_jobs` мають лише `fetcher`/`parser`/`scheduler` (enqueue наступних jobs);
`projector`/`translation` — ні. Інтеграційний тест `tests/integration/postgres/test_role_logins.py`
проганяє реальні репозиторні операції кожного компонента під його LOGIN-роллю.

## 2. §3/§5 `queue.release(job_id, owner)` — зроблено

`leased → pending`, lease очищено, `not_before = now` (claimable одразу), `attempt` **не**
змінюється, `last_error_code`/`last_error_message` **не** пишуться, dead letter не створюється,
на останній спробі карантину немає; чужий/завершений lease → `LeaseNotOwnedError`. Тести:
`tests/integration/postgres/test_queue_release.py`. Для projector-а є симетричний
`projection.release_projection_task`. Обхід через `retry(..., IMMEDIATE_RETRY_POLICY)` у
`_release_leases` можна замінити на `release` — це ваш файл, WP-01A його не чіпав.

## 3. §4 `create_engine(..., command_timeout=...)` — підтверджено

Зміна прийнята як є: keyword-only, default `None` (поведінка міграцій незмінна), значення йде в
`connect_args` asyncpg. Іншого місця не пропонуємо.

## 4. Зміни сигнатур, що зачіпають викликачів (для інформації)

Mutating-операції control plane тепер пишуть `audit_log` усередині репозиторію і вимагають
непорожні `actor`/`reason` (`InvalidValueError` до першого запису): `sources.set_source_state`
(`reason: str` замість `str | None`), `sources.add_policy_version`, `sources.upsert_route`,
`sources.set_route_state`, `sources.upsert_cursor`, `limiter.block_origin` (новий обов'язковий
`actor`), `queue.quarantine(owner=None)` (операторський виклик), `pools.upsert_pool`. Runtime
WP-01D викликає лише `upsert_pool` (з `actor`/`reason`, сумісно) і `quarantine` з `owner`
(аудиту не пише, сигнатура сумісна) — повний `pytest -m "not live"` включно з
`tests/integration/scaling/**` зелений.
