# WP-01A PR2: звіт незалежного тестувальника (gate 2)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR2 `wp/01a-2-artifacts-projection` |
| Перевірений коміт | `b5a48a6` (diff `main...HEAD`, WIP-база `c5f6f70`) |
| Картка | `docs/plan/cards/WP-01A.md`: «Спільні вимоги» + «PR2» |
| Dependency | `docs/plan/deps/WP-01D-to-WP-01A.md` (§2 LOGIN-ролі, §3/§5 `queue.release`) |
| Розділи ТЗ | §7.2, §7.3 кроки 2/4, §9.1, §9.5, §10 п.5/п.8, §13, §16.1 п.1 і п.3 |
| Середовище | Windows 11, uv, CPython 3.13.9, PostgreSQL 18 (`wp01a-pg`, loopback 55433); admin-DSN `postgresql://collector_test_admin:***@127.0.0.1:55433/postgres` |
| Вердикт | **pass** (одна знахідка medium, три low, решта info; див. «Знахідки») |

`implementation-pr2.md` я прочитав лише після власного прогону, мутацій і перевірки флаків.

## 1. Команди картки та дослівний вивід

Worktree `.worktrees/wp-01a` на `b5a48a6`:

```text
$ uv sync --frozen
Checked 66 packages in 6ms
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
237 files already formatted
$ uv run mypy src
Success: no issues found in 77 source files
```

Порожня БД `tester_alembic` (`CREATE DATABASE`), `COLLECTOR_POSTGRES_DSN=postgresql://collector_test_admin:***@127.0.0.1:55433/tester_alembic`:

```text
$ uv run alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_control_queue, WP-01A PR1: control plane, job queue, origin limiter, worker pools, audit log.
INFO  [alembic.runtime.migration] Running upgrade 0001_control_queue -> 0002_claim_index, ...
INFO  [alembic.runtime.migration] Running upgrade 0002_claim_index -> 0003_default_partition, ...
INFO  [alembic.runtime.migration] Running upgrade 0003_default_partition -> 0004_artifacts_projection, WP-01A PR2: artifacts, upload claims, projection tasks/acks, outboxes, entity index.
$ uv run alembic check
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.defaults
INFO  [alembic.runtime.plugins] setting up autogenerate plugin alembic.autogenerate.comments
No new upgrade operations detected.
```

(`...` означає кириличні описи ревізій, які консоль cp1251 вивела нечитабельно, а не пропущені рядки.)

```text
$ uv run pytest -m integration tests/integration/postgres -q
........................................................................ [ 43%]
........................................................................ [ 87%]
.....................                                                    [100%]
165 passed in 244.45s (0:04:04)

$ uv run pytest -m "not live" -q          # прогін 1, код реалізатора без моїх тестів
...
SKIPPED [6] tests\e2e\test_gui_runtime_contract.py:167: gui ... (стек не піднятий)
...
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback ...
941 passed, 23 skipped, 8 warnings in 378.02s (0:06:18)
```

23 skip-и: e2e GUI/runtime без піднятого compose-стеку і один Windows-only. Серед них немає
PG- чи PR2-тестів.

Після додавання моїх тестів (той самий `src/`, плюс `tests/integration/postgres/test_pr2_adversarial.py`):

```text
$ uv run ruff check tests && uv run ruff format --check tests
All checks passed!
76 files already formatted
$ uv run pytest -m integration tests/integration/postgres/test_pr2_adversarial.py -q
................                                                         [100%]
16 passed in 14.10s
$ uv run pytest -m "not live" -q          # прогони 2 і 3
957 passed, 23 skipped in 213.61s (0:03:33)
957 passed, 23 skipped in 203.90s (0:03:23)
```

## 2. Пункт картки → тест → результат

