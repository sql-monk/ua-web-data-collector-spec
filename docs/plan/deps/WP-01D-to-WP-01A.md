# Dependency-запит: WP-01D → WP-01A (PostgreSQL control plane)

Автор: WP-01D PR1 (`wp/01d-1-worker-runtime`). Статус: **запит**, реалізація — на боці WP-01A.
Нічого з переліченого не блокує PR1: runtime уже працює на наявних контрактах.

## 1. Нових таблиць/колонок не потрібно

Для протоколу: singleton-lease scheduler-а реалізовано через **session advisory lock**
(`pg_try_advisory_lock(classid, objid)`, `collector/workers/advisory.py`), а не через таблицю
lease. Причина — lock зникає разом із сесією, тому kill процесу/розрив TCP звільняє singleton
миттєво і без sweeper-а. Отже, `migrations/**` цим PR не змінюється.

## 2. Потрібні LOGIN-ролі per component і окремі DSN (§13) — **високий пріоритет, блокер pilot**

> Підвищено після gate 2 (знахідка F1, `docs/plan/reports/WP-01D/testing-pr1.md` §6).
> Рантайм-доказ на піднятому стеку (звіт тестувальника, дослівно): усі вісім довгоживучих
> процесів під'єднані як **superuser** тією самою роллю, якою виконуються міграції —
>
> ```text
>   usename  | rolsuper | rolbypassrls |       application_name       | count
> -----------+----------+--------------+------------------------------+-------
>  collector | t        | t            | collector-sch                |     2
>  collector | t        | t            | collector-worker-fetch       |     2
>  collector | t        | t            | collector-worker-parse       |     2
>  ...       |          |              |                              |
> ```
>
> `select rolname, rolcanlogin from pg_roles where rolname like 'collector%'` показує
> `rolcanlogin = f` для **всіх** `collector_*` ролей, тому альтернативи в PR1 не існувало
> (`migrations/**` і `sql/roles.sql` для WP-01D — forbidden). Класифікація: блокер
> pilot/production і будь-якого live-збору; merge PR1 не блокує.

`sql/roles.sql` створює group-ролі `NOLOGIN` (`collector_fetcher`, `collector_scheduler`, …),
і LOGIN-користувача, який у них входить, ще немає. Тому в `docker-compose.yml` worker- і
scheduler-сервіси тимчасово монтують **той самий** secret `postgres_dsn`, що й one-shot
`migrate-postgres` — тобто runtime ходить у БД з правами міграційної ролі. Це прямо суперечить
§13 («migration role не використовується runtime-процесами»), і поки так лишається, у
`docs/plan/reports/WP-01D/implementation-pr1.md` це записано як ризик.

Що просимо у WP-01A:

1. LOGIN-ролі (або один LOGIN-користувач на компонент) з `GRANT <group> TO <login>`;
2. рецепт генерації DSN-секретів per component (`init-secrets.sh` — owner WP-00, потрібен
   узгоджений формат імен: `postgres_dsn_fetcher`, `postgres_dsn_scheduler`, …);
3. підтвердження мінімальних прав для runtime: `crawl_jobs` (SELECT/UPDATE), `dead_letters`
   (INSERT), `worker_pools` (SELECT + INSERT на bootstrap), `worker_instances`
   (INSERT/UPDATE/SELECT), `audit_log` (INSERT через `request_scale` — лише PR3).

Після цього WP-01D змінить `environment`/`secrets` worker-сервісів на per-role DSN; тести
`tests/unit/test_compose_config*.py` доведеться оновити разом зі зміною.

**Тест-вартовий уже стоїть:**
`tests/unit/test_compose_config.py::test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire`
падає, щойно в `sql/roles.sql` зʼявиться перша LOGIN-роль — тобто ця вимога не може бути
виконана «тихо», без повернення §13-інваріанту на боці WP-01D. До того моменту зупинити claim
без перебудови image можна прапорцем `COLLECTOR_WORKER_PLACEHOLDER=1`.

## 3. Бажано: явний `release_lease` у queue-репозиторії — низький пріоритет

При drain-timeout runtime повертає lease через
`queue.retry(..., policy=IMMEDIATE_RETRY_POLICY, error_code="drain_timeout")`, щоб job став
claimable одразу. Семантика майже точна, але з одним побічним ефектом: якщо job уже на
останній спробі (`attempt >= max_attempts`), `retry` відправляє її в карантин із dead letter
`max_attempts`, хоча насправді її ніхто не «провалив» — просто контейнер зупинили.

