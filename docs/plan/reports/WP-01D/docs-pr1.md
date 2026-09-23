# WP-01D PR1 — документування (wp-docs-writer)

Гілка: `wp/01d-1-worker-runtime`, worktree `.worktrees/wp-01d`. Вхід: картка
`docs/plan/cards/WP-01D.md` (розділ «Docs (етап 5)» — лише те, що вже реалізовано в PR1; ADR про
deployment mode (Q-013) і autoscale (Q-014) залишено PR3, як і належить), звіти
`docs/plan/reports/WP-01D/{implementation,testing,code-review,spec-review}-pr1.md`, код
`src/collector/workers/**`, уже створений реалізатором `docs/workers.md`.

## Створено/оновлено

| Файл | Дія | Суть |
|---|---|---|
| `docs/workers.md` | оновлено | Перевірено проти коду (усі 9 модулів `src/collector/workers/**` прочитано наскрізь) — фактичний стан уже описано повно й точно. Додано: посилання на `docs/persistence/postgres.md` (схема, не дублюється) і на новий ADR-0006/runbook; два відсутні рядки env-таблиці (`COLLECTOR_WORKER_DEPLOYMENT`, `COLLECTOR_CONTAINER_ID`); дефолти scheduler-змінних окремою таблицею (`5`/`5`/`scheduler`/`60`/`1000` — раніше були лише назви без значень); у §8 «Експлуатація» — що `mark_draining`/`mark_ready` у PR1 без CLI (виклик репозиторію або прямий SQL) і посилання на новий runbook; у §6 — посилання на «Residual risks» ADR-0006 для TOCTOU scheduler-тіку. |
| `docs/decisions/0006-worker-lease-fencing-and-liveness.md` | створено | ADR: чому self-fencing керується часом від останнього підтвердженого heartbeat, а не фактом винятку (код-рев'ю H-1, зонд «2.01 с — fencing не спрацював»); три незалежні шари (watchdog-задача, бюджет тіку heartbeat, `command_timeout`/`statement_timeout`); чому liveness відокремлена від readiness (заміри «було/стало» 6.07→0.44 с при 0.25 CPU, бюджет healthcheck 15s/90s → 3s/20s, стек healthy за 22 с) і як це узгоджено з §7.5 (зміна `test_application_healthchecks_name_a_critical_dependency`); Residual risks — TOCTOU singleton-тіку scheduler-а (owner WP-01D PR3/доменні WP) і per-instance drain barrier до PoolController PR3. Context/Decision/Consequences/Related, Date 2026-09-23, Owner WP-01D, Status accepted. |
| `docs/runbooks/worker-recovery.md` | створено | Операційний runbook: SQL-запити для стану pools/instances; таблиця `stale` vs `draining` з тим, як відрізнити «процес мертвий» від «фенснутий, але живий»; дії при fenced-instance; безпечна зупинка репліки (`docker compose stop` → SIGTERM → drain → exit 0, з реальними даними зі звітів) і точковий per-instance барʼєр через SQL (з приміткою `operationally unverified` — тестований шлях лише через репозиторій); послідовність відновлення після `kill -9` (stale-детекція окремо від lease expiry); rollback на `COLLECTOR_WORKER_PLACEHOLDER=1`; чотирикроковий чекліст «worker німий». Масштабування/role-wide drain barrier явно позначено як PR3. |
| `README.md` | оновлено | Один рядок-посилання на `docs/workers.md` у списку документів кореня, поруч із `docs/persistence/postgres.md`. |
| `src/collector/workers/scheduler.py` | docstrings | Два відсутні docstrings додано без зміни логіки: `MaintenanceResult` (dataclass, підсумок `run_maintenance`) і `SchedulerRuntime.request_stop`. Решта публічних класів/функцій `src/collector/workers/**` (`advisory.py`, `config.py`, `handlers.py`, `liveness.py`, `roles.py`, `runtime.py`, `session.py`, `signals.py`, `__init__.py`) уже мали повні докстрінги — перевірено читанням усіх файлів наскрізь і перехресною звіркою `class`/`def` рядків з наявністю докстрінга; змін там не було. |

## Перевірка фактів проти коду (не зі слів звітів)

Перед написанням прочитано весь `src/collector/workers/**` (config.py, liveness.py, handlers.py,
runtime.py, scheduler.py, advisory.py, roles.py, session.py, signals.py, `__init__.py`) і
`docker-compose.yml` (anchors `x-healthcheck-budget`, `x-liveness-budget`, `x-liveness-probe`,
`x-worker`) — щоб env-таблиця, дефолти таймаутів, healthcheck-команда і твердження ADR/runbook
збігалися з реалізацією, а не переказом звітів. Схему `worker_pools`/`worker_instances`
(`INSTANCE_TRANSITIONS`, `drain_requested_at`, `heartbeat_instance`) звірено з
`src/collector/persistence/postgres/{models,repositories}/pools.py` для точності SQL-прикладів у
runbook.

## Lint

```text
$ npx --yes markdownlint-cli2 "docs/workers.md" "docs/decisions/0006-worker-lease-fencing-and-liveness.md" "docs/runbooks/worker-recovery.md" "README.md"
Summary: 0 issues in 0 files

$ uv run ruff check . && uv run ruff format --check . && uv run mypy src
All checks passed!
218 files already formatted
Success: no issues found in 70 source files
```

`markdown-link-check` не запускався: нові файли не містять зовнішніх (http/https) посилань —
лише внутрішні шляхи репозиторію.

## Що НЕ зроблено (свідомо, поза scope PR1/цього завдання)

- ADR-0007 «Autoscale вимкнений до pilot evidence» (Q-014) — картка відносить його до PR3
  (autoscale-політики в PR1 немає взагалі).
- ADR про deployment mode (Q-013, Compose MVP/Swarm) — теж PR3 (Swarm-адаптера в PR1 немає).
- Метрики/алерти `docs/observability/metrics.md` — PR1 має лише структуровані логи
  (`worker.*`/`scheduler.*`), самі лічильники — заготовка PR2 за карткою.
- Ризик §13 (спільний DSN міграційної ролі) — не новий ADR: це вже задокументоване тимчасове
  відхилення з owner WP-01A PR2 і тест-вартовим (`docs/plan/cards/WP-01D.md`, «Відомі ризики»);
  ADR був би доречний, лише якби §13 планували змінювати назавжди, а тут навпаки — інваріант
  мають повернути.

## Коміт

`docs(wp-01d): workers guide, ADR-0006, recovery runbook` на `wp/01d-1-worker-runtime`
(worktree `.worktrees/wp-01d`). Не push-ився.