| Пункт картки PR2 / «Спільних вимог» | Тест(и) | Результат |
|---|---|---|
| Upload claim: stale generation не може зробити commit | `test_upload_claims.py::test_stale_generation_cannot_commit` | pass; M2 червоний |
| Expired lease → reacquire іншим owner → старий commit падає | те саме; `::test_expired_lease_blocks_commit_even_for_the_owner`; **нове** `test_pr2_adversarial.py::test_commit_exactly_at_lease_expiry_is_rejected` | pass; M2 і M3 червоні |
| Два concurrent producers одного key → рівно один reference | `::test_two_concurrent_producers_of_one_key_create_exactly_one_reference`; **нове** `::test_concurrent_reacquire_of_expired_claim_bumps_generation_once` (3 producers перебирають прострочений claim) | pass |
| `list_orphan_candidates`: лише без DB reference і без живого claim | `::test_orphan_candidates_exclude_references_and_live_claims` | pass |
| 3 паралельні `record_parse_result` → 1,2,3 без дірок і дублів | `test_projection.py::test_parallel_record_parse_result_issues_versions_without_gaps_or_duplicates` | pass; M6 червоний |
| Повторний виклик для того самого artifact → той самий task | `::test_repeated_record_for_same_artifact_returns_same_task`; **нове** `::test_concurrent_record_of_same_artifact_yields_one_task` (одночасний дублікат ключа) | pass |
| Відкат транзакції `record_parse_result` не лишає дірки у версіях | **нове** `::test_rolled_back_record_parse_result_leaves_no_version_gap` | pass |
| Ack у порядку 3,1,2 → confirmed = 3 і ніколи не зменшується | `::test_out_of_order_acks_never_lower_confirmed_version`; **нове** `::test_concurrent_out_of_order_acks_end_at_max_and_emit_only_current` (5 одночасних acks) | pass; M1 червоний |
| Повторний ack ідемпотентний | `::test_repeated_ack_is_idempotent_and_emits_no_second_event`; **нове** `::test_concurrent_duplicate_acks_produce_one_ack_and_one_event` (4 одночасні) | pass |
| `domain.changed` лише для `applied_to_current AND state_changed` | `::test_applied_without_state_change_emits_no_domain_event`, `::test_out_of_order_…`; **нове** `::test_domain_changed_matrix_applied_and_changed` (усі 4 комбінації) | pass |
| Bytes в outbox побайтово дорівнюють bytes receipt | `::test_out_of_order_…` (лише канонічні bytes); **нове** `::test_non_canonical_receipt_bytes_are_stored_verbatim`; **нове** `::test_large_event_as_artifact_is_referenced_not_inlined` | pass; M4 ловить **лише** новий тест |
| Crash-вікно: ack відкочено після insert → жодних часткових записів | `::test_crash_between_ack_steps_leaves_no_partial_rows` | pass |
| Projection queue: claim, heartbeat, complete, index | `::test_projection_queue_claim_heartbeat_retry_release_and_recover`, `::test_retry_on_last_attempt_quarantines_projection_task`, `test_schema_contract.py::test_projection_claim_index_predicate_matches_claimable_statuses`, unit `test_metadata.py::test_mandatory_indexes_present` | pass |
| Outbox publisher API | `test_outbox_entities.py` (3 тести, у т.ч. 2 паралельні publisher-и) | pass |
| Entity index: upsert, `get_confirmed_version`, keyset pagination | `test_outbox_entities.py::test_upsert_entity_…`, `::test_list_entities_keyset_pagination_is_complete_and_stable` | pass |
| Partitioning: місяць без партиції → обрана поведінка задокументована; helper на N місяців | `test_fetch_partitions.py` (3), `test_migrations.py::test_month_partitions_…`, `::test_fetches_default_partition_survives_downgrade_and_is_reattached`; **нове** `::test_fetch_month_boundaries_are_utc` (3 кейси) | pass. Рішення «DEFAULT-партиція + метрика» задокументоване в `partitions.py` |
| Партиціонування `raw_objects`/`change_events`/`outbox_events` | немає (свідоме відхилення) | **відхилення від картки**, див. I-1 |
| `queue.release`: без `attempt++`, без помилки, без карантину | `test_queue_release.py` (2); **нове** `::test_release_by_previous_owner_after_recover_and_reclaim_is_rejected` | pass; M7 червоний |
| Транзакційний audit: mutating-операція без audit неможлива | `test_control_plane_audit.py` (13: сигнатура, рівно один рядок, rollback, порожні actor/reason, `quarantine(owner=None)`) | pass; M8 червоний |
| LOGIN-ролі, `db roles --with-login` ідемпотентний | `test_role_logins.py` (6), `test_cli_db.py::test_db_roles_with_login_…` (2), unit `tests/unit/persistence/postgres/test_role_logins.py` (5) | pass |
| Кожна роль може **рівно** те, що їй потрібно | `test_role_logins.py::test_components_cannot_do_each_others_work`, `::test_each_component_runs_…` | pass, але надлишкові UPDATE-права, див. F-1 і F-2 |
| `collector_fetcher` не читає `news_*` | `::test_components_cannot_do_each_others_work` | вакуумний до PR3 (таблиць `news_*` ще немає) |
| Жоден runtime-сервіс не використовує `collector_migrate` | `::test_every_runtime_role_logs_in_with_its_own_dsn_and_no_migrate_rights`, `::test_runtime_role_that_is_member_of_migrate_is_refused` | pass на рівні БД; compose-частина — за WP-01D, див. I-2 |
| `alembic upgrade head` з нуля → `alembic check` без drift | команди вище; `test_migrations.py::test_upgrade_check_downgrade_cycle_on_clean_database` | pass |
| Без domain JSONB; timestamptz; UUID PK | unit `test_metadata.py::test_no_domain_jsonb_…`, `::test_pr2_tables_carry_no_payload_beyond_bounded_event_bytes` | pass |
| Docstrings з transaction boundary | перевірено читанням `repositories/{artifacts,projection,outbox,entities,queue}.py` | pass |

