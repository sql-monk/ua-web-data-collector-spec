# WP-00 PR5 — implementation (`wp/00-5-object-store-secrets`)

Дата: 2026-09-24. Worktree `.worktrees/wp-00-5`. Картка: `docs/plan/cards/WP-00.md`, розділ PR5.
Узгоджено з `docs/plan/deps/WP-01B-to-WP-00.md` (гілка `wp/01b-1-mongo-schema`, п.2).

## Що зроблено

1. **MinIO buckets і облікові дані за ролями (вимога 1).** Новий one-shot `ensure-minio`
   (profile `core`, `depends_on: minio: service_healthy`) працює на **тому самому** pinned image,
   що й сервер `minio`: vendor `mc` уже в ньому, тож окремий digest не потрібен. Контейнер
   запускається від uid 10001, з `read_only`, `cap_drop: ALL`, `no-new-privileges`, tmpfs `/tmp`;
   `./deploy/compose/minio` монтується read-only. Скрипт `deploy/compose/minio/ensure-minio.sh`
   ідемпотентно створює:
   - buckets `raw`, `normalized`, `archive`, `translated`, `events`;
   - policies `collector-<component>` з `deploy/compose/minio/policies/<component>.json`;
   - користувачів `collector-<component>`.

   Після attach скрипт перевіряє, що в користувача рівно одна власна policy; інакше exit 1.
   Секрети не потрапляють в argv. Root передається через `MC_HOST_collector` у середовищі
   процесу. Secret key користувача йде через stdin `mc admin user add`; `mc alias set` не
   використовується. Root MinIO монтують лише `minio` і `ensure-minio`.

   **Формат секрету** (зафіксовано в `deploy/compose/README.md`): один файл `minio_<component>`
   з двома рядками — `access_key=collector-<component>` і `secret_key=<40 hex>`. Довжина 40 —
   історичний ліміт MinIO на secret key.

   Policies відповідають таблиці картки. «Head» = `s3:GetObject`, бо окремої дії в S3 немає.
   MinIO повертає 404 на HEAD відсутнього ключа і без `ListBucket` (перевірено). Тому
   `ListBucket` є лише в maintenance.
2. **Mongo URI за компонентами (вимога 2).** `init-secrets.sh` генерує чотири секрети:
   `mongo_uri_projector`, `mongo_uri_compactor`, `mongo_uri_api_ro`, `mongo_uri_export_ro`.
   Формат:
   `mongodb://collector_<c>:<48 hex>@${MONGO_HOST:-mongo}:${MONGO_PORT:-27017}/${MONGO_DB:-collector}?replicaSet=rs0&authSource=admin`.
   - **Сумісність з WP-01B.** Формат приймає `collector.persistence.mongo.users.credential_from_uri`
     з гілки WP-01B: користувач `collector_<c>`, `authSource=admin`, непорожній пароль.
   - **Монтування.** `ensure-mongo` монтує root і чотири URI. Каталог `/run/secrets` — типовий
     `COLLECTOR_MONGO_USER_SECRETS_DIR`.
   - **БД.** `ensure-mongo` отримує `COLLECTOR_MONGO_DATABASE=${MONGO_DB:-collector}`. Це п.2
     deps WP-01B: ролі дають права саме на цю БД, тож вона має збігатися зі шляхом URI.
   - **Перемикач `COLLECTOR_ENSURE_MONGO_SCHEMA`.** `0` — лише replica set; `1` —
     `--validators --indexes --users`; будь-яке інше значення — exit 2 без запуску. Типово `0`,
     бо в `main` CLI ще не має `--users`.
   - **Вартовий.** `test_ensure_mongo_schema_switch_default_follows_cli_capability` читає опції
     CLI. Він падає, щойно з'явиться `--users`, якщо default лишився `0`. Хто зливається другим,
     ставить `1`.
3. **Секрет провайдера перекладу (вимога 3).** Для `google_translation_credentials` додано
   `.example` з позначкою `OPERATOR-SUPPLIED`.
   - `init-secrets.sh` значення не генерує. Він лише створює порожній файл, якщо файла немає, і
     замінює порожній каталог-заглушку Docker Desktop. Наявний файл не чіпає ніколи.
   - Секрет монтує лише `translation-worker`.
   - Env сервісу: `COLLECTOR_TRANSLATION_PROVIDER=${…:-disabled}`, `COLLECTOR_TRANSLATION_PROJECT`,
     `COLLECTOR_TRANSLATION_LOCATION` (типово `global`), `COLLECTOR_TRANSLATION_CREDENTIALS_FILE`.
   - Gitleaks працює без allowlist.
