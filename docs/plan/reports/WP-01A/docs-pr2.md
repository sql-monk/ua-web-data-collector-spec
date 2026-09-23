# WP-01A PR2 — звіт документування (`wp/01a-2-artifacts-projection`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR2 «artifacts, upload claims, projection tasks/acks, outboxes, entity index» |
| Branch / worktree | `wp/01a-2-artifacts-projection` / `.worktrees/wp-01a` |
| Базовий коміт | `c8ae84d` (після «Fixes after gate 4»: SR-1/SR-2/SR-5/N-1..N-5 fixed) |
| Вхід | `docs/plan/cards/WP-01A.md`, `implementation-pr2.md`, `testing-pr2.md`, `code-review-pr2.md`, `code-review-pr2-r2.md`, `security-pr2.md`, `spec-review-pr2.md` |

## Що зроблено

### 1. ADR-0007 (spec-review PR2 §5.1 SR-3, §5.2 D-2)

`docs/decisions/0007-event-tables-global-unique-over-partitioning.md` — новий ADR:

- **D-1**: `raw_objects`, `change_events`, `outbox_events` лишаються непартиціонованими
  (глобальний unique `sha256`/`event_id` несумісний з partition-local unique PostgreSQL);
  `fetches`/`audit_log` — партиціоновані, як і раніше. Retention: `outbox_events` —
  `purge_published(older_than)` заплановано на WP-01A PR3, виклик — WP-12; `change_events` —
  прийнятий тимчасовий ризик (Q-005) з описаною відхиленою альтернативою (місячні партиції +
  `change_event_ids(event_id PK)`) і тригером перегляду (2× річний прогноз §15, owner WP-01A
  PR3/WP-12).
- **D-2**: `parse_key` — ідентичність parse-кроку `(fetch_id, raw_sha256, parser_version,
  entity_uuid, target_collection)`, не вміст artifact; обґрунтування через R-36/§16.3/§9.3 п.5.
  Наслідки для WP-02 (`docs/plan/deps/WP-01A-to-WP-02.md`, уже існував — підтверджено, не
  дублювався).

Поля Context/Decision/Consequences/Date (2026-09-23)/Owner (WP-01A)/Status (accepted) — за
форматом наявних ADR-0005/0006.

### 2. `TECHNICAL_SPECIFICATION.md` §9.1

Дві точкові правки (решта рядків не змінено):

- рядок ~508 «Великі fetch/event tables партиціонуються щомісяця…» → «`fetches`, `audit_log`
  партиціонуються; `raw_objects`/`change_events`/`outbox_events` — ні» з посиланням на
  ADR-0007;
- рядок таблиці `projection_tasks` («… і artifact projection key») → уточнено як `parse_key`
  (ідентичність parse-кроку) з посиланням на ADR-0007 D-2.

### 3. Картка `docs/plan/cards/WP-01A.md`

- «Спільні вимоги»: список партиційованих таблиць звужено до `fetches`/`audit_log`, додано
  посилання на ADR-0007 для трьох виключених;
- PR2 тест «projection version»: переформульовано під D-2 (ідемпотентність за `parse_key`,
  а не вмістом artifact; «той самий artifact у новому fetch → нова версія, той самий рядок
  `normalized_artifacts`»);
- PR2 тест partitioning: переформульовано під фактичний вибір (DEFAULT-партиція + сигнал
  `default_partition_row_count`, а не «помилка або авто-створення»);
- PR3: додано `purge_published(older_than)` (ADR-0007 retention) і PG-запити для reconciler
  §7.3 крок 5 (spec-review PR2 SR-4: «незавершені tasks», «повнота cursor»).

### 4. `docs/acceptance/traceability.md`

Шість нових рядків WP-01A PR2 (Історія без втрат/дублів, Узгодженість двох БД, Доказовість і
відтворення, Технічна безпека, Експлуатація, Незалежна реалізація) зі статусами на поточний
HEAD: SR-1/SR-2 — `fixed`, відображено як `evidenced`; runtime на superuser DSN — `open`
(owner WP-00/WP-01D, блокер pilot, не merge).

### 5. `docs/plan/ledger.md`

- WP-01A: «PR1 merged; PR2 docs; очікує повторного пострев'ю/PR» + підсумок закритих
  gate-4-знахідок і ADR-0007;
- dependency-таблиця: об'єднано дублікат `WP-01A-to-WP-00.md` в один рядок (§1–3 resolved,
  §4 open + ризик I-2), `WP-01D-to-WP-01A.md` → «WP-01A part done, pending WP-00/WP-01D»,
  додано `WP-01A-to-WP-01D.md` (open) і `WP-01A-to-WP-02.md` (open);