## 3. Рівні §16.1 → тести

| Рівень (картка: 3 Integration, 1 Unit) | Тести PR2 |
|---|---|
| 3. Integration: PostgreSQL з нуля, lease/recovery, outbox, out-of-order і concurrent projection, міграції з нуля | `tests/integration/postgres/test_{upload_claims,projection,outbox_entities,fetch_partitions,queue_release,control_plane_audit,role_logins,cli_db,migrations,schema_contract}.py` + нове `test_pr2_adversarial.py`. Кожен тест отримує чисту БД з template (`alembic upgrade head` + ролі + партиції) |
| 1. Unit: логіка без БД | `tests/unit/persistence/postgres/test_metadata.py` (unique-ключі, indexes, відсутність JSONB, CHECK-значення з контрактів), `tests/unit/persistence/postgres/test_role_logins.py` (імена секретів, all-or-nothing, SCRAM за RFC 7677) |

## 4. Додані тести

Файл `tests/integration/postgres/test_pr2_adversarial.py`, 16 кейсів:

| Тест | Adversarial-сценарій |
|---|---|
| `test_non_canonical_receipt_bytes_are_stored_verbatim` | receipt несе **неканонічні** bytes (інший порядок ключів, відступи, `\u`-escape); outbox і `change_events` мають зберегти саме їх і їхній SHA-256 |
| `test_domain_changed_matrix_applied_and_changed[4]` | усі чотири комбінації `applied × changed`, включно з `(False, True)` |
| `test_large_event_as_artifact_is_referenced_not_inlined` | подія >256 KiB як `event_artifact`: URI/hash/size, `bytea` порожній (раніше не покрито) |
| `test_concurrent_duplicate_acks_produce_one_ack_and_one_event` | 4 одночасні acks того самого receipt → 1 ack, 1 подія |
| `test_concurrent_out_of_order_acks_end_at_max_and_emit_only_current` | 5 одночасних acks у порядку 5,3,1,4,2 → confirmed = 5, одна подія |
| `test_concurrent_record_of_same_artifact_yields_one_task` | дублікат ключа ідемпотентності від 3 parser-ів одночасно → одна task; наступна версія = 2 |
| `test_rolled_back_record_parse_result_leaves_no_version_gap` | crash до commit → 0 рядків, лічильник не зрушив, replay отримує версію 1 |
| `test_concurrent_reacquire_of_expired_claim_bumps_generation_once` | 3 producers перебирають прострочений claim → generation рівно 2, старий owner не комітить |
| `test_commit_exactly_at_lease_expiry_is_rejected` | межа `lease_expires_at > now` строга |
| `test_release_by_previous_owner_after_recover_and_reclaim_is_rejected` | `queue.release` від owner-а, чий lease уже перебрав інший worker, відхиляється, чужий lease лишається цілим |
| `test_fetch_month_boundaries_are_utc[3]` | `23:59:59.999999Z`, `00:00Z` і `01:00+02:00` на межі місяця; сесія PG у `Pacific/Kiritimati` |

Жоден наявний тест не змінено, не пропущено і не послаблено.

## 5. Mutation-перевірка

