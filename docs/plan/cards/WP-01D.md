# Картка WP-01D — Worker pool control

| Поле | Значення |
|---|---|
| Owner | wp-implementer (єдиний owner worker runtime і orchestration adapters) |
| Branch | `wp/01d-1-worker-runtime`, `wp/01d-2-limiter-runtime`, `wp/01d-3-drain-adapters` |
| Worktree | `.worktrees/wp-01d` |
| Залежить від | WP-00 (усі три PR) `merged`, WP-01A PR1 `merged` |
| Розблоковує | WP-02, WP-03, WP-04 (runtime workers), WP-11C (екран Workers), WP-12 |
| Розмір | L → три PR одного owner |
| Розділи ТЗ | §7.5 (scaling, Compose/Swarm), §7.6 (повністю), §13 (без Docker socket, allowlist controller), §15 (capacity ceiling), §16.1 п.15, §16.3 (scale/drain/kill пункти), FR-031—FR-033, FR-035 |
| Рівні тестів §16.1 | 15 Scaling, 3 Integration, 1 Unit |
| Регресії REVIEW.md | R-52 (незалежні pools, drain, lease recovery), R-53 (aggregate rate не залежить від replicas), R-55 (GUI/API без socket; Swarm controller ізольований), R-57 (role-wide drain barrier, не покладатися на вибір контейнера orchestrator-ом) |
| Q-питання | Q-013 (Compose MVP / Swarm для GUI-scaling — default, ADR у PR3), Q-014 (autoscale off за замовчуванням) |

## Scope

Перетворити placeholder-процеси `collector worker <role>`/`scheduler` на справжній stateless runtime: claim із черги WP-01A, lease heartbeat, graceful drain, реєстрація instance, роздача origin-permits усім реплікам, desired/current state pools, scale-команди та два deployment adapters (Compose CLI — без socket; Swarm — ізольований allowlisted controller).

## Out of scope

Доменна робота workers (fetch — WP-02, discovery — WP-03, translation — WP-04, projector — WP-01B): цей WP дає **runtime-каркас**, у який вони вставляють `handle(task)`. GUI (WP-11C), метрики/алерти (WP-12), autoscale-політика в production (після pilot, Q-014).

## Owned files

`src/collector/workers/**` (крім доменних handler-ів інших WP), `src/collector/orchestration/{compose,swarm}/**`, `src/collector/core/limiter_runtime.py` (клієнтська обгортка над PG-лімітером WP-01A), `src/collector/cli.py` (лише команди `worker`, `scheduler`, `controller`), `tests/integration/scaling/**`, `tests/unit/workers/**`, `docs/plan/reports/WP-01D/**`, `docs/plan/deps/WP-01D-to-*.md`, `docker-compose.yml` (лише worker/scheduler/controller-сервіси та їхні env/healthcheck — узгоджувати з WP-00 через dependency-запит, якщо зачіпає інші сервіси).

Forbidden: `src/collector/contracts/**`, `schemas/**`, `migrations/**` (нові таблиці/колонки — dependency-запит до WP-01A), `web/**`, `deploy/compose/gui/**`.

---

## PR1 — `wp/01d-1-worker-runtime`: worker loop, lease, heartbeat, registration

### Вимоги

