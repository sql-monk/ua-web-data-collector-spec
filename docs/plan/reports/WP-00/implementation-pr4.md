# WP-00 PR4 — `wp/00-4-role-dsn-secrets`: звіт реалізації

Картка: `docs/plan/cards/WP-00.md`, розділ «PR4». Закриває dependency
`docs/plan/deps/WP-01A-to-WP-00.md` §4 (п.1–2) і §6. П.3 §4 (runtime-сервіси на власних DSN)
належить WP-01D PR1b і в цій гілці свідомо не зроблений.

## Що зроблено

Коміти (branch `wp/00-4-role-dsn-secrets`, від `bc1af47`):

| Коміт | Зміст |
|---|---|
| `14c7ce7` feat(wp-00) | `init-secrets.sh`: гілка `postgres_dsn_*)` генерує `postgresql://collector_<component>:<random_hex_48>@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-collector}`, кожен DSN зі своїм `random_hex`; наявні файли не перезаписуються. Сім `postgres_dsn_<component>.example` з плейсхолдером `<random_hex_48>` |
| `d1c013e` feat(wp-00) | `docker-compose.yml`: top-level `secrets:` — сім записів **дослівно** з картки, одразу після `postgres_dsn`; `migrate-postgres` — `sh -c 'collector db migrate && exec collector db roles --with-login'`, монтує `postgres_dsn` + сім per-role DSN, каталог — типовий `/run/secrets` (env `COLLECTOR_POSTGRES_ROLE_SECRETS_DIR` не задається). `x-worker`/`scheduler`/`*-worker` не змінено |
| `8f83fba` feat(wp-00) | `deploy/compose/postgres/init/02-revoke-public.sql` + README: REVOKE PUBLIC (обґрунтування нижче) |
| `60f9294` test(wp-00) | новий `tests/unit/test_secrets_role_dsn.py`; оновлено `test_compose_config.py` (команда one-shot, вартовий init-скриптів, новий тест REVOKE, скоуп `postgres_dsn`) і `test_compose_config_adversarial.py` (мапа споживачів секретів) |
| `bb91d1d` feat(wp-00) | CI job `docker`: крок перевірки LOGIN-ролей, ACL PUBLIC, підключення `collector_fetcher` і витоку паролів; `deploy/compose/README.md`, `docs/runbooks/clean-host-start.md` |
| `d7c73eb` docs(wp-00) | markdownlint MD001 у `postgres/init/README.md` |

### Вимога 4: REVOKE PUBLIC — обґрунтування

`02-revoke-public.sql` (виконується після `01-roles.sql` за алфавітом, тому ролі вже існують):

- БД застосунку береться як `current_database()` (entrypoint виконує init-скрипти в
  `POSTGRES_DB`), а не захардкоджений `collector` — override `POSTGRES_DB` не ламає скрипт.
  `REVOKE CONNECT, TEMPORARY … FROM PUBLIC`, потім `GRANT CONNECT` восьми group-ролям §13.
  `collector_migrate` теж отримує CONNECT: зараз міграції ходять superuser-ом (оминає перевірку),
  але майбутній non-superuser login-член `collector_migrate` без нього не підключився б.
  TEMPORARY не видано нікому: у `src/` немає тимчасових таблиць (`grep -i "create temp"` — порожньо).
- `postgres`, `template1`: `REVOKE ALL … FROM PUBLIC`. Безпечно, бо: (а) healthcheck
  `pg_isready` не автентифікується і прав на БД не потребує; (б) entrypoint/`psql` оператора —
  superuser, який перевірку CONNECT оминає; (в) one-shots (`migrate-postgres`) підключаються до
  БД застосунку, не до службових; (г) `CREATE DATABASE … TEMPLATE template1` потребує CREATEDB,
  а не CONNECT на template1. `template0` уже `datallowconn = false`. Якщо `POSTGRES_DB` =
  `postgres`, ця БД пропускається в першому циклі й отримує схему БД застосунку.
