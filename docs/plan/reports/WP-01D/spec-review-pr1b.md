# WP-01D PR1b: приймання за ТЗ, gate 4 (`wp/01d-1b-runtime-role-dsn`)

| Поле | Значення |
|---|---|
| Branch / HEAD | `wp/01d-1b-runtime-role-dsn` @ `f721571` (worktree `.worktrees/wp-01d-1b`), 10 комітів від `origin/main` `8be045d` (merge-base = `origin/main`, тобто гілка стоїть поверх злитого WP-00 PR4, PR #7 `ae63917`) |
| Diff | `git diff origin/main...wp/01d-1b-runtime-role-dsn`: 25 файлів, +2369/−242 |
| Дата | 2026-09-24 |
| Рев'юер | `wp-spec-reviewer` (read-only; пише лише цей файл і `docs/acceptance/traceability.md`) |
| Обсяг | Картка `docs/plan/cards/WP-01D.md`: розділ «PR1b» (вимоги 1–6, тести, acceptance) і «Відомі ризики»; `docs/plan/deps/WP-01A-to-WP-01D.md` §1, §2, §6, §7; `docs/plan/deps/WP-01D-to-WP-01A.md` (§6 — виняток оркестратора на `roles.py`); ТЗ §7.6, §13, §16.1 п.15, §16.3, §17.2 (рядок WP-01D), §18, §20 (Q-013, Q-014), Додаток C; REVIEW.md R-52, R-55, R-57 |
| Вхідні звіти | `implementation-pr1b.md` (разом з «After rebase on WP-00 PR4» і «Fixes after gate 3»), `testing-pr1b.md` (pass), `code-review-pr1b.md` (approve, 3 low), `security-pr1b.md` (approve, S-1 medium, S-2…S-4 low, S-5/S-6 info); контекст пари: `docs/plan/reports/WP-00/spec-review-pr4.md` SR-5, `docs/plan/reports/WP-01A/security-pr2.md` I-1 |
| Метод | Статичний аналіз diff, коду, тестів і звітів. Власна верифікація без Docker (Docker за умовою завдання не піднімався): unit-модулі PR1b, lint/types, gitleaks, стан remote |
| **Вердикт** | **`accept`**: 0 `missing` для acceptance/DoD; 7 рядків `partial` (усі — або WP-рівень PR2/PR3, або прийнятий ризик S-1 export-worker, або DoD п.8/п.9: статуси findings і CI). Знахідок critical/high немає; S-1 (medium, security) має статус `accepted` з owner, датою, жорстким тригером і тест-вартовим. Умова злиття — зелений CI (SR-2) |

---

## Власна верифікація

```text
$ uv run pytest tests/unit/workers/test_db_login.py tests/unit/test_compose_config.py \
    tests/unit/test_compose_config_adversarial.py tests/unit/test_cli_compose_commands.py -q -p no:warnings -p no:cacheprovider
119 passed in 5.74s

$ uv run ruff check . ; uv run ruff format --check . ; uv run mypy src
All checks passed!
265 files already formatted
Success: no issues found in 78 source files

$ gitleaks git --log-opts="origin/main..HEAD" --no-banner
10 commits scanned. ... no leaks found

$ git ls-remote --heads origin wp/01d-1b-runtime-role-dsn      # порожньо: гілку не запушено, CI не запускався
$ grep -c docker.sock docker-compose.yml                        # 0
$ git diff origin/main...HEAD --stat -- src/collector/persistence tests/integration/postgres
 src/collector/persistence/postgres/roles.py | 52 +++++---   # лише виняток deps WP-01D→WP-01A §6; тести WP-01A не змінено
```

Gitleaks охоплює всі 10 комітів гілки, включно з `091ea5a` і `f721571` (fixes після gate 3), яких немає в gitleaks-прогоні `security-pr1b.md` (`4f84020..c32f4e1`). Integration/scaling (testcontainers) і Docker-стек не запускав — докази беру з фактичного виводу у звітах, посилання в матриці.

---

## 1. Acceptance criteria

### 1.1. Картка WP-01D, розділ PR1b

| # | Вимога | Доказ | Статус |
|---|---|---|---|
| P1 | Кожен runtime-сервіс монтує **лише свій** `postgres_dsn_<component>` як `COLLECTOR_POSTGRES_DSN_FILE`; спільний `postgres_dsn` не монтує жоден runtime-сервіс; мапінг за карткою | `docker-compose.yml:137-146` (anchor `x-worker` без `secrets`/DSN), `:455-459` (scheduler), `:481-485`, `:496-500`, `:519-523`, `:538-542`, `:559-563`, `:574-578`, `:597-601`, `:620-624`; мапінг у коді `src/collector/workers/roles.py:78-106`; тести `tests/unit/test_compose_config.py:463` `test_runtime_services_use_only_their_own_login_dsn_13`, `:492` `test_worker_anchor_carries_no_dsn_so_a_new_worker_cannot_inherit_one`, `:403` `test_postgres_dsn_secret_is_scoped_to_migration_and_queue_consumers`; `tests/unit/workers/test_db_login.py:54` `test_mapping_matches_the_card`, `:68` `test_compose_mounts_the_dsn_of_the_role_the_process_verifies` (зв'язує compose і runtime-перевірку). Власний прогін: 119 passed. Мутація M5 (fetch-worker → `postgres_dsn`) → 3 red (`testing-pr1b.md` «Mutation-перевірка»). Рендер `config --format json` — `implementation-pr1b.md:122-136` | evidenced |
| P2 | `export-worker` → варіант (а): runtime-черга під `collector_scheduler`; ризик у картці з owner | `src/collector/workers/roles.py:88-93`; `docker-compose.yml:571-578`; картка `docs/plan/cards/WP-01D.md` «Відомі ризики», рядок export-worker (accepted, owner WP-11A / WP-01A, заведено 2026-09-24, жорсткий тригер «до merge першого реального export handler (WP-11A) або до pilot»); тест-вартовий `tests/unit/workers/test_db_login.py:162` `test_export_worker_keeps_scheduler_role_only_while_its_handler_is_noop`; повний цикл під роллю `tests/integration/scaling/test_runtime_login_adversarial.py:134` `[export]` (`testing-pr1b.md`, рядок 2 таблиці acceptance) | evidenced (саме рішення); §13 «exporter read-only» — див. S13-3 |
| P3 | `verify_runtime_login` при старті `WorkerRuntime` і `scheduler` до першого claim; `RoleLoginError` → ненульовий exit, зрозуміле повідомлення без DSN/пароля; rollback `COLLECTOR_WORKER_PLACEHOLDER=1` | `src/collector/workers/login.py:27-39` (`verify_runtime_login` + збіг з мапінгом); `src/collector/workers/runtime.py:279-284` (перший крок `_boot`, до `_ensure_pool`/`register_instance`/claim); `src/collector/workers/scheduler.py:155-158` (до сигналів і advisory lease); `src/collector/cli.py:471-484` (`_run_runtime` → `role login: …`, `typer.Exit(1)`); placeholder до БД не доходить — `src/collector/cli.py:590-592`. Тести: `tests/integration/scaling/test_runtime_login.py:73,101,133,148,173,186,228,253,287,296`; справжній процес з DEBUG-логами, 0 записів, без пароля у виводі — `test_runtime_login_adversarial.py:312,341,367`; rollback з відмовленим DSN — `:387`; unit `tests/unit/workers/test_db_login.py:105,122,139`. Мутації M1/M4 (прибрати виклик у worker/scheduler) → red (`testing-pr1b.md`). Живий стек: `collector worker fetch` у контейнері з `postgres_dsn_scheduler` → `role login: …`, `exit=1` (`testing-pr1b.md` «Docker acceptance») | evidenced |
| P4 | Тест-вартовий `test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` і зонди замінено позитивним тестом §13 | `grep -rn tripwire tests/ src/` — порожньо; позитивний тест — P1 (`test_compose_config.py:463`). `_strip_sql_comments` повернуто, бо ним користуються тести WP-00 PR4 (`implementation-pr1b.md:219-222`), це не залишок вартового | evidenced |
| P5 | `_release_leases` під час планового drain → `queue.release(job_id, owner)`; тест: drain не змінює `attempt`, не пише полів помилки | `src/collector/workers/runtime.py:770-793` (`queue_repo.release` на `:781`; `IMMEDIATE_RETRY_POLICY`/`DRAIN_TIMEOUT_ERROR_CODE` видалено); тести `tests/integration/scaling/test_worker_runtime.py:220` `test_drain_timeout_returns_the_lease_to_the_queue` (`:244` claim +1, `:254` `attempt == before`, `last_error_*` NULL), `test_worker_runtime_adversarial.py:466` (остання спроба — без карантину і dead letter), `test_runtime_login_adversarial.py:203` (чужий lease не чіпається). Мутація M3 (повернути `retry`) → `('retry', None, 1) != ('pending', None, 0)` | evidenced |
| P6 | Out of scope: publisher loop outbox (deps §7, N-2) → картка власника (WP-01B) | Diff publisher не чіпає; `docs/plan/cards/WP-01D.md:65`. Картки WP-01B ще немає (`docs/plan/cards/` — WP-00/01A/01C/01D), тож вимога N-2 зараз живе лише в deps §7 і картці WP-01D — див. SR-4 | not applicable (out of scope за карткою; перенесення — SR-4) |
| P7 | Тести: повний `tests/integration/scaling/**` під LOGIN-ролями, а не superuser, де можливо | `tests/integration/scaling/conftest.py:146-230` (`login_urls`, `role_engine`, `runtime_sessions` = `collector_fetcher`, `scheduler_sessions`); напр. `test_worker_runtime.py:138-147` і `test_worker_runtime_adversarial.py:225-232` запускають runtime через `runtime_sessions`, superuser `pg_sessions` лише для підготовки/перевірок. Вивід: `44 passed` (`implementation-pr1b.md:245`), після fixes gate 3 `87 passed` (scaling + `test_role_logins.py`, `implementation-pr1b.md:392-396`); `testing-pr1b.md`: 65 тестів, 3 падіння розібрано (2 — дефекти нових тестів, виправлені; 1 — відомий флейк класу A), `30 passed` login-модулів | evidenced |
| P8 | Acceptance: після `init-secrets.sh` + `docker compose --profile core --profile workers up -d --wait` усі runtime-процеси підключені не-superuser ролями (`pg_stat_activity` у звіті); вартового прибрано, позитивний тест §13 зелений | `implementation-pr1b.md:401-423` (після rebase на `main` і fixes S-2/S-3: 8 application_name → `collector_scheduler/fetcher/parser/projector/translation`, `rolsuper=f`; єдиний `t` — адмін-`psql` самого запиту); незалежно `testing-pr1b.md` «Docker acceptance» (той самий результат, `--scale fetch-worker=4` → 4 × `collector_fetcher`, `leaks=0`); вартовий/позитивний тест — P4, P1 | evidenced |

### 1.2. ТЗ §17.2, рядок WP-01D

| # | Критерій | Доказ | Статус |
|---|---|---|---|
| W1 | «role commands, pool/instance/scale contracts, PostgreSQL origin limiter, heartbeat/drain, Compose command adapter і Swarm replica adapter; scale/rate/fault tests green» | PR1b додає до role commands/heartbeat/drain (PR1) least-privilege запуск і drain через `release` (P3, P5, P7). Origin limiter — PR2, pool controller/adapters/scale 1→4→1→0→2 — PR3 | partial (WP-рівень; решта — PR2/PR3 за карткою) |

### 1.3. ТЗ §13 (дотичні пункти)

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| S13-1 | «Облікові дані БД розділені за компонентами» | P1 + P8: п'ять ролей на вісім runtime-процесів за мапінгом; процес сам перевіряє, що роль саме його (`login.py:33-38`), чужий DSN → exit 1 (`test_runtime_login.py:173`, `:296`; `test_runtime_login_adversarial.py:367`, 6 кейсів) | evidenced |
| S13-2 | «Migration role не використовується runtime-процесами» (pilot-блокер I-1) | `test_compose_config.py:403`, `:463` (`postgres_dsn` лише в `migrate-postgres`); runtime-перевірка: superuser / член `collector_migrate` / інша `collector_*` / моніторингові вбудовані ролі / `session_user ≠ current_user` → відмова (`src/collector/persistence/postgres/roles.py:185-241`, `:297-329`; `test_runtime_login.py:73,101,133,148`); стек — P8 | evidenced |
| S13-3 | «API/exporter має read-only ролі в обох БД» | exporter: `export-worker` під `collector_scheduler` (не read-only) — прийнятий ризик S-1, картка «Відомі ризики», тест-вартовий `test_db_login.py:162`. API — WP-11A (`api` без секретів, `test_compose_config_adversarial.py::test_api_has_no_secrets_and_runtime_has_only_the_dsn`) | partial (accepted S-1; пропозиція ТЗ-1) |
| S13-4 | «scheduler/fetcher не має MongoDB credentials» | кожен runtime-сервіс має рівно один секрет — свій `postgres_dsn_<component>` (`test_compose_config.py:463`, `test_compose_config_adversarial.py::test_each_secret_has_exactly_documented_consumers`) | evidenced |
| S13-5 | «Secrets … logs приховують» | `RoleLoginError` містить лише імена ролей (`roles.py:323-329`, `login.py:34-38`); `lease_release_failed` через `redact` (`runtime.py:786-790`); стек: пароль у `docker inspect`/логах — 0 (`implementation-pr1b.md:420-421`, `testing-pr1b.md`); gitleaks 10 комітів — чисто (власний прогін) | evidenced |
| S13-6 | «GUI/API/worker images не отримують Docker socket» | `tests/unit/test_compose_config.py:149` `test_no_docker_socket_mount_anywhere` (у власному прогоні 119 passed); `grep -c docker.sock docker-compose.yml` = 0 | evidenced |

### 1.4. ТЗ §7.6, §16.1 п.15, §16.3 (дотичні пункти)

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| G1 | §7.6: drain повертає leases; lease owner/expiry/heartbeat; ролі і pools незмінні | P5; defaults pools не змінено (diff `roles.py` лише додає мапінг БД-ролей); `test_bootstraps_missing_pool_from_spec_defaults` (`test_worker_runtime.py:113`) під LOGIN-роллю | evidenced |
| G2 | §16.1 п.15: drain during active task, killed replica lease recovery (частина PR1b — під LOGIN-ролями) | `test_worker_runtime.py:138` (kill → `recover_expired_leases`), `:220` (drain), `test_worker_runtime_adversarial.py:225` (R-57 per-instance барʼєр), усе через `runtime_sessions`; `44 passed` / `87 passed` (P7). Replicas 1→4→1→0→2, rate, stale command, controller allowlist — PR2/PR3 | evidenced (частина PR1b); решта — not applicable (PR2/PR3) |
| G3 | §16.3: «scale-down під активним job завершує або повертає lease без втрати; kill replica відновлюється після lease expiry» | G2, P5 | evidenced |
| G4 | §16.3: «чистий Docker host підіймає core/workers … однією documented командою» (не зламано переходом на per-role DSN) | `init-secrets.sh` + `up -d --wait --build` → усі `Healthy`, `migrate-postgres` `Exited (0)` (`implementation-pr1b.md:401-404`; `testing-pr1b.md` «Docker acceptance») | evidenced |
| G5 | §16.3: «scale fetch 1→4→1 … змінює ready replicas без дублів, сумарний origin rate не перевищує policy» | Лише `--scale fetch-worker=4` на стеку: 4 × `ready`, 4 × `collector_fetcher` (`testing-pr1b.md`). Зменшення, дублі й rate — PR2/PR3 | partial (WP-рівень, PR2/PR3) |
| G6 | §16.3: «Compose mode повертає audited scale command, Swarm … GUI/API не мають Docker socket» | socket — S13-6; adapters — PR3 | evidenced (socket); решта — not applicable (PR3) |

---

## 2. Definition of Done §18

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| D1 | Одне issue/WP, без сторонніх змін | Diff лише в owned-файлах картки PR1b, крім `src/collector/persistence/postgres/roles.py` (owner WP-01A) — виняток оркестратора, задокументований у `docs/plan/deps/WP-01D-to-WP-01A.md` §6 (зміст, тести, прохання до WP-01A прийняти); тести WP-01A не змінювались (`git diff --stat -- tests/integration/postgres` порожній), `test_role_logins.py` зелений (`implementation-pr1b.md:392-396`). Блок top-level `secrets:` — версія WP-00 (злився автоматично, `implementation-pr1b.md:213-214`) | evidenced (з винятком; SR-3) |
| D2 | formatter, lint, types, unit/contract/integration | Власний прогін: ruff/format/mypy чисто, unit 119 passed; `pytest -m "not live"` → `1068 passed, 23 skipped` (`implementation-pr1b.md:386-390`; 23 skip — GUI e2e без стека і `test_network_blocked` на Windows); scaling + role_logins `87 passed` | evidenced |
| D3 | Зміна схеми → migration + compatibility | Схема/міграції не змінювались (`migrations/**`, `sql/**` поза diff) | not applicable (немає зміни схеми) |
| D4 | Timestamp/matching/release contract → temporal/replay evidence | Не зачеплено | not applicable |
| D5 | Новий адаптер → manifest/fixtures/… | Адаптерів немає | not applicable |
| D6 | Документація, метрики, runbook | `docs/workers.md` (життєвий цикл, §7.1 «Ролі БД (§13)» з мапінгом, drain через `release`); `docs/runbooks/worker-recovery.md` (крок 4 drain, діагностика `role login`, відкат); картка «Відомі ризики». Метрики не змінювались (WP-12) | evidenced |
| D7 | Secret scan чистий; контакти не в logs | gitleaks `origin/main..HEAD` 10 комітів — no leaks (власний); `pre-commit run --all-files` Passed (`implementation-pr1b.md:398-399`); синтетичні паролі генеруються в рантаймі тестів | evidenced |
| D8 | Findings: `fixed` / `accepted with owner/date` / `not applicable` | Security: S-1 accepted (owner WP-11A/WP-01A, 2026-09-24, тригер, вартовий), S-2/S-3 fixed з тестами (`test_runtime_login.py:133,148`), S-4 accepted (WP-13, 2026-09-24), S-5 accepted (2026-09-24, «розглянути з WP-02 PR3»). Code review #1–#3 fixed (`implementation-pr1b.md:368-370`). Без явного статусу: security S-6 (rebase — фактично виконано), testing T-1 (відомий флейк класу A), 2 low з незакоміченого gate 3' re-review `code-review-pr1b.md` — SR-1 | partial (SR-1) |
| D9 | PR злитий після CI і required review; SHA і evidence зафіксовані | HEAD `f721571` зафіксовано; гілку не запушено (`git ls-remote` порожній), CI `integration (PostgreSQL 18)` і `docker` не запускались — SR-2 | partial (SR-2, умова злиття) |

---

## 3. Додаток C

| Ціль | Вимоги | Що покриває PR1b | Статус |
|---|---|---|---|
| Технічна безпека | FR-013, §13 | Per-component LOGIN-DSN для 8 runtime-процесів, runtime-перевірка ролі при старті, позитивний тест §13 замість вартового, закриття I-1 | partial (exporter — S-1 accepted) |
| Docker і масштабування | FR-030—FR-033, §7.5—§7.6 | Drain через `queue.release`; scaling-набір під LOGIN-ролями; clean-host `up --wait` з per-role DSN; `--scale fetch-worker=4` під `collector_fetcher` | evidenced (частина PR1b) |
| Незалежна реалізація | §17, §18 | Звіти 4 gate-ів, виняток на чужий файл через deps §6, findings зі статусами | partial (D8, D9) |

---

## 4. Регресія REVIEW.md

| R | Суть | Чи втілено в коді/тестах PR1b (а не лише в ТЗ) | Статус |
|---|---|---|---|
| R-52 | Незалежні pools, drain, lease recovery | Drain тепер повертає lease одразу і без «згорання» спроби: `runtime.py:770-793`, `test_worker_runtime.py:220`, `test_worker_runtime_adversarial.py:466`; kill-recovery під LOGIN-роллю `test_worker_runtime.py:138`; повний цикл під кожною з 8 ролей `test_runtime_login_adversarial.py:134` | evidenced |
| R-55 | GUI/API без socket; Swarm controller ізольований | Socket відсутній (`test_compose_config.py:149`, grep 0); PR1b нових привілеїв/секретів GUI/API не додає (`api` без секретів). Controller — PR3 | evidenced (регресія); controller — not applicable (PR3) |
| R-57 | Role-wide drain barrier | Per-instance примітив і вимога «барʼєр на КОЖНОМУ instance» лишаються зеленими під LOGIN-роллю: `test_worker_runtime_adversarial.py:225`; `release` не обходить барʼєр (`runtime.py:781` викликається лише для власних активних tasks). Role-wide controller — PR3 | evidenced (регресія); controller — not applicable (PR3) |

---

## 5. Q-питання §20

| Q | Default | Як у PR1b | Статус |
|---|---|---|---|
| Q-013 | Compose для single-host MVP; Swarm для GUI-scaling | Compose лишається (ADR-0002); per-role DSN — Compose file-secrets, production — Swarm secrets (ADR-0002:170). ADR «Deployment mode» за карткою — у PR3 | not applicable (PR1b режим деплою не змінює; ADR у PR3) |
| Q-014 | Autoscale off | Не зачеплено | not applicable |

---

## 6. Pilot-блокер §13 (I-1, `docs/plan/reports/WP-01A/security-pr2.md:17`): висновок

**Закрито парою WP-00 PR4 (злито, PR #7) + WP-01D PR1b — з одним прийнятим залишком.**

- I-1 мав дві частини. (а) «Runtime досі на superuser DSN міграцій» — закрито P1/P8: на стеку жодного runtime-з'єднання з `rolsuper=t`. (б) «`verify_runtime_login` ніде не викликається» — закрито P3: виклик першим кроком `WorkerRuntime._boot` і `SchedulerRuntime.run`, доведено мутаціями M1/M4.
- Умову SR-5 з `docs/plan/reports/WP-00/spec-review-pr4.md:158-160` виконано: там просили перевірити, що кожен сервіс монтує **свій** DSN, а не лише щонайбільше один. `test_compose_config.py:463` звіряє відповідність сервіс → конкретний секрет, а `test_db_login.py:68` звіряє compose з роллю, яку перевіряє процес. Навіть при помилці в compose процес під чужою роллю не стартує (P3, живий стек).
- **Залишок — export-worker (S-1, medium):** §13 «exporter read-only» не виконано: `export-worker` має права `collector_scheduler`, ширші за потреби черги експорту. Статус `accepted`: owner WP-11A / WP-01A, заведено 2026-09-24. Жорсткий тригер: закрити до merge першого реального export handler або до pilot, що настане раніше. Тест-вартовий `test_db_login.py:162` падає, щойно EXPORT отримує не-Noop handler. Фактичного використання поки немає (`NoopHandler`). Отже, **до pilot** залишок треба закрити (окрема роль `collector_exporter` + `collector_export_ro`-з'єднання). Інакше для exporter pilot-блокер §13 формально лишається відкритим.
- Супутні прийняті залишки, які pilot не блокують: S-4 (CI на trust-auth, owner WP-13) і S-5 (`browser-worker` ділить `postgres_dsn_fetcher`, розглянути з WP-02 PR3).

Accepted-ризики з owner і датою. Картка, розділ «Відомі ризики», 4 рядки: §13 — closed у PR1b, крім export; drain — closed; export — accepted; TOCTOU scheduler — mitigated. Owner і дату заведення має кожен рядок. Для export замість календарної дати закриття стоїть подієвий тригер. Це прийнятно, бо тригер перевіряється тестом.

---

## 7. Знахідки

### SR-1 | low | `docs/plan/reports/WP-01D/implementation-pr1b.md:361-370`

DoD п.8. У таблиці «Fixes after gate 3» немає статусу для двох знахідок:

- security S-6 (rebase на `main`) фактично виконано (`implementation-pr1b.md:356-359`), але не позначено `fixed`;
- testing T-1 (відомий флейк класу A `test_self_fencing_fires_…`) відстежується в ledger WP-01D, проте у звіті PR1b немає ні `not applicable (pre-existing, flaky-scaling-tests.md)`, ні owner/дати.

Крім того, під час цього рев'ю в worktree з'явився незакомічений розділ «Re-review (gate 3')» у `code-review-pr1b.md` (інкремент `091ea5a`, `f721571`). У ньому 2 нові low без статусу: зсунутий docstring `SLOW`/`CYCLE_CLOCK` у `test_runtime_login_adversarial.py:58-65` і однорядковий regex у вартовому S-1 (`test_db_login.py:185-193`, багаторядкову реєстрацію не помітить). Обом потрібен `fixed` або `accepted (owner, дата)`; для другого достатньо послатися на жорсткий тригер картки як на компенсацію.

S-5 варто переписати з явним owner («owner WP-02 PR3») замість «розглянути». Виправити треба лише документацію, злиття це не блокує.

### SR-2 | info (умова злиття) | гілка

DoD п.9: гілку не запушено, CI не запускався. Зливати лише після зелених jobs:

- `integration (PostgreSQL 18)` — має реально виконати `test_runtime_login*.py` (без Docker skip неможливий через `COLLECTOR_TEST_REQUIRE_DOCKER=1`, `testing-pr1b.md` п.8);
- `docker` — clean-host з per-role DSN на Linux.

Якщо якийсь job впаде, повторити gate 2 для виправлення.

### SR-3 | info | `src/collector/persistence/postgres/roles.py:185-241`, `:297-329`; `docs/plan/deps/WP-01D-to-WP-01A.md` §6

Правку у файлі WP-01A зроблено за винятком оркестратора, і її задокументовано. Відкритим лишається прийняття зміни власником («рев'ю у наступному PR WP-01A»). Варто записати це в `docs/plan/ledger.md` (рядок WP-01A), інакше пункт загубиться.

Дрібниця в документації: `docs/workers.md` §7.1 перелічує не всі нові відмови. Бракує `session_user ≠ current_user`, членства в іншій `collector_*` і моніторингових вбудованих ролей (S-2/S-3). Діагностику в runbook (`worker-recovery.md` п.5) це не зачіпає.

### SR-4 | low | `docs/plan/cards/WP-01D.md:65`, `docs/plan/deps/WP-01A-to-WP-01D.md` §7

Вимогу N-2 (poison event у publisher loop) передано «в картку WP-01B», але картки WP-01B ще немає. HANDOFF і ledger про N-2 теж не згадують. Оркестратору: занести N-2 у HANDOFF/ledger або в картку WP-01B під час її створення. Інакше вимога лишиться тільки в deps-файлі, який власник publisher-а може не прочитати.

### Пропозиція зміни ТЗ / ADR

**ТЗ-1 (§13, «API/exporter має read-only ролі в обох БД»).** Для `export` як worker pool §7.6 цю вимогу неможливо виконати буквально. Будь-який worker runtime пише в `worker_instances` (heartbeat), `crawl_jobs` (claim/lease/release) і `audit_log` (bootstrap pool), а `collector_export_ro` за визначенням цього не може.

Пропоную уточнити §13 до злиття першого реального export handler (WP-11A), найкраще через ADR з owner WP-01A + WP-11A. Формулювання: «exporter читає доменні дані (PostgreSQL/MongoDB) лише read-only ролями; runtime-черга export-worker-а ходить окремою мінімальною роллю (`collector_exporter`: лише спільна база черги, як у fetcher/parser, без control-plane таблиць)».

PR1b ця пропозиція не блокує: поточне відхилення тимчасове й прийняте з owner, тригером і тест-вартовим (§6).

---

## 8. Підсумок

- Acceptance (картка P1–P8, §17.2, §13, §7.6/§16.1/§16.3): `missing` немає. `partial` — 4 рядки (W1, S13-3, G5, Додаток C «Технічна безпека»); усі зводяться до WP-рівня (PR2/PR3) або до прийнятого S-1.
- DoD: 5 evidenced (D1 — з винятком, SR-3), 2 partial (п.8, п.9), 3 not applicable (п.3–5). `missing` немає.
- Додаток C: 1 evidenced, 2 partial.
- Разом `partial`: 7 (W1, S13-3, G5, D8, D9, Додаток C «Технічна безпека», Додаток C «Незалежна реалізація»). `missing`: 0.
- Знахідки: SR-1 low, SR-2 info (умова злиття), SR-3 info, SR-4 low; пропозиція ТЗ-1. Critical/high немає; security S-1 (medium) має статус `accepted`.
- **Вердикт: `accept`.** Злиття — після зеленого CI (SR-2).