- WP-01D рядок: додано нотатку про флак-ризик
  `test_self_fencing_fires_when_the_database_hangs_without_raising` (спостережено на верифікації
  WP-01A PR2, не внесений PR2).

### 6. `docs/plan/deps/WP-01A-to-WP-00.md`

Новий §6: ризик I-2 (`security-pr2.md`) — `REVOKE CONNECT, TEMP ON DATABASE … FROM PUBLIC`,
owner WP-00 (`deploy/compose/postgres/init/**`), не блокує PR2.

### 7. `docs/persistence/postgres.md`

Оновлено з «лише PR1» до PR1+PR2 (23 таблиці):

- розділ 1 — 10 нових таблиць PR2 з призначенням;
- розділ 2 — ER-діаграма доповнена зв'язками `fetches → parse_attempts → normalized_artifacts →
  projection_tasks → {projection_acknowledgements, outbox_events}`, пояснення відсутності FK
  для lineage-колонок;
- розділ 3 — нові підрозділи transaction boundaries: Artifacts/upload claims, Projection,
  Outbox publisher (lease + паркування), Entity index (keyset); уточнено, що PR2 поширив
  транзакційний audit на решту control-plane операцій і додав `queue.release`;
- розділ 5 — перейменовано («Партиціонування `audit_log` і `fetches`; чому решта PR2-таблиць
  не партиціонована»), додано DEFAULT-партицію `fetches_default` і посилання на ADR-0007;
- розділ 6 — обов'язкові indexes/unique PR2 (`projection_tasks`, `outbox_events`,
  `entity_index`, `raw_objects.sha256`, `change_events.event_id`, partial indexes hot path);
- розділ 7 — переписано на «Ролі БД, LOGIN, column-level GRANT і RLS»: оновлена матриця прав
  PR1+PR2, новий §7.1 (чому column-level GRANT з явним REVOKE перед звуженням), §7.2
  (`collector db roles --with-login`, SCRAM verifier, `verify_runtime_login`, ще не викликається
  runtime — dependency WP-01D), §7.3 (RLS на `outbox_events` для `topic`-розділення);
- «Джерела» — додано ревізії `0004`/`0005`, звіти PR2, ADR-0007, dependency-файли.

### 8. `docs/runbooks/migrations.md`

Знято застарілу заяву «наразі лише PR1»; додано `--with-login` у секцію 1 і швидкий довідник;
розділ «Відсутня партиція» доповнено `fetches`/`fetches_default` і згадкою про свідомо
непартиційовані таблиці (ADR-0007); «Джерела» доповнено посиланнями.

## Що не робилось (поза скоупом інструкції)

- Docstrings публічних інтерфейсів PR2 — уже мають transaction boundary (spec-review PR2 AC-4
  `evidenced`); код і тести цим PR2-docs-етапом не змінювались.
- Окремий runbook для LOGIN/RLS — не додавав: дія одноразова/ідемпотентна (`db roles
  --with-login`), вже описана в `docs/runbooks/migrations.md` короткою командою й розкрита
  детально в `docs/persistence/postgres.md` §7.
- `docs/observability/metrics.md` — у репозиторії такого файлу ще немає (жоден попередній WP
  його не створив); нових метрик/алертів PR2 не вводить понад уже задокументовані сигнали
  (`default_partition_row_count`, `count_backlog`/`oldest_unpublished_age`) — вони описані в
  `docs/persistence/postgres.md` і traceability; створення окремого observability-документа
  поза скоупом WP-01A (WP-12 §14.1/§14.2).

## Lint

```text
$ uv run pre-commit run markdownlint-cli2 --files TECHNICAL_SPECIFICATION.md \
    docs/acceptance/traceability.md docs/persistence/postgres.md \
    docs/plan/cards/WP-01A.md docs/plan/deps/WP-01A-to-WP-00.md docs/plan/ledger.md \
    docs/runbooks/migrations.md \
    docs/decisions/0007-event-tables-global-unique-over-partitioning.md
markdownlint-cli2........................................................Passed
```

Новий файл (`docs/decisions/0007-…md`) не містить зовнішніх посилань — `markdown-link-check`
не застосовний.

## Файли

Створено: `docs/decisions/0007-event-tables-global-unique-over-partitioning.md`.

Оновлено: `TECHNICAL_SPECIFICATION.md`, `docs/acceptance/traceability.md`,
`docs/persistence/postgres.md`, `docs/plan/cards/WP-01A.md`,
`docs/plan/deps/WP-01A-to-WP-00.md`, `docs/plan/ledger.md`, `docs/runbooks/migrations.md`.
