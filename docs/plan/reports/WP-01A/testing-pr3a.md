# WP-01A PR3a — незалежне тестування (`wp/01a-3a-queue-outbox-preflight`)

Worktree `.worktrees/wp-01a-3a`, diff `git diff main...HEAD` (база `626b7e4`, HEAD реалізатора
`ce3d9b4`). Контракт: `docs/plan/cards/WP-01A.md` «PR3a» + «Спільні вимоги»,
`docs/plan/deps/WP-01A-to-WP-01D.md` §7 (N-2), §8; споживачі WP-01D PR1c, WP-02 PR2, WP-01B PR3.
Рівень §16.1: 3 Integration (PostgreSQL 18, testcontainers; compose не піднімався).
`implementation-pr3a.md` прочитано лише після власного прогону й mutation-перевірки.

Хост Windows 11, Docker Desktop, спільний з іншими агентами (кілька паралельних
testcontainers) — звідси тривалість. Кирилиця у виводі консолі Windows показується як `�`.

## Команди та дослівний вивід

```text
$ uv sync --frozen
Checked 66 packages in 8ms
EXIT=0
$ uv run ruff check .
All checks passed!
EXIT=0
$ uv run ruff format --check .
280 files already formatted
EXIT=0
$ uv run mypy src
Success: no issues found in 79 source files
EXIT=0
```

Integration PostgreSQL (до додавання моїх тестів; `COLLECTOR_TEST_REQUIRE_DOCKER=1`, тобто
skip неможливий — перетворився б на fail):

```text
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres -q
........................................................................ [ 28%]
........................................................................ [ 56%]
........................................................................ [ 84%]
.......................................                                  [100%]
255 passed in 820.68s (0:13:40)
EXIT=0
```

Мої нові модулі окремо:

```text
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres/test_pr3a_adversarial.py -q
.......................                                                  [100%]
23 passed in 212.96s (0:03:32)
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/postgres/test_pr3a_migration_roles_adversarial.py -q
......                                                                   [100%]
6 passed in 124.62s (0:02:04)
```

`collector db migrate --check` / `alembic check` — одноразовий loopback-контейнер
`postgres:18@sha256:86c951e0…` (`docker run --rm -p 127.0.0.1::5432`), пароль одноразовий і
замаскований, контейнер зупинено після перевірки:

```text
$ uv run collector db migrate --check (порожня БД)
Target database is not up to date.
schema drift: ����� ����������� �� �������
EXIT=1
$ uv run collector db migrate
No new upgrade operations detected.
migrated postgresql+asyncpg://postgres:***@127.0.0.1:53834/postgres: empty -> 0006_queue_outbox_preflight
partition created: audit_log_y2026m09
partition created: audit_log_y2026m10
partition created: audit_log_y2026m11
partition created: audit_log_y2026m12
partition created: fetches_y2026m09
partition created: fetches_y2026m10
partition created: fetches_y2026m11
partition created: fetches_y2026m12
EXIT=0
$ uv run collector db migrate --check
No new upgrade operations detected.
schema up to date: revision=0006_queue_outbox_preflight
EXIT=0
$ uv run alembic check
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.schemas
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.tables
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.types
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.constraints
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.defaults
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.comments
No new upgrade operations detected.
EXIT=0
$ uv run alembic heads
0006_queue_outbox_preflight (head)
```

```text
$ uv run pre-commit run --all-files   (з моїми тестами в index)
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
EXIT=0
```

Повний `pytest -m "not live"` незалежно не повторювався: PostgreSQL integration і два нові
adversarial-модулі вже тривали ~19 хв на спільному Docker Desktop. Реалізатор зафіксував у
`implementation-pr3a.md` 5 падінь wall-clock scaling-тестів під паралельним навантаженням і
успішний ізольований повтор тих самих модулів (21 passed); це раніше відомий флак WP-01D,
не код PR3a. Власний обов'язковий набір `tests/integration/postgres` виконано без skip і без
повторів: 255 passed; після додавання adversarial-тестів окремо 29 passed.

