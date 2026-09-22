# WP-01A PR1 — код-рев'ю (`wp/01a-1-control-queue`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR1 «control plane, job queue, origin limiter, worker pools» |
| Branch / worktree | `wp/01a-1-control-queue` @ `9c964af` / `.worktrees/wp-01a` |
| Diff | `git diff main...HEAD` без `docs/plan/reports/**` — 54 файли, +7188/−10 |
| Картка | `docs/plan/cards/WP-01A.md` («Спільні вимоги» + PR1) |
| Звіт тестування | `docs/plan/reports/WP-01A/testing-pr1.md` (вердикт `pass`) |
| Середовище рев'ю | Windows 11, PostgreSQL 18 у Docker (`postgres:18@sha256:86c951e0…`), venv worktree |
| **Вердикт** | **`changes_requested`** (1 high) |

Питання рев'ю — «чи код правильний, безпечний для даних і не складніший, ніж потрібно».
Відповідність ТЗ не оцінюється (це пострев'ю); явні розбіжності з контрактом позначені
`spec-mismatch`.

Загальне враження: транзакційні межі продумані й задокументовані в кожному docstring, черга
(`SKIP LOCKED` + priority + `not_before`, lease/owner-предикати, dead letter рівно один раз) і
глобальний лімітер (row lock на bucket, жодного локального кешу токенів — R-53) реалізовані
коректно; перевірки нижче підтвердили це на живій БД. Знайдені дефекти зосереджені **не** в
черзі, а на периферії: CI-тест, партиціонування, стан instance, дрейф CHECK-ів.

---

## 1. Знахідки

### High

**H-1 — integration-тест падає з `TypeError` на конфігурації DSN, яку задає власний CI job**
`tests/integration/postgres/test_cli_db.py:37` | `.github/workflows/ci.yml:74-88`

- claim: рядок `assert pg_empty_database.url.password not in migrate.output` припускає, що
  admin-DSN має пароль. CI-джоб `integration-postgres` піднімає service container з
  `POSTGRES_HOST_AUTH_METHOD: trust` і задає `COLLECTOR_TEST_POSTGRES_ADMIN_DSN:
  postgresql://collector_ci@127.0.0.1:5432/postgres` — **без пароля**, тож
  `url.password is None`.
- failure scenario: `COLLECTOR_TEST_POSTGRES_ADMIN_DSN=postgresql://rev@127.0.0.1:55611/postgres`
  (точний аналог CI) → `pytest -m integration tests/integration/postgres` →
  `1 failed, 74 passed`; помилка `TypeError: 'in <string>' requires string as left operand, not
  NoneType` у `test_db_migrate_check_and_roles_from_env`. Локальні прогони тестувальника цього не
  спіймали, бо і testcontainers, і його зовнішній DSN мали згенерований пароль.
- наслідок: acceptance PR1 «усі команди зелені … в CI job `integration-postgres`» не
  виконується — job червоний із першого запуску. Додатково: у беспарольній конфігурації сама
  перевірка «пароля немає у виводі CLI» нічого не перевіряє (порівняння з `None`), тобто гарантія
  «DSN без пароля в логах» лишається без тесту саме там, де він потрібен.
- verdict: **CONFIRMED** (відтворено запуском набору).
- рекомендація: `password = pg_empty_database.url.password; assert password is None or password
  not in migrate.output` — або ставити CI на пароль/`scram-sha-256` і прибрати умовність.

### Medium

**M-1 — межі місячних партицій залежать від `TimeZone` сесії, що виконує DDL**
`src/collector/persistence/postgres/partitions.py:56-63` (`MonthPartition.create_sql`)

- claim: `FOR VALUES FROM ('2031-03-01') TO ('2031-04-01')` — це неконкретизовані date-літерали;
  для колонки `timestamptz` PostgreSQL інтерпретує їх у `TimeZone` **сесії на момент DDL** і
  зберігає вже зсунуті межі. Ні `engine.py:create_engine`, ні `migrations.env` не фіксують
  `TimeZone=UTC`, тож межі визначає server-side GUC/`PGTZ` клієнта.
- failure scenario (обидва відтворені на PostgreSQL 18):
  1. `ensure_month_partitions(2031-03)` під `TimeZone=UTC`, потім `ensure_month_partitions(2031-04)`
     під `Europe/Kyiv` → `InvalidObjectDefinitionError: partition "audit_log_y2031m04" would
     overlap partition "audit_log_y2031m03"`. Оскільки `ops.migrate_database` виконує
     `upgrade_to_head` + `ensure_month_partitions` в **одній** транзакції, падає вся команда
     `collector db migrate`, включно з міграціями.
  2. Зворотний порядок (`2032-06` під `Europe/Kyiv`, `2032-07` під `UTC`) створюється без помилки
     і лишає **діру 21:00Z–00:00Z**: `INSERT INTO audit_log … '2032-06-30T22:00:00Z'` →
     `no partition of relation "audit_log" found for row`. Фактичні межі в `pg_class`:
     `…y2032m06 FROM ('2032-05-31 21:00:00+00') TO ('2032-06-30 21:00:00+00')`,
     `…y2032m07 FROM ('2032-07-01 00:00:00+00')`.
- зараз проєкт врятований тим, що образ `postgres:18` має `timezone = Etc/UTC`; дефект спрацює
  після зміни `postgresql.conf`, `PGTZ`, або переїзду на керований інстанс з іншим дефолтом.
- verdict: **CONFIRMED**.
- рекомендація: `FOR VALUES FROM ('2031-03-01 00:00:00+00') TO (…+00)` (або `SET LOCAL TIME ZONE
  'UTC'` перед DDL) + тест на межі місяця під не-UTC сесією. Виправити **до PR2**: той самий
  helper обслуговуватиме `fetches`/`raw_objects`/`change_events`/`outbox_events`, де діра в
  партиціях означає втрату domain-подій, а не лише аудиту.

**M-2 — heartbeat «оживляє» instance у `ready`, стираючи стан `draining`**
`src/collector/persistence/postgres/repositories/pools.py:215-216` (+ `INSTANCE_TRANSITIONS:47`)

- claim: `heartbeat_instance` робить `stale → ready` беззастережно, а `mark_stale_instances`
  переводить у `stale` й instances зі статусом `draining`. Пара переходів
  `draining → stale → ready` втрачає намір оператора/контролера зупинити instance.
- failure scenario (відтворено): pool `fetch`, instance `ready` → `mark_draining` (`draining`) →
  пауза heartbeat > 60 с → `mark_stale_instances` (`stale`) → instance відновлює heartbeat →
  статус `ready`, `observed_capacity` = `ready_replicas=1, total_slots=2`. Drain, ініційований
  scale-командою, мовчки скасовано; `transition_scale_command('applied')`, що звіряє
  heartbeat-derived capacity з `requested_replicas`, після цього бачить зайву репліку і
  відхиляє `applied` (або, за іншого requested, приймає його для непорожнього pool).
- verdict: **CONFIRMED**.
- рекомендація: зберігати попередній стан (`stale` як прапорець, а не статус) або повертати
  `stale → draining`, якщо `stopped_at is NULL` і останній явний статус був `draining`.

**M-3 — CHECK-константи й предикат partial index дублюються в міграції літералами; `alembic
check` дрейф не бачить**
`migrations/postgres/versions/20260922_0001_control_queue.py:144-147, 177-182, 531-534` vs
`src/collector/persistence/postgres/models/base.py:66` (`enum_check`),
`migrations/postgres/versions/20260922_0002_claim_index.py:39` vs
`src/collector/persistence/postgres/models/queue.py:49`

- claim: моделі генерують CHECK динамічно з контрактних enum (`SourceState`, `RouteState`,
  `DataDomain`, `WorkerRole`), а міграції містять їхню копію рядком. Alembic autogenerate не
  порівнює CHECK-констрейнти і не порівнює `WHERE` partial index-ів, тому єдиний заявлений
  захист картки («`alembic check` без drift») цей клас розходжень не ловить. Unit-тест
  `tests/unit/persistence/postgres/test_metadata.py:82` звіряє enum **лише з метаданими моделі**
  (тобто з самим собою) — не з БД.
- failure scenario (відтворено): у БД після `upgrade head` замінено
  `ck_sources_state` на `CHECK (state IN ('enabled','paused'))` і предикат
  `ix_crawl_jobs_claimable_order` на `WHERE status IN ('pending')` → `alembic check` →
  `No new upgrade operations detected` (drift = `[]`), при цьому
  `INSERT … state='blocked_anonymous'` (значення чинного контракту) падає з
  `CheckViolationError`. Реальний сценарій: WP-01C додає значення до `SourceState` → моделі й
  усі тести зелені → runtime-помилка на першому джерелі з новим станом; для index — тихе
  падіння hot path `claim` на seq scan.
- verdict: **CONFIRMED**.
- рекомендація: integration-тест, що читає `pg_constraint.consrc`/`pg_get_constraintdef` і
  `pg_indexes.indexdef` з живої БД і звіряє зі значеннями `collector.contracts.enums` /
  `CLAIMABLE_JOB_STATUSES` — тоді контрактна зміна ламає CI, а не production.

**M-4 — `block_origin` не скидає стан refill: після зняття блокування origin отримує повний
burst**
`src/collector/persistence/postgres/repositories/limiter.py:125-136`

- claim: гілка `blocked` повертається **до** оновлення `last_refill_at`, тож час блокування
  повністю зараховується в накопичення токенів; `_refilled_tokens` обмежує це лише
  `capacity_tokens`.
- failure scenario (відтворено): bucket `capacity_tokens=5`, `refill=0.2/s`, токени вичерпано →
  origin повертає 429 → `block_origin(until=now+1h)` → через 1 год 1 с перші ж виклики
  `acquire_permit` видають **5 permits поспіль** (burst), тобто сайт, який щойно нас забанив,
  одразу отримує повний сплеск. Поведінка формально «як у простійного token bucket», але
  суперечить призначенню `block_origin` (§7.6/FR-033 ввічливість після 429/`Retry-After`).
- verdict: **CONFIRMED**.
- рекомендація: у `block_origin` виставляти `last_refill_at = until` (або `available_tokens = 0`
  + `last_refill_at = until`), щоб після блокування видача починалася «з нуля».

**M-5 — `audit_log` без DEFAULT-партиції: пропущене обслуговування зупиняє всі audited дії
control plane**
`migrations/…/0001_control_queue.py:69` | `src/collector/persistence/postgres/partitions.py:10-12,
91` | `.github/workflows/ci.yml:108-112`

- claim: міграція створює партиційовану таблицю **без жодної партиції** і без DEFAULT-партиції;
  партиції створює лише `collector db migrate` (на 3 місяці наперед) і maintenance WP-12.
  `alembic upgrade head` — команда перевірки з картки і перший крок CI — партицій не створює
  взагалі.
- failure scenario: (а) чистий `alembic upgrade head` → перший `append_audit` падає з
  `no partition of relation "audit_log" found for row`; (б) maintenance не працює 3+ місяці →
  те саме в production, а оскільки `request_scale` пише audit у **тій самій** транзакції
  (`pools.py:399`), жодна scale-команда/зміна desired state більше не проходить — control plane
  стає read-only без очевидної причини.
- це свідомо обране «зрозуміла помилка замість auto-create» (зафіксовано
  `test_migrations.py:118`), але для `audit_log` ціна — не втрата журналу, а відмова керуючих
  операцій.
- verdict: **CONFIRMED** (за читанням коду + тестом, що фіксує саме цю поведінку).
- рекомендація: DEFAULT-партиція `audit_log_default` у міграції (рядки не губляться, maintenance
  їх перерозподіляє) або хоча б створення партицій у самій `0001` + метрика «місяців партицій
  попереду» у §14.1.

### Low

**L-1 — `request_scale` не ідемпотентна під конкуренцією**
`pools.py:353-357`

- claim: перевірка `idempotency_key` — звичайний `SELECT` без блокування, до захоплення
  `worker_pools` `FOR UPDATE`.
- failure scenario (відтворено): два одночасні `request_scale(..., expected_revision=1,
  idempotency_key='same-key')` → один повертає команду, другий отримує
  `StaleRevisionError: revision 1 застаріла (поточна 2)` замість наявної команди (docstring
  обіцяє «повторний виклик … повертає існуючу команду без змін»). Даних це не псує: у БД
  залишається 1 команда, 1 audit-запис, `revision=2`; повтор із оновленою revision повертає
  існуючу команду. Проблема — контрактна (оператор бачить conflict на подвійному кліку).
- verdict: **CONFIRMED**.

**L-2 — сирі `IntegrityError` повз типізовані помилки репозиторіїв**
`pools.py:113-129` (`upsert_pool`, `expected_revision=None`), `sources.py:59-71` (`create_source`)

- claim: `errors.py:1` декларує «викликачі ловлять `errors.*` замість `sqlalchemy.exc.*`»;
  створення вже існуючого pool/source кидає сирий `IntegrityError` і псує транзакцію викликача.
  Аналогічна знахідка тестувальника (L-1) закрита лише для `request_scale`.
- failure scenario: GUI/CLI двічі створює pool `fetch` → `IntegrityError` на `pk_worker_pools`
  замість `ConflictError`, викликач не може відрізнити її від будь-якого іншого порушення.
- verdict: CONFIRMED (за читанням).

**L-3 — `collector db roles` віддає traceback замість типізованої помилки**
`src/collector/cli.py:169-175` (`_run_async`) | `src/collector/persistence/postgres/roles.py:41-43`

- claim: `apply_roles` виконує скрипт через **сирий** `asyncpg.Connection.execute`, тож помилки
  приходять як `asyncpg.exceptions.*`, що не є ні `SQLAlchemyError`, ні `OSError` — фільтр
  `_run_async` їх не ловить, попри власний docstring «без traceback у stderr».
- failure scenario (відтворено): `COLLECTOR_POSTGRES_DSN=…//weakuser@…` →
  `collector db roles` → повний rich-traceback на 60 рядків, який завершується
  `InsufficientPrivilegeError: permission denied to create role` (exit code 1 коректний).
  `db migrate` на тому самому DSN поводиться правильно: `postgres error: … permission denied for
  schema public`, exit 1.
- verdict: **CONFIRMED**.

**L-4 — `db migrate --check` не є read-only**
`ops.py:52-55` («check — окреме read-only з'єднання») | `cli.py` `--check` («Не застосовувати»)

- claim: `command.check` через `MigrationContext` намагається створити `alembic_version`, тобто
  вимагає `CREATE` на схемі `public`.
- failure scenario (відтворено): на порожній БД під користувачем без прав —
  `postgres error: … permission denied for schema public`, тобто моніторинговою read-only роллю
  (`collector_api_ro`) `--check` запустити не можна. На привілейованому з'єднанні побічного
  ефекту немає: DDL відкочується разом із `engine.connect()` (перевірено — 0 таблиць після
  `--check`), тож ідеться про формулювання і про роль, під якою команду можна виконувати.
- verdict: **CONFIRMED**.

**L-5 — `release_permit` не перевіряє власника**
`limiter.py:197-209`

- claim: предикат лише `permit_id AND released_at IS NULL`; `owner_instance` у таблиці є, але не
  використовується.
- failure scenario: worker із переплутаним `permit_id` (баг у власному стані, повтор із черги)
  звільняє чужий concurrency slot → origin отримує на один паралельний запит більше, ніж
  дозволяє policy. Не гонка в самій БД, але єдиний рубіж проти помилки викликача відсутній.
- verdict: PLAUSIBLE.

**L-6 — jitter додається поверх cap, тому фактичний максимум backoff перевищує `maximum`**
`queue.py:74-80`

- claim: `delay = min(base*mult**n, maximum)`, далі `+ uniform(0, delay*jitter_ratio)` → реальна
  межа `maximum * (1 + jitter_ratio)` = 7.2 год при заявлених 6 год.
- failure scenario: тест/оператор, що спирається на «не пізніше ніж через `maximum`», бачить
  `not_before` на 1.2 год далі. Детермінізм для тестів забезпечено (`rng` — параметр), дефолт
  `SystemRandom` доречний.
- verdict: CONFIRMED (за читанням).

**L-7 — `upsert_cursor` перезаписує курсор без монотонної перевірки**
`sources.py:272-285`

- claim: `ON CONFLICT DO UPDATE` беззастережно ставить нове значення; єдиний versioned-ресурс
  PR1 без `expected_revision`/`GREATEST`. Під READ COMMITTED виграє той, хто закомітив пізніше,
  навіть якщо його значення старіше.
- failure scenario: два discovery-workers на одному route (recover після expired lease —
  штатна ситуація at-least-once) пишуть watermark; курсор може відкотитися назад. Напрямок
  безпечний (повторний обхід, не пропуск), тому low, але це єдине місце, де optimistic
  `revision` не використано.
- verdict: PLAUSIBLE.

**L-8 — `enqueue` мовчки повертає термінальний job для повторно використаного ключа**
`queue.py:83-96`

- claim: `ON CONFLICT DO NOTHING` + `SELECT` повертає рядок у будь-якому статусі, зокрема
  `succeeded`/`quarantined`; docstring цього не згадує.
- failure scenario: WP-01D бере `fetch_idempotency_key` без дискримінатора вікна/циклу → після
  першого успішного обходу кожен наступний `enqueue` повертає старий `succeeded` job, і джерело
  більше ніколи не фетчиться, без жодної помилки. Контракт картки («той самий job, без дубля»)
  дотримано — бракує попередження у docstring.
- verdict: CONFIRMED (за читанням).

**L-9 — `set_instance_status` не ідемпотентна**
`pools.py:228-249`

- claim: `ready → ready`/`draining → draining` заборонені `INSTANCE_TRANSITIONS`.
- failure scenario: контролер повторює `mark_draining` після втраченої відповіді →
  `InvalidTransitionError` замість no-op; повтор доведеться гасити на боці викликача.
- verdict: CONFIRMED (за читанням).

### Informational / спрощення

- **I-1. Спільний lease-механізм.** PR1 має дві незалежні реалізації lease (черга:
  `status+lease_owner+lease_expires_at` на робочому рядку; permits: окремий рядок із
  `released_at`), PR2 додасть ще дві («`claim_projection_tasks` аналогічно crawl queue» +
  upload claim із `claim_generation`). Черга і permits справді різні за формою, але
  queue-варіант варто параметризувати (таблиця/статуси) **до** PR2, інакше `projection_tasks`
  отримає копію `claim/heartbeat/complete/retry/recover` на ~200 рядків.
- **I-2. Дрібне дублювання:** `_raise_stale_or_missing` (`sources.py:293`) і
  `_raise_stale_or_missing_pool` (`pools.py:501`) — один і той самий код на два ресурси;
  `EXPECTED_TABLES`/`ALLOWED_JSONB` продубльовані в `tests/unit/.../test_metadata.py` і
  `tests/integration/.../test_migrations.py`.
- **I-3. Тривалість транзакції викликача нічим не обмежена.** І `claim` (row locks на jobs), і
  `acquire_permit` (exclusive lock на рядок bucket) тримають блокування до commit викликача.
  Це коректно задокументовано в docstring, але якщо WP-01D виконає HTTP-запит, не закривши
  транзакцію, **весь** origin стане на час фетчу. Варто або повертати permit уже закоміченим
  (репозиторій володіє власною короткою транзакцією), або додати регресійний тест-вартовий.
- **I-4. `ix_crawl_jobs_status_not_before_priority` + `ix_crawl_jobs_claimable_order`** —
  дублювання уявне, а не фактичне: `EXPLAIN` на 200 000 pending jobs показує
  `Index Scan using ix_crawl_jobs_claimable_order` з `Index Cond: (not_before <= now())`, тобто
  новий partial index справді обслуговує hot path, а обов'язковий за карткою лишається для
  вибірок за `(status, not_before)`. Предикат `status IN ('pending','retry')` точно збігається з
  `CLAIMABLE_JOB_STATUSES` (ризик розходження — у M-3).

Знахідок `critical` немає. `spec-mismatch` для пострев'ю не виявлено.

---

## 2. Що перевірено окремо

**Транзакції та ізоляція.** Прочитано межу транзакції кожної публічної операції. Усі
read-modify-write ходять під row lock (`claim` — `FOR UPDATE SKIP LOCKED`; `retry`/`quarantine` —
`SELECT … FOR UPDATE` того самого рядка; `acquire_permit`/`block_origin` — `FOR UPDATE` на
bucket; `request_scale`/`add_policy_version` — `FOR UPDATE` з предикатом `revision`), тож
lost update у `worker_pools`/`origin_rate_buckets` не відтворюється: під READ COMMITTED друга
транзакція після зняття блокування перечитує рядок, предикат `revision = :expected` не
виконується і вона отримує `StaleRevisionError`. `UPDATE … WHERE revision = :expected`
(`set_source_state`, `set_route_state`, `upsert_pool`) — той самий ефект через EPQ-перевірку.
`GREATEST`/монотонних лічильників у PR1 немає (вони в PR2 — `confirmed_projection_version`),
тож перевіряти нічого. Advisory locks не використовуються і не потрібні: кожна операція має
природний рядок-власник.

**Черга.** `claim` сортує `priority DESC, not_before, job_id`, фільтрує
`status IN ('pending','retry') AND not_before <= now`; `RETURNING` додатково сортується в Python
(коректно — порядок `RETURNING` не гарантований). Job не може «зависнути» leased назавжди:
`lease_expires_at` виставляється **у момент claim**, а не першим heartbeat, тож смерть worker до
першого heartbeat усе одно призводить до `recover_expired_leases`. Гонка recovery vs активний
heartbeat безпечна в обидва боки: recover бере `FOR UPDATE SKIP LOCKED` (пропускає рядок, який
саме heartbeat-иться), а якщо heartbeat уже закомічений — EPQ-перевірка `lease_expires_at <= now`
виключає рядок; якщо ж recover був першим, heartbeat падає на предикаті `status='leased' AND
lease_owner=:owner`. `attempt` при recovery зберігається і збільшується лише наступним claim, тож
цикл «crash → recover → claim» гарантовано вичерпує `max_attempts`. Dead letter рівно один раз
захищено тим, що і `retry`, і `quarantine` вимагають нетермінального статусу під блокуванням
(підтверджено `test_adversarial.py::test_retry_after_max_attempts_writes_exactly_one_dead_letter`).
Backoff детермінований при переданому `rng`; єдина претензія — L-6.

**Limiter (R-53).** Локального кешу токенів немає: єдине джерело істини — рядок
`origin_rate_buckets` під `FOR UPDATE`, тож усі репліки серіалізуються. Rate token і concurrency
slot справді незалежні (відмова через concurrency не списує токен; `test_expiry_frees_
concurrency_slot_even_after_rate_token_spent` перевіряє зворотний випадок). Подвійне звільнення
слота неможливе: і `release_permit`, і `expire_permits` мають предикат `released_at IS NULL`, а
live-лічильник і так ігнорує прострочені permits. `block_origin` має пріоритет — перевіряється
першим, до refill (M-4 — про побічний ефект цього порядку). Математику refill перевірено на
живій БД: довга пауза дає рівно `capacity_tokens` (burst cap тримається); дрейф від квантування
`0.0001` з `ROUND_DOWN` мізерний — 500 викликів за 5 с при 0.2 rps дали 0.9858 замість 1.0
(≈1.4 %), у знахідки не виніс. `CHECK refill_per_second > 0` закриває ділення на нуль у гілці
`reason='rate'`.

**Міграції.** `upgrade head` → `check` → `downgrade base` → `upgrade head` відпрацьовує на чистій
БД (тест + власний прогін); `downgrade` реалізовано повністю і в зворотному порядку залежностей,
циклічний FK `sources ↔ source_policy_versions` знімається окремо. Типи: усі timestamps —
`timestamptz`, усі PK — UUID (крім natural `worker_pools.role`/`origin_rate_buckets.origin`),
JSONB лише `crawl_jobs.args` (+ `CHECK octet_length ≤ 8 KiB`) і `audit_log.before/after_state` —
R-27 дотримано; грошових колонок у PR1 немає, тож `amount_minor BIGINT` перевіряти нічого.
Усі шість обов'язкових index-ів картки та FR-002 partial unique присутні. `0002_claim_index`
обов'язковий index не дублює (I-4). Партиціонування `audit_log` — M-1 і M-5.

**Ролі/GRANT.** GRANT мінімальні й пооб'єктні; послідовностей у схемі немає (усі PK — UUID
застосунку), тож зайвих `GRANT … ON SEQUENCE` бути не може; GRANT на функції теж немає —
`audit_log_append_only()` належить `collector_migrate` і викликається тригером. `alembic_version`
не згадана в жодному GRANT. Паролів у SQL немає: усі ролі `NOLOGIN` group roles, login-користувачі
— зона відповідальності оператора (задокументовано в шапці `roles.sql`). `db roles` ідемпотентна
(guard на `pg_roles`, повторні GRANT/ALTER OWNER безпечні) — прогнав двічі поспіль на чистій БД.
Ownership-блок коректно самоусувається, якщо поточний користувач не член `collector_migrate`.
Дочірні партиції окремих GRANT не потребують (перевірка прав іде по батьківській таблиці).

**CLI.** `db migrate --check`: exit 1 + «schema drift» на БД не на head і на drift, exit 0 +
`schema up to date: revision=…` інакше; після non-check upgrade drift теж перевіряється і дає
exit 1 — семантика правильна (обмеження — L-4). Помилка з'єднання: `postgres error: [WinError
1225] …`, exit 1, без traceback, DSN не друкується (`secret_pw` у DSN не потрапив у вивід);
відсутній DSN — `postgres config: не задано COLLECTOR_POSTGRES_DSN`, exit 1. Усі повідомлення
успіху використовують `settings.redacted_dsn`. Виняток — L-3.

**Запуски (read-only).**

```text
pytest -m integration tests/integration/postgres  (external DSN, PostgreSQL 18 у Docker)
  → 1 failed, 74 passed in 151.22s      # єдиний fail — H-1
EXPLAIN claim на 200 000 pending jobs  → Index Scan using ix_crawl_jobs_claimable_order
alembic check після підміни ck_sources_state + предиката claim-index → drift не виявлено (M-3)
ensure_month_partitions під UTC/Europe/Kyiv → overlap-помилка і діра між партиціями (M-1)
collector db roles під непривілейованим користувачем → traceback (L-3)
```

Тимчасовий контейнер `wp01a-review-pg` видалено; у worktree нічого не змінено, окрім цього звіту
(`git status --short` чистий).

---

## 3. Вердикт

**`changes_requested`** — через H-1 (CI job `integration-postgres` червоний на власній
конфігурації PR).

Блокує merge: **H-1**. Настійно рекомендую разом із ним закрити **M-1** (одна зміна рядка + тест;
до PR2 helper успадкують таблиці, де діра в партиціях коштує втрати подій), **M-3** (тест,
який ловить розходження DB ↔ контракт) і **M-2**. M-4/M-5 і всі low можна винести окремими
задачами до PR2, якщо зафіксувати їх у картці.

Знахідки: 1 high, 5 medium, 9 low, 4 informational.