1. `WorkerRuntime`: boot `worker_instance_id` (UUIDv7), реєстрація в `worker_instances` (role, version, deployment metadata, status `starting`), перехід у `ready` після readiness-перевірки залежностей, heartbeat-таск із інтервалом менше lease TTL.
2. Claim-loop: бере до `desired_concurrency` tasks через репозиторій WP-01A, виконує `handle(task)` (інтерфейс `TaskHandler`, реалізації — інші WP; тут — `NoopHandler` для тестів), продовжує lease під час виконання, звітує `complete`/`retry`/`quarantine`.
3. Обробка сигналів: SIGTERM → `draining` (нові claim заборонені, активні завершуються в межах `stop_grace_period`, lease повертаються), потім exit 0; SIGKILL — fault case, lease відновлює `recover_expired_leases` іншого instance (тест).
4. `desired_concurrency` змінюється без рестарту: нові slots відкриваються одразу, зайві закриваються після завершення активних tasks (тест на гарячу зміну).
5. Жодного стану на локальному диску; `worker_instance_id` генерується на boot; Docker hostname лише як metadata.
6. `scheduler` — singleton через advisory lease у PostgreSQL: другий instance не стає активним, а чекає; при втраті lease — припиняє планування (тест на два scheduler).
7. **Дешевий liveness-probe для Docker healthcheck** (запит від WP-00 PR3, CI PR #4). Зараз healthcheck кожного application-контейнера — `python -m collector.api.health <deps>`, тобто **повний старт інтерпретатора** на кожну пробу плюс з'єднання з БД. Заміряно на образі `collector:dev` (`docker run --cpus=N`, медіана з 3): 1.0 CPU — 2.9 с, 0.5 CPU — 4.1 с, 0.25 CPU — 6.0 с; на 2-ядерному CI-runner-і зі стартом 17 контейнерів це вимагало підняти `timeout` до 15 с і `start_period` до 90 с (`docker-compose.yml`, anchor `x-healthcheck-budget`). Коли з'явиться реальний worker runtime (довгоживучий процес), він має віддавати liveness **без** запуску нового інтерпретатора і **без** звертання до БД — наприклад unix-socket/файл heartbeat у tmpfs або мінімальний HTTP-endpoint у самому процесі; readiness (залежності) лишається за `depends_on`/one-shots і за `worker_instances.status`. Тоді бюджет healthcheck можна повернути до секундних значень. Розділяти liveness і readiness у WP-00 не можна: §7.5 прямо вимагає, щоб healthcheck перевіряв «process + критичну dependency», і це закріплено тестом `tests/unit/test_compose_config_adversarial.py::test_application_healthchecks_name_a_critical_dependency`, тож зміна семантики має йти разом зі зміною ТЗ/тесту — тобто у власника runtime.

### Тести

Killed replica → lease recovery іншим instance; drain під активним task (task завершується, lease не втрачено); гаряча зміна concurrency; два scheduler → рівно один активний; heartbeat не продовжує чужий lease.

---

## PR2 — `wp/01d-2-limiter-runtime`: global origin permits у runtime

### Вимоги (R-53, FR-033)

1. `OriginPermitClient`: перед кожним зовнішнім запитом бере leased permit через PG-лімітер WP-01A; повертає ідемпотентно; при expiry — не робить запит.
2. Локальний семафор на контейнер лише **додатково** обмежує concurrency, ніколи не підвищує дозволену частоту.
3. `Retry-After`/429 від джерела → `block_origin` через лімітер; усі репліки бачать блок (тест з двома runtime-процесами).
4. Aggregate-тест: N реплік × M concurrency проти одного origin із політикою 0.2 rps/1 concurrent → сумарна частота не перевищує політику (детермінований вимір через симульований годинник або підрахунок виданих permits, не wall-clock).
5. Метрики-заготовки (`origin_rate_permits_total{origin_group,result}`, `origin_inflight`) — лише лічильники в коді, експорт — WP-12.

---

## PR3 — `wp/01d-3-drain-adapters`: pools desired state, scale commands, Compose/Swarm adapters

### Вимоги (§7.5, §7.6, R-55, R-57)

1. `PoolController`: звіряє desired (`worker_pools`) і current (heartbeat-derived) стан; формує `scale_commands` із idempotency key і expected revision.
2. **Role-wide drain barrier** перед зменшенням replicas (PR1 дає лише per-instance примітив — `worker_instances.drain_requested_at` зупиняє claim того instance, у якого він виставлений; «role-wide» означає, що `PoolController` зобовʼязаний виставити барʼєр **кожному** живому instance ролі й дочекатися підтвердження від усіх, перш ніж зменшувати replicas): усі instances ролі припиняють claim, повертають lease, orchestrator зменшує replicas, survivors відновлюють claim після підтвердження нової revision. Не покладатися на те, який контейнер видалить Compose/Swarm (R-57) — тест, що після `4→1` жоден task не втрачено і не дубльовано.
3. **Compose adapter:** не має Docker socket; переводить команду в `awaiting_manual_apply` з точним CLI-рядком; `applied` лише коли heartbeat-derived replicas збігаються з desired revision.
4. **Swarm adapter:** окремий процес `collector controller` (запускається лише на manager node); allowlist за label `collector.scalable=true`; дозволений diff — **лише** `replicas` у межах min/max; заборонено змінювати images/mounts/networks/secrets/stateful services (тест, що спроба відхиляється); читає лише committed `scale_commands` з audit link.
5. Autoscale вимкнений за замовчуванням (Q-014); політика (queue oldest age + pending/running ratio, 3 вікна, 5-хв cooldown, min/max, окремий бюджет browser/translation) реалізована, але активується прапорцем; manual override має пріоритет.
6. Compose: worker-сервіси отримують реальні команди `collector worker <role>` замість placeholders; scheduler — singleton; controller — окремий сервіс у profile (за замовчуванням не запускається).

### Тести (§16.1 п.15, §16.3)

Replicas `1→4→1→0→2`; concurrent global rate-limit; expired permit recovery; concurrency hot-change; drain during active task; killed replica lease recovery; stale command/revision відхиляється; controller allowlist (спроба змінити image/mount/network → відмова); Compose mode повертає audited CLI; тест, що GUI/API/worker images не мають socket (успадкований від WP-00 — перевірити, що лишається зеленим).

## Команди перевірки (усі PR)

```bash
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run mypy src
uv run pytest -m "not live"
uv run pytest -m integration tests/integration/scaling
docker compose config --quiet
docker compose --profile core --profile workers up -d --wait
docker compose up -d --no-recreate --scale fetch-worker=4
docker compose down -v
```

## Acceptance (§17.2)

«role commands, pool/instance/scale contracts, PostgreSQL origin limiter, heartbeat/drain, Compose command adapter і Swarm replica adapter; scale/rate/fault tests green».

## Відомі ризики (перенесені з PR1)

| Ризик | Статус | Owner | Дата |
|---|---|---|---|
| §13: усі 8 runtime-процесів ходять у PostgreSQL під тим самим DSN, що й міграції (роль `collector`, superuser), бо всі `collector_*` ролі — `NOLOGIN`. Радіус ураження — увесь worker pool; **блокер pilot/production і live-збору**, не блокер merge PR1 (знахідка F1 gate 2, доказ у `docs/plan/reports/WP-01D/testing-pr1.md`) | open | **WP-01A PR2** (LOGIN-ролі + per-role DSN), потім WP-01D повертає §13-інваріант у `tests/unit/test_compose_config*.py` | заведено 2026-09-23 |
| Плановий drain на останній спробі не повертає lease одразу, а чекає експірації (немає `queue.release`) | mitigated (карантину більше немає) | WP-01A PR2 — `queue.release(job_id, owner)` | заведено 2026-09-23 |
| Залишковий TOCTOU singleton-тіку scheduler-а: перевірка lease і сам тік ідуть різними зʼєднаннями, тому два тіки теоретично можуть перекритися. Закрито **контрактом** «тік мусить бути ідемпотентним» (докстрінги `scheduler.py`, `docs/workers.md` §6) і вартовим `test_maintenance_tick_is_safe_when_two_schedulers_overlap`, а не конструкцією — зобовʼязання лягає на майбутні доменні тіки (насамперед WP-03 `enqueue` discovery-jobs: потрібен власний ключ ідемпотентності §9.3 п.3) | mitigated (контракт + тест-вартовий) | WP-01D PR3 (fencing-токен у тіку, якщо зʼявиться неідемпотентне планування) / доменні WP | заведено 2026-09-23 |

Тест-вартовий: `tests/unit/test_compose_config.py::test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` падає, щойно в будь-якому SQL-файлі ролей зʼявиться LOGIN-роль (`CREATE ROLE … LOGIN`, `ALTER … WITH LOGIN`, `CREATE USER`; case-insensitive, коментарі ігноруються) — сам вартовий накритий зондами `::test_login_tripwire_detects_every_way_to_create_a_login_role` і `::test_login_tripwire_is_quiet_on_nologin_and_comments`.

## Rollback/disable

Autoscale off; controller у окремому profile (не запускається за замовчуванням); worker-сервіси можна повернути на placeholder-команду через env (задокументувати).

## Docs (етап 5)

`docs/runbooks/scale-drain-recover.md`, ADR-0006 «Deployment mode: Compose MVP, Swarm для GUI-scaling» (Q-013), ADR-0007 «Autoscale вимкнений до pilot evidence» (Q-014), `docs/workers.md` (ролі, lease/drain, як додати handler).
