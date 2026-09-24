# Worker runtime: ролі, lease, fencing, drain і як додати handler

Документ описує runtime-каркас, який дає WP-01D: `collector worker <role>` і
`collector scheduler`. Доменна робота (fetch, discovery, translation, projection) сюди не
входить — вона підключається через `TaskHandler`.

Розділи ТЗ: §7.5 (Docker, SIGTERM/SIGKILL, healthcheck), §7.6 (pools, масштабування),
§15 (stateless worker), §9.3 (ідемпотентність), §13 (секрети й ролі БД).

Схема `worker_pools`/`worker_instances`/`scale_commands` і ролі БД — `docs/persistence/postgres.md`
(розділи 3, 9); цей документ її не дублює, лише посилається. Обґрунтування часового self-fencing
і відокремлення liveness від readiness — `docs/decisions/0006-worker-lease-fencing-and-liveness.md`.
Операційні дії (діагностика, безпечна зупинка, kill -9, placeholder-відкат) —
`docs/runbooks/worker-recovery.md`.

## 1. Ролі та їхні defaults (§7.6)

| Role | Робота | Default replicas × concurrency | Особливості |
|---|---|---:|---|
| `discovery` | RSS/sitemap/API pagination → fetch jobs | 1 × 4 | singleton per source/run через lease |
| `fetch` | HTTP GET → raw artifact | 2 × 8 | global origin limiter має верховенство |
| `browser` | anonymous JS rendering | 0 × 1 | окремий image, CPU/RAM budget |
| `parse` | raw → normalized artifact | 2 × CPU | без network egress |
| `projector` | projection task → Mongo + ack | 1 × 8 | одна транзакція на task/entity |
| `translation` | segments → український переклад | 1 × 4 | character/cost budget |
| `export` | dataset release parts/manifests | 1 × 2 | immutable output |
| `maintenance` | reconcile, compaction, sweeps | 1 × 1 | взаємовиключні named leases |

Джерело істини для `desired_replicas`/`desired_concurrency` — таблиця `worker_pools`, а не
код і не env. `collector.workers.roles.DEFAULT_POOL_SPECS` використовується **лише** тоді, коли
рядка pool ще немає (перший boot на чистій БД).

## 2. Життєвий цикл процесу

```text
boot → login check (§13) → register(starting) → readiness → ready ⇄ claim / handle / heartbeat
                                          │
                       SIGTERM / drain barrier ▼
                                       draining → активні tasks дотягуються в межах
                                       stop_grace_period → stopped → exit 0
```

- **login check:** перший запит процесу — `collector.workers.login.verify_component_login`:
  з'єднання має бути LOGIN-роллю цього компонента без зайвих прав (розділ 7.1). Інакше
  `RoleLoginError` ще до bootstrap pool, реєстрації і claim, і CLI завершується з exit 1.
- **boot:** `worker_instance_id` — UUIDv7, згенерований у пам'яті; рядок у `worker_instances`
  містить role, version, deployment/hostname/container metadata і `pool_revision`.
- **readiness:** `SELECT 1` + `TaskHandler.check_ready()`. Перехід у `ready` повторюється з
  backoff; якщо база так і не підтвердила — процес завершується з ненульовим кодом, і
  оркестратор перезапускає репліку (краще, ніж «живий, але німий» worker).
- **claim:** не більше `min(desired_concurrency, стеля процесу) − активні` jobs за раз через
  `queue.claim` (`FOR UPDATE SKIP LOCKED`).
- **heartbeat:** один тік оновлює `worker_instances` і продовжує lease **усіх** активних jobs;
  інтервал планується від дедлайну, тому тривалість тіку не накопичує дрейф.
- **drain:** SIGTERM → `draining`, нові claim заборонені, активні дотягуються; ті, що не
  вклались у `stop_grace_period`, скасовуються, а їхні lease повертаються в чергу через
  `queue.release`: job одразу `pending`, `attempt` не змінюється (release компенсує інкремент
  claim), полів помилки й dead letter немає — плановий drain нікого не провалив, тож і job на
  останній спробі не йде в карантин.