Мутації робились в окремому worktree сесії, на тому самому коміті `b5a48a6` з доданим тестом.
Після кожної мутації виконано `git checkout -- <file>`, і `git diff --stat` порожній.

| # | Мутація | Червоні тести (дослівно) |
|---|---|---|
| M1 | `projection.py` ack: `GREATEST(existing, v)` замінено на `= receipt.projection_version` | `FAILED test_projection.py::test_out_of_order_acks_never_lower_confirmed_version` (`assert 1 == 3`); `FAILED test_pr2_adversarial.py::test_concurrent_out_of_order_acks_end_at_max_and_emit_only_current` (`assert 3 == 5`) |
| M2 | `artifacts.commit_reference`: прибрано `owner`/`claim_generation`/`lease_expires_at` з предиката UPDATE | `FAILED test_upload_claims.py::test_stale_generation_cannot_commit`, `::test_expired_lease_blocks_commit_even_for_the_owner`, `test_pr2_adversarial.py::test_concurrent_reacquire_of_expired_claim_bumps_generation_once`, `::test_commit_exactly_at_lease_expiry_is_rejected`: `4 failed, 8 passed` |
| M3 | `commit_reference`: `lease_expires_at > now` замінено на `>=` | `FAILED test_upload_claims.py::test_expired_lease_blocks_commit_even_for_the_owner`, `FAILED test_pr2_adversarial.py::test_commit_exactly_at_lease_expiry_is_rejected`: `2 failed, 23 passed` |
| M4 | `_domain_changed_rows`: `payload_bytes = canonical_json_bytes(descriptor)` (reserialization) | весь `tests/integration/postgres`: `FAILED test_pr2_adversarial.py::test_non_canonical_receipt_bytes_are_stored_verbatim`, `1 failed, 180 passed`. **Наявні тести цю мутацію пропускали**, бо фікстури дають уже канонічні bytes (I-3) |
| M6 | `record_parse_result`: прибрано `.with_for_update()` з `entity_index` | `FAILED test_projection.py::test_parallel_record_parse_result_issues_versions_without_gaps_or_duplicates` (`UniqueViolationError … uq_projection_tasks_entity_uuid_projection_version`) |
| M7 | `queue.release` пише `last_error_code="drain_timeout"` | `FAILED test_queue_release.py::test_release_returns_job_immediately_without_attempt_or_error` |
| M8 | `sources.set_source_state`: `append_audit` не виконується (coroutine не awaited) | `FAILED test_control_plane_audit.py::test_every_listed_mutation_writes_exactly_one_audit_row`, `::test_rollback_removes_change_and_audit_together` |

Мутацію «emit лише за `applied_to_current`» не ставив осмислено: контракт
`AppliedProjectionReceipt._consistent` забороняє event descriptor, якщо не
`applied AND changed`, тож така мутація падає ще на валідації контракту, а не в репозиторії.

## 6. Флаки `pytest -m "not live"` (WP-01D scaling): перевірка твердження реалізатора

Твердження: у 4 з 5 повних прогонів падав один scaling-тест WP-01D (щоразу інший), і той самий
клас збою відтворюється на базі `88323ab`.

Як перевіряв: чистий `git worktree add --detach <scratchpad>/base-88323ab 88323ab` з власним
`.venv` (`uv sync --frozen --offline`). Прогони PR2 і бази чергувались. Після перевірки
worktree видалено (`git worktree prune`, `git worktree list` — лише main, сесія і `wp-01a`).

