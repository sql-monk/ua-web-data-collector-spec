# WP-01D — флейки scaling-тестів worker runtime під навантаженням

Гілка: `fix/wp-01d-flaky-worker-tests` від `main` (`8d3ee74`). Файли WP-01A не змінювались
(міграції, `src/collector/persistence/postgres/**`). Змінено лише файли WP-01D:
`src/collector/workers/runtime.py`, `tests/integration/scaling/test_worker_runtime.py`.

**Вердикт:** флейк не пов'язаний із WP-01A PR2 і відтворюється на чистому `main`. Причин
**дві**, і вони незалежні:

| # | Тести, що падали | Причина | Клас |
|---|------------------|---------|------|
| A | `test_self_fencing_fires_when_the_database_hangs_without_raising`, `test_self_fencing_cancels_active_tasks_when_the_database_stops_confirming_the_lease` | Вікно fencing у тестах (0.2 / 0.3 с) коротше за паузу event loop-у на завантаженій машині → **хибний fence на здоровій базі** ще до того, як тест «зламав» БД | припущення тесту про таймінг |
| B | `test_drain_barrier_must_be_set_on_every_instance_of_the_role`, `test_instance_marked_stopped_claims_nothing_and_exits` (потенційно й `test_role_wide_drain_barrier_stops_claim_without_sigterm`) | Claim, що вже був у базі, брав job, покладену **після** коміту `mark_draining` | **реальна гонка в runtime** |

Попереднє припущення код-рев'юера («таймінговий баг у claim loop») підтверджується для B.

## 1. Відтворення

На машині без навантаження повний `uv run pytest -m "not live"` пройшов (`873 passed`, 164 с).
Щоб відтворити, `tests/integration/scaling` ганяли з CPU-навантаженням: 16 процесів
busy-loop на 12 логічних ядрах (Docker/PostgreSQL на тому самому хості). `pytest-randomly` у
проєкті не встановлено, тож порядок тестів детермінований; параметр, який впливає, — лише
навантаження.

**Baseline, `main` `8d3ee74`, 6 прогонів під навантаженням — 4 червоні, 7 падінь:**

```text
run 1: FAILED test_self_fencing_cancels_active_tasks_…  FAILED test_self_fencing_fires_when_the_database_hangs_…
run 2: FAILED test_self_fencing_fires_when_the_database_hangs_…  FAILED test_instance_marked_stopped_claims_nothing_and_exits
run 3: FAILED test_self_fencing_cancels_active_tasks_…  FAILED test_self_fencing_fires_when_the_database_hangs_…
run 4: 33 passed
run 5: 33 passed
run 6: FAILED test_drain_barrier_must_be_set_on_every_instance_of_the_role
```

Тож «не відтворюється на base за 14 прогонів» пояснюється тим, що тестувальник ганяв набір без
навантаження. На `main` флейк є.

## 2. Причина A — вікно fencing коротше за паузу event loop-у

Сторож lease (`_watchdog_loop`) рахує час від останнього підтвердженого heartbeat за
`monotonic()`. Тести задавали `fence_after_seconds=0.2` і `0.3`. Під навантаженням процес pytest
не отримує CPU десятки–сотні мілісекунд (у логах `worker.event_loop_stalled lag_seconds=0.843`).
Сторож прокидається, бачить `seconds_since_heartbeat=0.326 > 0.3` і вмикає fence **на здоровій
базі**. Це правильна поведінка runtime: з погляду процесу heartbeat справді не підтверджено
довше за вікно. Хибним було припущення тесту, що 0.2–0.3 с завжди вистачить.

Сигнатура з baseline run 1 (зависання ще не ввімкнене, а fence уже спрацював):

```text
worker.fenced  fence_after_seconds=0.3 seconds_since_heartbeat=0.326 cancelled_tasks=1
E  assert sessions.hangs >= 1   # 0 >= 1
```

У сусіднього тесту те саме з `fence_after_seconds=0.2`: або `assert not runtime.fenced` одразу
після старту task, або task скасовується раніше, ніж тест вмикає «відмову» бази.

**Фікс (лише тест).** `FENCE_WINDOW = 1.5` с для обох fencing-тестів (lease 6 / 9 с,
щоб `fence_after <= lease` і `heartbeat*3 <= lease`). Зависаючий тік живе
`heartbeat_tick_budget = 2 × fence_after`, тому поріг «ще тіки зависають» знижено з
`hangs >= 4` до `hangs >= 3`: одного додаткового завислого тіку досить, щоб довести, що
`heartbeats` не росте, а тест не подовжується на ~6 с. Семантика перевірок не змінилась.

