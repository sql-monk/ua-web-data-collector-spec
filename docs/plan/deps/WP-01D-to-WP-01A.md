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