## Acceptance-пункт → тест → результат

| Пункт картки PR3a | Тести (реалізатор / **мої**) | Результат |
|---|---|---|
| 1 queue `release(not_before=+10 хв)` → pending, attempt без змін, не claim-иться раніше | `test_queue_defer.py::test_release_with_not_before_defers_without_burning_attempt`; **`test_defer_keeps_previous_error_fields_and_compensates_attempt`**, **`test_defer_at_max_attempts_does_not_dead_letter`** | pass |
| 1 `retry(not_before=+2 год)` → ≥ +2 год (реалізовано «рівно») | `test_retry_with_not_before_uses_exactly_that_bound`, `…short_table_delay…` | pass |
| 1 `not_before` минуле / далеке майбутнє / інша tz / naive | `…past_not_before_is_clamped…`; **`test_queue_far_future_not_before_is_stored_exactly_and_not_claimable`**, **`test_not_before_with_non_utc_offset_is_the_same_instant`**, **`test_naive_not_before_is_rejected_without_any_write`**, **`test_projection_release_and_retry_clamp_past_not_before`** | pass (naive → `TypeError`, див. F-3) |
| 1 те саме для projection tasks | `test_release_projection_task_with_not_before_defers`, `…retry_projection_task…`; **`test_projection_defer_keeps_error_fields_and_never_quarantines`** | pass |
| 1 конкурентні claim vs defer | **`test_uncommitted_defer_hides_job_from_concurrent_claim`**, **`test_defer_by_stale_owner_waits_for_rival_claim_and_is_rejected`** (два з'єднання, row lock) | pass |
| 2 N-2: crash після `fetch_unpublished` N разів → parked, решта видається | `test_crashing_publisher_parks_poison_row_after_max_delivery_attempts`, `test_parked_poison_row_does_not_block_the_rest` | pass |
| 2 паркування рівно на межі | **`test_max_delivery_attempts_one_parks_on_second_hand_out`**, **`test_raising_the_limit_later_does_not_resurrect_parked_row`** | pass |
| 2 конкурентні publisher-и не видають той самий рядок двічі в межах lease; лічильник росте на кожну видачу | **`test_concurrent_publishers_never_hand_out_the_same_row_within_lease`** (4 publisher-и × 3 раунди × 10 подій + фаза паркування) | pass |
| 2 `mark_failed` + повторна видача не рахують двічі; internal не видається | `test_mark_failed_and_redelivery_do_not_count_one_attempt_twice`, `test_internal_topic_is_not_delivered_by_default` | pass |
| 2 `[]` від `fetch_unpublished` ≠ порожній backlog | **`test_batch_of_only_exhausted_rows_returns_empty_while_backlog_remains`** | pass (фіксує поведінку, F-2) |
| 3 purge: acknowledged internal старший за поріг → видалено; неacknowledged/quarantined/неопублікований domain → лишився | `test_purge_deletes_…`, `test_purge_keeps_…`, `test_purge_is_batched_by_event_id_keyset`; **`test_purge_cutoff_is_strict_and_parked_or_young_rows_stay`**, **`test_concurrent_purges_do_not_double_delete_or_fail`** | pass |
| 4 preflight: paused / circuit_open / відсутній UUID; LOGIN fetcher | `test_fetch_preflight.py` (4), `test_pr3a_roles.py::test_fetcher_runs_…`; **`test_fetch_reads_are_denied_to_roles_without_fetch_grants`** | pass |
| 5 validators: 200+ETag, пізніший 304, новий 200; EXPLAIN без seq scan | `test_validators_follow_last_body_response_and_ignore_304`, `…206…`, `…partial_index_without_seq_scan` | pass |
| 6 route: поріг 3 → circuit_open + audit; reset; конкурентні інкременти | `test_route_failure_threshold_…`, `test_concurrent_route_failures_are_not_lost`; **`test_rolled_back_route_failure_leaves_no_counter_state_or_audit`**, **`test_sub_threshold_failures_do_not_stale_revision_and_reset_is_idempotent`**, **`test_concurrent_failures_crossing_small_threshold_open_exactly_once`** | pass |
| 7 ack fencing: чужий owner → `LeaseNotOwnedError`, жодного запису; без owner — як раніше | `test_ack_with_foreign_owner_is_rejected_without_any_write`, `…taken_over…`, `…without_owner…`; **`test_ack_with_owner_after_recovery_is_rejected_without_any_write`**, **`test_ack_with_owner_for_missing_task_is_rejected`**, **`test_ack_with_owner_on_expired_but_unrecovered_lease_is_accepted`** | pass (див. F-4) |
| 8 `count_retries_since` — межі вікна | `test_count_retries_since_counts_only_retryable_…`; **`test_count_retries_since_window_bounds_partitions_and_timezones`** | pass (naive `since` — F-3) |
| 9 SR-4: stale leased видно; quarantined блокує «повноту»; `settled` vs `complete` | `test_stale_tasks_…`, `test_quarantined_task_blocks_completeness_…`, `test_completeness_reports_unpublished_…`; **`test_settled_and_complete_flags_follow_their_documented_meaning`** | pass |
| 10 ролі: нова операція під своєю LOGIN-роллю проходить, під чужою — denied | `test_pr3a_roles.py` (8); **`test_outbox_publisher_ops_are_denied_outside_scheduler`**, **`test_projection_fencing_and_defer_are_denied_to_non_projector_roles`**, **`test_reconciler_queries_are_denied_to_fetcher_and_translation`**, **`test_queue_defer_is_denied_to_read_only_roles`**, **`test_fetch_reads_are_denied_to_roles_without_fetch_grants`** | pass |
| Міграція `0006`: `alembic check`; upgrade поверх даних; downgrade/upgrade | `test_migrations.py` (цикл base↔head); **`test_upgrade_0006_over_existing_rows_then_downgrade_and_upgrade_again`**; CLI вище | pass |
| Integration-тести не skip-аються мовчки в CI | unit `test_integration_suite_guard.py` (параметризований по модулях — підхопив і мої два); прогін з `COLLECTOR_TEST_REQUIRE_DOCKER=1` | pass |
| `docs/persistence/postgres.md` доповнено | — | **не виконано** (F-1) |
| `deps/WP-01A-to-WP-01D.md` §7 → resolved разом із WP-01B PR3 | §7 оновлено: «PG-частину реалізовано, resolved після WP-01B PR3» | pass (очікувано частково) |

### `settled` і `complete` (ТЗ §7.3 крок 5 vs картка п.9)

`ProjectionCompleteness.settled = open_tasks == 0` (quarantined = done за §7.3 крок 5),
`complete = open_tasks == 0 and quarantined_tasks == 0` (картка п.9: quarantined блокує
«повноту cursor»). Перевірено матрицею в `test_settled_and_complete_flags_follow_their_documented_meaning`:

| Стан | settled | complete |
|---|---|---|
| немає tasks | True | True |
| є pending task | False | False |
| task лише quarantined | True | False |
| task acknowledged + неопублікований `domain.changed` | True | True (outbox на прапорці не впливає, як задокументовано) |
| пізніша відкрита task за межею `created_before` / без межі | True, True / False, False | — |

Обидва прапорці поводяться як задокументовано в docstring. Два прапорці замість одного — свідоме
узгодження розбіжності ТЗ і картки; споживач (WP-01B reconciler) має явно обрати, який з них
вважати сигналом (для drift — `settled`, для закриття cursor — `complete`).

## Рівень §16.1 → тести

| Рівень | Тести |
|---|---|
| 3 Integration (PostgreSQL 18 з нуля, template DB на тест) | `test_queue_defer.py`, `test_outbox_delivery.py`, `test_fetch_preflight.py`, `test_ack_fencing_reconcile.py`, `test_pr3a_roles.py` (реалізатор, 46); **`test_pr3a_adversarial.py` (23)**, **`test_pr3a_migration_roles_adversarial.py` (6)**; регресія всього набору `tests/integration/postgres` |
| 1 Unit (де можливо) | `test_queue_timing.py` (3), `test_metadata.py` (доповнено), `test_integration_suite_guard.py` |

## Додані тести

Коміт `91cd19f test(wp-01a): PR3a adversarial — …` (лише `tests/**`):

- `tests/integration/postgres/test_pr3a_adversarial.py` — 23 тести: `not_before` (далеке
  майбутнє, інша tz, naive, минуле для projection), defer зберігає поля помилки і не веде в dead
  letter на межі `max_attempts`, конкурентні claim vs defer (два з'єднання), fencing ack
  (відновлений lease, відсутній task, протермінований невідновлений lease), N-2 (межа 1,
  підняття ліміту не воскрешає parked, 4 конкурентні publisher-и, `[]` при непорожньому
  backlog), purge (строга межа cutoff, parked, молодий internal, конкурентні purge), route
  counter (rollback атомарний з audit, revision не старіє, reset ідемпотентний, degraded →
  circuit_open, 12 конкурентних через поріг 2), `count_retries_since` (межі, партиції, tz),
  матриця `settled`/`complete`.
- `tests/integration/postgres/test_pr3a_migration_roles_adversarial.py` — 6 тестів: `0005` з
  даними → `0006` (`delivery_attempts=0`, `requested_url_md5` для наявних рядків, partial index
  на кожній партиції, CHECK `>= 0`) → downgrade зі збереженням даних → head без drift; нові
  операції під чужими LOGIN-ролями → permission denied (translation/projector — fetch reads;
  projector/parser/fetcher/api_ro — `fetch_unpublished`/`purge_published`;
  parser/fetcher/api_ro/scheduler — fencing ack і defer projection task; fetcher/translation —
  SR-4 запити; api_ro/export_ro — `queue.release(not_before=)`).

## Mutation-перевірка

Кожна мутація — тимчасова правка `src/`, прогін, `git checkout -- <file>`; після всіх —
`git status` показував лише мої нові тести.

| # | Мутація | Тести | Результат |
|---|---|---|---|
| M1 | `outbox.py` `fetch_unpublished`: `else_=OutboxEvent.delivery_attempts + 1` → `else_=OutboxEvent.delivery_attempts` (видача не рахується) | `-k "max_delivery_attempts_one or concurrent_publishers or only_exhausted"` | **3 failed** (`assert (0 == 1)`, `assert {0} == {1}`, `assert [<…>] == []`) |
| M2 | `projection.py` `acknowledge_projection`: `if owner is not None:` → `if False:` (fencing вимкнено) | `-k ack_with_owner` | **2 failed** (`DID NOT RAISE LeaseNotOwnedError`; `NotFoundError` замість `LeaseNotOwnedError`), 1 passed (протермінований власний lease — дозволено в обох версіях) |
| M3 | `queue.py` `release`: `attempt=func.greatest(CrawlJob.attempt - 1, 0)` → `attempt=CrawlJob.attempt` (defer спалює спробу) | `-k "defer_keeps_previous or defer_at_max"` | **2 failed** (`('pending', 2) == ('pending', 1)`, `('pending', 1) == ('pending', 0)`) |
| M4 | `outbox.py` `purge_published`: `published_at < cutoff` → `<= cutoff` | `-k cutoff_is_strict` | **1 failed** (`published_at == cutoff: межа строга (<)`) |

## Звірка з `implementation-pr3a.md`

- Статичні перевірки, `migrate --check`/`alembic check`, 255 integration — підтверджено
  (у мене 280 файлів у `ruff format` проти 279 — різниця в незакоміченому `.codex`/часі
  прогону, не впливає).
- Семантика `retry(not_before)` «рівно `max(not_before, now)`», defer з компенсацією
  `attempt`, fencing «протермінований невідновлений lease власника дозволено», purge
  internal лише після ack — підтверджено моїми тестами й мутаціями.
- «`fetch_unpublished` може повернути менше за `limit`» — підтверджено, але вводить в оману в
  граничному випадку: може повернути `[]` при непорожньому backlog (F-2).
- Заявлене «`docs/persistence/postgres.md` не доповнено, передано docs-етапу» — підтверджено
  (F-1).
- Флейки `tests/integration/scaling` — див. розділ `not live` вище.

## Знахідки

| ID | Severity | Файл:рядок | Опис |
|---|---|---|---|
| F-1 | medium | `docs/persistence/postgres.md` (не змінено); `docs/plan/cards/WP-01A.md:235` | Acceptance PR3a вимагає «`docs/persistence/postgres.md` доповнено»; не зроблено (реалізатор посилається на owned files/«Docs (етап 5)»). До merge треба або доповнити (docs-етап), або оркестратору явно перенести пункт. Код не блокує. |
| F-2 | low | `src/collector/persistence/postgres/repositories/outbox.py:141-143` | `fetch_unpublished` повертає `[]`, якщо всі вибрані `limit` рядків вичерпали `max_delivery_attempts` (їх лише паркує), хоча видимі рядки лишаються (`count_backlog > 0`). Publisher loop, що трактує `[]` як «черга порожня» і засинає на poll-інтервал, отримає затримку доставки після сплеску poison-подій. Коректність і межа N-2 не порушені. Рекомендація: задокументувати «`[]` ≠ порожній backlog» у docstring або повторювати вибірку в тій самій транзакції, доки не видано хоч щось / не вичерпано кандидатів; WP-01B PR3 має це врахувати. Зафіксовано тестом `test_batch_of_only_exhausted_rows_returns_empty_while_backlog_remains`. |
| F-3 | low | `src/collector/persistence/postgres/repositories/queue.py:96-98` (`clamp_not_before`); `repositories/artifacts.py:160` (`count_retries_since`); `repositories/reconciliation.py:142` (`created_before`) | Naive datetime обробляється неоднорідно: naive `not_before` дає сирий Python `TypeError: can't compare offset-naive and offset-aware datetimes` (не `ValueError` з повідомленням, як `resolve_now`), запису немає (перевірено); naive `since`/`created_before` приймається мовчки (asyncpg інтерпретує як UTC) — проба: `count_retries_since(..., datetime(2026, 9, 1))` → `0` без помилки. Політика §9.6 «UTC only, naive відхиляється» застосовується не всюди. |
| F-4 | info | `src/collector/persistence/postgres/repositories/projection.py:564-570` | Fencing ack дозволяє власнику з **протермінованим, але не відновленим** lease зробити ack (задокументовано, як `queue.complete`). Бриф тестування формулював «протермінований lease → `LeaseNotOwnedError`»; фактично відмова лише після recover/claim іншим. Безпечно (recover/claim серіалізуються тим самим row lock — перевірено), але WP-01D PR1c має знати, що fencing — за власником, а не за часом. |

Flaky у PR3a-наборі не виявлено: усі нові й наявні тести `tests/integration/postgres` пройшли з
першого разу; жодних повторів.

## Вердикт

**CHANGES REQUESTED.** Продуктивна поведінка за пунктами 1–10 підтверджена, 29 нових
adversarial-тестів і чотири mutation-перевірки чутливі до реалізації. До наступного gate треба:

1. закрити F-3 — однаково відхиляти naive `not_before`/`since`/`created_before` до SQL;
2. закрити acceptance F-1 на docs-етапі до merge;
3. зафіксувати контракт F-2 (`[]` не доводить порожній backlog) для WP-01B publisher.

F-4 — інформаційна, виправлення коду не потрібне; достатньо зберегти описану owner-based
семантику fencing у документації споживача WP-01D PR1c.
