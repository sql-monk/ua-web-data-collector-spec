# Код-рев'ю WP-01D PR1 — worker runtime, lease/heartbeat/self-fencing, drain, singleton scheduler

| Поле | Значення |
|---|---|
| Гілка | `wp/01d-1-worker-runtime`, HEAD `692e475` |
| Worktree | `.worktrees/wp-01d` (гілка ще не перебазована на `main` — оцінювалось як є) |
| Diff | `git diff main...HEAD` без `docs/plan/reports/**` — 26 файлів, +4063/−30 |
| Рев'юер | `wp-code-reviewer` (read-only) |
| Дата | 2026-09-23 |
| **Вердикт** | **`changes_requested`** (1 × high) |

Знахідок: **1 high**, **4 medium**, **6 low**, 0 critical.

---

## Знахідки

### H-1 — `high` — self-fencing не спрацьовує, коли PostgreSQL **зависає**, а не помиляється

`src/collector/workers/runtime.py:419-422`, `:461-464`, `:475-504`;
`src/collector/persistence/postgres/engine.py:33-39`

**Claim.** `_fence_if_lease_unconfirmed()` викликається **рівно з одного місця** — з гілки
`except (SQLAlchemyError, OSError, PersistenceError)` у `_heartbeat()`. Fencing керується не
часом, що минув від останнього підтвердженого heartbeat, а фактом **винятку**. `_heartbeat_loop`
послідовний (`await sleep(...); await self._heartbeat()`), тому щойно `_heartbeat()` заблокувався
всередині драйвера, цикл стоїть і жоден код fencing більше не виконується. Виняток при цьому не
приходить ніколи: `create_engine` не задає asyncpg `command_timeout`, а сесія не задає
`statement_timeout`, тож запит у «чорну діру» TCP чекає до RTO ядра (десятки хвилин), а не
`lease_seconds`.

Це саме той сценарій, заради якого F3 і додавали: «недоступна база» на практиці — це не
`ConnectionRefusedError` (як у тесті
`test_self_fencing_cancels_active_tasks_when_the_database_stops_confirming_the_lease`, який
використовує `FlakySessions`, що кидає `OperationalError` **синхронно**), а failover / мережевий
поділ / `pg_stat_activity` у `idle in transaction` — тобто **зависання без помилки**.

**Failure scenario** (`lease_seconds=60`, `heartbeat_seconds=20`, `fence_after=30`):