4. **Монтування (вимога 4)** — за таблицею картки. Мапи в
   `tests/unit/test_compose_config_adversarial.py`: `SECRET_CONSUMERS` і нова
   `RUNTIME_SECRET_FILES` (рівно ці `*_FILE` → рівно ці secrets).
   - `projector-worker` монтує два Mongo-секрети: `mongo_uri_projector`
     (`COLLECTOR_MONGO_URI_FILE`) і `mongo_uri_compactor` (`COLLECTOR_MONGO_COMPACTOR_URI_FILE`).
     Причина: за карткою compactor живе в тому ж процесі, але під окремим Mongo-користувачем.
   - Сервіс чекає на `ensure-mongo` і `ensure-minio` (`depends_on`, completed).
5. **`init-secrets.sh`.**
   - `new_hex` тепер приймає довжину (для MinIO — 20 байтів).
   - Патерн той самий, що після gate 3 PR4: значення генерується в основному shell, формат
     перевіряється до запису, запис атомарний через `write_secret`, паралельні запуски
     серіалізує lock.
   - Нове: невідомий `*.example` зупиняє скрипт **до будь-якого запису**. Раніше гілка `*)`
     мовчки копіювала будь-який новий приклад як «секрет».
   - `minio_root_user` копіюється з прикладу окремою явною гілкою.
6. **Docs.**
   - `deploy/compose/README.md` — мапа споживачів, формати MinIO/Mongo, ротація, перемикач,
     провайдер, змінні.
   - `docs/runbooks/clean-host-start.md` — секрети PR5, перевірка MinIO, типові проблеми.
   - Dependency-запит `docs/plan/deps/WP-00-to-WP-01D.md`.

Коміти: `f10cc33`, `fcc99db`, `9b00e9d`, `6c98840`, `51768a3` (+ коміт цього звіту).

### Acceptance → тест

| Пункт | Перевірка |
|---|---|
| кожен runtime-сервіс монтує рівно свої MinIO/Mongo-секрети | `test_compose_config_adversarial.py::test_runtime_services_mount_exactly_their_own_credentials`, `::test_each_secret_has_exactly_documented_consumers`; Docker: mounts з `docker inspect` (нижче) |
| root MinIO — лише `ensure-minio` (+ сервер), root Mongo — лише `ensure-mongo` (+ сервер) | `test_secrets_object_store.py::test_root_credentials_only_in_their_server_and_init_one_shot` |
| scheduler/fetcher/parser без Mongo | `::test_scheduler_fetcher_parser_have_no_mongo_credentials` (5 параметрів) |
| `google_translation_credentials` лише в `translation-worker` | `::test_provider_credential_only_in_translation_worker_with_disabled_default` |
| init-secrets: усі нові секрети, попарно різні паролі, наявні не перезаписуються, credential провайдера порожній | `::test_init_secrets_generates_minio_credentials_per_component`, `::…generates_mongo_uris_per_component`, `::…honours_mongo_host_port_db_overrides`, `::test_every_generated_password_is_unique_across_all_secrets`, `::test_init_secrets_is_idempotent_for_new_secrets`, `::…adds_only_pr5_secrets_on_pre_pr5_host`, `::…creates_empty_provider_credential_and_never_fills_it`, `::…keeps_operator_provider_credential_verbatim`, `::…replaces_docker_placeholder_dir_with_empty_provider_file`, `::…fails_loudly_when_generator_fails_for_minio_key`, `::…rejects_unknown_example_before_writing_anything` |
| `.example` без секретів | `::test_new_examples_are_placeholders_without_secrets` (11 параметрів) + gitleaks у pre-commit |
| policies відповідають таблиці картки | `::test_minio_policy_grants_exactly_the_card_table` (6), `::test_only_maintenance_and_projector_archive_may_delete` |
| `ensure-minio` ідемпотентний, секрети не в argv/виводі, fail-closed на зіпсованих секретах | stub-`mc`: `::test_ensure_minio_creates_buckets_users_and_policies_idempotently`, `::…never_puts_secrets_in_argv_or_output`, `::…fails_when_user_has_extra_policy`, `::…rejects_malformed_component_secret` (4), `::…fails_on_missing_root_secret`, `::…propagates_mc_failure`; Docker: повторний `up` |
| `ensure-mongo`: перемикач, вартовий, контракт WP-01B | `::test_ensure_mongo_switch_off_runs_replica_set_only`, `::…switch_on_adds_validators_indexes_users`, `::…switch_rejects_unknown_values` (3) — дослівна команда з compose у `sh` зі stub-`collector`; `::test_ensure_mongo_schema_switch_default_follows_cli_capability`; `::test_ensure_mongo_mounts_root_and_all_component_uris` |
| `minio_fetcher` не може Delete у `raw` і Put у `normalized`; `minio_maintenance` може Delete | Docker, 2026-09-24 (матриця нижче) |
| `mongo_uri_api_ro` не може insert | **not testable у цьому PR** (2026-09-24): Mongo-користувачів створює `--users` WP-01B PR1, у `main` його немає. На стеку `COLLECTOR_ENSURE_MONGO_SCHEMA=1` дає `No such option: --users` (нижче). Перевірка — `tests/integration/mongo` WP-01B і стек після merge обох PR |
| жоден пароль не потрапляє в `environment`/`docker inspect`/логи | `::test_new_credentials_never_inlined_into_environment`; Docker leak-check: 22 секрети проти `docker inspect` 17 контейнерів і `compose logs` — `leaks: none` |
| `ensure-minio`/`ensure-mongo` завершуються 0 і ідемпотентні при повторному `up` | Docker: `Exited (0)` після першого й повторного `up`, `RestartCount 0`, «ensure-minio: done» двічі |
| `docker compose config --quiet` | зелений (нижче) |

