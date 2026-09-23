# WP-01A PR2 — повторне пострев'ю за ТЗ (gate 4', `wp/01a-2-artifacts-projection`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR2 «artifacts, upload claims, projection tasks/acks, outboxes, entity index» |
| Branch / worktree | `wp/01a-2-artifacts-projection` / `.worktrees/wp-01a` |
| Рев'юваний commit | `321515f` (diff від попереднього рев'ю `bad6a25..HEAD`: 26 файлів, +1235 / −99; коміти `75a8bdc`, `396eed5`, `c8ae84d`, `321515f`) |
| Попередній звіт | `spec-review-pr2.md` @ `bad6a25` — changes_requested |
| Вхідні звіти | `code-review-pr2-r2.md` (approve, N-1..N-5 low), `implementation-pr2.md` «Fixes after gate 4», `docs-pr2.md` |
| Рев'юер | wp-spec-reviewer, read-only (звіт збережено оркестратором — у рев'юера немає Write) |

## Вердикт

**`accept`**: `missing` 0, `partial` 14 (13 в acceptance/DoD — передано іншим WP/PR3 або «CI ще не запускався»; 1 в умовах D-2 — SR-7), відкритих critical/high немає.

Умови злиття (не блокують приймання):

1. **SR-7 (low).** Виправити рядок картки `WP-01A.md:115`, що суперечить D-2.
2. **CI.** Зелений CI на відкритому PR, зокрема job-и `secrets` і `integration-postgres` (LOGIN-тести в режимі `trust`).
3. **Спосіб злиття.** Merge-commit, без rebase: fingerprint у `.gitleaksignore` прив'язаний до SHA `bad6a25` (SR-9).

## Власна верифікація (PostgreSQL 18 `wp01a-pg`, HEAD `321515f`)

```text
$ uv run ruff check .                        → All checks passed!
$ uv run ruff format --check .               → 247 files already formatted
$ uv run mypy src                            → Success: no issues found in 77 source files
$ uv run pytest tests/unit/persistence -q    → 57 passed in 1.81s
$ uv run pytest -m integration tests/integration/postgres -q
                                             → 209 passed in 262.16s (0:04:22)   exit=0
$ gitleaks git --log-opts="main..HEAD" --no-banner --redact      (gitleaks 8.30.1)
  14 commits scanned. ... no leaks found     exit=0
# контроль: clone у scratchpad без .gitleaksignore робочого дерева
$ gitleaks git --log-opts="main..tmpbr" -v
  RuleID: generic-api-key  File: tests/unit/persistence/postgres/test_role_logins.py  Line: 126
  Commit: bad6a259ba02126bec66cfc1febbdb846d0c0e3d
  Fingerprint: bad6a259ba02126bec66cfc1febbdb846d0c0e3d:tests/unit/persistence/postgres/test_role_logins.py:generic-api-key:126
  14 commits scanned. ... leaks found: 1
$ gh pr list --head wp/01a-2-artifacts-projection --state all  → (порожньо; PR не відкрито)
```

Без ignore-файлу gitleaks знаходить рівно одну знахідку, і її fingerprint посимвольно збігається з єдиним рядком `.gitleaksignore`; інших придушених знахідок немає. `pytest -m "not live"` не повторювався (реалізатор: 989 passed / 23 skipped з другої спроби; перша впала на відомому нестабільному тесті WP-01D `test_self_fencing_fires_when_the_database_hangs_without_raising`, який PR2 не змінював).

## 0. Статус знахідок gate 4 і gate 3'

| ID | Severity | Доказ | Статус |
|---|---|---|---|
| SR-1 | high | `test_role_logins.py:127-128` (`secrets.token_hex`); `.gitleaksignore:1-9` (лише fingerprint + пояснення); `deps/WP-01A-to-WP-00.md` §5; gitleaks-вивід вище | **fixed** |
| SR-2 | medium | `projection.py:178`, `:281-308` (`_require_consistent_lineage` до першого запису); `conftest.py:237-249`; `test_projection.py::test_attempt_and_artifact_lineage_must_describe_one_parse` (5 кейсів) | **fixed** |
| SR-3 | medium (ADR/ТЗ) | ADR-0007; `TECHNICAL_SPECIFICATION.md:499`, `:508`; `cards/WP-01A.md:39`, `:127`, PR3 `purge_published` | **fixed** |
| SR-4 | low | картка PR3 «Reconciler §7.3 крок 5» | **transferred** (WP-01A PR3; споживач — WP-01B) |
| SR-5 | low | `partitions.py:9-10` | **fixed** |
| SR-6 | low | `ledger.md:20`, `:60-63`; `deps/WP-01A-to-WP-00.md` §6 | **fixed** (owner і рубіж «до pilot») |
| N-1 | low | `deps/WP-01A-to-WP-02.md` §1 | **transferred** (WP-02) |
| N-2 | low | `deps/WP-01A-to-WP-01D.md` §7 | **transferred** (WP-01D) |
| N-3 | low | `outbox.py:247-271`; `test_outbox_entities.py::test_oldest_unpublished_age_matches_backlog_scope` | **fixed** |
| N-4 | low | `sql/roles.sql:162-169` (звірено з усіма UPDATE у `projection.py`); `test_role_logins.py::test_projector_cannot_rewrite_task_identity_columns` | **fixed** |
| N-5 | low | `roles.py:185-200`, `:283-298`; `test_role_logins.py::test_gate4_privileged_memberships_are_refused[4]`, `::test_runtime_login_with_own_createdb_is_refused` | **fixed** |