## 3. Причина B — гонка claim ↔ drain barrier у runtime

`_claim_loop` перевіряє локальний `claiming`, потім `await self._claim(...)`: транзакція з
кількома round trip-ами (checkout, `set_config`, `claim`). Барʼєр runtime дізнається лише з
heartbeat. Послідовність під навантаженням:

1. claim-loop: `claiming == True`, відкриває транзакцію claim;
2. тест/контролер: `mark_draining` комітиться; heartbeat його бачить → `claiming == False`;
3. тест: `wait_for(not claiming)` пройшло → `enqueue` комітиться;
4. `SELECT … FOR UPDATE SKIP LOCKED` із кроку 1 виконується лише тепер і **бере нову job**.

Лог baseline (instance під барʼєром бере job):

```text
worker.drain_barrier  active=True
worker.claimed        active_tasks=1 count=1
worker.task_done      disposition=complete
```

Це порушує контракт R-57, як його формулює картка: «барʼєр зупиняє claim». Контролер PR3,
побачивши барʼєр і `active_leases = 0` у heartbeat, міг би вважати instance порожнім, хоча
запізнілий claim ось-ось візьме job.

**Фікс (runtime).** `_claim_allowed()` у **тій самій транзакції**, що й claim, читає власний
рядок `worker_instances` під `FOR SHARE` і не claim-ить, якщо `drain_requested_at` задано або
`status != 'ready'`. `set_instance_status` (через `mark_draining`/`mark_stopped`) блокує рядок
`FOR UPDATE`, а це конфліктує з `FOR SHARE`. Тож коміт барʼєра чекає, доки in-flight claim цього
instance закомітиться, і будь-який наступний claim барʼєр уже бачить. Інваріант: **після коміту
`mark_draining` жоден claim цього instance не візьме нової job.** Deadlock-циклу немає: claim
бере `crawl_jobs` через `SKIP LOCKED`, тобто ніколи не чекає на heartbeat. Ціна — один
`SELECT` за PK на кожен claim.

**Суміжний дефект того самого класу, виправлено тут само.** Якщо fence спрацював, поки claim
був у базі, runtime стартував щойно взяті tasks. Сторож уже не скасує їх, бо fence спрацьовує
один раз, а отже вони могли б працювати без підтвердженого lease довше за TTL (подвійне
виконання). Тепер такі jobs не стартують: у логах `worker.lease_left_to_expire
reason="claimed while fenced"`, lease спливає, і job повертає `recover_expired_leases`.

## 4. Регресійні тести (детерміновані, без навантаження)

У `tests/integration/scaling/test_worker_runtime.py`:

- `test_claim_in_flight_never_takes_a_job_enqueued_after_the_drain_barrier`: обгортка над
  `queue_repo.claim` запускає `mark_draining` + `enqueue` посеред claim;
- `test_jobs_claimed_while_the_fence_went_up_are_not_started`: база «падає» посеред claim, а
  claim повертається вже під fence.

На `main`-версії `runtime.py` обидва тести падають:

```text
E  AssertionError: claim, що був у базі під час барʼєра, не взяв нову job
E  assert [UUID('01a0cf…')] == []
E  Failed: стан не настав за 15.0 с: claimed-під-fence job відкладено
```

Із фіксом обидва проходять (`2 passed in 9.20s`).

Коментар у `test_role_wide_drain_barrier_stops_claim_without_sigterm` оновлено: він
описував гонку B як очікувану поведінку.

## 5. Стабільність після фіксу

Та сама кампанія (16 busy-loop процесів, CPU 100 %), `tests/integration/scaling`, гілка з
фіксом (+2 нові тести, тому 35) — **10 з 10 прогонів зелені**:

```text
fix run 1..10: 35 passed   (96–261 с на прогін)
```

Повний `uv run pytest -m "not live"` тричі поспіль без навантаження:

```text
full run 1: 875 passed, 23 skipped, 8 warnings in 134.16s
full run 2: 875 passed, 23 skipped, 8 warnings in 133.67s
full run 3: 875 passed, 23 skipped, 8 warnings in 194.30s
```

Для порівняння: baseline — 4 червоні прогони з 6 за тих самих умов (§1).

## 6. Контракт команд

```text
uv sync --frozen              Checked 66 packages in 8ms
uv run ruff check .           All checks passed!
uv run ruff format --check .  221 files already formatted
uv run mypy src               Success: no issues found in 70 source files
```