## Команди та вивід

### Статичні перевірки (після коміту `6c98840`; `51768a3` змінив compose/тест/доки — ruff і pre-commit повторено, зелені)

```text
$ uv sync --frozen
Checked 66 packages in 7ms
exit=0
$ uv run ruff check .
All checks passed!
exit=0
$ uv run ruff format --check .
272 files already formatted
exit=0
$ uv run mypy src
Success: no issues found in 78 source files
exit=0
$ docker compose config --quiet
exit=0
```

`uv run pre-commit run --all-files` (після `51768a3`):

```text
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
```

Перший прогін pre-commit знайшов MD004/MD032 у runbook (рядок, що починався з `+`) —
виправлено в `6c98840`.

### `uv run pytest -m "not live"` (один повний прогін, Windows-хост, після `6c98840`)

```text
FAILED tests/unit/workers/test_db_login.py::test_compose_mounts_the_dsn_of_the_role_the_process_verifies
1 failed, 1126 passed, 23 skipped, 9 warnings in 4559.77s (1:15:59)
EXIT=1
```

Skip-и: 20 — `tests/e2e/test_gui_runtime_contract.py` (gui на `127.0.0.1:80` не піднятий),
2 — `tests/e2e/test_runtime_suite_is_enforced.py` (лише з `COLLECTOR_E2E_REQUIRED=1`),
1 — `test_network_blocked.py:27` (Windows). Флейків scaling WP-01D у цьому прогоні не було.

**Єдиний провал — очікуваний наслідок PR5 у тесті, яким володіє WP-01D (forbidden для PR5).**
Тест вимагає `services[name]["secrets"] == [dsn]`, тобто «runtime-сервіс монтує лише DSN».
Картка PR5 навмисно додає сервісам MinIO/Mongo/provider-секрети. Запит на послаблення до
«рівно один `postgres_dsn*`» (least privilege тримає `RUNTIME_SECRET_FILES` WP-00) —
`docs/plan/deps/WP-00-to-WP-01D.md` п.2. Альтернатива для оркестратора — разовий виняток на
цей рядок.

Після `51768a3` повторено лише файли PR5 (повний прогін — один раз, за вказівкою):

```text
$ uv run pytest -q -p no:cacheprovider tests/unit/test_secrets_object_store.py tests/unit/test_compose_config.py tests/unit/test_compose_config_adversarial.py tests/unit/test_secrets_role_dsn.py tests/unit/test_secrets_role_dsn_adversarial.py
........................................................................ [ 75%]
...............................................                          [100%]
191 passed in 1530.80s (0:25:30)
```

### `init-secrets.sh` на чистому каталозі