- **SIGKILL:** fault case. Lease лишається простроченим, і його повертає
  `recover_expired_leases` (тік scheduler-а).

## 3. Lease і self-fencing

Кожна claim-нута job має `lease_owner = worker_instance_id` і `lease_expires_at`. Три правила,
на яких тримається «рівно один виконавець»:

1. **heartbeat продовжує лише свій lease.** Репозиторій оновлює рядок за предикатом owner-а;
   чужий або прострочений lease → `LeaseNotOwnedError`, і runtime негайно скасовує локальний
   task (job уже може виконувати інший instance).
2. **self-fencing за часом, а не за помилкою.** Окремий сторож порівнює `monotonic()` із
   моментом останнього **підтвердженого базою** heartbeat. Якщо минуло більше за
   `fence_after` (типово ½ lease TTL), runtime скасовує всі активні tasks, не звітує за ними
   `complete` і не бере нових, поки heartbeat не підтвердиться. Це працює і тоді, коли база
   не помиляється, а **зависає** (мережевий поділ, failover).
3. **таймаути драйвера.** `command_timeout` (asyncpg) і `statement_timeout` (кожна транзакція
   runtime) дають нижню межу: запит не може пережити вікно fencing.

Наслідок для домену: подвійне виконання можливе лише у вузькому вікні, і §9.3 (ідемпотентність
за ключем) лишається обов'язковою — fencing зменшує ймовірність, а не скасовує вимогу.

## 4. Healthcheck: liveness vs readiness

- **liveness** (Docker healthcheck): процес сам оновлює mtime маркера в tmpfs
  (`$TMPDIR/collector-runtime.alive`), проба читає лише mtime — без старту Python і без
  запиту в БД. Маркер оновлює сторож lease, тому заблокований event loop (синхронний
  `handle`, дедлок) робить контейнер unhealthy.
- **readiness** (залежності): `depends_on` у Compose (`postgres: service_healthy`,
  one-shots `service_completed_successfully`) плюс `worker_instances.status` і
  `last_heartbeat_at`, які бачить оператор на екрані Workers.

Недоступна база **не** робить worker-контейнер unhealthy: рестарт цього не лікує, а fencing
уже зупинив claim.

## 5. Контракт `TaskHandler`

```python
from collector.workers.handlers import Task, TaskHandler, TaskResult


class FetchHandler(TaskHandler):
    @property
    def job_types(self) -> tuple[str, ...]:
        return ("fetch.http",)  # типи jobs, які claim-ить ця роль

    async def check_ready(self) -> None: ...  # readiness-перевірка залежностей ролі (необов'язково)

    async def handle(self, task: Task) -> TaskResult:
        ...
        return TaskResult.success()
```

Правила:

1. **`handle` — тільки `async def`, і він не має блокувати event loop.** CPU-bound роботу
   (парсинг великого HTML, regex по body, розпакування) виносьте в
   `await asyncio.to_thread(...)` або в executor. Синхронний `handle` одночасно зупиняє
   heartbeat (lease спливає), fencing (нікому рахувати час) і drain (task не скасовується).
   Runtime перевіряє це на boot (`check_handler_contract`) і логує `worker.event_loop_stalled`,
   якщо сторож прокидається пізно.
2. **Handler не пише статус job-и.** `complete`/`retry`/`quarantine` записує runtime у своїй
   транзакції за поверненим `TaskResult`.
3. **Помилки:** будь-який виняток → retry з кодом `handler_error`; `PermanentTaskError` →
   карантин + dead letter. Тексти винятків проходять `redact()` (credentials у URL,
   `token/api_key/password/...`), але класти секрети в повідомлення все одно не можна (§13).
