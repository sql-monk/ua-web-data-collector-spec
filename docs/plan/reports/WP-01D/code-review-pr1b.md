# WP-01D PR1b — код-рев'ю (runtime під LOGIN-ролями §13, drain через `queue.release`)

Гілка `wp/01d-1b-runtime-role-dsn` (HEAD `c32f4e1`). Базова гілка `wp/00-4-role-dsn-secrets` під час
рев'ю видалено (PR #7 змерджено в `main` як `ae63917`), тому diff PR1b рахувався від точки
відгалуження `8775f7e` (`git diff 8775f7e c32f4e1`) — це ті самі 7 комітів PR1b
(`8c65e33..c32f4e1`), що й `wp/00-4-role-dsn-secrets...wp/01d-1b-runtime-role-dsn` до видалення.

## Знахідки

| # | severity | file:line | claim | failure scenario | verdict |
|---|---|---|---|---|---|
| 1 | low | `tests/integration/scaling/test_runtime_login_adversarial.py:164-175` | У тесті повного циклу job `n=1` іде в `retry` з типовим `BackoffPolicy` (`base=30s`, `repositories/queue.py:80`), а тест живе довше. За звітом тестування цикл під навантаженням триває 45–49 с (`testing-pr1b.md`, `[fetch]`/`[discovery]`), та ще й `SLOW=60` свідомо закладає довгі очікування. | Тест триває понад ~30 с + jitter → runtime знову claim-ить `n=1`, handler падає ще раз. `wait_for(active_tasks == 1)` у рядку 166 може спрацювати на повторному `n=1`, а не на `n=3`. Тоді `stop.set()` приходить раніше, ніж claim-нуто `n=3` → `handler.cancelled == []` ≠ `[3]` (рядок 175), або `n=1` у роботі під час stop → `[1]`/`[1, 3]`. Виходить флейк на повільному хості, дефекту продукту немає. Можливі виправлення: `max_attempts=1` для `n=1`, або `n=1` через `PermanentTaskError`, або перевіряти `n=3` у `handler.started`. | PLAUSIBLE |
| 2 | low | `tests/integration/scaling/conftest.py:146-199` (+ порядок параметрів у `test_worker_runtime*.py`, `test_scheduler_singleton*.py`) | `runtime_sessions`/`scheduler_engine` у більшості тестів оголошено **після** `running`. Pytest знімає фікстури у зворотному порядку: спочатку `role_engine.dispose()` і `ALTER ROLE … NOLOGIN PASSWORD NULL`, лише потім `running` скасовує runtime-задачі. | Тест падає посередині, runtime ще живий → engine вже disposed, роль вже NOLOGIN → наступний checkout відкриває нове з'єднання і отримує `role ... is not permitted to log in`. У логах з'являються heartbeat_failed/fenced, які зашумлюють справжню причину падіння. На зеленому шляху нічого не відбувається: runtime зупиняють явно. Щоб прибрати ефект, `running` має залежати від `role_engine` або стояти в параметрах після нього. | PLAUSIBLE |
| 3 | low | `tests/integration/scaling/test_runtime_login.py:202-207` | `_cli_env` для `CliRunner` лише додає `COLLECTOR_POSTGRES_DSN` і не прибирає `COLLECTOR_POSTGRES_DSN_FILE` з оточення хоста. `PostgresSettings.from_env` (`config.py:49-58`) віддає пріоритет `*_FILE`, а `CliRunner` решту `os.environ` не чіпає. У subprocess-тестах (`_run_cli` в adversarial) усі `COLLECTOR_*` фільтруються, тут — ні. | У розробника чи в CI виставлено `COLLECTOR_POSTGRES_DSN_FILE` → CLI-тест бере не той DSN, який задав, і перевіряє відмову не для тієї ролі (або падає з `postgres config:`/`postgres error:` замість `role login:`). Виправлення: `monkeypatch.delenv(...)`, як у `tests/unit/workers/test_db_login.py:143`. | CONFIRMED (читанням коду) |

Critical / high / medium знахідок немає.