```text
$ ./deploy/compose/secrets/init-secrets.sh
empty google_translation_credentials (порожній файл; credential вписує оператор — deploy/compose/README.md)
gen   minio_fetcher (random, користувач collector-fetcher)
gen   minio_maintenance (random, користувач collector-maintenance)
gen   minio_parser (random, користувач collector-parser)
gen   minio_projector (random, користувач collector-projector)
gen   minio_readonly (random, користувач collector-readonly)
gen   minio_root_password (random)
copy  minio_root_user (from example — non-secret)
gen   minio_translation (random, користувач collector-translation)
gen   mongo_keyfile (random keyfile)
gen   mongo_root_password (random)
gen   mongo_uri_api_ro (random, користувач collector_api_ro)
gen   mongo_uri_compactor (random, користувач collector_compactor)
gen   mongo_uri_export_ro (random, користувач collector_export_ro)
gen   mongo_uri_projector (random, користувач collector_projector)
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
```

### Docker: `up -d --wait` (один раз; tag `collector:wp005`, щоб не перезаписати чужий `collector:dev`)

```text
$ COLLECTOR_IMAGE=collector:wp005 docker compose --profile core --profile workers build
 Image collector:wp005 Built
 Image collector:wp005 Built
 Image collector:wp005 Built
 Image collector:wp005 Built
 Image collector:wp005 Built
exit=0
$ COLLECTOR_IMAGE=collector:wp005 docker compose --profile core --profile workers up -d --wait
 Container collector-mongo-1 Waiting
 Container collector-discovery-worker-1 Waiting
 Container collector-projector-worker-1 Waiting
 Container collector-scheduler-1 Waiting
 Container collector-minio-1 Waiting
 Container collector-export-worker-1 Waiting
 Container collector-maintenance-worker-1 Waiting
 Container collector-parse-worker-2 Waiting
 Container collector-parse-worker-1 Waiting
 Container collector-fetch-worker-2 Waiting
 Container collector-fetch-worker-1 Waiting
 Container collector-api-1 Waiting
 Container collector-ensure-mongo-1 Waiting
 Container collector-api-1 Healthy
 Container collector-ensure-mongo-1 Exited
 Container collector-maintenance-worker-1 Healthy
 Container collector-projector-worker-1 Healthy
 Container collector-migrate-postgres-1 Exited
 Container collector-mongo-1 Healthy
 Container collector-minio-1 Healthy
 Container collector-discovery-worker-1 Healthy
 Container collector-export-worker-1 Healthy
 Container collector-scheduler-1 Healthy
 Container collector-ensure-minio-1 Exited
 Container collector-postgres-1 Healthy
 Container collector-translation-worker-1 Healthy
 Container collector-parse-worker-2 Healthy
 Container collector-parse-worker-1 Healthy
 Container collector-fetch-worker-2 Healthy
 Container collector-fetch-worker-1 Healthy
exit=0
$ docker compose --profile core --profile workers ps -a --format "table {{.Service}}\t{{.Status}}"
SERVICE              STATUS
api                  Up 18 seconds (healthy)
discovery-worker     Up 43 seconds (healthy)
ensure-minio         Exited (0) About a minute ago
ensure-mongo         Exited (0) About a minute ago
export-worker        Up 22 seconds (healthy)
fetch-worker         Up 13 seconds (healthy)
fetch-worker         Up 28 seconds (healthy)
maintenance-worker   Up 37 seconds (healthy)
migrate-postgres     Exited (0) About a minute ago
minio                Up About a minute (healthy)
mongo                Up About a minute (healthy)
parse-worker         Up 47 seconds (healthy)
parse-worker         Up 53 seconds (healthy)
postgres             Up About a minute (healthy)
projector-worker     Up 56 seconds (healthy)
scheduler            Up 25 seconds (healthy)
translation-worker   Up 33 seconds (healthy)
```

### Docker: `ensure-minio` і матриця дозволів MinIO

Облікові дані читалися лише з `/run/secrets` у контейнері на образі `ensure-minio`. У curl вони
йшли через `--config -` зі stdin, тож в argv їх не було. Друкувалися лише HTTP-коди.

