# WP-01A PR2 — пострев'ю за ТЗ (`wp/01a-2-artifacts-projection`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR2 «artifacts, upload claims, projection tasks/acks, outboxes, entity index» |
| Branch / worktree | `wp/01a-2-artifacts-projection` / `.worktrees/wp-01a` |
| Рев'юваний commit | `bad6a25` (`git diff main...HEAD` — 49 файлів, +8333 / −108; 10 комітів `c5f6f70..bad6a25`) |
| Картка | `docs/plan/cards/WP-01A.md` — «Спільні вимоги» + «PR2» |
| Розділи ТЗ | §7.3 кроки 2/4/5, §9.1, §9.3 п.4–5, §9.5, §10 п.5/п.8/п.13, §13, §15, §16.1 п.3, §16.3 (дотичні), §17.2, §18, §20, Додаток C |
| REVIEW.md | R-24, R-27, R-28, R-32, R-36, R-38/R-41, R-43, R-53 |
| Вхідні звіти | `implementation-pr2.md`, `testing-pr2.md` (pass @ `b5a48a6`), `code-review-pr2.md` (changes_requested @ `e395b24`), `security-pr2.md` (approve @ `e395b24`) |
| Dependency | `deps/WP-01D-to-WP-01A.md`, `deps/WP-01A-to-WP-01D.md`, `deps/WP-01A-to-WP-00.md` §4 |
| Рев'юер | wp-spec-reviewer, read-only (звіт збережено оркестратором — у рев'юера немає Write) |

## Вердикт

**`changes_requested`** — `missing`: 2 (Acceptance PR2 «CI зелений», DoD §18 п.7 «secret scan чистий»), `partial`: 19. Одна відкрита знахідка **high** — SR-1.

Усі знахідки попередніх етапів (F-1…F-3, CR-1…CR-10, S-1…S-6) — `fixed`, перевірено в коді й тестах на `bad6a25` (§7). Для злиття потрібно:

1. закрити SR-1 (gitleaks);
2. оформити ADR-0007 і правку ТЗ §9.1 + картки для рішення D-1 (партиціювання) — ТЗ змінюється до злиття;
3. внести правку тексту картки PR2 для рішення D-2 (`parse_key`);
4. бажано закрити SR-2 (medium, узгодженість lineage) у цьому ж PR, бо від неї залежить коректність D-2.

Обсяг приймання — лише PR2. PR3 (news/matching/release/retention/capacity), `docs/persistence/postgres.md` і `docs/runbooks/migrations.md` цим вердиктом не закриваються.

## Власна верифікація (Windows 11, uv, CPython 3.13, PostgreSQL 18 `wp01a-pg`, HEAD `bad6a25`)

```text
$ uv run ruff check .                      → All checks passed!
$ uv run ruff format --check .             → 242 files already formatted
$ uv run mypy src                          → Success: no issues found in 77 source files
$ uv run pytest tests/unit/persistence -q  → 57 passed in 3.66s
$ uv run pytest -m integration tests/integration/postgres -q
                                           → 197 passed in 382.36s (0:06:22)   exit=0
$ gitleaks git --log-opts="main..HEAD" --no-banner --redact -v
  RuleID: generic-api-key  File: tests/unit/persistence/postgres/test_role_logins.py  Line: 126
  Commit: bad6a259ba02126bec66cfc1febbdb846d0c0e3d
  10 commits scanned. ... WRN leaks found: 1
$ gh pr list --head wp/01a-2-artifacts-projection --state all   → (порожньо; PR не відкрито, CI не запускався)
```

`pytest -m "not live"` на HEAD не повторювався — взято вивід реалізатора (`977 passed, 23 skipped`).

Статичні зонди: diff лише в owned-файлах картки; `grep -rn "offset(" src/collector/persistence/postgres/` — 0 (§15); `verify_runtime_login` поза визначенням не викликається; ADR про відмову від партиціювання event-таблиць немає.

## 1. Acceptance: картка WP-01A → доказ → статус

### 1.1. PR2 — операції

