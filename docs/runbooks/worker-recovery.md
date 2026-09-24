# Runbook: worker/scheduler recovery (Docker Compose, single host)

Мета: діагностика й безпечні операційні дії над `collector worker <role>`/`collector
scheduler` (WP-01D PR1, `docs/workers.md`) — побачити стан pools/instances, відрізнити `stale`
від `draining`, безпечно зупинити worker, зрозуміти, що відбувається при `kill -9`, тимчасово
відкотитись на placeholder-процес і діагностувати worker, який не бере jobs. Схема
`worker_pools`/`worker_instances` — `docs/persistence/postgres.md` розділи 3 і 9 (тут не
дублюється). Масштабування (`replicas`, `PoolController`, role-wide drain barrier, Compose/Swarm
adapters) — WP-01D PR3, поза цим runbook.

## Контекст і обмеження

- У PR1 немає CLI-команди для ручних `mark_draining`/`mark_ready` — це функції репозиторію
  (`collector.persistence.postgres.repositories.pools`), які викликає сам runtime і, з PR3,
  `PoolController`. До появи адмін-CLI (WP-01D PR3) той самий перехід доступний оператору лише
  прямим SQL `UPDATE worker_instances` (розділ 4 нижче) — **operationally unverified** шляхом
  прямого SQL (тестований шлях — виклик функції репозиторію, `tests/integration/scaling/test_worker_runtime_adversarial.py`), тому перевіряйте результат (розділ 1) після кожної такої дії.
- Найнадійніший спосіб зупинити конкретну репліку — SIGTERM через `docker compose stop`
  (розділ 3): це той самий шлях, що й штатний scale-down/redeploy, і єдиний, перевірений і в
  тестах, і на реальному стеку (`docs/plan/reports/WP-01D/{implementation,testing}-pr1.md`).
- Команди нижче припускають `docker compose` без `-p`/`-f` override (типовий `docker-compose.yml`
  у корені); підставте свій `-p <project>` за потреби ізоляції (детальніше —
  `docs/runbooks/clean-host-start.md`).

## 1. Побачити стан pools/instances

Зведення по ролях (defaults §7.6, `docs/workers.md` §1):

```bash
docker compose exec -T postgres psql -U "${POSTGRES_USER:-collector}" -d "${POSTGRES_DB:-collector}" -c "
  SELECT role, status, count(*) AS instances, sum(slots_total) AS slots
  FROM worker_instances
  GROUP BY role, status
  ORDER BY role, status;"
```

Один instance детально (heartbeat, drain-намір, pool revision):

```bash
docker compose exec -T postgres psql -U "${POSTGRES_USER:-collector}" -d "${POSTGRES_DB:-collector}" -c "
  SELECT instance_id, role, status, hostname, container_id, version,
         slots_total, slots_active, active_leases, pool_revision,
         drain_requested_at, started_at, last_heartbeat_at, stopped_at
  FROM worker_instances
  WHERE role = 'fetch'
  ORDER BY started_at DESC
  LIMIT 5;"
```

Desired state ролі (джерело істини для `desired_replicas`/`desired_concurrency`):

```bash
docker compose exec -T postgres psql -U "${POSTGRES_USER:-collector}" -d "${POSTGRES_DB:-collector}" -c "
  SELECT role, desired_replicas, desired_concurrency, min_replicas, max_replicas, mode, revision
  FROM worker_pools
  ORDER BY role;"
```

Логи одного контейнера (структуровані JSON-рядки, `worker.*`/`scheduler.*` події):

```bash
docker compose logs --tail 50 fetch-worker
docker compose logs -f fetch-worker | grep -E '"event": "worker\.(fenced|unfenced|lease_lost|drain_timeout|status)"'
```

## 2. `stale` проти `draining`