Запит: `release(job_id, owner)` — `leased → pending`, lease очищено, `attempt` **не**
змінюється, dead letter не пишеться. Це той самий перехід, що його вже робить
`recover_expired_leases`, але за явним викликом власника, без очікування експірації.

**Що змінилось після gate 2 (знахідка F2).** Хибного карантину більше немає: `_release_leases`
викликає `retry` лише коли `attempt < max_attempts`, а job на останній спробі лишає `leased` —
у чергу її повертає `recover_expired_leases` після експірації, як і після SIGKILL (§15).
Ціна тимчасового обходу — затримка до `lease_seconds` (типово 60 с) для таких jobs при
плановому scale-down. `release()` прибирає саме цю затримку; пріоритет лишається низьким, бо
коректність уже забезпечена (тест
`test_drain_timeout_on_the_last_attempt_never_quarantines_a_job_nobody_failed`).

## 4. Зроблено в цьому PR у файлі WP-01A: `create_engine(..., command_timeout=...)` — потрібне підтвердження

`src/collector/persistence/postgres/engine.py` отримав **необов'язковий** keyword-параметр
`command_timeout: float | None = None` (кладеться в `connect_args` asyncpg). Причина — знахідка
H-1 gate 3: без таймауту на рівні драйвера запит у «чорну діру» TCP (мережевий поділ, failover)
чекає до RTO ядра, тобто десятки хвилин, і жоден таймаут у застосунку не має нижньої межі;
worker і scheduler задають його від свого вікна self-fencing.

Зміна адитивна і зворотно сумісна: default `None` лишає поведінку міграцій і всіх наявних
викликів незмінною (`alembic upgrade` легально буває довгим, тому one-shot його не задає).
Прошу власника WP-01A підтвердити її при merge або запропонувати інше місце для налаштування.

## 5. Уточнення до §3 після code review (L-5)

`release(job_id, owner)` має не лише не інкрементувати `attempt`, а й **не писати**
`last_error_code`/`last_error_message`: плановий drain — не помилка job-и, і слово
«`drain_timeout`» у полі помилки вводить в оману оператора та псує статистику dead letters.

## 6. Жорсткіша `verify_runtime_login` — resolved by orchestrator exception, 2026-09-24

Джерело: WP-01D PR1b gate 3, `docs/plan/reports/WP-01D/security-pr1b.md` S-2, S-3 (low). Оркестратор
дозволив WP-01D **як виняток із owned files** мінімальну правку
`src/collector/persistence/postgres/roles.py` (owner WP-01A) замість окремого запиту. Що змінено:

1. `verify_runtime_login`: вимагає `session_user = current_user` і `current_setting('is_superuser') =
   'off'` (S-2: логін привілейованою роллю з default GUC `role` давав `current_user` = runtime-роль, а
   `RESET ROLE` повертав би superuser).
2. `privileged_memberships` (отже і `verify_runtime_login`, і `apply_logins`): привілейованим членством
   тепер вважається також (S-3):
   - будь-яка **інша** роль `collector_*` (`COMPONENT_ROLE_PATTERN = r"collector\_%"`): `GRANT
     collector_scheduler TO collector_fetcher` непомітно дав би fetcher-у control plane, а
     `collector_api_ro` — SELECT на все; у `roles.sql` role-to-role GRANT-ів між компонентами немає;
   - вбудовані `pg_read_all_stats`, `pg_stat_scan_tables`, `pg_monitor` (тексти запитів і статистика
     чужих сесій), `pg_read_all_settings` (усі GUC, шляхи й параметри сервера), `pg_create_subscription`
     (логічна реплікація), `pg_checkpoint`, `pg_use_reserved_connections`, `pg_database_owner` (права
     власника БД) — додано до `PRIVILEGED_BUILTIN_ROLES`. Runtime жодна з них не потрібна; імена, яких
     немає в поточній версії PostgreSQL, просто не збігаються.
3. Тести (у файлах WP-01D, тести WP-01A не змінювались):
   `tests/integration/scaling/test_runtime_login.py::test_worker_refuses_a_privileged_session_with_a_default_role`,
   `::test_worker_refuses_membership_in_another_component_or_monitoring_role[collector_scheduler|collector_api_ro|pg_read_all_stats|pg_monitor]`;
   наявні `tests/integration/postgres/test_role_logins.py` лишаються зеленими.