- Вартовий `test_postgres_init_scripts_are_mounted_read_only_for_wp_01a` раніше забороняв
  будь-який `GRANT` в одному файлі. Уточнено, не послаблено: тепер він перевіряє **всі** `*.sql`
  (їх рівно два, `*.sh` заборонено), забороняє `PASSWORD`/`LOGIN`/`ALTER ROLE|TABLE|FUNCTION`, а
  кожне входження `GRANT` мусить бути саме `GRANT CONNECT ON DATABASE %I TO %I'`; `TO PUBLIC`
  заборонено. Мутаційна перевірка: дописаний `GRANT SELECT ON ALL TABLES …` і
  `GRANT CONNECT … TO PUBLIC` — тест падає (вивід нижче).

### Тести ↔ acceptance

| Пункт картки | Тест |
|---|---|
| `migrate-postgres` монтує рівно `postgres_dsn` + сім, команда з `--with-login` | `test_migrate_postgres_mounts_exactly_migration_and_role_dsns`, `test_migrate_postgres_runs_roles_with_login_after_migrate_keeping_exit_code`, `test_one_shot_commands_match_spec_16_2` |
| кожен per-role секрет оголошено (дослівно, після `postgres_dsn`) | `test_every_role_dsn_secret_is_declared_verbatim_after_postgres_dsn`, `test_secrets_are_files_with_examples_and_gitignored` |
| `init-secrets.sh`: вісім DSN, попарно різні паролі, користувач = роль (tmp-каталог, без мережі) | `test_init_secrets_generates_eight_dsns_with_distinct_passwords_and_role_users`, `…_is_idempotent_and_never_overwrites`, `…_adds_only_missing_role_dsns_on_pre_pr4_host`, `…_honours_postgres_host_port_db_overrides` |
| формат збігається з `db roles --with-login` | `test_generated_role_dsns_are_accepted_by_db_roles_with_login` (парсер WP-01A `load_role_logins`), `test_component_list_matches_runtime_roles_of_db_roles` |
| `.example` без секретів | `test_role_dsn_example_is_a_placeholder_without_secret` (×7) |
| REVOKE PUBLIC у init | `test_postgres_init_revokes_public_on_app_and_service_databases`, вартовий вище |
| після `up --wait`: `collector_fetcher` підключається до `collector`, до `postgres` — ні; сім LOGIN-ролей; паролі не в логах/`docker inspect` | крок CI `Per-component LOGIN-ролі §13, REVOKE PUBLIC, паролі не витікають` (виконано локально, вивід нижче); наявність і порядок кроку — `test_ci_docker_job_checks_role_logins_revoke_public_and_leaks` |
| повторний `up` ідемпотентний | виконано локально (другий `up -d --wait` перезапустив one-shot, exit 0) |

Тест у CI не пропускається мовчки: на POSIX відсутність `bash` — провал (`assert`), skip
можливий лише на Windows без Git Bash (System32\bash.exe — WSL, свідомо не використовується).
Локально на Windows усі 5 тестів зі скриптом виконались (Git Bash знайдено через шлях `git`).
Мережі тести не використовують (лише `subprocess` bash + файли в `tmp_path`).

## Команди та вивід

Windows 11, Docker Engine 29.8.0, worktree `C:\repos\webscraper\.worktrees\wp-00-4`.

```text
$ uv sync --frozen
Checked 66 packages in 8ms

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
251 files already formatted

$ uv run mypy src
Success: no issues found in 77 source files

$ uv run pytest -m "not live"
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:191: gui не відповідає на http://127.0.0.1:80 …
… (ще 14 skip того самого модуля — стек gui не піднятий)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49: перевірка діє лише там, де стек обіцяний …
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:62: …
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
1010 passed, 23 skipped, 9 warnings in 757.10s (0:12:37)
```

Усі 23 skip — наявні до PR4 (e2e без стека, Windows-специфіка); жоден не з нових тестів.