| Статус | Що означає | Як побачити причину |
|---|---|---|
| `draining` | Instance сам собі (або йому) поставив `drain_requested_at`: активні tasks дотягуються, нові claim не беруться, після завершення переходить у `stopped` | `drain_requested_at IS NOT NULL`; логи `worker.status status=draining`, `worker.drain_barrier active=true` |
| `stale` | Instance **не надсилав heartbeat** довше `COLLECTOR_SCHEDULER_STALE_AFTER_SECONDS` (типово 60 с) — сам процес міг уже не існувати (SIGKILL, OOM, зникнення контейнера) або лишався живий, але фенснутий і не зміг записати heartbeat | `last_heartbeat_at < now() - interval '60 seconds'`; перевірте, чи контейнер ще запущений (`docker compose ps <service>`) — якщо так, дивіться `worker.fenced`/`worker.heartbeat_failed` у логах |

`stale` — не команда і не намір, а **діагноз** scheduler-а (`mark_stale_instances`, тік
`collector scheduler`). Instance може повернутись із `stale` у `ready` сам, щойно heartbeat
знову підтвердиться (`heartbeat_instance`: `stale → ready`, або `stale → draining`, якщо
`drain_requested_at` уже було виставлено до паузи — намір drain переживає `stale`, стирає його
лише явний `mark_ready`). Якщо `stale`-рядок ніколи не оживає — контейнер справді мертвий;
рядок лишається історичним доказом (нове завантаження того самого контейнера отримує **новий**
`instance_id`, §15 — `docs/workers.md` §2).

## 3. Fenced-instance: що робити

`worker.fenced` у логах (`reason="lease not confirmed by database"`) означає, що instance
довше за `fence_after` (типово половина lease TTL) не отримав підтвердженого heartbeat і сам
скасував усі активні tasks, не звітуючи за них `complete` (`docs/workers.md` §3,
`docs/decisions/0006-worker-lease-fencing-and-liveness.md`). Це не помилка, яку треба «лагодити»
руками — instance сам вийде з fenced-стану (`worker.unfenced`), щойно heartbeat знову
підтвердиться, і продовжить claim-ити нові jobs.

Дії:

1. **Перевірте PostgreSQL**, а не instance: fencing — симптом, причина — недоступність або
   перевантаженість БД (`docker compose logs postgres`, `docker compose exec -T postgres
   pg_isready`).
2. **Не рестартуйте worker-контейнер через fenced-стан** — рестарт нічого не лікує (проблема на
   боці БД), а liveness-проба контейнера вже показувала б `healthy` увесь цей час: fencing
   спеціально не завʼязаний на healthcheck (`docs/decisions/0006-*.md`, «Consequences»).
   Перевіряйте `worker_instances.status`/`last_heartbeat_at`, а не колонку `STATUS` у `docker
   compose ps`.
3. **Скасовані jobs самі повертаються в чергу.** Fencing нічого не пише в `crawl_jobs` (їх або
   вже перехопив інший instance через `recover_expired_leases`, або лишається чекати lease
   expiry) — нічого додатково повертати не треба.
4. Якщо `worker.fenced`/`worker.unfenced` чергуються часто (flapping) — це ознака нестабільної
   мережі до PostgreSQL або надто короткого `fence_after` відносно реальної латентності; підняти
   `COLLECTOR_WORKER_FENCE_AFTER_SECONDS` (у межах `(0, lease_seconds]`) або
   `COLLECTOR_WORKER_LEASE_SECONDS`.

## 4. Безпечно зупинити одну репліку без рестарту (SIGTERM)

Рекомендований шлях — SIGTERM (той самий, що й `down`/scale-down/redeploy):

```bash
docker compose stop fetch-worker   # усі репліки сервісу; додайте `--timeout <с>` за потреби
```

Що відбувається (перевірено на реальному стеку,
`docs/plan/reports/WP-01D/implementation-pr1.md` §«SIGTERM → drain → exit 0»):

1. `worker.stop_requested` — процес отримав SIGTERM;
2. `status=draining` — claim зупинено, активні tasks дотягуються;
3. активні tasks завершуються (або скасовуються після
   `COLLECTOR_WORKER_STOP_GRACE_SECONDS`, типово 90 с — менше за Compose
   `stop_grace_period: 120s`, щоб встигнути повернути leases до SIGKILL);