```text
$ docker compose logs ensure-minio
ensure-minio: bucket raw ok
ensure-minio: bucket normalized ok
ensure-minio: bucket archive ok
ensure-minio: bucket translated ok
ensure-minio: bucket events ok
ensure-minio: user collector-fetcher → policy collector-fetcher ok
ensure-minio: user collector-parser → policy collector-parser ok
ensure-minio: user collector-projector → policy collector-projector ok
ensure-minio: user collector-translation → policy collector-translation ok
ensure-minio: user collector-maintenance → policy collector-maintenance ok
ensure-minio: user collector-readonly → policy collector-readonly ok
ensure-minio: done
$ docker compose run --rm --no-deps -v <scratch>/chk:/chk:ro --entrypoint bash ensure-minio /chk/perm.sh
 Container collector-ensure-minio-run-1d6b2b6e8465 Creating
 Container collector-ensure-minio-run-1d6b2b6e8465 Created
fetcher      PUT raw/probe              -> 200 (очікувано 200)
fetcher      HEAD raw/probe             -> 200 (очікувано 200)
fetcher      HEAD raw/missing           -> 404 (очікувано 404)
fetcher      DELETE raw/probe           -> 403 (очікувано 403)
fetcher      PUT normalized/probe       -> 403 (очікувано 403)
fetcher      LIST raw                   -> 403 (очікувано 403)
parser       GET raw/probe              -> 200 (очікувано 200)
parser       PUT raw/probe2             -> 403 (очікувано 403)
parser       PUT normalized/probe       -> 200 (очікувано 200)
projector    PUT events/probe           -> 200 (очікувано 200)
projector    PUT archive/probe          -> 200 (очікувано 200)
projector    DELETE archive/probe       -> 204 (очікувано 204)
projector    DELETE normalized/probe    -> 403 (очікувано 403)
translation  PUT translated/probe       -> 200 (очікувано 200)
translation  PUT normalized/probe       -> 403 (очікувано 403)
readonly     GET raw/probe              -> 200 (очікувано 200)
readonly     PUT raw/probe3             -> 403 (очікувано 403)
maintenance  LIST raw                   -> 200 (очікувано 200)
maintenance  PUT raw/probe4             -> 403 (очікувано 403)
maintenance  DELETE archive/probe       -> 403 (очікувано 403)
maintenance  DELETE raw/probe           -> 204 (очікувано 204)
maintenance  DELETE normalized/probe    -> 204 (очікувано 204)
exit=0
```

### Docker: leak-check і фактичні mounts

Скрипт звіряє значення всіх згенерованих секретів з виводом `docker inspect` і
`docker compose logs`. Друкує лише імена.

```text
$ python leaks.py  # секрети з deploy/compose/secrets проти docker inspect + compose logs
containers=17 secrets_checked=22 inspect_bytes=246683 logs_bytes=369073
leaks: none
mounted secrets per service:
  api: minio_readonly, mongo_uri_api_ro | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_MONGO_URI_FILE
  discovery-worker: minio_fetcher, postgres_dsn_fetcher | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_POSTGRES_DSN_FILE
  ensure-minio: minio_fetcher, minio_maintenance, minio_parser, minio_projector, minio_readonly, minio_root_password, minio_root_user, minio_translation | env *_FILE: MINIO_ACCESS_KEY_FILE, MINIO_CONFIG_ENV_FILE, MINIO_KMS_SECRET_KEY_FILE, MINIO_ROOT_PASSWORD_FILE, MINIO_ROOT_USER_FILE, MINIO_SECRET_KEY_FILE
  ensure-mongo: mongo_root_password, mongo_uri_api_ro, mongo_uri_compactor, mongo_uri_export_ro, mongo_uri_projector | env *_FILE: COLLECTOR_MONGO_ROOT_PASSWORD_FILE
  export-worker: minio_readonly, mongo_uri_export_ro, postgres_dsn_scheduler | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_MONGO_URI_FILE, COLLECTOR_POSTGRES_DSN_FILE
  fetch-worker: minio_fetcher, postgres_dsn_fetcher | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_POSTGRES_DSN_FILE
  fetch-worker: minio_fetcher, postgres_dsn_fetcher | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_POSTGRES_DSN_FILE
  maintenance-worker: minio_maintenance, postgres_dsn_scheduler | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_POSTGRES_DSN_FILE
  migrate-postgres: postgres_dsn, postgres_dsn_api_ro, postgres_dsn_export_ro, postgres_dsn_fetcher, postgres_dsn_parser, postgres_dsn_projector, postgres_dsn_scheduler, postgres_dsn_translation | env *_FILE: COLLECTOR_POSTGRES_DSN_FILE
  minio: minio_root_password, minio_root_user | env *_FILE: MINIO_ACCESS_KEY_FILE, MINIO_CONFIG_ENV_FILE, MINIO_KMS_SECRET_KEY_FILE, MINIO_ROOT_PASSWORD_FILE, MINIO_ROOT_USER_FILE, MINIO_SECRET_KEY_FILE
  mongo: mongo_keyfile, mongo_root_password | env *_FILE: MONGO_INITDB_ROOT_PASSWORD_FILE
  parse-worker: minio_parser, postgres_dsn_parser | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_POSTGRES_DSN_FILE
  parse-worker: minio_parser, postgres_dsn_parser | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_POSTGRES_DSN_FILE
  postgres: postgres_password | env *_FILE: POSTGRES_PASSWORD_FILE
  projector-worker: minio_projector, mongo_uri_compactor, mongo_uri_projector, postgres_dsn_projector | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_MONGO_COMPACTOR_URI_FILE, COLLECTOR_MONGO_URI_FILE, COLLECTOR_POSTGRES_DSN_FILE
  scheduler: postgres_dsn_scheduler | env *_FILE: COLLECTOR_POSTGRES_DSN_FILE
  translation-worker: google_translation_credentials, minio_translation, postgres_dsn_translation | env *_FILE: COLLECTOR_MINIO_CREDENTIALS_FILE, COLLECTOR_POSTGRES_DSN_FILE, COLLECTOR_TRANSLATION_CREDENTIALS_FILE
exit=0
```