```text
# isolated: uv run pytest tests/integration/scaling -q, 8 пар
run=1 tree=pr2 :: 33 passed in 47.88s     run=1 tree=base :: 33 passed in 44.23s
run=2 tree=pr2 :: 33 passed in 42.90s     run=2 tree=base :: 33 passed in 45.49s
run=3 tree=pr2 :: 33 passed in 41.85s     run=3 tree=base :: 33 passed in 41.96s
run=4 tree=pr2 :: 33 passed in 45.58s     run=4 tree=base :: 33 passed in 46.50s
run=5 tree=pr2 :: 33 passed in 49.23s     run=5 tree=base :: 33 passed in 42.12s
run=6 tree=pr2 :: 33 passed in 49.04s     run=6 tree=base :: 33 passed in 46.71s
run=7 tree=pr2 :: 33 passed in 49.54s     run=7 tree=base :: 33 passed in 42.37s
run=8 tree=pr2 :: 33 passed in 49.93s     run=8 tree=base :: 33 passed in 49.87s

# під CPU-навантаженням (12 процесів busy-loop на 12 ядрах, перші 75 с кожного прогону)
load run=1 tree=pr2 :: 33 passed in 91.35s   load run=1 tree=base :: 33 passed in 86.96s
load run=2 tree=pr2 :: 33 passed in 85.02s   load run=2 tree=base :: 33 passed in 83.97s
load run=3 tree=pr2 :: 33 passed in 87.24s   load run=3 tree=base :: 33 passed in 91.79s
load run=4 tree=pr2 :: 33 passed in 82.67s   load run=4 tree=base :: 33 passed in 89.75s

# повний uv run pytest -m "not live" -q
full run=1 tree=pr2 :: 957 passed, 23 skipped in 213.61s (0:03:33)
full run=1 tree=base :: 873 passed, 23 skipped in 155.16s (0:02:35)
full run=2 tree=pr2 :: 957 passed, 23 skipped in 203.90s (0:03:23)
full run=2 tree=base :: 873 passed, 23 skipped in 145.80s (0:02:25)
```

Разом із першим повним прогоном: PR2 — 15 прогонів, 0 падінь; база — 14 прогонів, 0 падінь.

**Висновок: не відтворено ні на PR2, ні на базі.** Тому твердження «відтворюється на `88323ab`»
я не можу ні підтвердити, ні спростувати. Воно спирається лише на лог реалізатора (1 з 3
ізольованих прогонів). До того ж той прогін робився через `git checkout 88323ab -- src …` у
робочому дереві PR2, а не на чистому checkout. Флак, імовірно, залежить від навантаження хоста:
перший повний прогін реалізатора тривав ~6 хв, мої пізніші — ~3,5 хв. PR2 не змінює
`src/collector/workers/**`. Для scaling-тестів PR2 додає лише один audit-INSERT в
`pools.upsert_pool` і `tests/integration/postgres/conftest.py`. Жодних ознак, що флак внесено
PR2, я не знайшов. Гіпотеза реалізатора про TOCTOU у `_claim_loop` належить до коду WP-01D і тут
не перевірялась. Статус: low, owner WP-01D (L-4).

## 7. Звірка з `implementation-pr2.md`

| Заява реалізатора | Мій прогін |
|---|---|
| ruff, format, mypy зелені; `upgrade head` + `check` без drift | підтверджено (у мене 237 файлів, бо звіт реалізатора вже закомічено) |
| `tests/integration/postgres`: 165 passed | підтверджено: 165 passed |
| `not live`: 1 з 5 прогонів зелений, решта з одним флаком WP-01D | у мене 3 з 3 зелені (941 → 957 з моїми тестами) |
| Флак відтворюється на `88323ab` | не відтворено за 14 прогонів бази (§6) |
| Таблиця acceptance → тест | тести існують і зелені. Два уточнення: «bytes побайтово» наявні тести доводили лише для канонічних bytes (I-3); «кожна роль рівно те, що потрібно» тестами не доведено (F-1, F-2) |
| Партиціоновано лише `fetches` і `audit_log` | підтверджено; відхилення I-1 |
| Тест-вартовий WP-01D лишився зеленим | підтверджено (I-2) |
| `news_*` — вакуумна перевірка | підтверджено |

## 8. Знахідки