1. `t=0` worker claim-ить fetch-job, `lease_expires_at = 60`.
2. `t=5` мережевий поділ до PostgreSQL (пакети дропаються, RST немає).
3. `t=20` `_heartbeat()` відкриває сесію → запит зависає. Винятку немає.
4. `t=30` вікно `fence_after` минуло, але `_fence_if_lease_unconfirmed` ніхто не викликав.
5. `t=60` lease спливає. `t=65` scheduler (інший контейнер, зі своїм здоровим з'єднанням)
   `recover_expired_leases` → job `pending`.
6. `t=70` інший instance claim-ить ту саму job і виконує доменну роботу **вдруге**: зовнішній
   запит до джерела (WP-02), запис у Mongo (WP-01B) — те, що self-fencing мав виключити.
7. `t≈900` TCP нарешті відвалюється, наш instance ловить `OSError`, фенситься — через 14 хвилин
   після того, як job уже виконав хтось інший.

**Доказ (зонд, запущено).** `WorkerRuntime` з `fence_after_seconds=0.2`, `lease_seconds=4`,
session-фабрикою, чий `__aenter__` зависає (без винятку), і одним активним task:

```text
fenced           : False
fences           : 0
heartbeats ok    : 0
active_tasks     : 1
handler cancelled: False
elapsed since ok : 2.01 s (fence_after = 0.2 s, lease = 4 s)
claiming         : True
```

10× понад `fence_after` і 50% lease TTL — task живий, fencing не спрацював.

**Verdict CONFIRMED.**

**Що робить фікс дешевим:** (а) обмежити сам тік — `await asyncio.wait_for(self._heartbeat(),
timeout=...)` з бюджетом < `fence_after`, і у `except TimeoutError` теж кликати
`_fence_if_lease_unconfirmed()`; (б) зробити перевірку fencing **часовою**, а не подієвою — кликати
її також із `_claim_loop` і перед `_report` (`monotonic() - _last_heartbeat_ok >= fence_after` →
не звітувати й не claim-ити), тоді вона працює навіть коли heartbeat-цикл стоїть; (в) додати
`connect_args={"command_timeout": ...}` в `create_engine` — без нього жоден із таймаутів у
репозиторіях не має нижньої межі.

---

### M-1 — `medium` — одна невдала `_set_status("ready")` на boot назавжди робить worker «живим, але німим»

`src/collector/workers/runtime.py:267-271`, `:618-632`, `:193-198`

**Claim.** `_boot()` викликає `_set_status("ready")` **рівно один раз**. `_set_status` ловить
`(SQLAlchemyError, OSError, PersistenceError)`, логує `worker.status_update_failed` і **повертається
не змінивши `self._status`**. Властивість `claiming` вимагає `self._status == "ready"`, тому після
такої помилки instance ніколи не claim-ить жодної job. Ніщо не повторює перехід: `_heartbeat`
оновлює `slots/leases/pool`, але не статус; `_claim_loop` крутиться на `_idle(poll_seconds)` без
жодного логування.

**Чому це не видно ззовні.** Heartbeat продовжує ходити успішно → `last_heartbeat_at` свіжий →
`mark_stale_instances` цей instance **не** позначить. Container healthcheck
(`python -m collector.api.health postgres`) перевіряє доступність PostgreSQL, а не готовність
worker-а, тож Docker рапортує `healthy`. У `worker_instances` рядок назавжди залишається
`status='starting'`, `slots_active=0`.

**Failure scenario.** Під час rolling-restart PostgreSQL коротко перезавантажує backend саме між
`_check_ready()` (успіх) і `_set_status("ready")`. Контейнер живий і healthy, heartbeat-и йдуть,
черга росте — жоден із 8 pool-ів цю репліку не «недолічить», бо `observed_capacity` рахує
`ready`-instances і просто бачить на одну менше, без жодного сигналу про причину.

**Verdict CONFIRMED** (читанням коду; єдиний шлях до `_status == "ready"` — `_boot`).

Фікс: або підняти помилку з `_set_status` на boot (fail fast → рестарт контейнера, як робить
`_check_ready`), або повторювати перехід у `ready` з heartbeat-циклу, поки `self._status !=
"ready"`.

---

### M-2 — `medium` — блокуючий `handle()` знімає **всі** гарантії runtime, і контракт handler-а цього не вимагає

`src/collector/workers/handlers.py:106-108` (контракт), `src/collector/workers/runtime.py:349-351`,
`:535-547`; `src/collector/workers/roles.py:62` (`parse` → `2 × CPU`)

**Claim.** Runtime запускає `handle(task)` як звичайний `asyncio.Task` у тому самому event loop, що
й claim-loop і heartbeat-loop. `entry.handle.cancel()` (і в `_abandon`, і в `_drain`) — це
**кооперативне** скасування: воно нічого не зупиняє, поки корутина не дійде до await. Синхронний
CPU-bound `handle` (парсинг HTML, lxml, regex по великому body) блокує loop цілком: heartbeat не
йде, fencing не рахується (див. H-1), drain-timeout не скасовує task, `stop_grace_period` не
дотримується.

Контракт `TaskHandler.handle` у `handlers.py` каже лише «має бути придатним до скасування» — він
**не** вимагає ні `asyncio.to_thread`, ні executor-а, ні порогу на синхронну роботу. При цьому
`parse` — єдина роль, для якої §7.6 задає concurrency `2 × CPU`, тобто рівно та, яку писатимуть
синхронною. Оскільки PR1 — це каркас, на який сідають WP-02/03/04/01B, ціна помилки множиться на
всі домени.

**Failure scenario.** `parse`-handler робить `lxml.html.fromstring(body)` на 30-мегабайтній
сторінці (8 с CPU) × 8 слотів → event loop заблокований ~60 с. `heartbeat_seconds=20`,
`lease_seconds=60` → жоден lease не продовжено, всі 8 leases спливають, scheduler їх recover-ить,
інша репліка claim-ить ті самі jobs і парсить їх удруге. Ані fencing, ані drain у цьому вікні не
працюють.

**Verdict CONFIRMED** (властивість asyncio + відсутність вимоги в контракті; не відтворював
запуском, бо `NoopHandler`/`ControlledHandler` у PR1 обидва асинхронні).

Фікс: у контракті `TaskHandler` прямо зобов'язати виносити CPU-bound роботу в
`asyncio.to_thread`/executor (або дати runtime-обгортку, яка це робить), плюс watchdog: логувати,
коли між ітераціями heartbeat-циклу минуло > `heartbeat_seconds × k`.

---

### M-3 — `medium` — pool з'єднань рахується від **статичного** default concurrency, а сам concurrency гарячо змінюваний і не обмежений зверху

`src/collector/cli.py:391-396`; `src/collector/workers/runtime.py:319-322`;
`migrations/postgres/versions/20260922_0001_control_queue.py:183`
(`CHECK desired_concurrency >= 1` — верхньої межі немає);
`src/collector/persistence/postgres/repositories/pools.py:91-93` (`validate` теж лише `>= 1`)

**Claim.** `_run_worker` бере `slots = default_pool_spec(role).desired_concurrency` (для `fetch` —
8) і будує engine з `pool_size=10, max_overflow=12` → стеля 22 з'єднання. Але джерело істини
concurrency — `worker_pools.desired_concurrency` у БД, яку оператор/GUI (§7.6, WP-11C) змінює **без
рестарту** і **без верхньої межі**. `_claim_loop` бере `free = desired_concurrency - len(_active)`
без жодного cap на розмір pool-у. Кожна активна task у `_report` бере власну session, плюс
heartbeat, плюс claim, плюс `_set_status`.

При `desired_concurrency > pool_size + max_overflow − 3` черга checkout-ів переповнюється, і
`QueuePool` після `pool_timeout` (default **30 с**) кидає `sqlalchemy.exc.TimeoutError` —
підклас `SQLAlchemyError`. Якщо таймаут дістається heartbeat-у, спрацьовує
`_fence_if_lease_unconfirmed` (`runtime.py:461-464`) — тобто **надлишкова конкурентність
маскується під втрату lease**, і runtime скасовує цілком здорові активні tasks.

**Failure scenario.** Оператор через GUI піднімає `fetch.desired_concurrency` 8 → 32 (в межах
`max_replicas`, жодна валідація не заперечує). Наступний heartbeat відкриває 32 слоти. 32 task-и
завершуються майже одночасно → 32 конкурентні `_report` → 22 з'єднання зайняті, heartbeat чекає
30 с і падає `TimeoutError` → `fence_after=30 с` минув → `fenced=True`, усі активні tasks
скасовано, jobs залишаються `leased` до кінця TTL (`_abandon` навмисно нічого не пише в чергу).
Pool «самозаблокувався» від власного hot-change; в логах — `worker.fenced reason="lease not
confirmed by database"`, що вказує на БД, а не на справжню причину.

**Verdict PLAUSIBLE** (арифметика підтверджена читанням `cli.py` + дефолтів SQLAlchemy; не
відтворював на живому стеку).

Фікс: або рахувати pool від прочитаної з БД `desired_concurrency` й піднімати межі при
hot-change, або (простіше) обмежити `free` розміром pool-у мінус резерв на heartbeat/claim і
логувати, що concurrency впирається в ліміт з'єднань; додати верхню межу `desired_concurrency` у
`PoolDesiredState.validate`.

---

### M-4 — `medium` — перевірка singleton-lease у scheduler TOCTOU щодо самого тіку

`src/collector/workers/scheduler.py:132-141`, `:171-188`

**Claim.** `run()` перевіряє `_still_active()` (сервер-сайд `pg_locks` по **lease-з'єднанню**), а
потім `_run_tick()` відкриває **іншу** session із pool-у й пише в ній. Між перевіркою і commit-ом
тіку lease може зникнути (`pg_terminate_backend`, failover, мережа) — і тоді другий scheduler,
який уже взяв lease, планує паралельно з нами. Fencing-токена в транзакції тіку немає: жоден
запис тіку не перевіряє, що lease досі наш.

Сьогодні це нешкідливо: default-тік (`recover_expired_leases` + `mark_stale_instances`)
ідемпотентний і працює через `SKIP LOCKED`. Але докстрінг модуля прямо запрошує доменне планування
(«enqueue discovery-jobs») через параметр `tick`, а enqueue з `idempotency_key` без дискримінатора
циклу — це вже подвійне планування.

**Failure scenario.** Активний scheduler A пройшов `_still_active()` (`t=0`), почав тік. `t=0.01`
`pg_terminate_backend` вбиває його lease-backend. `t=0.02` standby B бере lease і запускає свій
тік. Обидва виконують `tick(session, now)` одночасно; для доменного тіку WP-03 це два набори
discovery-jobs.

**Verdict CONFIRMED** (читанням: `_run_tick` — `self._sessions()`, а не `self.lease._connection`).

Фікс (дешевий і остаточний): виконувати тік **на тому самому з'єднанні, що тримає lease** — тоді
смерть сесії робить commit тіку неможливим за побудовою. Альтернатива — передавати в `tick`
epoch/fencing-токен і перевіряти його в межах транзакції.

---

### L-1 — `low` — межа `heartbeat × 2 ≤ lease` допускає рівність, а фактичний період heartbeat більший за налаштований

`src/collector/workers/config.py:121-128`; `src/collector/workers/runtime.py:419-422`

Докстрінг обіцяє «один пропущений heartbeat пробачається», але валідація пропускає
`heartbeat_seconds * 2 == lease_seconds` (наприклад 30/60): другий beat припадає **рівно** на
момент експірації, тож будь-який RTT робить пропущений beat фатальним. Додатково `_heartbeat_loop`
робить `sleep(interval)` **після** завершення тіку, тому реальний період = `interval + тривалість
тіку`; на повільному PG дрейф накопичується саме тоді, коли запас потрібен найбільше.
Verdict CONFIRMED. Фікс: вимагати `heartbeat_seconds * 3 <= lease_seconds` і планувати тік від
дедлайну (`sleep(max(0, next_deadline - monotonic()))`).

### L-2 — `low` — fenced-стан не видно ззовні процесу

`src/collector/workers/runtime.py:145-148`, `:176-182`, `:492-501`

`fenced`/`fences`/`lost_leases` існують лише як атрибути Python-об'єкта і один лог-рядок
`worker.fenced`. У `worker_instances` немає ні поля, ні лічильника; метрик PR1 не додає (їх owner —
WP-12, але тут немає навіть заготовки, на відміну від PR2, де картка просить лічильники в коді).
Оператор на екрані Workers (WP-11C) побачить лише те, що instance став `stale` через ≥ 60 с — і не
дізнається, що runtime сам скасував N tasks. Verdict CONFIRMED. Фікс: лічильник у коді (як вимагає
картка для PR2) + заповнювати `slots_active=0`/окреме поле причини на найближчому успішному
heartbeat.

### L-3 — `low` — `_abandon` губить handle: скасовані tasks не чекає ні `_drain`, ні `stop_grace_period`

`src/collector/workers/runtime.py:535-547`, `:551-566`

`_abandon` спершу `self._active.pop(job_id)`, потім `entry.handle.cancel()` — handle більше ніде не
зберігається. `_drain` будує `handles` з `self._active`, тому фенснуті tasks у бюджет
`stop_grace_seconds` не входять і на них ніхто не чекає. У CLI це рятує `asyncio.run`, який на
виході скасовує й **дочекується** решти tasks, але у вбудованому запуску (кілька runtime в одному
процесі — режим, який `run(install_signals=False)` явно підтримує) доменний handler може лишитись
із незакритим Mongo-session/HTTP-з'єднанням. Verdict CONFIRMED. Фікс: тримати скасовані handles у
окремому списку і `gather` їх у `_drain`.

### L-4 — `low` — `_report` ловить вужчий набір помилок, ніж решта runtime

`src/collector/workers/runtime.py:402-411` проти `:340-342`, `:461-464`, `:624-630`

`_claim`, `_heartbeat` і `_set_status` ловлять `(SQLAlchemyError, OSError, PersistenceError)`, а
`_report` — лише `LeaseNotOwnedError` і `(SQLAlchemyError, OSError)`. Будь-який інший
`PersistenceError` (`ConflictError`, `InvalidTransitionError`, `NotFoundError`) вилітає з
`_execute`, вбиває asyncio-task із невитягнутим винятком (лише warning від GC), і **жодного**
`worker.report_failed` в логах не буде — при тому що job лишиться `leased` до кінця TTL.
Verdict CONFIRMED. Фікс: додати `PersistenceError` до `except` у `_report`.

### L-5 — `low` — плановий drain-timeout витрачає спробу і пише `last_error_code='drain_timeout'`

`src/collector/workers/runtime.py:568-616`

F2 виправили лише **останню** спробу (job на `attempt >= max_attempts` більше не карантиниться).
Для решти `_release_leases` викликає `queue.retry`, а `attempt` уже інкрементований у `claim`, тож
кожен scale-down/деплой «з'їдає» одну з 5 спроб і лишає в `crawl_jobs.last_error_code` слово
«помилка» там, де помилки не було. При активному autoscale (PR3, Q-014) послідовність scale-down
може довести job до `max_attempts` без жодного реального збою. Ризик уже заведено в картку як
залежність від `queue.release(job_id, owner)` — тут лише фіксую, що `release` має відновлювати ще й
`attempt`, інакше проблема залишиться. Verdict CONFIRMED.

### L-6 — `low` — `AdvisoryLease._discard()` повертає з'єднання в pool без підтвердженого unlock

`src/collector/workers/advisory.py:116-134`

`release()` глушить будь-який `SQLAlchemyError/OSError` від `pg_advisory_unlock` і **не дивиться на
його результат** (`pg_advisory_unlock` повертає `false`, коли lock не наш), після чого
`_discard()` робить `connection.close()` → з'єднання повертається у pool. Session-scoped advisory
lock переживає `ROLLBACK`, який SQLAlchemy робить при поверненні у pool; крім того
`pg_try_advisory_lock` **реентрантний** у межах сесії, тож витік lock-у у pool не побачить ні
`is_held()` (той самий `pg_backend_pid`), ні наступний `try_acquire()`. Наслідок — singleton
назавжди зайнятий простоюючим pooled-з'єднанням і жоден інший процес його не візьме, поки engine
живий. У CLI радіус малий (`_run_scheduler` одразу робить `engine.dispose()`), але PR3 планує
другий іменований lease (`controller`) на тому самому шаблоні. Verdict PLAUSIBLE. Фікс:
перевіряти boolean-результат unlock і за будь-якого сумніву робити `connection.invalidate()`
замість `close()`.

### L-7 — `low` — дрібниці спрощення/послідовності

- `WorkerRuntime.run/_on_signal/_idle/_stop` і `SchedulerRuntime.run/_on_signal/_idle/_stop`
  (`runtime.py:200-243`, `scheduler.py:115-152`, `:190-192`) — ~35 рядків майже дослівного
  дублювання; напрошується спільна база «lifecycle + signals + stop-event».
- `_ensure_pool` (`runtime.py:273-306`) — єдиний запис desired state у системі **без**
  `append_audit`: усі інші йдуть через `request_scale`. Bootstrap pool-у worker-ом взагалі спірний
  (§7.6/§7.7 віддають desired state оператору/контролеру) — як мінімум варто audit-рядок.
- `_claim` при помилці БД повертає 0 і йде в `_idle(poll_seconds)` (`runtime.py:323-324`, default
  **1 с**) — кожен worker гатить у мертвий PostgreSQL раз на секунду без backoff.
- `result_for_exception` (`handlers.py:143-152`) і лог `worker.task_failed`
  (`runtime.py:361-366`) кладуть сирий текст винятку в `crawl_jobs.last_error_message`,
  `dead_letters` і в лог без жодної редакції; §13 тут покладається виключно на дисципліну
  доменного handler-а (`URL` з credentials у тексті `InvalidURL` пройде наскрізь). Для PR1 з
  `NoopHandler` нешкідливо, але редакцію дешевше додати в runtime, ніж у 5 доменних WP.
- `TaskHandler.job_types` документований як «непорожній кортеж», але порожній кортеж ніде не
  відхиляється — `queue.claim` просто поверне `[]`, і worker мовчки простоюватиме вічно.

---

## Вердикт

**`changes_requested`.**

Каркас зроблено акуратно: транзакційні межі коректні (claim — окрема транзакція, звіт по кожній
task — окрема, heartbeat instance + продовження leases — одна на тік; `handle` виконується **поза**
транзакцією), інваріант «lease належить instance» витриманий наскрізь, репозиторії WP-01A не
дублюються й не обходяться, drain і per-instance барʼєр мають чесні adversarial-тести, а рішення
F2 (не карантинити job на останній спробі при плановому drain) — правильне і добре аргументоване.
Типізація чиста (`mypy strict` без жодного невиправданого ignore: 2 ignore/noqa, обидва
обґрунтовані), тести перевіряють поведінку, а не реалізацію, і не залежать від wall-clock.

Блокує merge одна знахідка — **H-1**: self-fencing, заради якого і переробляли PR після gate 2,
захищає лише від «БД кидає виняток» і повністю сліпий до «БД не відповідає», який і є типовою
формою відмови PostgreSQL. Оскільки саме на цьому runtime сидітимуть усі доменні workers, дірку
краще закрити тут, а не в п'яти місцях потім. Фікс дешевий: таймаут на тік heartbeat +
часова (а не подієва) перевірка вікна fencing + `command_timeout` в engine.

M-1 і M-3 варто взяти в цей самий PR (обидва — кілька рядків), M-2 і M-4 допустимо винести в PR3
разом із `PoolController`, але M-2 (контракт handler-а щодо CPU-bound роботи) має потрапити в
`docs/workers.md` до того, як WP-02/03/04 почнуть писати handler-и.

---

## Що перевірено окремо

**Запущено (read-only, у worktree):**

| Команда | Результат |
|---|---|
| `uv run pytest -m "not live and not integration" tests/unit/workers tests/unit/test_compose_config*.py tests/unit/test_cli_compose_commands.py -q` | `145 passed in 8.54s` |
| `uv run pytest -m integration tests/integration/scaling -q -p no:randomly` | `28 passed in 107.14s` (testcontainers; flakiness F0 не відтворилась) |
| `uv run ruff check .` | `All checks passed!` |
| `uv run mypy src` | `Success: no issues found in 68 source files` |
| `docker compose --profile core --profile workers --profile browser config` | валідний; ефективний merge перевірено окремо (нижче) |
| Зонд `probe_fence_hang.py` (scratchpad) | доказ H-1 — див. вивід у знахідці |

Docker-стек не піднімався (не обов'язково за завданням); жодного контейнера не створено й не
змінено.

**Перевірено читанням і визнано коректним (знахідок немає):**

- **Self-fencing, шлях «БД кидає виняток»** — працює як заявлено: `_abandon` навмисно не чіпає
  рядок у `crawl_jobs` (писати нічого й нічим), скасована task не проходить через `_report`, тож
  тихого `complete` немає; вихід із fenced-стану безпечний, бо `_active` до того порожній і старі
  tasks підхопити нізвідки.
- **Вікна «fence спрацював, а `complete` усе одно пішов» немає.** Навіть якщо скасування
  застане `_report` рівно на commit-і, fence за замовчуванням відбувається на ½ TTL, коли lease ще
  наш і job нікому не віддана; а після `recover_expired_leases` предикат репозиторію
  (`status='leased' AND lease_owner=:owner`, `queue.py:354-359`) відхиляє `complete`/`retry`
  чужим. Це підтверджує й тест
  `test_hanging_task_does_not_extend_its_lease_and_never_reports_a_silent_complete`.
- **`fence_after` при зміні lease TTL «на льоту»** — питання не виникає: `lease_seconds` читається
  лише з env у frozen-dataclass, гарячої зміни немає, тож `fence_after` (`config.py:142-147`) не
  може розійтися з TTL. Валідація `0 < fence_after <= lease_seconds` присутня.
- **Heartbeat не блокує claim-loop**: це окремий `asyncio.Task` (`runtime.py:226-228`); claim-loop
  прокидається через `_wakeup`, а не чекає на heartbeat.
- **Втрати task при завершенні heartbeat-транзакції** немає: `_abandon` на job, якої вже немає в
  `_active`, — no-op, тому гонка «task відзвітувався поки йшов heartbeat» не накручує
  `lost_leases` і нічого не скасовує; `LeaseNotOwnedError` усередині транзакції не ламає session
  (це не помилка драйвера), тож решта leases у тому ж тіку продовжуються.
- **Deadlock між heartbeat-транзакцією і `_report`** неможливий: heartbeat бере lock рядка
  `worker_instances` + рядки jobs, `_report` бере лише рядок job — циклу очікувань немає.
- **Drain по всіх шляхах виходу**: виняток у handler → `result_for_exception` → `_report` у власній
  транзакції; SIGTERM під час claim → claim добігає, claim-нуті jobs потрапляють у `_active` і в
  drain; SIGTERM під час heartbeat → heartbeat живе весь drain і продовжує leases активних tasks
  (це правильно і необхідно); подвійний/потрійний SIGTERM ідемпотентний (`request_stop` лише
  ставить `Event`) — покрито тестом.
- **`stop_grace_period` узгоджений**: compose `120s` (worker) проти
  `COLLECTOR_WORKER_STOP_GRACE_SECONDS=90` і `90s` (scheduler) — запас 30 с на `_release_leases`
  (послідовні транзакції по числу активних tasks) і `_set_status("stopped")`; перевірено на
  ефективному `docker compose config`.
- **Ефективний compose-merge**: усі 8 worker-сервісів + scheduler отримують
  `COLLECTOR_POSTGRES_DSN_FILE`, secret `postgres_dsn`, `depends_on` (postgres healthy +
  migrate-postgres completed) і healthcheck — жоден сервіс не перевизначає `environment`/`secrets`
  так, щоб загубити anchor. `COLLECTOR_WORKER_PLACEHOLDER` за замовчуванням `0`, що
  `placeholder_requested` трактує як «реальний runtime» — rollback-прапорець працює в правильний
  бік.
- **Concurrency hot-change**: зменшення не вбиває активні tasks (`free` стає від'ємним →
  claim просто не викликається), збільшення відкриває слоти на найближчому heartbeat; витоку
  задач немає, бо облік один — `len(self._active)`. Окремого семафора немає і він не потрібен.
  `desired_concurrency=0` у БД **неможливий** (`CHECK desired_concurrency >= 1` у міграції
  `0001` + `PoolDesiredState.validate`), тож scale-to-zero робиться через `desired_replicas=0` +
  drain-барʼєр, а не через concurrency — тут дірки немає.
- **Scheduler singleton, `pg_try_advisory_lock` на pool-і**: з'єднання **навмисно** тримається поза
  pool-ом на весь час володіння (`advisory.py:75-96`, `AUTOCOMMIT`), тому «повернули в pool і
  втратили lock» у штатному шляху не відбувається; перевірка володіння — сервер-сайд по `pg_locks`
  - `pg_backend_pid()`, а не локальний прапорець, тож reconnect детектується. Єдина щілина —
  L-6 (unlock без перевірки результату).
- **Maintenance-tick ідемпотентний**: `recover_expired_leases` (`UPDATE ... WHERE status='leased'
  AND lease_expires_at <= now`, `SKIP LOCKED`) і `mark_stale_instances` (`UPDATE ... WHERE
  last_heartbeat_at < now - ttl`) — обидва no-op при повторі; повторний тік після падіння нічого
  не псує.
- **Інтеграція з WP-01A**: власної SQL-логіки черги/pool-ів runtime не має (єдиний сирий SQL —
  `SELECT 1` readiness і `pg_*advisory*` у `advisory.py`, для яких у WP-01A немає API);
  транзакція ніколи не тримається під час `handle` — `Task` (`handlers.py:45-57`) навмисно є
  знімком без ORM-об'єктів і без session; `_task_from_job` копіює `args` у звичайний `dict`.
- **Дані/типи**: усі timestamps — aware UTC через `clock.utcnow`/`resolve_now`; грошових величин у
  цьому PR немає; `worker_instance_id` — UUIDv7 на boot, жодного стану на диску (§15) —
  підтверджено тестом `test_second_boot_of_the_same_container_creates_a_new_instance`.
- **Нескінченних retry немає**: `PermanentTaskError` → `quarantine` + рівно один dead letter;
  retryable → `queue.retry` з експоненційним backoff і жорстким `max_attempts`.

**Не перевірялось:** поведінка під реальним навантаженням (M-3 лишився PLAUSIBLE); Windows-гілка
`signals.py` (продакшен — Linux-контейнери); PR2/PR3 предмети (лімітер, `PoolController`,
Compose/Swarm adapters).