4. **Жодного стану на локальному диску** (§15): усе, що має пережити рестарт, — у PostgreSQL,
   Mongo або object storage. Єдиний файл, який пише сам runtime, — маркер liveness у tmpfs.
5. **Скасовуваність:** `handle` має коректно реагувати на `asyncio.CancelledError` (drain-timeout,
   fencing) — закривати з'єднання і не залишати часткових зовнішніх ефектів без ідемпотентного
   ключа.

### Як додати handler

1. Реалізуйте `TaskHandler` у своєму WP (наприклад `collector/fetch/handler.py`).
2. Зареєструйте фабрику в реєстрі при імпорті модуля своєї ролі:

   ```python
   from collector.workers.handlers import HANDLER_FACTORIES
   from collector.workers.roles import WorkerRole

   HANDLER_FACTORIES[WorkerRole.FETCH] = lambda role: FetchHandler()
   ```

3. Ролі без запису працюють на `NoopHandler` (claim-ить `job_type == role`, одразу успіх) —
   контейнер не падає, pool просто порожній.

## 6. Scheduler (singleton)

`collector scheduler` бере session-scoped advisory lock у PostgreSQL. Другий процес не падає і
не стає активним — він чекає; втрата lock-а (kill, failover) негайно зупиняє планування до
нового `try_acquire`. Дефолтний тік — `recover_expired_leases` + `mark_stale_instances`.

**Тік мусить бути ідемпотентним.** Перевірка lease і сам тік ідуть різними з'єднаннями, тому
теоретичне перекриття двох тіків можливе; доменне планування зобов'язане мати власний ключ
ідемпотентності (§9.3 п.3), а не покладатися на lease. Це залишковий ризик, прийнятий свідомо —
обґрунтування і owner подальшого закриття: `docs/decisions/0006-worker-lease-fencing-and-liveness.md`
(«Residual risks», п. 1).

## 7. Конфігурація (env)

| Змінна | Типово | Призначення |
|---|---|---|
| `COLLECTOR_POSTGRES_DSN[_FILE]` | — | DSN **власної** LOGIN-ролі компонента (розділ 7.1) |
| `COLLECTOR_WORKER_LEASE_SECONDS` | `60` | TTL lease job-и |
| `COLLECTOR_WORKER_HEARTBEAT_SECONDS` | `20` | період heartbeat (≤ ⅓ lease) |
| `COLLECTOR_WORKER_FENCE_AFTER_SECONDS` | ½ lease | вікно до self-fencing |
| `COLLECTOR_WORKER_MAX_CONCURRENCY` | default ролі | стеля слотів = розмір pool з'єднань |
| `COLLECTOR_WORKER_STOP_GRACE_SECONDS` | `90` | бюджет drain (менший за Compose grace) |
| `COLLECTOR_WORKER_POLL_SECONDS` | `1.0` | пауза claim-loop |
| `COLLECTOR_WORKER_CLAIM_BATCH` | `8` | максимум jobs за один claim |
| `COLLECTOR_WORKER_LIVENESS_FILE` | `$TMPDIR/collector-runtime.alive` | маркер liveness |
| `COLLECTOR_WORKER_DEPLOYMENT` | `compose` | metadata `worker_instances.deployment` |
| `COLLECTOR_CONTAINER_ID` | `$HOSTNAME` (Docker короткий id контейнера) | metadata `worker_instances.container_id` |
| `COLLECTOR_WORKER_PLACEHOLDER` | `0` | rollback до placeholder-процесу WP-00 |

Scheduler:

| Змінна | Типово | Призначення |
|---|---:|---|
| `COLLECTOR_SCHEDULER_TICK_SECONDS` | `5` | пауза між maintenance-тіками активного scheduler-а |
| `COLLECTOR_SCHEDULER_LEASE_RETRY_SECONDS` | `5` | пауза перед повторною спробою взяти advisory lease |
| `COLLECTOR_SCHEDULER_LEASE_NAME` | `scheduler` | ім'я advisory lease (`controller` PR3 — інше ім'я, той самий примітив) |
| `COLLECTOR_SCHEDULER_STALE_AFTER_SECONDS` | `60` | TTL heartbeat, після якого `mark_stale_instances` позначає instance `stale` |
| `COLLECTOR_SCHEDULER_RECOVER_LIMIT` | `1000` | максимум leases за один прохід `recover_expired_leases` |

### 7.1. Ролі БД (§13)

Кожен runtime-процес ходить у PostgreSQL власною LOGIN-роллю і монтує лише свій Docker secret
`postgres_dsn_<component>` як `COLLECTOR_POSTGRES_DSN_FILE`. Паролі ролям виставляє
`collector db roles --with-login` в one-shot `migrate-postgres`; спільний міграційний
`postgres_dsn` runtime не монтує (тест `tests/unit/test_compose_config.py::
test_runtime_services_use_only_their_own_login_dsn_13`).

| Процес | Роль БД | Secret |
|---|---|---|
| `scheduler`, `maintenance-worker` | `collector_scheduler` | `postgres_dsn_scheduler` |
| `discovery-`, `fetch-`, `browser-worker` | `collector_fetcher` | `postgres_dsn_fetcher` |
| `parse-worker` | `collector_parser` | `postgres_dsn_parser` |
| `projector-worker` | `collector_projector` | `postgres_dsn_projector` |
| `translation-worker` | `collector_translation` | `postgres_dsn_translation` |
| `export-worker` | `collector_scheduler` (тимчасово) | `postgres_dsn_scheduler` |

`export-worker` під `collector_scheduler` — тимчасове рішення: runtime-черга пише
`worker_instances`/`crawl_jobs`/`audit_log`, а read-only `collector_export_ro` цього не може;
доменне читання даних експортом піде окремим `collector_export_ro`-з'єднанням (ризик у картці
WP-01D). Мапінг у коді — `collector.workers.roles.DB_ROLE_BY_WORKER_ROLE`.

При старті процес перевіряє (`verify_runtime_login` WP-01A + збіг із мапінгом): не superuser,
не член `collector_migrate` чи привілейованих вбудованих ролей, і роль саме цього компонента.
Відмова — `role login: …` у stderr (без DSN і пароля) та exit 1; Docker перезапускатиме
контейнер, доки DSN не виправлено. Новий `*-worker` у Compose без власного секрету не стартує:
anchor `x-worker` DSN не містить навмисно.

## 8. Експлуатація

Покрокові рецепти (SQL, вивід, orientировочні часи) — `docs/runbooks/worker-recovery.md`. Коротко:

- **Зупинити claim окремої репліки без рестарту:** `pools_repo.mark_draining(session,
  instance_id)`; повернути — `pools_repo.mark_ready(session, instance_id)`. У PR1 немає CLI для
  цього — виклик репозиторію (як роблять adversarial-тести) або прямий SQL `UPDATE
  worker_instances SET status='draining', drain_requested_at=now() WHERE instance_id=...`
  (`docs/runbooks/worker-recovery.md` §4).
- **Зупинити claim усієї ролі:** барʼєр треба виставити **кожному** живому instance ролі — це
  робить `PoolController` (WP-01D PR3); у PR1 доступний лише per-instance примітив.
- **Повернути placeholder-процес** (без перебудови image): `COLLECTOR_WORKER_PLACEHOLDER=1`.
- **Змінити concurrency без рестарту:** оновити `worker_pools.desired_concurrency` — репліки
  підхоплять на наступному heartbeat; значення понад стелю процесу буде обрізане з
  попередженням `worker.concurrency_clamped`.
- **Fenced-instance, `stale` vs `draining`, `kill -9`, «worker німий»** —
  `docs/runbooks/worker-recovery.md` §2–3, §5, §7.