```text
$ uv run pytest tests/unit/test_secrets_role_dsn.py tests/unit/test_compose_config.py \
    tests/unit/test_compose_config_adversarial.py -q -p no:warnings
117 passed in 83.46s (0:01:23)

$ # мутація вартового: + "GRANT SELECT ON ALL TABLES IN SCHEMA public TO collector_fetcher;"
AssertionError: 02-revoke-public.sql: дозволено лише GRANT CONNECT ON DATABASE, не ' SELECT ON ALL TABLES IN SCHEMA public TO collector_fetcher;'
1 failed, 3 passed, 74 deselected in 0.58s
$ # мутація: + "GRANT CONNECT ON DATABASE collector TO PUBLIC;"
1 failed, 3 passed, 74 deselected in 0.55s

$ uv run pre-commit run --files <усі змінені файли>
… Detect hardcoded secrets ... Passed; markdownlint-cli2 ... Passed (після d7c73eb); решта Passed
$ gitleaks git --log-opts="bc1af47..HEAD"
5 commits scanned. … no leaks found

$ ./deploy/compose/secrets/init-secrets.sh
gen   minio_root_password (random)
copy  minio_root_user (from example — non-secret)
gen   mongo_keyfile (random keyfile)
gen   mongo_root_password (random)
gen   postgres_password (random, для DSN)
gen   postgres_dsn (з postgres_password)
gen   postgres_dsn_api_ro (random, роль collector_api_ro)
gen   postgres_dsn_export_ro (random, роль collector_export_ro)
gen   postgres_dsn_fetcher (random, роль collector_fetcher)
gen   postgres_dsn_parser (random, роль collector_parser)
gen   postgres_dsn_projector (random, роль collector_projector)
gen   postgres_dsn_scheduler (random, роль collector_scheduler)
gen   postgres_dsn_translation (random, роль collector_translation)
skip  postgres_password (exists)

$ docker compose config --quiet                                                   → exit 0
$ docker compose --profile core --profile workers --profile browser --profile gui config --quiet → exit 0
$ docker compose --profile core config --format json   # migrate-postgres
['sh', '-c', 'collector db migrate && exec collector db roles --with-login']
['postgres_dsn', 'postgres_dsn_scheduler', 'postgres_dsn_fetcher', 'postgres_dsn_parser', 'postgres_dsn_projector', 'postgres_dsn_translation', 'postgres_dsn_api_ro', 'postgres_dsn_export_ro']
{…, 'COLLECTOR_POSTGRES_DSN_FILE': '/run/secrets/postgres_dsn', …}   # паролів в environment немає

$ docker compose --profile core --profile workers build   → Image collector:dev Built
$ docker compose --profile core --profile workers up -d --wait --wait-timeout 300
… Container collector-migrate-postgres-1 Exited / … Healthy
real 2m10.203s
up exit=0
api Up (healthy); discovery/export/fetch×2/maintenance/parse×2/projector/translation-worker,
scheduler, postgres, mongo, minio Up (healthy); ensure-mongo Exited (0); migrate-postgres Exited (0)

$ docker compose logs migrate-postgres     # (hex48 замасковано перевіркою; у логах їх і не було)
migrated postgresql+asyncpg://collector:***@postgres:5432/collector: empty -> 0005_entity_version_guard
partition created: … (8 рядків)
roles applied to postgresql+asyncpg://collector:***@postgres:5432/collector from roles.sql: collector_migrate, collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro
login enabled: collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro

$ COMPOSE_PROFILES=core,workers bash -eo pipefail role-check.sh   # дослівно `run:` нового кроку CI
LOGIN roles:
collector_api_ro
collector_export_ro
collector_fetcher
collector_parser
collector_projector
collector_scheduler
collector_translation
PUBLIC ACL / TEMP:
temp:false
role-check exit=0
$ cat "$RUNNER_TEMP/denied.txt"
psql: error: connection to server at "postgres" (172.19.0.3), port 5432 failed: FATAL:  permission denied for database "postgres"
DETAIL:  User does not have CONNECT privilege.

$ # повторний up (ідемпотентність)
$ docker compose up -d --wait --wait-timeout 300          → up#2 exit=0
migrate-postgres Exited (0) 6 seconds ago                  # one-shot виконався вдруге
"login enabled" у логах migrate-postgres: 2
$ docker compose run --rm --no-deps migrate-postgres       → explicit rerun exit=0
roles applied to … / login enabled: … (ті самі сім ролей)
$ bash role-check.sh                                       → role-check#2 exit=0

$ docker compose down -v --remove-orphans                  → down exit=0; томів collector_* — 0
```