4. lease незавершених tasks повертаються в чергу через `queue.release` (`worker.lease_released`):
   job одразу `pending`, `attempt` не змінюється, полів помилки й dead letter немає — і для job-и
   на останній спробі теж (`docs/workers.md` §2; WP-01D PR1b);
5. `status=stopped`, процес виходить з кодом 0.

Зупинка займає стільки, скільки треба на дотягування активних tasks (спостережено ~2–4 с при
порожній черзі, `implementation-pr1.md`/`testing-pr1.md`), а не весь бюджет grace period.

Точковий барʼєр без зупинки контейнера (лише цей instance перестає claim-ити, лишаючись
`ready`-схожим процесом, який тримає heartbeat) — прямий SQL, поки немає CLI (розділ «Контекст»
вище):

```bash
docker compose exec -T postgres psql -U "${POSTGRES_USER:-collector}" -d "${POSTGRES_DB:-collector}" -c "
  UPDATE worker_instances
  SET status = 'draining', drain_requested_at = now(), updated_at = now()
  WHERE instance_id = '<instance-uuid>' AND status IN ('starting','ready');"
```

Повернути в роботу — `status = 'ready'`, `drain_requested_at = NULL` (лише з `draining`/`stale`,
`INSTANCE_TRANSITIONS` у `repositories/pools.py`):

```bash
docker compose exec -T postgres psql -U "${POSTGRES_USER:-collector}" -d "${POSTGRES_DB:-collector}" -c "
  UPDATE worker_instances
  SET status = 'ready', drain_requested_at = NULL, updated_at = now()
  WHERE instance_id = '<instance-uuid>' AND status IN ('draining','stale');"
```

Це **не** масштабує роль і не зупиняє інші instances того самого сервісу — «role-wide» барʼєр
(усі instances ролі одночасно) належить `PoolController` (WP-01D PR3).

## 5. `kill -9`: що відбувається і коли task повернеться

`docker kill --signal=SIGKILL <container>` (або OOM-killer, або зникнення хоста) — навмисно
**не** перехоплюється runtime (§7.5: «SIGKILL є fault case з lease recovery»). Процес зникає
миттєво, нічого не пише в `worker_instances`/`crawl_jobs`. Послідовність відновлення
(спостережена на реальному стеку, `implementation-pr1.md`/`testing-pr1.md`):

1. `docker inspect <container> --format 'ExitCode={{.State.ExitCode}}'` → `137`;
2. instance лишається `ready`/`draining` у БД (heartbeat просто перестає приходити);
3. через `COLLECTOR_SCHEDULER_STALE_AFTER_SECONDS` (типово 60 с) `collector scheduler`
   позначає рядок `stale` (`mark_stale_instances`, тік кожні `COLLECTOR_SCHEDULER_TICK_SECONDS`,
   типово 5 с);
4. незалежно від цього, щойно `lease_expires_at` job-и (яку instance тримав) минає (типово 60 с
   від claim/останнього heartbeat), `recover_expired_leases` повертає її в `pending` — **це і є
   момент, коли task «повертається»**, не момент позначення `stale`; за наявності іншого
   instance тієї самої ролі job зазвичай підхоплюється на найближчому claim-циклі
   (`COLLECTOR_WORKER_POLL_SECONDS`, типово 1 с);
5. якщо той самий контейнер запускається знову (`docker start <container>` або оркестратор
   перестворює його) — це **новий boot**: новий UUIDv7 `instance_id`, старий рядок лишається
   `stale` як історичний доказ (§15 — жодного локального стану, hostname/container_id лише
   metadata).

Орієнтир по часу для дефолтів (`lease_seconds=60`, `stale_after_seconds=60`): task стає
претендентом на повторний claim не пізніше ніж за `lease_seconds` від моменту `kill -9`, а
рядок instance стає видимо `stale` окремо, приблизно в тому самому вікні.

## 6. Тимчасово повернути placeholder-процес

Rollback-прапорець без перебудови image (§«Rollback/disable» картки WP-01D,
`docs/workers.md` §7):

```bash
COLLECTOR_WORKER_PLACEHOLDER=1 docker compose up -d --no-build
```

