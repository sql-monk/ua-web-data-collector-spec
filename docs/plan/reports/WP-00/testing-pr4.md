# WP-00 PR4 — незалежне тестування (`wp/00-4-role-dsn-secrets`)

Контракт: `docs/plan/cards/WP-00.md` «PR4», `docs/plan/deps/WP-01A-to-WP-00.md` §4, §6, ТЗ §13, §7.5.
Рівні §16.1: 14 (Docker), 1 (Unit). Середовище: Windows 11, Git Bash, Docker Desktop, worktree
`C:\repos\webscraper\.worktrees\wp-00-4`, HEAD `8fb6ef4`. Секретів у worktree на старті не було
(чистий стан); після прогону їх видалено, томи `collector_*` відсутні.

## Команди та дослівний вивід

```text
$ uv sync --frozen
Checked 66 packages in 7ms
exit=0
$ uv run ruff check .
All checks passed!
exit=0
$ uv run ruff format --check .
252 files already formatted
exit=0
$ uv run mypy src
Success: no issues found in 77 source files
exit=0

$ uv run pytest -m "not live" -q -rs          # до додавання моїх тестів
SKIPPED [6..1] tests\e2e\test_gui_runtime_contract.py:167..310: gui не відповідає на http://127.0.0.1:80 …  (21 шт.)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49: …
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:62: …
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
1010 passed, 23 skipped, 8 warnings in 513.27s (0:08:33)

$ docker compose config --quiet
compose config exit=0
```

Жоден skip не належить тестам PR4.

```text
$ ./deploy/compose/secrets/init-secrets.sh      # чистий стан
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
exit=0
--- second run
skip  minio_root_password (exists)
… (усі 13 — skip)
exit=0
$ grep -c $'\r' deploy/compose/secrets/postgres_dsn_*   → усі 0

$ docker compose --profile core --profile workers up -d --wait
… Container collector-migrate-postgres-1 Exited / collector-ensure-mongo-1 Exited / решта Healthy
exit=0
$ docker inspect -f '{{.State.ExitCode}}' collector-migrate-postgres-1
0
$ docker compose logs --no-color migrate-postgres
migrate-postgres-1  | No new upgrade operations detected.
migrate-postgres-1  | migrated postgresql+asyncpg://collector:***@postgres:5432/collector: empty -> 0005_entity_version_guard
migrate-postgres-1  | partition created: audit_log_y2026m09 … fetches_y2026m12 (8 рядків)
migrate-postgres-1  | roles applied to postgresql+asyncpg://collector:***@postgres:5432/collector from roles.sql: collector_migrate, collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro
migrate-postgres-1  | login enabled: collector_scheduler, collector_fetcher, collector_parser, collector_projector, collector_translation, collector_api_ro, collector_export_ro

$ RUNNER_TEMP=<scratch> bash cistep.sh     # дослівний `run:` кроку CI «Per-component LOGIN-ролі §13 …», витягнутий з ci.yml
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
exit=0
$ cat $RUNNER_TEMP/denied.txt
psql: error: connection to server at "postgres" (172.23.0.2), port 5432 failed: FATAL:  permission denied for database "postgres"
DETAIL:  User does not have CONNECT privilege.
(пошук паролів усіх 8 DSN у `docker compose logs` і `docker inspect` — збігів немає, інакше exit 1)

$ docker compose --profile core --profile workers up -d --wait --force-recreate   # повторний up
exit=0
0                                   # exit code migrate-postgres
migrated …: 0005_entity_version_guard -> 0005_entity_version_guard
roles applied … / login enabled: (ті самі сім)
sha256sum -c (8 DSN): OK            # секрети не змінились
ci-step after 2nd up exit=0

# adversarial на справжньому image: збій `db migrate` → `db roles` не стартує
$ docker compose run --rm --no-deps -e COLLECTOR_POSTGRES_DSN_FILE=/run/secrets/does_not_exist migrate-postgres
postgres config: COLLECTOR_POSTGRES_DSN_FILE='/run/secrets/does_not_exist': не вдалося прочитати файл ([Errno 2] No such file or directory: '/run/secrets/does_not_exist')
exit=1                              # рядка "login enabled" немає
# збій `db roles` (порожній каталог секретів) → exit code one-shot
$ docker compose run --rm --no-deps -e COLLECTOR_POSTGRES_ROLE_SECRETS_DIR=/tmp migrate-postgres
No new upgrade operations detected.
migrated …: 0005_entity_version_guard -> 0005_entity_version_guard
role logins: у /tmp бракує DSN-секретів: postgres_dsn_scheduler, postgres_dsn_fetcher, postgres_dsn_parser, postgres_dsn_projector, postgres_dsn_translation, postgres_dsn_api_ro, postgres_dsn_export_ro
exit=1

$ docker compose --profile core --profile workers down -v
… Volume collector_postgres-data Removed … Network collector_backend Removed
exit=0
```