F-1…F-3, CR-1…CR-10, S-1…S-6 лишаються `fixed` (209 passed).

## 1. Acceptance — змінені рядки

| # | Вимога | Статус |
|---|---|---|
| P-3 | Власний DSN кожному сервісу | partial (WP-00/WP-01D) |
| P-4 | Кожна роль може рівно те, що їй потрібно | partial (`news_*` — PR3) |
| P-5 | Runtime не використовує `collector_migrate` | partial (compose — WP-01D/WP-00) |
| P-11 | `record_parse_result` в одній транзакції | **evidenced** (було partial) |
| P-12 | Unique `(entity_uuid, projection_version)` + `parse_key` | evidenced |
| T-2, T-5 | формулювання картки оновлено | evidenced |
| AC-1, AC-2, AC-4 | 209 passed; `alembic check`; docstrings | evidenced |
| AC-3 | CI зелений | **partial** — PR не відкрито; умова злиття |
| S-11 | Партиціювання + helper | **evidenced** (було partial; ADR-0007) |

Розділи ТЗ: Z-1, Z-2, Z-4..Z-6, Z-8 — evidenced; Z-3 — partial (PR3, SR-4); Z-7 — partial (runtime у compose). §16.3: H-1 evidenced; H-2..H-6 partial (WP-01B/WP-02/WP-01D/WP-11A).

## 2. Definition of Done §18

| # | Статус |
|---|---|
| 1 Один WP | evidenced (`.gitleaksignore` — погоджений виняток) |
| 2 lint/types/tests | evidenced |
| 3 Migration + compatibility | evidenced (dev-БД з ранніх комітів PR2 пересоздати — вказати в описі PR) |
| 4 Temporal/replay | evidenced |
| 5 Адаптер | not applicable |
| 6 Документація/метрики/runbook | partial (метрики — WP-12) |
| 7 Secret scan | **evidenced** (було missing) |
| 8 Findings позначені | evidenced |
| 9 Злиття після CI | partial (умова злиття) |

## 3. Регресія REVIEW.md

R-24, R-27, R-28, R-32, R-38/R-41, R-43, R-53 — evidenced; R-36 — evidenced (посилено SR-2).

## 4. Умови D-1 / D-2

- **D-1** — усі умови виконано (ADR-0007, точкова правка ТЗ §9.1, картка). Info: ADR-0007 посилається на R-30 як джерело ідемпотентності за `event_id` (R-30 — про розділення `projection.command` і `domain.changed`); ТЗ `:508` називає `audit_log` «fetch-таблицею».
- **D-2** — виконано, крім рядка картки `:115` (SR-7). Запит до WP-01C не потрібен: `NormalizedArtifactRef.fetch_id: UUID` уже обов'язковий.

## 5. Нові знахідки

| ID | Severity | file:line | Суть | Що зробити | Статус |
|---|---|---|---|---|---|
| SR-7 | low | `docs/plan/cards/WP-01A.md:115` | Рядок «Parse/normalized» описує ідемпотентність за artifact — суперечить ТЗ §9.1:499, ADR-0007 D-2 і T-2 | Замінити на unique `parse_key` (ADR-0007 D-2) | open → до злиття |
| SR-8 | low | `docs/plan/cards/WP-01A.md` PR3 | ADR-0007 призначає тригер перегляду `change_events`, у картці PR3 його немає | Додати пункт у PR3 або в картку WP-12 | open (не блокує) |
| SR-9 | info | `.gitleaksignore:9` | Fingerprint прив'язаний до `bad6a25`; rebase зламає job `secrets` | Merge-commit без rebase | умова злиття |

## 6. Поза PR2 (передано з owner)

| Owner | Що лишається |
|---|---|
| WP-00 | 7 секретів `postgres_dsn_<component>` + one-shot `migrate-postgres` з `db roles --with-login` (§4); `REVOKE CONNECT, TEMP ON DATABASE … FROM PUBLIC` (§6, security I-2) |
| WP-01D | per-role DSN для `scheduler`/`worker-*`, прибрати `postgres_dsn` з runtime, замінити тест-вартовий, `verify_runtime_login` на старті, роль для `export`, `queue.release`; §7 N-2 poison event; нестабільний self-fencing тест. Security I-1 — **блокер pilot і non-dev розгортання** |
| WP-02 | ключ normalized artifact = f(`sha256`, `entity_uuid`); справжній `fetch_id`; зміна схеми = зміна `parser_version` |
| WP-01A PR3 | `purge_published`; PG-запити reconciler (SR-4); тригер `change_events` (SR-8); news/matching/release/retention/capacity; GRANT-и `news_*` |
| WP-01B | reconciler і Mongo-сторона replay (H-2, H-3) |
| WP-12 | виклик `purge_published`, `ensure_month_partitions`; метрики/алерти §14.2 |
| WP-11A | lineage API (H-6), export-сторона H-3 |

## 7. Підсумок

`missing` 0; `partial` 14; critical/high без `fixed` — немає. **`accept`**. Умови злиття: SR-7, зелений CI на PR, merge-commit без rebase (SR-9).