| ID | Severity | Файл:рядок | Опис |
|---|---|---|---|
| F-1 | **medium** | `src/collector/persistence/postgres/sql/roles.sql:123`, `:131` | `collector_parser` і `collector_projector` мають табличний `UPDATE` на `entity_index`. Parser може **знизити** `confirmed_projection_version` і скинути `projection_version`. Projector теж може скинути `projection_version`, і тоді наступний `record_parse_result` впаде на unique `(entity_uuid, projection_version)`. Інваріант §9.5 «ніколи не зменшується» тримається лише кодом репозиторію, а на рівні БД ні GRANT, ні trigger/CHECK його не захищають. Картка вимагає «кожна роль може **рівно** те, що їй потрібно». Відтворення (psql, `SET ROLE collector_parser`): `UPDATE entity_index SET confirmed_projection_version = 0, projection_version = 0 RETURNING …` → `0 \| 0`, `UPDATE 1`. Для projector так само: `UPDATE 1`. Рекомендація: column-level `GRANT UPDATE (projection_version, updated_at)` для parser (для `FOR UPDATE` цього достатньо: PG вимагає UPDATE хоча б на одну колонку) і `(confirmed_projection_version, confirmed_at, mongo_collection, mongo_document_id, updated_at)` для projector; плюс trigger, що відхиляє зменшення обох лічильників. Після виправлення потрібен тест у `test_role_logins.py` |
| F-2 | low | `src/collector/persistence/postgres/sql/roles.sql:125` | `collector_parser` має `INSERT` на `outbox_events` без обмеження `topic`. Parser може вставити `topic='domain'` і так підробити `domain.changed` в обхід ack-шляху (відтворено: `INSERT … 'domain' … RETURNING topic` → `domain`). Захист у глибину §13 «parser пише лише pointer/task/outbox(projection.command)»: CHECK/trigger за `current_user` або окрема таблиця чи RLS-політика для `internal` |
| F-3 | low | `src/collector/persistence/postgres/repositories/projection.py:520-532` | Повторний ack з **іншим** receipt для того самого `task_id` (інші `result_hash`/`event_bytes`/`event_id`) мовчки повертає наявний запис з `created=False` без порівняння. Суперечливий replay reconciler-а (наприклад, після ручного втручання в Mongo) нічим не сигналізується. Варто порівнювати ключові поля й кидати `ConflictError` при розбіжності (так уже зроблено в `commit_reference` для `sha256`) |
| L-4 | low | `tests/integration/scaling/**` (WP-01D) | Флак, про який заявив реалізатор, за 29 прогонів не відтворено (§6). Для PR2 не блокер, передано WP-01D (`WP-01A-to-WP-01D.md` §5) |
| I-1 | info (відоме відхилення) | `src/collector/persistence/postgres/partitions.py:5-14`, `migrations/.../20260923_0004_artifacts_projection.py:18-24` | `raw_objects`, `change_events`, `outbox_events` не партиціоновані через глобальні unique (`sha256`, `event_id`). Це відхилення від «Спільних вимог» картки. Обґрунтування задокументоване, рішення за spec-review/оркестратором; ризик — ріст `outbox_events` без retention (WP-12) |
| I-2 | info | `tests/unit/test_compose_config.py:406` (WP-01D) | Тест-вартовий не спрацював, бо LOGIN вмикається з Python (`roles.apply_logins`), а не в SQL-файлах. Поки WP-01D не переведе сервіси на `postgres_dsn_<component>`, runtime ходить superuser-DSN. Явно оформлено в `docs/plan/deps/WP-01A-to-WP-01D.md` §1. Пункт картки «жоден runtime-сервіс не використовує `collector_migrate`» закрито лише на боці БД |
| I-3 | info (закрито цим gate) | `tests/integration/postgres/conftest.py` (`receipt`) | Наявний тест «bytes побайтово» не відрізняв збереження bytes від reserialization (M4 пройшов усі 180 наявних тестів). Закрито тестом `test_non_canonical_receipt_bytes_are_stored_verbatim` |
| I-4 | info | `src/collector/persistence/postgres/clock.py:1-7`, `repositories/artifacts.py` (`commit_reference`) | Предикат lease порівнюється з часом застосунку, а не з `now()` БД, як написано в картці. Для коректності fencing це безпечно, бо генерацію захищає row lock і `claim_generation`. Припущення NTP задокументоване |
| I-5 | info | `tests/integration/postgres/test_role_logins.py` (`news_*`) | Перевірка «fetcher не читає `news_*`» вакуумна до PR3 |

## 9. Вердикт

**pass.** Усі тест-пункти й операції PR2 з картки мають тести, які проходять на реальному
PostgreSQL 18. `alembic check` без drift, lint і types зелені. Ключові гілки (GREATEST, fencing
предикат, межа lease, row lock видачі версії, bytes без reserialization, `queue.release`,
audit) захищені тестами, що червоніють на мутаціях. Critical/high знахідок немає. F-1 (medium)
варто виправити до merge PR2 або явно перенести в PR3 рішенням оркестратора: це порушення
буквального «рівно те, що потрібно» з картки, а не функціональна помилка репозиторію.
Відхилення I-1 фіксується для spec-review.