Крок `role-check` на кожному прогоні також шукає пароль кожного з восьми DSN у
`docker compose logs` усіх сервісів, `docker inspect` усіх контейнерів проєкту і stderr
відмови — збігів немає (інакше exit 1).

## Що не перевірено

- CI job `docker` на Linux runner-і (GitHub Actions) — не запускався: push/PR робить
  оркестратор. Локально (Windows + Docker Desktop) той самий `run:` кроку пройшов; уроки
  попередніх PR (POSIX sh, CRLF) враховано: крок — bash GitHub Actions, `init-secrets.sh`
  має `eol=lf` у `.gitattributes`, unit-тест запускає скрипт через справжній bash.
- Повний стек з профілем `gui` локально не піднімався (картка вимагає `core`+`workers`);
  у CI крок іде після `up` усіх трьох профілів.
- Поведінка на кластері, створеному до PR4: init-скрипт там не виконується. Команда ручного
  застосування задокументована в `deploy/compose/postgres/init/README.md`, але на реальному
  «старому» томі не прогонялась.

## Ризики

- **Конфлікти злиття з WP-01D PR1b.** Top-level `secrets:` вставлено дослівно з картки
  (тест `test_every_role_dsn_secret_is_declared_verbatim_after_postgres_dsn` стежить за цим).
  Але обидві гілки, найімовірніше, чіпають `SECRET_CONSUMERS` у
  `test_compose_config_adversarial.py` і `test_postgres_dsn_secret_is_scoped_…` /
  `test_api_has_no_secrets_and_runtime_has_only_the_dsn`: тут per-role DSN споживає лише
  `migrate-postgres`, PR1b додасть runtime-сервіси. Розв'язання — об'єднання множин.
  `test_role_dsn_secrets_are_not_mounted_into_services_outside_the_plan` уже допускає рівно один
  per-role DSN на runtime-сервіс, тож після PR1b змін не потребує.
- Тест-вартовий `test_runtime_dsn_is_a_temporary_deviation_from_13_with_a_tripwire` лишився
  зеленим і не змінювався: LOGIN видає Python (`apply_logins`), а не SQL-файли ролей.
- REVOKE TEMPORARY на БД застосунку: якщо майбутній код почне створювати тимчасові таблиці під
  runtime-роллю, отримає `permission denied` — тоді потрібен явний `GRANT TEMPORARY` конкретній
  ролі в цьому init-скрипті.
- Хости, ініціалізовані до PR4: без повторного `init-secrets.sh` `migrate-postgres` завершиться
  exit 1 («бракує DSN-секретів») і `up --wait` впаде — задокументовано в
  `docs/runbooks/clean-host-start.md` (таблиця «Типові проблеми»).

## Як вимкнути або відкотити

- Повний відкат: `git revert d7c73eb bb91d1d 60f9294 8f83fba d1c013e 14c7ce7`.
- Лише LOGIN-ролі (без відкату секретів): у `migrate-postgres` повернути
  `command: ["collector", "db", "migrate"]` (або `… && exec collector db roles` без
  `--with-login`). Уже видані логіни лишаються, поки ролі не зробити `ALTER ROLE … NOLOGIN`
  вручну (`db roles` без `--with-login` їх не вимикає — поведінка WP-01A).