`MINIO_*_FILE` у `ensure-minio`/`minio` — змінні, вбудовані у vendor image (порожні, не наші).

### Docker: повторний `up` (ідемпотентність one-shot-ів)

```text
$ docker compose --profile core --profile workers up -d --wait   # повторний up
 Container collector-ensure-mongo-1 Starting
 Container collector-ensure-minio-1 Starting
 Container collector-migrate-postgres-1 Starting
 Container collector-ensure-mongo-1 Started
 Container collector-ensure-minio-1 Started
 Container collector-migrate-postgres-1 Started
 Container collector-ensure-minio-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-ensure-mongo-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-ensure-mongo-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-ensure-mongo-1 Waiting
 Container collector-ensure-mongo-1 Exited
 Container collector-ensure-mongo-1 Exited
 Container collector-ensure-mongo-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-ensure-minio-1 Exited
 Container collector-ensure-mongo-1 Waiting
 Container collector-ensure-minio-1 Waiting
 Container collector-migrate-postgres-1 Waiting
 Container collector-ensure-mongo-1 Exited
 Container collector-migrate-postgres-1 Exited
 Container collector-ensure-minio-1 Exited
exit=0
$ docker compose ps -a ensure-minio ensure-mongo migrate-postgres --format "{{.Service}} {{.Status}}"
ensure-minio Exited (0) 12 seconds ago
ensure-mongo Exited (0) 36 seconds ago
migrate-postgres Exited (0) 12 seconds ago
$ docker compose logs ensure-minio | grep -c "ensure-minio: done"
2
$ docker inspect -f "{{.State.ExitCode}} {{.RestartCount}}" collector-ensure-minio-1
0 0
```

### Docker: перемикач `ensure-mongo` на справжньому image

```text
$ COLLECTOR_ENSURE_MONGO_SCHEMA=1 docker compose run --rm --no-deps ensure-mongo
Usage: collector db ensure-mongo [OPTIONS]
Try 'collector db ensure-mongo --help' for help.
Error: No such option: --users
exit=2
$ COLLECTOR_ENSURE_MONGO_SCHEMA=yes docker compose run --rm --no-deps ensure-mongo
COLLECTOR_ENSURE_MONGO_SCHEMA має бути 0 або 1

exit=2
```

### Docker: `down -v`, видалення згенерованих секретів

```text
$ docker compose --profile core --profile workers down -v
 Volume collector_postgres-data Removed
 Network collector_frontend Removed
 Network collector_source_egress Removed
 Network collector_provider_egress Removing
 Network collector_backend Removed
 Network collector_provider_egress Removed
exit=0
$ git clean -fdX deploy/compose/secrets  # видалення згенерованих (ignored) секретів
24
exit=0
$ ls deploy/compose/secrets | grep -v -e .example -e init-secrets.sh | wc -l
0
```