| # | Вимога картки | Доказ | Статус |
|---|---|---|---|
| P-1 | 10 таблиць §9.1 PR2 | міграція `0004` (усі 10 `create_table`); `test_metadata.py::test_pr1_and_pr2_tables_are_registered`; `test_migrations.py::test_models_match_card_contracts` | evidenced |
| P-2 | LOGIN-ролі для 7 runtime-ролей, `db roles --with-login`, ідемпотентно | `roles.py:215` `apply_logins`, `:268` `verify_runtime_login`; `cli.py:336-378`; `test_role_logins.py` (integration + unit); `test_cli_db.py::test_db_roles_with_login_*` | evidenced |
| P-3 | Власний DSN кожному сервісу | формат `postgres_dsn_<component>` (`roles.py:68`); секрети/compose — `deps/WP-01A-to-WP-00.md` §4, `deps/WP-01A-to-WP-01D.md` §1 (open) | partial (WP-00/WP-01D) |
| P-4 | Кожна роль може рівно те, що їй потрібно | `test_role_logins.py::test_each_component_runs_its_repository_operations_under_its_own_role`, `::test_components_cannot_do_each_others_work`, `::test_column_level_grants_close_gate3_security_findings`, `::test_parser_cannot_forge_domain_outbox_events`; `sql/roles.sql:142-184` | partial (`news_*` — вакуумно до PR3) |
| P-5 | Жоден runtime-сервіс не використовує `collector_migrate` | на рівні БД — `test_role_logins.py::test_every_runtime_role_logs_in_with_its_own_dsn_and_no_migrate_rights`, `::test_runtime_role_that_is_member_of_migrate_is_refused`, `::test_privileged_membership_is_refused_by_apply_and_verify`; compose досі на `postgres_dsn` | partial |
| P-6 | Узгодити тест-вартовий WP-01D | `deps/WP-01A-to-WP-01D.md` §1 | evidenced |
| P-7 | `queue.release` без інкременту `attempt`, без карантину | `queue.py:344`, `:377`; `test_queue_release.py`; `test_pr2_adversarial.py::test_release_by_previous_owner_after_recover_and_reclaim_is_rejected`; мутація M7 | evidenced |
| P-8 | Транзакційний audit для 7 операцій control plane | `sources.py`, `limiter.py:249`, `queue.py:288`, `pools.py:109`; `test_control_plane_audit.py` (15); мутація M8 | evidenced |
| P-9 | Upload claim fencing | `artifacts.py:152`, `:238`; `test_upload_claims.py` (11); adversarial; мутації M2, M3 | evidenced (I-4: час застосунку, не `now()` БД) |
| P-10 | `list_orphan_candidates(grace)` | `artifacts.py:399` + протокол sweeper-а; `test_upload_claims.py::test_orphan_candidates_exclude_references_and_live_claims`, `::test_sweeper_fencing_protocol_blocks_the_race_with_a_new_producer`, `::test_sweeper_claim_is_refused_when_a_producer_reacquired_first` | evidenced |
| P-11 | `record_parse_result` в одній транзакції | `projection.py:134-275` (`FOR UPDATE` `:177-183`); `test_projection.py::test_parallel_record_parse_result_issues_versions_without_gaps_or_duplicates`; adversarial rollback; мутація M6 | partial — SR-2 |
| P-12 | Unique `(entity_uuid, projection_version)` і artifact projection key | `0004:511-518`; `projection.py:114-131,198-204`; тести A→B→A, same bytes/another key, typed conflict, concurrent same artifact | evidenced — з відхиленням D-2 (прийнято, §5.2) |
| P-13 | Projection queue + index | `projection.py:314-503`; `0004:539-543`; `test_metadata.py::test_mandatory_indexes_present` | evidenced |
| P-14 | `acknowledge_projection` | `projection.py:505-630` (`GREATEST` `:596`, `_require_same_receipt` `:668`); тригер `0005:29-52`; T-3/T-4 | evidenced |
| P-15 | Outbox publisher API | `outbox.py:50`, `:108`, `:132`, `:183`; `0004:295-300`; `test_outbox_entities.py` | evidenced |
| P-16 | Entity index + keyset | `entities.py:51,123,163`; `0004:157-166`; `test_outbox_entities.py::test_upsert_entity_is_idempotent_by_source_identity`, `::test_list_entities_keyset_pagination_is_complete_and_stable` | evidenced |

