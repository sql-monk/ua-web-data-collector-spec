# WP-01A PR1 — звіт документування (`wp/01a-1-control-queue`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-01A / PR1 «control plane, job queue, origin limiter, worker pools» |
| Branch / worktree | `wp/01a-1-control-queue` / `.worktrees/wp-01a` |
| Джерела | `git diff main...HEAD`; `docs/plan/cards/WP-01A.md` (розділ «Docs (етап 5)»); звіти `docs/plan/reports/WP-01A/{implementation,testing,code-review,spec-review}-pr1.md`; код `src/collector/persistence/postgres/**`, `migrations/postgres/**`, `sql/roles.sql` |
| Вердикт пострев'ю | `accept` (`spec-review-pr1.md`) |
| Роль | wp-docs-writer — лише документація, код/тести не змінено (крім docstrings, розділ 4) |

Картка WP-01A відносить `docs/persistence/postgres.md`, `docs/runbooks/migrations.md` і
ADR-0005 до «Docs (етап 5)» усього WP-01A, з приміткою, що ці документи закриваються
PR3-acceptance (§17.2). Цей етап документує **PR1-частину**, яка вже реалізована й прийнята —
відповідно до задачі оркестратора.

## Створені файли

1. **`docs/persistence/postgres.md`** — огляд PostgreSQL-схеми PR1 (13 таблиць): список таблиць
   із призначенням, ER-схема (`mermaid erDiagram`), transaction boundaries за групами операцій
   (queue claim/heartbeat/complete/retry, limiter acquire/release/expire/block, pools
   desired-state + scale command, audit), вимога READ COMMITTED для `enqueue`, партиціонування
   `audit_log` (місячні + DEFAULT, хто їх створює), обов'язкові indexes картки +
   `0002_claim_index` з поясненням виміру 331 мс → 0.18 мс, чому `alembic check` не ловить
   дрейф CHECK/predicate (і як це закрито `test_schema_contract.py`), ролі БД і матриця прав
   із `sql/roles.sql`, покроково «як додати нову міграцію» (forward-only, `alembic check`,
   enum-контракт), інтерпретація `desired` vs heartbeat-derived `current` у `worker_pools`.
   Явно позначено, що таблиці PR2/PR3 ще не існують.