Додатково прибрано: тимчасовий образ `collector:wp005`, scratch-контейнер `wp005-minio` і мережу
`wp005-scratch`. На них до compose-прогону відлагоджувався скрипт: `mc` idempotency, HEAD без
`ListBucket`, stdin для `user add`. Стек `puluj-g-*` не чіпався.

## Що не перевірено

- **`mongo_uri_api_ro` не може insert; реальні Mongo-користувачі.** Not testable до merge WP-01B
  PR1 (у `main` немає `--users`), дата 2026-09-24. Після merge обох PR:
  `COLLECTOR_ENSURE_MONGO_SCHEMA=1` (або default `1` за вартовим) і повторна перевірка на стеку.
- **Зміна `51768a3` на живому стеку не перевірялася.** Вона додала лише env
  `COLLECTOR_MONGO_DATABASE` для `ensure-mongo`, а поточний CLI її не читає. Compose-стек
  дозволено підняти один раз, і він уже був піднятий. Зміну покривають
  `docker compose config --quiet` і unit-тест.
- **CI job `docker`.** `ci.yml` для PR5 forbidden. Секрети в CI генерує той самий
  `init-secrets.sh`, тож нові файли з'являться там автоматично; `ensure-minio` входить у
  `--profile core`. Фактичний зелений прогін CI буде видно лише на PR.
- **Linux-запуск unit-тестів shell-скриптів.** Прогін був на Windows (Git Bash). Тести не
  пропускаються на POSIX: відсутність `bash`/`sh` там — провал, а не skip.

## Ризики

- **`depends_on: ensure-minio`** є лише в `projector-worker`. Для решти MinIO-споживачів і `api`
  картка забороняє змінювати `depends_on`, тому подано запит `WP-00-to-WP-01D.md` п.1. Поки
  runtime не ходить у MinIO з обліковими даними (до WP-02 PR2), це не проявляється.
- **Fetcher може перезаписати наявний об'єкт у `raw`** (`PutObject` без умови; в IAM немає
  «put-if-absent»). Ключі content-addressed, а WP-02 перевіряє sha256, тож перезапис дає той
  самий вміст. Object lock / versioning — поза PR5.
- **§13 «Raw bucket шифрується» і журнал видалень** не зроблено. Потрібен KMS/audit
  (WP-12/WP-13); access key = ім'я компонента, тож журнал MinIO показує, хто видаляв.
- **Maintenance отримує `GetObject`** (S3 авторизує HEAD як Get), тобто може читати вміст
  `raw`/`normalized`/`events`/`translated`.
- **Файли секретів мають права 0644** — прийняте відхилення ADR-0002. Це стосується і
  credential провайдера, коли оператор його впише.
- **Прийняте відхилення від таблиці §7.5:** у core-профілі з'явився one-shot `ensure-minio`,
  якого в таблиці немає. Тест профілів оновлено з коментарем.

## Як вимкнути або відкотити

- **Revert комітів PR5.** Схема БД не змінюється; нові файли секретів лишаються на диску і
  нікому не шкодять.
- **Користувачі та policies у MinIO** після revert лишаються. Прибрати вручну:
  `mc admin user remove`, `mc admin policy remove`. Bucket-и з даними не видаляються.
- **Mongo-схема і користувачі:** `COLLECTOR_ENSURE_MONGO_SCHEMA=0` (default).
- **Провайдер перекладу:** `COLLECTOR_TRANSLATION_PROVIDER=disabled` (default), порожній
  credential.

## Dependency-запити

- `docs/plan/deps/WP-00-to-WP-01D.md` (новий, open):
  - п.1 — `depends_on: ensure-minio` для MinIO-споживачів і `api`;
  - п.2 — послабити `test_db_login.py::test_compose_mounts_the_dsn_of_the_role_the_process_verifies`
    до «рівно один DSN-секрет». **Блокує зелений `pytest -m "not live"` цієї гілки.**
- `docs/plan/deps/WP-01B-to-WP-00.md` п.2 (гілка WP-01B) — виконано з боку PR5:
  - команда `collector db ensure-mongo --validators --indexes --users` за перемикачем;
  - чотири `mongo_uri_*` у `/run/secrets`, користувач `collector_<c>`, `authSource=admin`;
  - `COLLECTOR_MONGO_DATABASE` дорівнює шляху URI;
  - root лише в `ensure-mongo`, scheduler/fetch/discovery/browser/parse без Mongo.

  Хто зливається другим, перемикає default на `1` (вартовий змусить).