- REVOKE PUBLIC на живому кластері скасовується вручну superuser-ом:
  `GRANT CONNECT, TEMPORARY ON DATABASE collector TO PUBLIC;` і
  `GRANT CONNECT, TEMPORARY ON DATABASE postgres, template1 TO PUBLIC;`
  (видалення файла з `init/` діє лише на нові кластери).
- Згенеровані файли `deploy/compose/secrets/postgres_dsn_<component>` поза git; видалення
  безпечне, доки `migrate-postgres` не монтує їх.

## Dependency-запити

Нових немає. `docs/plan/deps/WP-01A-to-WP-00.md` §4 п.1–2 і §6 цим PR виконано; статус у
самому файлі не оновлено (не owned file WP-00 PR4 — оновлює оркестратор). §4 п.3 (runtime на
власних DSN) — WP-01D PR1b.

## Fixes after gate 2

Відповідь на `docs/plan/reports/WP-00/testing-pr4.md`. Коміти тестувальника `ba09483`,
`a1ae4ca` не змінювались.

| Знахідка | Статус | Що зроблено |
|---|---|---|
| F-1 (medium) каталог на місці секрету вважався секретом | **fixed** | `init-secrets.sh`: замість `[ -e ]` функція `secret_present`. Порожній каталог (так Docker Desktop підміняє відсутній file-secret) прибирається через `rmdir`, секрет генерується, у stdout з'являється `fix   <name> (…прибрано)`. Непорожній каталог або інший не-файл → `error: … rm -r …` у stderr і exit 1, вміст не чіпається. Порожній файл вважається відсутнім і генерується заново. Та сама перевірка діє і для `postgres_password` у гілці `postgres_dsn`. Чому так: у порожньому каталозі чи порожньому файлі немає даних, тож автоматичне виправлення нічого не губить і робить рецепт із runbook робочим без ручного `rmdir`. Непорожній каталог — це вже щось незрозуміле, тому вгадувати не беремось, зупиняємось голосно. Тести: `test_init_secrets_replaces_empty_directory_left_by_docker`, `test_init_secrets_stops_on_non_empty_directory_with_hint`, `test_init_secrets_regenerates_empty_file` (`tests/unit/test_secrets_role_dsn.py`) |
| F-2 (low) runbook описував не ту помилку | **fixed** | `docs/runbooks/clean-host-start.md`: новий рядок про каталоги-заглушки після `git pull` без `init-secrets.sh` (що робить скрипт і що робити, якщо він зупинився). Рядок «бракує DSN-секретів» тепер описує реальну причину: частину `postgres_dsn_<component>` не змонтовано в контейнер (наприклад, власний override) |
| F-3 (info) CRLF у `02-revoke-public.sql` на Windows | **fixed** | `deploy/compose/postgres/init/.gitattributes`: `*.sql text eol=lf` (кореневий `.gitattributes` не входить в owned files). `git ls-files --eol`: обидва `.sql` тепер `i/lf w/lf`. Entrypoint Postgres файли без `.sql`/`.sh` ігнорує. Вартовий init-скриптів зелений |
| F-4 (info) скрипт bash-only | **accepted** | Shebang `#!/usr/bin/env bash`; CI (`bash ./deploy/compose/secrets/init-secrets.sh`) і документація викликають його через bash (`./…` або `bash …`); unit-тести запускають справжній bash. Переписувати на POSIX `sh` без потреби — зайвий ризик регресії (`BASH_SOURCE` для запуску з будь-якого cwd) |

Залишковий ризик F-1: якщо `postgres_password` порожній, а `postgres_dsn` уже не порожній,
пароль згенерується заново і перестане збігатися з DSN. Раніше в такому стані стек теж не
працював (Postgres не ініціалізується з порожнім паролем), тож нового ризику немає; вихід —
видалити обидва файли разом із томом (`down -v`).

```text
$ uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -m "not live"
All checks passed!
254 files already formatted
Success: no issues found in 77 source files
1027 passed, 23 skipped in 316.24s (0:05:16)
exit=0
```

Усі 23 skip — ті самі, що й до gate 2 (e2e без стека, Windows-специфіка).