**Інформаційно (не дефект коду, для оркестратора):** PR1b відгалужено від PR4 до gate-3'-фіксів.
У `main` після `8775f7e` додано `9281cdf`, `4f84020` (зокрема `deploy/compose/secrets/init-secrets.sh`,
`tests/unit/test_compose_config.py:700-709`). PR1b теж суттєво правив `tests/unit/test_compose_config.py`,
тож перед мерджем гілку треба rebase-нути на `main` і повторно прогнати unit + scaling тести.
`src/collector/persistence/postgres/roles.py` між точкою відгалуження і `main` не змінювався,
тому контракт `verify_runtime_login`/`RoleLoginError`, на який спирається PR1b, лишився тим самим.

## Вердикт

**approve** — critical/high немає; 3 low (усі в тестах).

## Що перевірено окремо

- **Місце виклику `verify_runtime_login`.**
  - Worker: `_boot` (`runtime.py:277-285`) — перший запит, до `_ensure_pool` (bootstrap pool + audit), `register_instance`, heartbeat і claim. Він стоїть усередині `try/finally` у `run()`, тож liveness-маркер і сигнали прибираються.
  - Scheduler: `scheduler.py:155-158` — до встановлення сигналів і до `try`, тобто до `lease.try_acquire`. Прибирати в `finally` нічого, а engine закриває `_run_scheduler`.
- **Чи достатньо перевірки на одному з'єднанні.** Для процесу — так. Усі з'єднання pool-у (і після reconnect/recycle) створює один `AsyncEngine` з одного URL, прочитаного один раз на старті (`_postgres_settings()` → `create_engine`). Advisory-з'єднання scheduler-а — `self._engine.connect()` (`advisory.py:82`) того самого engine, яким `_run_scheduler` створює і session factory. Креденшели між з'єднаннями змінитися не можуть; `SET ROLE` runtime не виконує.
- **Помилка і exit code.** `RoleLoginError` (підклас `ValueError`) `_run_async` не перехоплює: `_is_postgres_error` → false. Далі її ловить `_run_runtime` → `role login: ...` у stderr, `typer.Exit(1)`, без traceback. У повідомленнях лише імена ролей і атрибути (`roles.py:295-306`, `login.py:34-38`); DSN/пароль у виняток не потрапляють. У лог іде `worker.db_login`/`scheduler.db_login` з `db_role` — це ім'я ролі, не секрет. Помилку з'єднання на етапі перевірки обробляє, як і раніше, `postgres error:` → exit 1; повідомлення asyncpg про невірний пароль пароля не містить. Rollback `COLLECTOR_WORKER_PLACEHOLDER=1` до БД не доходить.
- **`docker-compose.yml`, override anchor.** YAML резолвлено (`yaml.safe_load`):
  - Кожен сервіс має рівно один секрет і відповідний `COLLECTOR_POSTGRES_DSN_FILE`: discovery/fetch/browser → `postgres_dsn_fetcher`, parse → `_parser`, projector → `_projector`, translation → `_translation`, export/maintenance/scheduler → `_scheduler`. `postgres_dsn` лишився тільки в `migrate-postgres`.
  - `x-collector-runtime` не має `secrets`, тож після видалення `secrets` з `x-worker` через `<<` нічого не протікає.
  - Merge-ключ YAML замінює ключі цілком, списки не зливає: `secrets` сервісу — рівно його список.
  - Вкладений `<<: *worker-env` (anchor на мапінг, який сам містить `<<: *collector-env`) коректно дає повне env: `COLLECTOR_WORKER_STOP_GRACE_SECONDS`, `PLACEHOLDER`, `*_HOST` і власний `DSN_FILE`.
  - `migrate-postgres` виконує `db roles --with-login`, від нього залежать усі runtime-сервіси (`service_completed_successfully`).