### 1.2. PR2 — тести картки

| # | Тест картки | Доказ | Статус |
|---|---|---|---|
| T-1 | upload claim: stale generation; expired → reacquire; 2 producers → 1 reference | `test_upload_claims.py::test_stale_generation_cannot_commit`, `::test_expired_lease_blocks_commit_even_for_the_owner`, `::test_two_concurrent_producers_of_one_key_create_exactly_one_reference` | evidenced |
| T-2 | 3 паралельні `record_parse_result` → 1,2,3; повтор → той самий task | `test_projection.py:54`, `:90`, `:389` | evidenced (D-2) |
| T-3 | ack 3,1,2; ідемпотентність; `domain.changed` за умовою; bytes побайтово | `test_projection.py` (4 тести); `test_pr2_adversarial.py` (4 тести); мутації M1, M4 | evidenced |
| T-4 | crash-вікно ack | `test_projection.py::test_crash_between_ack_steps_leaves_no_partial_rows` | evidenced |
| T-5 | `fetches` без партиції; helper на N місяців | обрано DEFAULT-партицію + `default_partition_row_count` (`partitions.py:24-31`); `test_fetch_partitions.py` (3); `test_migrations.py::test_fetches_default_partition_survives_downgrade_and_is_reattached`; `::test_fetch_month_boundaries_are_utc[3]` | evidenced (формулювання картки оновити разом з D-1) |

### 1.3. Acceptance PR2

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| AC-1 | Тести зелені | власний прогін 197 passed на `bad6a25` | evidenced |
| AC-2 | `alembic check` | `implementation-pr2.md`; `test_migrations.py::test_upgrade_check_downgrade_cycle_on_clean_database` | evidenced |
| AC-3 | CI зелений | PR не відкрито; job `secrets` (`gitleaks-action@v2`, `ci.yml:231-249`) впаде через SR-1; LOGIN-тести в режимі `trust` у CI не прогнані | **missing** |
| AC-4 | Docstrings з transaction boundary | `repositories/{artifacts,projection,outbox,entities,queue}.py` | evidenced |

### 1.4. «Спільні вимоги» — у межах PR2

| # | Вимога | Доказ | Статус |
|---|---|---|---|
| S-1 | Alembic forward-only; upgrade з нуля → check | `0004` downgrade лише dev (`:26-29`); `0005`; AC-2 | evidenced |
| S-2 | UUID PK | `raw_objects.raw_object_id` UUID + unique `sha256`; `fetches` PK `(fetch_id, fetched_at)` вимушено партиціюванням | evidenced |
| S-3 | Гроші | — | not applicable |
| S-4 | timestamptz UTC | `test_metadata.py::test_no_domain_jsonb_and_all_timestamps_are_timestamptz`; UTC-межі партицій | evidenced |
| S-5 | Source-time колонки | `fetches.last_modified_raw` сирий | not applicable (PR3 news) |
| S-6 | Без domain JSONB (R-27) | `test_metadata.py::test_pr2_tables_carry_no_payload_beyond_bounded_event_bytes` | evidenced |
| S-7 | Optimistic `revision` | — | not applicable |
| S-8 | Типізований async API | `errors.py`; сирий `IntegrityError` назовні не виходить | evidenced |
| S-10 | Integration на чистій схемі | `tests/integration/postgres/conftest.py` | evidenced |
| S-11 | Місячне партиціювання 5 таблиць + helper | `fetches` — `0004:213-214`; `PARTITIONED_TABLES = ("audit_log", "fetches")`; решта три свідомо ні | partial — D-1 |

### 1.5. §17.2 (частина PR2)

artifact pointers, projection tasks/acks, outboxes, entity index/lineage; clean SQL integration green — evidenced для scope PR2 (CI — AC-3).

### 1.6. Розділи ТЗ