Проброшено у всі worker-сервіси та `scheduler`. Процес лишається живим (`not implemented: owned
by WP-01D` у stderr), нічого не claim-ить і не пише в `worker_instances`; healthcheck і
`stop_grace_period` не змінюються. Повернення до реального runtime:

```bash
COLLECTOR_WORKER_PLACEHOLDER=0 docker compose up -d --no-build   # або зняти змінну повністю
```

Активні leases, які тримав реальний runtime до відкату, повертаються тим самим шляхом, що й
після SIGKILL (розділ 5) — placeholder-процес не heartbeat-ить і не продовжує lease.

## 7. Діагностика «worker німий» (status не `ready`)

Симптоми: контейнер `healthy` (liveness-проба проходить — процес живий), але `worker_instances`
для нього ніколи не показує `status='ready'`, і repликa не claim-ить jobs.

1. **Перевірте, чи це `starting`, що не рухається:**

   ```bash
   docker compose exec -T postgres psql -U "${POSTGRES_USER:-collector}" -d "${POSTGRES_DB:-collector}" -c "
     SELECT instance_id, role, status, started_at, last_heartbeat_at
     FROM worker_instances
     WHERE status = 'starting'
     ORDER BY started_at DESC;"
   ```

   Свіжий `last_heartbeat_at` при `status='starting'` — це не постійна проблема: heartbeat сам
   повторює перехід у `ready`, поки `status == 'starting'` (виправлення M-1 код-рев'ю). Якщо
   heartbeat теж не оновлюється — дивіться п. 2.

2. **Перевірте логи на `worker.ready_retry`/`WorkerRuntimeError`:**

   ```bash
   docker compose logs fetch-worker | grep -E '"event": "worker\.(ready_retry|status_update_failed)"'
   ```

   Після 5 невдалих спроб (`READY_RETRY_ATTEMPTS`, експоненційний backoff від 0.5 с) процес
   піднімає `WorkerRuntimeError` і завершується ненульовим кодом — Docker перезапускає репліку
   видимо (`restart: unless-stopped`), а не лишає її «живою, але німою». Якщо контейнер
   рестартує в циклі — проблема в PostgreSQL (права, недоступність), не в runtime.

3. **Перевірте, чи `job_types` handler-а взагалі відповідає тому, що ставить у чергу домен:**

   ```bash
   docker compose logs fetch-worker | grep '"event": "worker.registered"'
   ```

   Рядок містить `job_types=[...]`. Порожній claim при `status='ready'` і `active_leases=0`
   зазвичай означає, що домен ставить у чергу інший `job_type`, ніж claim-ить handler
   (`docs/workers.md` §5, «Як додати handler») — не збій runtime.

4. **PostgreSQL повністю недоступний.** Liveness лишиться `healthy` (readiness і liveness
   розведені навмисно, `docs/decisions/0006-*.md`) — перевіряйте не healthcheck, а
   `worker_instances.last_heartbeat_at`/логи `worker.heartbeat_failed`/`worker.claim_failed`.

5. **Контейнер перезапускається, рядка в `worker_instances` немає взагалі, у логах
   `role login: …`.** Процес відмовився працювати під чужою роллю БД (§13, `docs/workers.md`
   §7.1): змонтовано не той `postgres_dsn_<component>`, міграційний DSN, або роль має зайві
   права (superuser, член `collector_migrate`). Перевірте, що сервіс монтує свій секрет і що
   `migrate-postgres` (`collector db roles --with-login`) завершився 0:

   ```bash
   docker compose logs --no-log-prefix fetch-worker | grep 'role login'
   docker compose ps -a migrate-postgres
   ```

   Тимчасовий відкат без БД — `COLLECTOR_WORKER_PLACEHOLDER=1` (розділ 6).

## Обмеження / не покриває цей runbook

- Масштабування (`replicas`, `PoolController`, `scale_commands`, role-wide drain barrier для
  scale-down) — WP-01D PR3.
- Compose/Swarm adapters, allowlist controller — WP-01D PR3.
- Origin rate limiter (`OriginPermitClient`, `origin_rate_permits`) — WP-01D PR2.