Примітка: використано вже зібраний локально image `collector:dev` (`src/**` у PR4 не змінено,
`db roles --with-login` у ньому є — видно з логів).

Після додавання тестів:

```text
$ uv run pytest tests/unit/test_secrets_role_dsn_adversarial.py -q -rs
14 passed in 36.39s
$ uv run pytest tests/unit/test_secrets_role_dsn.py tests/unit/test_secrets_role_dsn_adversarial.py \
    tests/unit/test_compose_config.py tests/unit/test_compose_config_adversarial.py -q -p no:warnings -rs
131 passed in 46.10s
$ uv run ruff check / ruff format --check / mypy  tests/unit/test_secrets_role_dsn_adversarial.py
All checks passed! / 1 file already formatted / Success: no issues found in 1 source file
```

## Acceptance-пункт → тест → результат

| Acceptance / вимога | Тест(и) | Результат |
|---|---|---|
| В1: сім `postgres_dsn_<component>`, формат, власний пароль hex | `test_secrets_role_dsn.py::test_init_secrets_generates_eight_dsns_…`, `…::test_generated_role_dsns_are_accepted_by_db_roles_with_login`; **нові** `::test_all_passwords_pairwise_distinct_across_secrets_and_across_hosts`, `::test_dsn_user_equals_role_by_db_roles_mapping` | pass |
| В1: ідемпотентно, наявний файл не перезаписується | `…::test_init_secrets_is_idempotent_and_never_overwrites`; **нові** `::test_rerun_leaves_every_existing_secret_byte_identical`, `::test_rerun_keeps_operator_supplied_role_dsn_verbatim`, `::test_partial_role_dsn_set_is_completed_without_touching_present_files`; ручний другий прогін скрипту | pass |
| В1: `.example` без секрету | `…::test_role_dsn_example_is_a_placeholder_without_secret` ×7 | pass |
| В2: сім записів top-level `secrets:` дослівно після `postgres_dsn` | `…::test_every_role_dsn_secret_is_declared_verbatim_after_postgres_dsn` | pass |
| В3: `migrate && roles --with-login` без втрати exit code | `…::test_migrate_postgres_runs_roles_with_login_…` (статичний); **нові** `::test_migrate_failure_propagates_and_roles_is_not_run`, `::test_roles_failure_is_the_exit_code_of_the_one_shot`, `::test_migrate_then_roles_success_is_zero` (дослівна команда з compose у POSIX `sh` зі stub); ручні `docker compose run` зі збоєм кожної команди → exit 1 | pass |
| В3: монтує `postgres_dsn` + сім, `/run/secrets` | `…::test_migrate_postgres_mounts_exactly_migration_and_role_dsns`, `test_compose_config.py::test_postgres_dsn_secret_is_scoped_…` | pass |
| Жоден сервіс поза `migrate-postgres` не має всіх семи; DSN не в `environment` | `…::test_role_dsn_secrets_are_not_mounted_into_services_outside_the_plan`, `test_compose_config_adversarial.py` (SECRET_CONSUMERS); **нові** `::test_no_service_other_than_migrate_postgres_holds_all_role_dsns`, `::test_no_extension_anchor_distributes_role_dsns`, `::test_role_dsn_never_inlined_into_environment` | pass |
| В4: REVOKE PUBLIC у init, вартовий «немає GRANT на таблиці» | `test_compose_config.py::test_postgres_init_revokes_public_…`, `::test_postgres_init_scripts_are_mounted_read_only_for_wp_01a`; Docker: `temp:false`, порожній PUBLIC ACL | pass |
| `migrate-postgres` exit 0, сім LOGIN-ролей | Docker-прогін (exit 0, «login enabled: …» 7 ролей; CI-крок LOGIN roles) | pass |
| Повторний `up` ідемпотентний | Docker: другий `up --force-recreate` exit 0, one-shot exit 0, секрети незмінні | pass |
| Паролі не в `environment`/`docker inspect`/логах | CI-крок локально (grep паролів у logs/inspect), `…::test_role_dsn_never_inlined_into_environment` | pass |
| `collector_fetcher` → `collector` так, → `postgres` ні | CI-крок локально (`current_user = collector_fetcher`, `permission denied for database "postgres"`) | pass |
| В5: CI генерує секрети тим самим шляхом | `…::test_ci_docker_job_checks_role_logins_revoke_public_and_leaks`; `ci.yml:266 bash ./deploy/compose/secrets/init-secrets.sh` | pass (Linux runner не запускався — поза моїм середовищем) |
| Скрипт без CRLF, bash/POSIX | **нові** `::test_init_secrets_is_lf_in_index_and_worktree_and_pinned_by_gitattributes`, `::test_crlf_examples_do_not_leak_carriage_returns_into_secrets`, `::test_script_runs_in_bash_posix_mode` | pass |