| # | Вимога ТЗ | Доказ | Статус |
|---|---|---|---|
| Z-1 | §7.3 крок 2 | P-11/P-12 | evidenced (SR-2 окремо) |
| Z-2 | §7.3 крок 4, §10 п.10, §9.5 | P-14, T-3; тригер `0005`; `test_role_logins.py::test_version_guard_applies_even_to_the_superuser` | evidenced |
| Z-3 | §7.3 крок 5 (reconciler) | немає запитів «незавершені tasks» і «повнота cursor» | partial — SR-4 |
| Z-4 | §10 п.5 | P-9, P-10 | evidenced |
| Z-5 | §10 п.8 | P-11, S-6 | evidenced |
| Z-6 | §10 п.13 | visibility lease | evidenced |
| Z-7 | §13 | column GRANT + RLS; runtime у compose — P-5 | partial |
| Z-8 | §15 keyset | P-16 | evidenced |

### 1.7. Дотичні пункти §16.3 (PostgreSQL-частина)

| # | Пункт | Статус |
|---|---|---|
| H-1 | Повторний parse/projection тієї самої raw відповіді без дублікатів | evidenced (PG) |
| H-2 | Fault після Mongo commit до PG ack → replay; drift → 0 | partial (reconciler — WP-01B; SR-4) |
| H-3 | Порядок 3,1,2 → current 3; один ack на task | partial (Mongo/export — WP-01B/WP-11A) |
| H-4 | Fault після S3 PUT до PG commit | partial (S3/HEAD — WP-02) |
| H-5 | Scale-down повертає lease | partial (runtime WP-01D ще на `retry`) |
| H-6 | Lineage за один lookup | partial (SR-2; API — WP-11A) |

## 2. Definition of Done §18

| # | Пункт | Статус |
|---|---|---|
| 1 | Один WP, без сторонніх змін | evidenced |
| 2 | formatter, lint, types, tests | evidenced |
| 3 | Migration + compatibility evidence | evidenced (0004 змінено in-place після gate 3 — dev-БД з ранніх комітів PR2 пересоздати; вказати в описі PR) |
| 4 | Temporal/replay evidence | evidenced |
| 5 | Адаптер | not applicable |
| 6 | Документація, метрики, runbook | partial (за карткою — PR3/WP-12) |
| 7 | Secret scan чистий | **missing** — SR-1 |
| 8 | Findings рецензентів позначені | partial — SR-6 (security I-1/I-2 без owner/date) |
| 9 | Злиття після CI і review | partial (PR не відкрито) |

## 3. Додаток C — див. рядки traceability (`docs/acceptance/traceability.md`, блок WP-01A PR2)

## 4. Регресія REVIEW.md

| R | Статус |
|---|---|
| R-24 Polyglot boundary | evidenced |
| R-27 Без payload JSONB | evidenced |
| R-28 Точні storage-контракти | evidenced |
| R-32 Compound indexes | evidenced |
| R-36 Exact version record | evidenced (CR-1 був регресією R-36, виправлено) |
| R-38/R-41 Upload claim fencing | evidenced |
| R-43 Temporal axes | evidenced для scope PR2 |
| R-53 Origin permits у PG | evidenced (без регресії) |

## 5. Рішення щодо задекларованих відхилень

### 5.1. D-1: `raw_objects`, `change_events`, `outbox_events` не партиціоновані

**Прийняти для PR2 за умов, виконаних до злиття.** Аргумент коректний (PostgreSQL не підтримує unique без partition key), але ТЗ §9.1 прямо вимагає місячного партиціювання fetch/event-таблиць, тож потрібні ADR і правка ТЗ.

- `raw_objects` — прийняти без застережень (не fetch/event-таблиця в §9.1).
- `outbox_events` — прийняти за умови retention: функції видалення/архівації опублікованих рядків немає. ADR призначає owner: `purge_published(older_than)` — WP-01A PR3, виклик — WP-12.
- `change_events` — прийняти тимчасово: росте без меж (Q-005). ADR має записати варіант «місячні партиції + вузька непартиціонована `change_event_ids(event_id PK)`» і тригер перегляду (обсяг / 2× річний прогноз §15), owner — WP-01A PR3 / WP-12.

Умови: (1) `docs/decisions/0007-…md`; (2) правка ТЗ §9.1; (3) правка «Спільних вимог» картки і тесту T-5.

### 5.2. D-2: `parse_key` замість «той самий artifact → той самий task»