Прохання до WP-01A: прийняти зміну як свою (рев'ю у наступному PR WP-01A); за потреби винести
перелік дозволених членств у явний allowlist.

## 7. PR1c (`wp/01d-1c-handler-plumbing`): очікувані сигнатури WP-01A PR3a — звірка при rebase

Джерело: `docs/plan/cards/WP-01D.md` PR1c п.1, п.2, п.5; `docs/plan/cards/WP-01A.md` PR3a п.1, п.7.
На момент написання PR1c у гілці `wp/01a-3a-queue-outbox-preflight` ще немає комітів, тож PR1c
написано проти **задокументованого** інтерфейсу. Нових таблиць/колонок PR1c не потребує.

### 7.1. Як runtime викликає PR3a (`src/collector/workers/backends.py`)

До rebase виклики типізовані через `cast` до Protocol-ів `_Pr3a*` (без `# type: ignore`); після
rebase `cast` прибирається, а тести з маркером `NEEDS_PR3A` (`xfail(strict=True)`) почнуть давати
XPASS і червоніти, доки маркер не знято.

| Виклик runtime | Очікувана сигнатура PR3a | Для чого |
|---|---|---|
| `queue.retry(session, job_id, owner, *, error_code, error_message, policy=IMMEDIATE_POLICY, not_before=<t>, now=)` | `retry(..., policy: BackoffPolicy \| None = None, not_before: datetime \| None = None, ...)` | retry з нижньою межею / табличним розкладом |
| `queue.release(session, job_id, owner, *, not_before=<until>, now=)` | `release(job_id, owner, *, not_before: datetime \| None = None, now=None)` | `TaskResult.deferred` |
| `retry_projection_task(..., policy=IMMEDIATE_POLICY, not_before=<t>, now=)` | те саме, що `queue.retry` | retry projection task |
| `release_projection_task(session, task_id, owner, *, not_before=<until>, now=)` | те саме, що `queue.release` | defer projection task |
| `acknowledge_projection(session, task_id, receipt, *, event=, owner=<instance>, now=)` | `acknowledge_projection(..., owner: str \| None = None)` | ack у report-транзакції з fencing |

Коли `not_before` не задано (retry без розкладу і без нижньої межі, плановий drain-`release`),
runtime викликає репозиторій **без** нових аргументів — поведінка до PR1c, і вона працює вже зараз.

### 7.2. Семантика, на яку спирається runtime (прохання зафіксувати в docstring PR3a)

1. **`retry(not_before=t)`**: runtime сам обчислює `t = max(now + затримка, нижня межа handler-а)`
   і передає `policy=IMMEDIATE_POLICY` (`BackoffPolicy(base=0, jitter_ratio=0)`). Обидві семантики,
   дозволені карткою PR3a п.1 («`max(now + backoff, not_before)`» або «саме `not_before`»), дають
   тоді рівно `t` — прохання не додавати третьої (наприклад, `not_before + backoff`).
   `attempt >= max_attempts` → карантин + dead letter **незалежно** від `not_before`.
2. **`release(not_before=until)`**: `not_before = until` (runtime уже обрізав `until < now` до `now`
   і стелю `COLLECTOR_WORKER_MAX_DEFER_SECONDS`), `attempt = GREATEST(attempt - 1, 0)`, без полів
   помилки, без dead letter — як поточний `release`.
3. **`acknowledge_projection(owner=...)`**: перевірка lease — **до** будь-якого запису (ack, confirmed
   version, change event, outbox); чужий/відсутній lease → `LeaseNotOwnedError`. Runtime трактує її
   як `worker.lease_lost` (тест `test_lost_lease_blocks_the_ack_and_the_next_owner_acks_exactly_once`).

Розбіжність сигнатур чи семантики після злиття PR3a — WP-01D правка в `backends.py` (власний файл),
без змін у WP-01A.

### 7.3. Побажання (низький пріоритет, не блокує PR1c)

`quarantine_projection_task(session, task_id, *, error_code, ...)` — операторський виклик без
перевірки lease. Worker-у потрібен карантин **власником**: зараз `ProjectionTasksBackend.quarantine`
спершу викликає `heartbeat_projection_task(owner)` у тій самій транзакції (row lock +
`LeaseNotOwnedError` для чужого lease), потім `quarantine_projection_task`. Працює коректно (тест
`test_projection_quarantine_is_fenced_by_the_lease_owner`), але чистіше мати
`quarantine_projection_task(..., owner: str | None = None)` з `_lock_owned`, як у `queue.quarantine`.
Якщо PR3a це додасть — backend перейде на нього без зміни поведінки.