## Рівень §16.1 → тести

| Рівень | Тести |
|---|---|
| 1 Unit | `tests/unit/test_secrets_role_dsn.py` (15 з параметризацією), `tests/unit/test_secrets_role_dsn_adversarial.py` (14, нові), оновлені `test_compose_config.py`, `test_compose_config_adversarial.py` |
| 14 Docker | локальний clean-host `up --wait` core+workers, дослівний CI-крок PR4, повторний `up`, `docker compose run` зі збоями, `down -v` (вище); у CI — job `docker` крок «Per-component LOGIN-ролі §13…» |

Skip-умови нових тестів: на не-Windows відсутність `bash`/`sh`/`git` — `assert` (провал), skip
лише на Windows без Git Bash. Мережі не використовують (subprocess + `tmp_path`).

## Додані тести

`tests/unit/test_secrets_role_dsn_adversarial.py` — 14 тестів (список у таблиці вище).

## Mutation-перевірка

| Мутація (тимчасова, повернена `git checkout -- <file>`) | Результат |
|---|---|
| `docker-compose.yml:350` `&&` → `;` | `test_migrate_failure_propagates_and_roles_is_not_run`: `assert 0 == 3` → `1 failed, 2 passed` |
| `init-secrets.sh:46` — перезапис наявних `postgres_dsn_*` (`[ -e "$target" ] && [ "${name#postgres_dsn_}" = "$name" ]`) | `FAILED test_rerun_leaves_every_existing_secret_byte_identical`, `test_rerun_keeps_operator_supplied_role_dsn_verbatim`, `test_partial_role_dsn_set_is_completed_…` → `3 failed, 11 passed` |
| `init-secrets.sh` — спільний пароль для всіх role DSN (`"$shared_pw"` замість `"$(random_hex)"`) | `FAILED test_partial_role_dsn_set_…`, `test_all_passwords_pairwise_distinct_…` → `2 failed, 12 passed` |

Після кожної мутації `git status` чистий (лише новий тест-файл).

## Звірка з implementation-pr4.md

Заявлене підтверджується: результати команд, 7 комітів, вивід `migrate-postgres`, CI-крок
(LOGIN roles, `temp:false`, відмова до `postgres`), повторний `up` — збігаються з моїм прогоном.
Розбіжність лише у формулюванні runbook (див. F-2).

## Знахідки

| ID | Severity | file:line | Опис |
|---|---|---|---|
| F-1 | medium | `deploy/compose/secrets/init-secrets.sh:46` | `[ -e "$target" ]` приймає **каталог** як «наявний секрет». Відтворено: на хості без `postgres_dsn_fetcher` (сценарій «хост до PR4», `git pull` без `init-secrets.sh`) `docker compose --profile core up -d migrate-postgres` на Docker Desktop **не** падає, а створює на місці файла порожній каталог `deploy/compose/secrets/postgres_dsn_fetcher/`; наступний `init-secrets.sh` друкує `skip  postgres_dsn_fetcher (exists)` і секрет так і не генерується — рецепт із runbook не допомагає, потрібен ручний `rmdir`. Клас проблеми був і до PR4, але PR4 додає сім нових файлів, які на оновлених хостах гарантовано відсутні. Пропозиція: `[ -f ]` + явна помилка, якщо `target` — каталог (або видалення порожнього каталогу). |
| F-2 | low | `docs/runbooks/clean-host-start.md:128` | Рядок «`migrate-postgres` `Exited (1)`, `у /run/secrets бракує DSN-секретів`» описує поведінку, коли файл відсутній у контейнері; з bind-mount file-secrets Compose (перевірено на Docker Desktop) замість цього з'являється каталог (F-1) і помилка інша. Варто додати пункт про `rmdir` каталогів-заглушок. |
| F-3 | info | `deploy/compose/postgres/init/02-revoke-public.sql` | Без `eol=lf` у `.gitattributes`: на Windows з `core.autocrlf=true` робоча копія CRLF (`git ls-files --eol`: `w/crlf`). psql це приймає — локальний `up` пройшов, REVOKE застосовано; ризику не бачу, фіксую для повноти. |
| F-4 | info | `deploy/compose/secrets/init-secrets.sh:1,19` | Скрипт — bash-only (`BASH_SOURCE`, `pipefail`), не POSIX `sh`: `sh init-secrets.sh` у dash упаде на `set -u`. Усі виклики (shebang, `ci.yml:266 bash …`, документація) — bash; `bash --posix` працює (тест). |

Flaky-поведінки не спостерігалось.

## Вердикт

**pass** — усі acceptance-пункти PR4 підтверджено unit- і Docker-прогоном; F-1 (medium)
стосується операційного відновлення на оновлених хостах і не блокує контракт картки, але
рекомендовано виправити в наступному PR (owner WP-00).