- **`_release_leases` → `queue.release`** (`runtime.py:770-793`, `queue.py:344-386`).
  - `release` — CAS за `(job_id, status='leased', lease_owner=owner)` в окремій транзакції на кожну job. Помилка однієї job (`SQLAlchemyError`/`OSError` — сюди входить `TimeoutError` asyncpg — `PersistenceError`) логується через `redact` і не зупиняє решту. `LeaseNotOwnedError` означає, що lease уже чужий: чужий рядок не змінюється, `attempt` теж; це покриває `test_drain_never_releases_a_lease_that_another_owner_took_over`.
  - Гонка з heartbeat: heartbeat-цикл до кінця `_drain` ще працює. Якщо він узяв row lock першим, `release` чекає його коміту (обмежено `statement_timeout`), а потім повертає job. Якщо першим пройшов `release`, heartbeat отримує `LeaseNotOwnedError` → `_abandon` для вже виданої з `_active` job нічого не робить. Подвійного повернення чи хибного lost_lease із наслідками немає.
  - Self-fencing: `_abandon` виймає fenced-jobs з `_active`, тож `release` їх не чіпає — вони чекають `recover_expired_leases`, як і задумано. Якщо task скасовано посеред `_report(complete)` і коміт устиг пройти, `release` отримує `LeaseNotOwnedError`; якщо ні — транзакція відкочується і job повертається.
  - `now` рахується один раз на цикл; для `not_before` і `updated_at` це нешкідливо.
- **Тестові фікстури під LOGIN-ролями.** Паролі одноразові (`secrets.token_hex`), DSN-файли лежать у `tmp_path`, у teardown — `NOLOGIN PASSWORD NULL` для всіх runtime-ролей. `GRANT collector_migrate TO collector_fetcher` знімається `REVOKE` у `finally`, і фікстура знімається раніше за `login_urls` (залежність). Ролі кластерні, але контейнер PG — один на процес pytest, тож між воркерами xdist ролі не течуть. Порядок teardown — знахідка 2.
- **Стабільність нових scaling-тестів.** Очікування скрізь іде на стан (`wait_for`), а не на `sleep`. Для drain-тестів із чужим lease закріплено `concurrency=1`, heartbeat рідкий (30 с), тож гонки вільного слоту немає. Ризик 30-секундного backoff — знахідка 1. Відомий флейк класу A (`test_self_fencing_fires_when_the_database_hangs_without_raising`) з PR1b не пов'язаний.
- **Прогони.**
  - `uv run pytest -q tests/unit/workers/test_db_login.py tests/unit/test_compose_config.py tests/unit/test_compose_config_adversarial.py` → `97 passed`.
  - `uv run mypy src/collector/workers src/collector/cli.py` → `Success: no issues found in 12 source files`.
  - Integration (Docker) не запускались, за умовою завдання.

## Re-review (gate 3')

Обсяг: лише інкрементальні коміти `091ea5a` (код, тести) і `f721571` (картка, deps §6, звіти).

### Знахідки

- low | tests/integration/scaling/test_runtime_login_adversarial.py:58-65 | `CYCLE_CLOCK` вставлено між `SLOW = 60.0` і його docstring. Тепер docstring про таймаут стоїть окремим рядковим літералом одразу після docstring `CYCLE_CLOCK`, а `SLOW` лишився без опису | IDE/Sphinx показують для `CYCLE_CLOCK` лише перший рядок, `SLOW` — без документації, другий літерал висить як no-op. На поведінку не впливає | CONFIRMED (читання файлу)
- low | tests/unit/workers/test_db_login.py:185-193 | Другу половину тесту-вартового S-1 (перевірку джерел) побудовано на regex `HANDLER_FACTORIES\s*(\[|\.update|\.setdefault)[^\n]*EXPORT` по одному рядку. Перша половина (`resolve_handler`) бачить лише модулі, які вже імпортовано в тесті | Реєстрацію в модулі, який тест не імпортує, записану багаторядково (`HANDLER_FACTORIES[\n    WorkerRole.EXPORT\n] = ...`) або через `WorkerRole("export")`, вартовий не помітить. Тоді export отримає реальний handler під `collector_scheduler`. Жорсткий тригер у картці WP-01D (WP-11A / pilot) це страхує | PLAUSIBLE