**Прийняти.** Точніше відповідає §16.3 («тієї самої raw відповіді»), §9.3 п.5, §7.3 крок 3 і R-36 (A→B→A має дати нову версію). Дедуплікація вмісту лишилась на `normalized_artifacts`.

Наслідки для фіксації: нова `parser_version` → нова версія (очікувана reprojection); `target_schema_version` у ключ не входить (версія парсера має змінюватись разом зі схемою); ключ спирається на чесний `fetch_id`.

Умови: (1) правка тексту картки PR2; (2) рекомендовано уточнити ТЗ §9.1; (3) нотатка для `NormalizedArtifactRef.fetch_id` (WP-01C) і картки WP-02; (4) SR-2.

## 6. Q-питання §20

Q-005 — safe default дотримано (жодного DELETE), але D-1 потребує retention в ADR-0007. Q-010, Q-013 — not applicable для WP-01A.

## 7. Статус знахідок попередніх етапів

F-1…F-3, CR-1…CR-10, S-1…S-6 — fixed (перевірено на `bad6a25`). L-4 (testing) — transferred WP-01D. Security I-1, I-2 — **без owner/date** (SR-6).

## 8. Dependency WP-01D → WP-01A

У межах WP-01A закрито повністю (LOGIN-ролі, формат DSN-секретів, мінімальні права, `verify_runtime_login`, `queue.release`, `command_timeout`). Лишається: **WP-00** — 7 секретів в `init-secrets.sh` + `migrate-postgres` з `db roles --with-login`; **WP-01D** — per-role DSN, прибрати `postgres_dsn` з runtime, замінити тест-вартовий, викликати `verify_runtime_login`, роль для `export`, перейти на `release`. Порушення §13 у runtime блокує pilot і non-dev розгортання. Ledger застарів (SR-6).

## 9. Знахідки цього пострев'ю

| ID | Severity | file:line | Суть | Що зробити | Статус |
|---|---|---|---|---|---|
| SR-1 | **high** | `tests/unit/persistence/postgres/test_role_logins.py:126` (коміт `bad6a25`) | Синтетичний пароль ловить gitleaks `generic-api-key`; CI job `secrets` сканує всі коміти PR → червоний CI (DoD п.7, AC-3) | Будувати значення в рантаймі + `# gitleaks:allow`; для коміту в історії — переписати до відкриття PR або fingerprint у `.gitleaksignore`; підтвердити `gitleaks git --log-opts=main..HEAD` → no leaks | open |
| SR-2 | medium | `repositories/projection.py:191-215`; `tests/integration/postgres/conftest.py:237-240` | `record_parse_result` не звіряє `attempt.fetch_id/raw_sha256/parser_version/domain` з `artifact_ref`; `parse_key` змішує поля двох об'єктів; `parse_attempts.fetch_id` може бути NULL | Вимагати рівності (або брати з `artifact_ref`) → `InvalidValueError`/`ConflictError`; `fetch_id` обов'язковий для успішного parse; оновити фікстуру; негативний тест | open |
| SR-3 | medium (ADR/ТЗ) | `models/outbox.py:13-27`, `partitions.py:5-14`, ТЗ §9.1 | D-1 без ADR; retention outbox заявлено, не реалізовано | ADR-0007 + правка ТЗ §9.1 + картки | open |
| SR-4 | low | `repositories/projection.py` | Немає PG-запитів для reconciler §7.3 крок 5 | Dependency від WP-01B або пункт PR3 | open (не блокує) |
| SR-5 | low | `partitions.py:9` | Docstring каже PK `raw_objects` = `sha256(body)`; фактично UUID + unique `sha256` | Виправити текст | open |
| SR-6 | low | `security-pr2.md`, `docs/plan/ledger.md:60-61` | Security I-1/I-2 без owner/date; ledger застарів | I-1 → WP-01D/WP-00 (до pilot); I-2 → WP-00 (`REVOKE CONNECT, TEMP … FROM PUBLIC`); оновити ledger | open |

## 10. Підсумок

`missing`: 2 (через SR-1); `partial`: 19; critical/high без `fixed`: SR-1; D-1, D-2 прийнято з умовами. **`changes_requested`.** Повторне приймання — після SR-1 (обов'язково), SR-2 (рекомендовано) і документів D-1/D-2.