2. **`docs/runbooks/migrations.md`** — застосування міграцій у dev (`collector db migrate` →
   `collector db roles`, порядок при першому старті кластера), one-shot Compose
   `migrate-postgres` (з явним застереженням `operationally unverified`, бо `collector db
   roles` ще не додано до цього one-shot на `main` — відкритий пункт 2
   `docs/plan/deps/WP-01A-to-WP-00.md`), `collector db migrate --check` (виявлення drift, права
   доступу, зв'язок з CI), дії при drift/неуспішній міграції/відсутній партиції, forward-only
   політика і forward-fix замість downgrade, відкат схеми в dev (`alembic downgrade base` /
   `docker compose down -v`).
3. **`docs/decisions/0005-postgres-queue-and-outbox.md`** — ADR: PostgreSQL job table з
   `FOR UPDATE SKIP LOCKED` + lease замість брокера (§7.2), обґрунтування (транзакційний
   outbox, менше сервісів, спільний примітив з origin limiter), дослівні вимірювані критерії
   переходу на RabbitMQ/Redpanda з §7.2 (100 jobs/s, 1 млн pending, незалежне масштабування
   споживачів), що саме не зміниться при переході (job payload contract:
   `idempotency_key`/`args`/`attempt`/`errors.*`), і чому глобальний origin limiter у
   PostgreSQL — canonical (R-53), а Redis не є source of truth у v1. Поля Context/Decision/
   Consequences/Date 2026-09-22/Owner WP-01A/Status accepted.
4. **`docs/plan/reports/WP-01A/docs-pr1.md`** — цей звіт.

## Оновлені файли

- **`README.md`** — один рядок-посилання на `docs/persistence/postgres.md` у списку документів
  кореня (поруч із `docs/contracts.md`).
- **Docstrings репозиторіїв** (`src/collector/persistence/postgres/repositories/{queue,
  limiter,pools,crawl_runs}.py`) — 9 публічних функцій без docstring отримали docstring із
  transaction boundary; логіку не змінено (перевірено ruff/mypy нижче):
  - `queue.get_job`, `limiter.get_bucket`, `crawl_runs.get_run`, `pools.get_pool`,
    `pools.list_pools`, `pools.get_scale_command` — прості getters («один SELECT, без
    блокування»);
  - `pools.mark_draining`/`mark_stopped`/`mark_ready` — тонкі обгортки над
    `set_instance_status`, docstring посилається на її transaction boundary.

Решта публічних функцій репозиторіїв (`enqueue`, `claim`, `heartbeat`, `complete`, `retry`,
`quarantine`, `recover_expired_leases`, `list_dead_letters`, `ensure_bucket`, `acquire_permit`,
`release_permit`, `expire_permits`, `block_origin`, `live_permit_count`, `upsert_pool`,
`register_instance`, `heartbeat_instance`, `set_instance_status`, `mark_stale_instances`,
`observed_capacity`, `request_scale`, `transition_scale_command`, `start_run`, `finish_run`,
`create_source`, `get_source`, `set_source_state`, `add_policy_version`, `upsert_route`,
`set_route_state`, `upsert_cursor`, `append_audit`, `get_audit`) уже мали docstring із
зазначеним transaction boundary — перевірено читанням усіх шести файлів `repositories/**` і
підтверджено скриптовою перевіркою (перший рядок тіла кожної функції — `"""`). Зміни в них не
вносились.

Модулі поза `repositories/**` (`models/**`, `partitions.py`, `migrations.py`, `ops.py`,
`roles.py`, `errors.py`, `config.py`, `engine.py`, `clock.py`, `cli.py` команди `db migrate`/
`db roles`) також уже мали докладні docstrings з transaction boundary там, де це доречно —
завдання стосувалось лише репозиторіїв (§9.1/картка «Repository API… кожна операція документує
transaction boundary»), змін там не робив.

## Lint

```text
$ npx --yes markdownlint-cli2 "docs/persistence/postgres.md" "docs/runbooks/migrations.md" "docs/decisions/0005-postgres-queue-and-outbox.md" "README.md"
Summary: 0 issues in 4 files

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
157 files already formatted

$ uv run mypy src
Success: no issues found in 60 source files
```

Нові документи не мають зовнішніх (http) посилань — `markdown-link-check` не застосовувався
(правило агента: лише для нових файлів із зовнішніми посиланнями). Внутрішні відносні
посилання (`docs/runbooks/migrations.md` → `../persistence/postgres.md`,
`../plan/deps/WP-01A-to-WP-00.md`; `README.md` → `docs/persistence/postgres.md`) перевірено
`test -f` — обидва файли існують.

## Що не перевірено / межі документа

- `docs/persistence/postgres.md` і `docs/runbooks/migrations.md` описують **лише PR1** (13
  таблиць); таблиці PR2 (artifacts/projection/outbox/entity index) і PR3 (news/matching/
  release/retention/capacity) додаються відповідними етапами документування цих PR — обидва
  документи явно це позначають.
- Твердження про one-shot `migrate-postgres` у Compose спирається на поточний стан `main`
  (WP-00 PR2, файл поза цим worktree/PR) і позначено `operationally unverified` там, де
  поведінка ще не оновлена під WP-01A PR1 (додавання `collector db roles` до команди
  one-shot) — це відкритий пункт 2 `docs/plan/deps/WP-01A-to-WP-00.md`, не річ, яку може
  закрити цей етап документування.
- ADR-0005 не вимірює §15/§7.2 критерії переходу (100 jobs/s, 1 млн pending) — вони свідомо
  поза scope PR1 (`implementation-pr1.md`, «Що не перевірено»); ADR лише фіксує пороги
  дослівно з ТЗ і посилається на це обмеження.

## Коміт

`docs(wp-01a): PostgreSQL schema guide, migrations runbook, ADR-0005` — на branch
`wp/01a-1-control-queue`, без push.