Critical, high і medium знахідок немає.

### Вердикт

`approve`

### Що перевірено окремо

- **`verify_runtime_login`, `session_user`/`current_user`/`is_superuser`** (`roles.py:310-329`).
  - Перевірка стоїть до решти перевірок атрибутів і allowlist.
  - `current_setting('is_superuser')` повертає `'off'`/`'on'`, тож порівняння рядків коректне.
  - Default GUC `role` (`server_settings`/`ALTER ROLE … SET role`) дає `session_user` ≠ `current_user`, і логін відхиляється. Це покриває `test_worker_refuses_a_privileged_session_with_a_default_role`.
  - Окремо перевірив обхід через `session_authorization` на одноразовому `postgres:18`, `docker run`; compose не піднімав. `ALTER ROLE ops SET session_authorization = 'collector_fetcher'` і `PGOPTIONS='-c session_authorization=…'` на старті не застосовуються: `session_user` = `current_user` = логін, `is_superuser=on`. Отже цього обходу немає, і перевірка `session_user` достатня.
- **Рекурсивне членство** (`_PRIVILEGED_MEMBERSHIPS`).
  - `pg_has_role(:role, oid, 'MEMBER')` транзитивний і не залежить від `INHERIT`/`SET`-опцій гранту (PG16+). Отже `NOINHERIT`-грант, через який можна зробити `SET ROLE`, теж ловиться. `USAGE` пропустив би саме такі гранти, тож вибір `MEMBER` правильний.
  - `LIKE :component_roles` з bind-значенням `collector\_%` при `standard_conforming_strings=on` і default escape `\` дає буквальний `_`. `collectorX…` не збігається. Саму роль виключає `rolname <> :role`.
- **False positive для легітимних ролей.**
  - У `src/collector/persistence/postgres/sql/roles.sql` role-to-role GRANT-ів немає: лише `CREATE ROLE … NOLOGIN` і об'єктні GRANT-и до group-ролей §13. Тобто runtime-роль не є членом жодної іншої `collector_*`.
  - `pg_database_owner` збігається лише для власника поточної БД. У compose це `POSTGRES_USER`, а не runtime.
  - `tests/integration/postgres/test_role_logins.py` (WP-01A, без змін) зелений.
- **Міграційний шлях.** `apply_logins` (`db roles --with-login`) використовує той самий `privileged_memberships`. При чистих `roles.sql` нових відмов немає. Дрейф (зовнішній `GRANT collector_* TO collector_*`) тепер валить `migrate-postgres` з повідомленням без секретів — це очікувана поведінка S-3.
- **Детермінізм тесту з замороженим годинником.**
  - `clock` runtime використовується лише для `now=` у SQL claim/heartbeat/retry/release.
  - Self-fencing і drain рахуються від `monotonic()` (`runtime.py:160, 550-575, 657, 743`), тож заморожений годинник їх не зупиняє.
  - `not_before = CYCLE_CLOCK + backoff` > `CYCLE_CLOCK`, тому повторного claim job `n=1` не буде за жодної тривалості тесту. Це закріплено двома новими asserts.
- **Порядок teardown `running` → `role_engine`, `_cli_env` без `COLLECTOR_POSTGRES_DSN_FILE`** (`None` у `CliRunner.env` прибирає змінну). Обидві правки коректні.
- **Прогони.**
  - `uv run pytest tests/unit/workers/test_db_login.py -q` → `8 passed`.
  - `uv run pytest tests/integration/postgres/test_role_logins.py tests/integration/scaling/test_runtime_login.py tests/integration/scaling/test_runtime_login_adversarial.py -q -p no:randomly` (testcontainers) → `52 passed in 143.38s`.
  - `uv run ruff check` змінених файлів → `All checks passed!`.
  - `mypy` по `roles.py` і `test_db_login.py` помилок не дає. Помилки `import-not-found .conftest` та інші в `tests/integration/scaling/test_worker_runtime*.py` — артефакт прямого виклику mypy на теки тестів і файли поза інкрементом, до цих комітів не належать.
