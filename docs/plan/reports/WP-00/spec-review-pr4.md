# WP-00 PR4: приймання за ТЗ, gate 4 (`wp/00-4-role-dsn-secrets`)

| Поле | Значення |
|---|---|
| Branch / HEAD | `wp/00-4-role-dsn-secrets` @ `9281cdf` (worktree `.worktrees/wp-00-4`), 13 комітів від `main` (`2993b16`) |
| Diff | `git diff main...HEAD`: 23 файли, +1754/−36 |
| Дата | 2026-09-24 |
| Рев'юер | `wp-spec-reviewer` (read-only; записи лише в цей файл і `docs/acceptance/traceability.md`) |
| Обсяг | Картка `docs/plan/cards/WP-00.md` розділ PR4 (вимоги 1–5, тести, acceptance, owned/forbidden); `docs/plan/deps/WP-01A-to-WP-00.md` §4, §6; ТЗ §7.5, §13, §16.2, §16.3, §17.2 (рядок WP-00), §18, §20, Додаток C; REVIEW.md R-51, R-55 |
| Вхідні звіти | `implementation-pr4.md` (разом із «Fixes after gate 2/3»), `testing-pr4.md` (pass), `code-review-pr4.md` (approve, CR-1..CR-3 low), `security-pr4.md` (approve, L-1/L-2 low, I-1..I-5 info) |
| Метод | Статичний аналіз diff і звітів. Власна верифікація без Docker (Docker за умовою завдання не піднімався): unit-модулі PR4 і gitleaks (нижче) |
| Scope-застереження | PR4 закриває лише `WP-01A-to-WP-00.md` §4 п.1–2 і §6. Pilot-блокер §13 (HANDOFF #1: runtime ходить superuser-DSN) **закривається тільки парою PR4 + WP-01D PR1b** (`wp/01d-1b-runtime-role-dsn`, §4 п.3). Після злиття лише PR4 LOGIN-ролі існують, але runtime-сервіси ще монтують `postgres_dsn` |
| **Вердикт** | **`accept`**: 0 `missing` по acceptance/DoD; 8 рядків `partial`, і всі зводяться до SR-1 або SR-2; знахідок critical/high/medium немає. Є 1 low (SR-1, owner/date у accepted-знахідок) і 1 умова злиття (SR-2, зелений CI job `docker` на Linux) |

---

## Власна верифікація

```text
$ uv run pytest tests/unit/test_secrets_role_dsn.py tests/unit/test_secrets_role_dsn_adversarial.py \
    tests/unit/test_compose_config.py tests/unit/test_compose_config_adversarial.py -q -p no:warnings -rs
E  subprocess.TimeoutExpired: Command '[...bash.exe', '.../test_rerun_leaves_every_existi0/init-secrets.sh']' timed out after 60 seconds
2 failed, 138 passed in 477.36s (0:07:57)
$ uv run pytest <ті самі модулі> -q -p no:warnings --lf
2 passed, 39 deselected in 35.49s

$ gitleaks git --log-opts="main..HEAD" --no-banner      # gitleaks 8.30.1
13 commits scanned. ... no leaks found

$ git diff main...HEAD -- tests/unit/test_compose_config.py | grep "^@@"   # hunks 262, 348, 412, 433, 700
$ git ls-remote --heads origin wp/00-4-role-dsn-secrets                     # порожньо: гілку не запушено, CI не запускався
$ grep -c docker.sock docker-compose.yml                                    # 0
```

Два падіння — timeout 60 с підпроцесу `init-secrets.sh` у Git Bash на завантаженому хості, на повторі зелені (див. SR-3). Gitleaks охоплює всі 13 комітів, включно з `8775f7e` і `9281cdf`, яких немає в gitleaks-прогонах implementation- і security-звітів.

---

## 1. Acceptance criteria

### 1.1. Acceptance картки PR4

| # | Вимога | Доказ | Статус |
|---|---|---|---|
| A1 | `migrate-postgres` завершується 0 і дає сім LOGIN-ролей | `testing-pr4.md` рядки 60–70 (`docker inspect ... ExitCode` → `0`, `login enabled:` 7 ролей); `implementation-pr4.md` рядки 129–140; крок CI `.github/workflows/ci.yml:404-424` (точний набір із семи, без `collector_migrate`), виконаний локально дослівно (`testing-pr4.md:72-83`) | evidenced (локальний Docker; Linux CI див. A5) |
| A2 | Повторний `up` ідемпотентний | `testing-pr4.md:89-95` (`up --force-recreate` exit 0, one-shot exit 0, `sha256sum -c` 8 DSN OK); `implementation-pr4.md:158-164`; unit `test_secrets_role_dsn_adversarial.py:115` `test_rerun_leaves_every_existing_secret_byte_identical`, `:135`, `:145`; мутація (`testing-pr4.md:166`) → 3 failed | evidenced |
| A3 | Жоден пароль не потрапляє в `environment`/`docker inspect`/логи | `ci.yml:444-455` (grep plaintext усіх 8 паролів у `compose logs`, `docker inspect`, `denied.txt` → exit 1 при збігу), локальний прогін exit 0 (`testing-pr4.md:87`, `implementation-pr4.md:169-171`); `test_secrets_role_dsn_adversarial.py:301` `test_role_dsn_never_inlined_into_environment`; compose `environment` лише `*_FILE` (`docker-compose.yml:351-357`); лог маскує `***` (`testing-pr4.md:67`) | evidenced |
| A4 | §13 «REVOKE PUBLIC» для БД `collector` | `deploy/compose/postgres/init/02-revoke-public.sql:28` (`REVOKE CONNECT, TEMPORARY ... current_database()`), `:29-34` (GRANT CONNECT 8 group-ролям); `test_compose_config.py` `test_postgres_init_revokes_public_on_app_and_service_databases`; Docker: `temp:false`, порожній PUBLIC ACL, `permission denied for database "postgres"` (`testing-pr4.md:81-86`) | evidenced (лише для нових кластерів; наявні — accepted L-2, owner/дата є) |
| A5 | (вимога 5) CI job `docker` зелений, секрети генеруються тим самим шляхом | `ci.yml:265-266` `bash ./deploy/compose/secrets/init-secrets.sh` (той самий скрипт, що локально); порядок кроку після `up` і перед `down -v`: `ci.yml:358,404,457`; `test_secrets_role_dsn.py:273` `test_ci_docker_job_checks_role_logins_revoke_public_and_leaks`. Сам job на GitHub-runner **не запускався**: гілку не запушено | partial (SR-2: умова злиття) |

### 1.2. Вимоги PR4 1–5 і тести картки

| # | Вимога | Доказ | Статус |
|---|---|---|---|
| V1 | 7 × `postgres_dsn_<component>`, формат `postgresql://collector_<c>:<hex48>@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-collector}`, власний пароль, ідемпотентно, `.example` без секрету | `init-secrets.sh:153-165` (`new_hex` на кожен DSN, `:53` перевірка `^[0-9a-f]{48}$`); `test_secrets_role_dsn.py:208` (вісім DSN, попарно різні паролі, user = роль), `:233` (парсер WP-01A `load_role_logins` приймає), `:241`, `:250`, `:261` (override host/port/db); `…_adversarial.py:178`, `:199`; `.example` ×7 (`test_secrets_role_dsn.py:142`, параметризація); мутація «спільний пароль» → 2 failed (`testing-pr4.md:167`) | evidenced |
| V2 | Сім записів top-level `secrets:` дослівно й одразу після `postgres_dsn` | `docker-compose.yml:695-712`; `test_secrets_role_dsn.py:87` `test_every_role_dsn_secret_is_declared_verbatim_after_postgres_dsn` | evidenced |
| V3 | `migrate-postgres`: `db migrate && db roles --with-login` без втрати exit code; монтує `postgres_dsn` + 7; `/run/secrets`; повторний запуск ідемпотентний | `docker-compose.yml:339-366` (`sh -c 'collector db migrate && exec collector db roles --with-login'`); `test_secrets_role_dsn.py:97` (рівно 8 секретів, `COLLECTOR_POSTGRES_ROLE_SECRETS_DIR` не перевизначено = `/run/secrets`), `:110` (без `;`/`\|\|`); реальний `sh` зі stub: `…_adversarial.py:253,259,265`; мутація `&&`→`;` → failed (`testing-pr4.md:165`); Docker: збій кожної з двох команд → exit 1 (`testing-pr4.md:97-106`) | evidenced |
| V4 | Init: REVOKE CONNECT/TEMP на `collector`, явний GRANT CONNECT group-ролям; REVOKE ALL на `postgres`/`template1` з обґрунтуванням; вартовий «немає GRANT на таблиці/паролів» зелений і не послаблений | `02-revoke-public.sql:21-35`; обґрунтування — `implementation-pr4.md:20-41` і коментар `02-revoke-public.sql:1-15`. Вартовий `test_compose_config.py` `test_postgres_init_scripts_are_mounted_read_only_for_wp_01a` уточнено й **посилено**: перевіряє обидва `*.sql`, забороняє `*.sh`, `ALTER ROLE`, `TO PUBLIC`, будь-який GRANT, крім `GRANT CONNECT ON DATABASE %I TO %I`. Мутації (`GRANT SELECT ON ALL TABLES`, `GRANT CONNECT ... TO PUBLIC`) → failed (`implementation-pr4.md:94-98`). `.gitattributes` `eol=lf` для SQL (F-3) | evidenced. Замість літерала `collector` використано `current_database()`: це еквівалент, стійкий до override `POSTGRES_DB`, а не відхилення |
| V5 | CI job `docker` зелений | див. A5 | partial |
| T-int | Integration/Docker: під `collector_fetcher` підключення до `collector` є, до `postgres` немає | `ci.yml:436-442`; локально: `current_user = collector_fetcher`, `FATAL: permission denied for database "postgres"` (`testing-pr4.md:84-86`, `implementation-pr4.md:154-156`) | evidenced |
| Owned/forbidden | Лише owned-файли; `x-worker`/`scheduler`/`*-worker`, `src/**`, `sql/**`, `migrations/**`, тест-вартовий WP-01D не зачеплено | `git diff main...HEAD --stat`: 23 файли, усі в owned-списку картки (`deploy/compose/postgres/init/.gitattributes` входить у `deploy/compose/postgres/init/**`). Compose-hunks лише `docker-compose.yml:339-366` і `:695-712`. `test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` (`test_compose_config.py:455`) поза hunks диффу | evidenced |

### 1.3. §17.2, рядок WP-00 («...Compose .../secrets, migrations, CI...; clean-host stack smoke green»)

| Частина | Доказ (у scope PR4) | Статус |
|---|---|---|
| Compose secrets | V1, V2; `deploy/compose/README.md` (таблиця secrets, рядок `postgres_dsn_<component>`) | evidenced |
| migrations | V3: one-shot тепер застосовує і ролі §13 з LOGIN | evidenced |
| CI | Новий крок `ci.yml:404-455`; прогін на runner-і не відбувся | partial (SR-2) |
| clean-host stack smoke green | Локально `core+workers` `up -d --wait` exit 0 (`implementation-pr4.md:129-134`, `testing-pr4.md:60-62`); з `gui` і на Linux — лише в CI | partial (SR-2) |

### 1.4. Дотичні пункти §16.3

| Пункт | Доказ | Статус |
|---|---|---|
| Чистий Docker host піднімає core/workers/gui однією documented командою; migrations завершуються до readiness; restart не втрачає named-volume data | `migrate-postgres` лишився one-shot із `depends_on: service_completed_successfully` для `api` (`test_compose_config.py` `test_readiness_waits_for_one_shots`, не змінений); локальний `up --wait` і повторний `up` зелені (A1, A2); runbook для хостів до PR4 — `docs/runbooks/clean-host-start.md` (нові рядки «Типові проблеми»). Прогін з `gui` — лише в CI | partial (SR-2) |
| GUI/API не мають Docker socket | PR4 не додає mounts; `grep -c docker.sock docker-compose.yml` → 0; `test_compose_config.py:149` `test_no_docker_socket_mount_anywhere` | evidenced |
| Інші пункти §16.3 (pilot, fault injection, scale, GUI E2E, translation QA тощо) | не стосуються PR4: немає runtime-логіки, адаптерів і GUI | not applicable |

---

## 2. Definition of Done §18

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| 1 | Один WP, без сторонніх змін | §1.2 «Owned/forbidden» | evidenced |
| 2 | Formatter, lint, types, unit/contract/integration | `implementation-pr4.md:269-275` (ruff/format/mypy зелені, `1033 passed, 23 skipped` після gate 3); власний прогін модулів PR4: 138 passed + 2 passed на повторі (SR-3). Integration на рівні Docker — локально | evidenced |
| 3 | Зміна схеми має migration і compatibility evidence | Схему БД не змінено: `src/**`, `sql/**` і `migrations/**` поза диффом. Init-SQL змінює лише ACL на рівні БД | not applicable |
| 4 | Temporal/replay/reproducibility | Timestamp/matching/release не зачеплено | not applicable |
| 5 | Новий адаптер | Адаптерів немає | not applicable |
| 6 | Документація, метрики, runbook | `docs/runbooks/clean-host-start.md` (+25: перевірка LOGIN-ролей, REVOKE лише при initdb, хости до PR4, 2 рядки troubleshooting), `deploy/compose/README.md`, `deploy/compose/postgres/init/README.md` (ручне застосування на наявному кластері). Метрики: нових runtime-компонентів немає | evidenced |
| 7 | Secret scan чистий; контактів у logs/fixtures немає | Власний `gitleaks git main..HEAD`: 13 комітів, no leaks. `.example` лише плейсхолдери (`test_secrets_role_dsn.py:142`). Контактних даних у диффі немає | evidenced |
| 8 | Findings: `fixed` / `accepted with owner/date` / `not applicable` | Gate 2 F-1..F-3 fixed (`implementation-pr4.md:231-233`). Gate 3 CR-1/L-1, CR-2, CR-3 fixed з тестами (`:260-262`). L-2 accepted, owner WP-00 / оператор, 2026-09-24 (`:263`, `clean-host-start.md`). **Без owner/date:** F-4 «accepted» (`:234`), security I-1..I-4 «прийнято». I-2 фактично покритий ADR-0002:164 (owner WP-01D, 2026-09-22), але звіт на нього не посилається | partial (SR-1) |
| 9 | Злиття лише після CI і required review; SHA/evidence зафіксовані | Review gates 2–4 пройдено. CI ще не запускався, SHA злиття ще немає | partial (SR-2, виконується оркестратором при merge) |

---

## 3. Рядки Додатка C, які покриває PR4

| Ціль Додатка C | Вимоги | Що покриває PR4 | Доказ | Статус |
|---|---|---|---|---|
| Технічна безпека | FR-013, §13 | Per-component DB credentials: 7 DSN з власними паролями, LOGIN runtime-ролей через `db roles --with-login`; REVOKE PUBLIC; паролі не в env/inspect/logs | V1–V4, A3, T-int | evidenced для інфраструктурної частини. Використання цих DSN runtime-сервісами — WP-01D PR1b |
| Docker і масштабування | FR-030, §7.5 | Secrets лише як Docker file-secrets (`*_FILE`), one-shot migrations із ролями до readiness, clean-host start не зламано | `docker-compose.yml:339-366,695-712`, A1, A2 | evidenced локально, CI — partial (SR-2) |
| Незалежна реалізація | §17, §18 | Owned files, dependency-запит закрито без чужих файлів, звіти 4 gate-ів | §1.2 Owned/forbidden, §2 | partial (DoD 8, 9) |

---

## 4. Регресія REVIEW.md

| R | Суть | Втілення в коді/тестах PR4 | Статус |
|---|---|---|---|
| R-51 (Fixed у ТЗ) | Контейнерна поставка, відтворюваний clean-host start | PR4 додає 7 обов'язкових file-secrets, і хост без них не стартує. Регресію закрито в коді: `init-secrets.sh` генерує бракуючі й не перезаписує наявні (`test_init_secrets_adds_only_missing_role_dsns_on_pre_pr4_host`, `test_secrets_role_dsn.py:250`). Каталоги-заглушки Docker Desktop прибираються (`init-secrets.sh:102`, тести `:294,311,322`). CI генерує секрети тим самим скриптом (`ci.yml:266`). Clean-host `up --wait` локально зелений; runbook оновлено | evidenced (Linux CI — SR-2) |
| R-55 (Fixed у ТЗ) | Жодного Docker socket у GUI/API | Нових mounts немає; `test_no_docker_socket_mount_anywhere` (`test_compose_config.py:149`) і `test_ci_docker_job_profiles_and_no_socket_or_hardcoded_secrets` (`test_compose_config_adversarial.py:319`) зелені | evidenced |

---

## 5. Q-питання §20

| Q | Default | Як втілено в PR4 | Статус |
|---|---|---|---|
| Q-006 | Один хост MVP, SLO §2.4 | Single-host Compose, file-secrets на хості; ADR-0002 (`docs/decisions/0002-docker-compose-single-host.md:20`) | evidenced (ADR є) |
| Q-013 | Compose для MVP, Swarm для production | Права 0644 для 7 нових файлів — той самий risk acceptance ADR-0002:164 (owner WP-01D, 2026-09-22). Production — Swarm secrets | evidenced (ADR є) |

---

## 6. Accepted-ризики: owner і дата

| Ризик | Джерело | Owner / дата | Оцінка |
|---|---|---|---|
| L-2: REVOKE PUBLIC лише при першому initdb | `security-pr4.md:15`, `implementation-pr4.md:263`, `clean-host-start.md` | WP-00 / оператор, 2026-09-24 | ок |
| F-4: скрипт bash-only | `testing-pr4.md:184`, `implementation-pr4.md:234` | **немає** | SR-1 |
| I-1: one-shot тримає всі DSN | `security-pr4.md:16` | немає (info, ескалації немає) | SR-1 |
| I-2: секрети 0644 | `security-pr4.md:17` | ADR-0002:164, WP-01D, 2026-09-22 (посилання в звіті бракує) | SR-1 (лише посилання) |
| I-3: нові БД знову отримують PUBLIC CONNECT; `db roles` не видає CONNECT поза initdb | `security-pr4.md:18`, `code-review-pr4.md:51-52` | немає | SR-1: призначити owner для production-кластерів (не Compose) |
| I-4: SCRAM verifier у server log при збої `ALTER ROLE`; без `::add-mask::` | `security-pr4.md:19` | немає | SR-1 |
| I-5: migration DSN = superuser `POSTGRES_USER`; runtime на спільному DSN | `security-pr4.md:20` | pilot-блокер HANDOFF #1, WP-01D PR1b | поза scope PR4 (SR-5) |

---

## Знахідки

### SR-1 | low | `docs/plan/reports/WP-00/implementation-pr4.md:234`, `docs/plan/reports/WP-00/security-pr4.md:16-19`

§18 п.8 вимагає `accepted with owner/date`. Під час цього рев'ю в worktree з'явився незакомічений розділ «Re-review (gate 3')» у `code-review-pr4.md` (approve, CR-1..CR-3 і L-1 closed). У ньому 3 нові low без статусу: №1 — timeout і kill launcher-а Git Bash у тестах, №2 — зайві fork-и в `write_secret`, №3 — stale lock без PID і без згадки в runbook. Їм теж потрібен статус `fixed` або `accepted (owner, дата)`. F-4 позначено `accepted` без owner і дати. I-1..I-4 security-рев'ю закриті словом «прийнято» без owner/date і без відповіді в «Fixes after gate 3». Виправлення, лише документація і лише в implementation-звіті: додати рядки F-4, I-1..I-4 зі статусом `accepted (owner, 2026-09-24)` або `not applicable` з аргументом. Для I-2 послатися на ADR-0002:164. Для I-3 назвати owner-а production-hardening кластера (CONNECT/REVOKE поза Compose initdb), наприклад WP-01D production enablement або WP-13. Злиття не блокує, але бажано закрити до merge.

### SR-2 | low (умова злиття) | `.github/workflows/ci.yml:404-455`

Вимога 5 і DoD п.9: CI job `docker` на Linux runner-і не запускався, бо гілку не запушено (`git ls-remote` порожній). Новий крок (heredoc у `run:`, `$RUNNER_TEMP`, `docker compose exec -e PGDSN`) доведено лише на Windows + Docker Desktop двома незалежними прогонами. Злиття — лише після зеленого job `docker` (разом із `gui`-профілем і e2e) на PR. Якщо job впаде, повторити gate 2 для виправлення.

### SR-3 | info | `tests/unit/test_secrets_role_dsn_adversarial.py:92,248`, `tests/unit/test_secrets_role_dsn.py:196`

Незалежно підтверджено re-review gate 3' (№1, №2 у `code-review-pr4.md`: 8/8 паралельних прогонів дали timeout; причина — `<Git>/bin/bash.exe` як launcher і повільний fork). У моєму прогоні 2 з 140 тестів упали через `TimeoutExpired` (60 с на запуск `init-secrets.sh` у Git Bash) на завантаженому хості. `--lf` → `2 passed`. Це той самий клас, що описано в `implementation-pr4.md:278-283`: після CR-3 кожен секрет коштує кількох fork-ів (`mktemp`/`chmod`/`mv`). На Linux CI не очікується. Для Windows-розробників пропоную (owner WP-00, follow-up) підняти timeout до 180 с або скоротити кількість fork-ів. Контракт PR4 це не зачіпає.

### SR-4 | info | `docs/plan/deps/WP-01A-to-WP-00.md:8,90,134`

Статуси §4 (п.1–2) і §6 досі `open`. Файл не входить в owned-список PR4; при merge оркестратор має позначити §4 п.1–2 і §6 як `resolved by WP-00 PR4`, а §4 п.3 залишити за WP-01D PR1b.

### SR-5 | info | pilot-блокер §13 (HANDOFF #1)

§13 «облікові дані БД розділені за компонентами; migration role не використовується runtime-процесами» PR4 виконує лише інфраструктурно: секрети, LOGIN, REVOKE. `scheduler` і `*-worker` досі монтують `postgres_dsn` (superuser). Це свідомо і за карткою, а вартовий `test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` лишається за WP-01D. Блокер знімається лише після злиття WP-01D PR1b. Там варто перевірити, що кожен сервіс монтує **свій** DSN: `test_role_dsn_secrets_are_not_mounted_into_services_outside_the_plan` (`test_secrets_role_dsn.py:122`) перевіряє лише «≤ 1 per-role DSN на сервіс», а не відповідність сервіс → компонент. Merge-конфлікт у `SECRET_CONSUMERS` (`test_compose_config_adversarial.py`) розв'язується об'єднанням множин (`implementation-pr4.md:187-194`).

### Пропозиції ADR/зміни ТЗ

Немає. ТЗ і картку виконано буквально. Єдина вільна інтерпретація (`current_database()` замість літерала `collector` у V4) еквівалентна при default `POSTGRES_DB` і безпечніша при override.

---

## Підсумок

- Acceptance картки PR4: 4 evidenced, 1 partial (A5 CI).
- Вимоги 1–5: 4 evidenced, 1 partial (V5 = A5).
- DoD: 4 evidenced, 2 partial (п.8, п.9), 3 not applicable. Missing немає.
- Усього рядків `partial` у матриці: 8 (A5, V5, §17.2 CI, §17.2 smoke, §16.3 clean-host, DoD 8, DoD 9, Додаток C «Незалежна реалізація»), і всі зводяться до SR-1 або SR-2.
- Знахідки: critical/high/medium — 0; low — 2 (SR-1, SR-2); info — 3.
- **Вердикт: `accept`**, з умовою злиття SR-2 (зелений CI job `docker`) і рекомендацією закрити SR-1 до merge.
