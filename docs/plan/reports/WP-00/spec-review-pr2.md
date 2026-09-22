# WP-00 PR2 — пострев'ю за ТЗ (`wp/00-2-docker-compose`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR2 «Docker images + Compose profiles» |
| Branch / worktree | `wp/00-2-docker-compose` / `.worktrees/wp-00-2`, HEAD `eb280a6` |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR2» (вимоги 1–9, Acceptance, Docs) + «Спільні правила» |
| Розділи ТЗ | §1 п.8/10, §7.5, §7.6, §8, §13, §16.1 п.14, §16.2, §16.3, §17.2, §18, Додаток C; FR-030, FR-031, FR-035, FR-013 |
| REVIEW.md | R-51 (контейнерна поставка, clean-host start), R-55 (без Docker socket) |
| Q-питання §20 | Q-006, Q-013 (safe default + ADR-0002) |
| Вхідні звіти | `implementation-pr2.md`, `testing-pr2.md` (`pass`), `code-review-pr2.md` (`approve`, 0 critical/high), `security-pr2.md` (`approve`, 0 critical/high) |
| Рев'юер | wp-spec-reviewer, read-only; записи — лише цей файл і `docs/acceptance/traceability.md` |

## Вердикт

**`accept`** — `missing`: 0; `partial`: 11 (усі — або межі під-PR2, зафіксовані карткою/ADR, або датовані risk acceptance з owner, або три мої низькі знахідки нижче); `not applicable`: 12. Знахідок critical/high: 0. Усі знахідки gate 2 (11) і gate 3 (14 CR + 12 SEC) мають статус `fixed` / `accepted (owner, дата)` / `not applicable (аргумент)`; кожен «fixed» я перевірив у коді, а не лише у звіті (розділ 6).

Обсяг приймання: **лише PR2** рядка WP-00 у §17.2 — multi-stage image `collector`, Compose profiles/мережі/volumes/secrets, one-shots, health-стаб, CI-кроки Docker/SBOM/scan. `gui`, web locks і повний clean-host `core+workers+gui` — PR3; реальні migrations/validators — WP-01A/WP-01B; scale/drain/lease, origin rate limiter — WP-01D/WP-02. Ці елементи позначені `not applicable (PR3/інший WP)` і **не** закриваються цим вердиктом.

## Власна верифікація (Windows 11 + Docker Desktop 29.8 / Compose v5.5.1)

Усе виконано рев'юером у `.worktrees/wp-00-2`; стек піднято й знято, сторонній проєкт `puluj-g` на хості не чіпався.

| Команда | Результат |
|---|---|
| `uv run pytest -m "not live" -q` | **234 passed, 1 skipped** (Windows loopback/asyncio), exit 0 |
| `docker compose config --quiet` | exit 0 |
| `docker compose --profile core --profile workers --profile browser config --quiet` | exit 0 |
| `docker compose --profile workers config --quiet` | exit 1: `service "fetch-worker" depends on undefined service "migrate-postgres"` — відхилення підтверджено як задокументоване |
| `COMPOSE_PROFILES=core,workers docker compose up -d --wait` | exit 0, **26.6 с**, 16 контейнерів (14 healthy + 2 one-shot Exited 0) |
| `docker compose ps -a --format json \| python deploy/compose/check-healthy.py` | `all 16 containers healthy or exited 0`, exit 0 |
| `docker compose exec api python -m collector.api.health` | `postgres: ok (tcp reachable…)`, `mongo: ok (writable primary of replica set)`, `minio: ok (liveness HTTP 200)`, exit 0 |
| Маркери + `docker compose restart` | `postgres` `spec_review_marker → rev-marker`, MinIO `revmarker/m.txt → rev-marker` — збережені після restart; health знову `ok` |
| `up -d --no-recreate --scale fetch-worker=4 --wait` → `=1` | exit 0 / exit 0; 4 × healthy → 1 × healthy, решта сервісів не пересоздавалась |
| `docker inspect` усіх 16 контейнерів | `PortBindings={}` у всіх; `Binds` — лише named volumes stateful; **жодного `docker.sock`**; app: `RO=true User=10001:10001 Pids=256`; `postgres` `User=postgres`, `mongo` `999:999`, `minio` `RO=true User=""` (root — прийняте відхилення), stateful `Pids=1024` |
| `docker history \| grep -icE "password\|secret\|token\|api_key"` | `0`; `docker run --rm collector:dev env \| grep -ic` → `0`; `/run/secrets` в image відсутній; `id` → `uid=10001` |
| `COLLECTOR_IMAGE=collector:specrev COLLECTOR_GIT_SHA=$(git rev-parse HEAD) docker compose build` | Built; `collector version` → `git_sha=eb280a6…`; labels `revision=eb280a6…`, `ua.collector.schema-version=0.0.0-placeholder`; `Config.User=10001:10001`; `HEALTHCHECK=["CMD","collector","version"]` (тимчасовий tag видалено) |
| `docker compose down -v` | exit 0; `collector_*` volumes → 0; контейнерів `collector-*` → 0 |
| `git status --short` | порожньо; `git ls-files deploy/compose/secrets/` — лише `*.example` + `init-secrets.sh` |

## 1. Acceptance criteria

### 1.1. Acceptance картки PR2 (5 пунктів)

| Вимога | Доказ | Статус |
|---|---|---|
| чистий host піднімає `core`+`workers` однією командою; всі сервіси `healthy`; workers живі й не падають | моя верифікація: `up -d --wait` exit 0 за 26.6 с, `check-healthy.py` → 16/16; реалізатор: 10 циклів `down -v → up --wait` 10/10 exit 0 (`implementation-pr2.md`, «Виправлення після gate 2») + 3/3 після gate 3; workers — placeholder-процеси, `collector-*-worker` healthy, `RestartCount=0` | evidenced |
| restart не втрачає дані named volumes (маркерний запис у postgres/minio) | моя верифікація: маркер у `postgres.spec_review_marker` і `minio://revmarker/m.txt` читаються після `docker compose restart`; `testing-pr2.md` §4 — незалежне підтвердження | evidenced |
| `docker compose config` без `container_name` для workers, без Docker socket, без public port крім `gui`/dev-override | `docker-compose.yml:71-93` (x-worker), `tests/unit/test_compose_config.py::test_no_container_name_anywhere`, `::test_no_docker_socket_mount_anywhere`, `::test_base_compose_publishes_no_ports`, `::test_dev_override_binds_only_loopback`; `tests/integration/test_compose_render.py::test_no_docker_socket_and_no_published_ports`; мій `docker inspect` — `PortBindings={}` у 16/16 | evidenced |
| images non-root (`docker inspect` → `Config.User`), read-only rootfs у compose | мій build з HEAD: `User=10001:10001`; `read_only: true` + tmpfs у `x-collector-runtime` (`docker-compose.yml:50-56`) і `minio` (`:237-239`); runtime `RO=true` у 13 app-контейнерах і minio; CI-крок `docker build` має `grep -q 'User=10001:10001'` (`.github/workflows/ci.yml:135`) | evidenced |
| жоден secret не потрапив в image (`docker history`, `docker run --rm collector env`) | моя верифікація: 0 збігів у `history` і `env`, `/run/secrets` в image немає; `tests/unit/test_compose_config.py::test_dockerfile_is_multistage_pinned_non_root_without_secrets`, `::test_services_reference_only_declared_secrets_and_no_secret_env` | evidenced |

### 1.2. Вимоги картки PR2 (1–9, кожен підпункт — окремий рядок)

| # | Вимога | Доказ | Статус |
|---|---|---|---|
| 1a | multi-stage image (uv builder → runtime `python:3.13-slim@sha256:…`) | `Dockerfile:13-42` (`ARG PYTHON_IMAGE=…@sha256:8d9d0b8b…`, `UV_IMAGE=…@sha256:b485bd65…`), `tests/unit/test_compose_config_adversarial.py::test_dockerfile_runtime_stage_has_no_uv_and_no_pip` | evidenced |
| 1b | non-root user | `Dockerfile:63-65,90` (`USER 10001:10001`); `docker inspect` → `10001:10001` | evidenced |
| 1c | read-only rootfs + tmpfs `/tmp` | `docker-compose.yml:52-54`; runtime `ReadonlyRootfs=true`; `TMPDIR/HOME=/tmp` у `Dockerfile:86-87` | evidenced |
| 1d | OCI labels (Git SHA, schema version) | `Dockerfile:50-57`; мій inspect: `revision=eb280a6…`, `ua.collector.schema-version=0.0.0-placeholder` (placeholder до WP-01C) | evidenced |
| 1e | `HEALTHCHECK` в image | `Dockerfile:94-95`; inspect → `["CMD","collector","version"]` | evidenced |
| 1f | browser image не тут (WP-02 PR3) | `docker-compose.yml:513-526` — TODO, `replicas: 0`, тимчасово image `collector` | evidenced |
| 2a | profiles `core`, `workers`, `browser`, `gui`, `observability`, `tools` | `docker-compose.yml:3-9,141,275,291,315,362,518` + коментарі-резерв `:528-531`; `tests/unit/test_compose_config.py::test_services_match_spec_7_5_profile_table`, `::test_reserved_profiles_are_documented`. `gui`/`observability`/`tools` без сервісів — Compose не має декларації профілю без сервісу (ADR-0002) | partial (реалізовані `core`/`workers`/`browser`; `gui` — PR3, решта — WP-12/WP-11A/WP-14) |
| 2b | сервіси за таблицею §7.5 | `test_services_match_spec_7_5_profile_table` звіряє множини сервісів на профіль з таблицею §7.5 | evidenced |
| 2c | workers без `container_name`, host ports, local persistent state | `x-worker` (`:71-93`), `test_worker_is_scalable` (параметризовано на 8 ролей) | evidenced |
| 2d | `deploy.resources.limits` для всіх | `:98-131`, застосовано до 15 сервісів; `test_every_service_has_resource_limits`, `test_every_service_has_pids_limit_and_log_rotation`; runtime `Pids=256/1024` | evidenced |
| 2e | `stop_grace_period` ≥ 90 с для workers | `:77` (`120s`); `test_worker_is_scalable` (`>= 90`); runtime `StopTimeout=120` | evidenced |
| 3a | мережі `ingress`, `backend`, `source-egress`, `provider-egress`, `telemetry` | `:538-550`; `test_networks_match_spec_7_5` | evidenced |
| 3b | discovery/fetch/browser у `source-egress`, translation у `provider-egress` | `:392-394,402-404,468-470,520-522`; `test_service_network_placement` | evidenced |
| 3c | api — `backend` + `ingress` | `:329-331` | evidenced |
| 3d | gui бачить лише api | сервісу `gui` ще немає (PR3); рядок про internal-мережу gui↔api додано до картки PR3 | not applicable (PR3) |
| 4a | `postgres` PostgreSQL 18, pinned digest | `:140`; `test_stateful_image_pinned_by_digest_and_named_volumes`, `test_stateful_versions_match_spec_8` | evidenced |
| 4b | `mongo` 8.0 single-member RS, pinned digest, keyfile/секрети | `:175-204` (`--replSet rs0 --keyFile`, keyfile у tmpfs `install -m 0400`); RS реально ініціалізовано (`ensure-mongo` у моєму прогоні, health → `writable primary`) | evidenced |
| 4c | `minio` pinned digest | `:233` (`quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493…`) | evidenced |
| 4d | named volumes | `:553-557`; `test_named_volumes_only_for_stateful`; `docker inspect` — Binds лише у stateful | evidenced |
| 4e | ports не публікуються; `127.0.0.1` лише через `dev.override.yml` | base без `ports` (перевірено рендером і runtime), `deploy/compose/dev.override.yml:9-28` — усе на `127.0.0.1`; `test_dev_override_only_adds_loopback_ports_and_environment` | evidenced |
| 5a | one-shot `migrate-postgres` = `collector db migrate`, exit 0 з «no migrations yet; owner WP-01A» | `src/collector/cli.py:249-266`, `docker-compose.yml:273-285`; мій прогін — `Exited (0)`; `tests/unit/test_cli_compose_commands.py::test_db_migrate_exits_0_with_owner_message_when_postgres_reachable`, `::test_db_migrate_exits_1_when_postgres_unreachable` | evidenced |
| 5b | `ensure-mongo` ініціалізує single-member RS ідемпотентно; validators/indexes — стаб WP-01B | `cli.py:135-246` (код 94 → `replSetInitiate`, код 23 → idempotent, очікування `isWritablePrimary`); `test_ensure_replica_set_*` (5) + `test_gate3_fixes.py::test_ensure_replica_set_tolerates_already_initialized_race`; `--validators` → exit 2 (`test_db_ensure_mongo_validators_indexes_are_stub_after_init`) | evidenced |
| 5c | `api` — FastAPI-стаб лише з `GET /api/v1/health/components`, readiness `false` до one-shots | `src/collector/api/health.py:221-246` (200/503, `openapi_url=None`); readiness через `depends_on: service_completed_successfully` (`docker-compose.yml:339-342`); `test_readiness_waits_for_one_shots`, `tests/unit/test_health.py` (13) | evidenced |
| 6a | secrets через Docker secrets/files, `*.example` у git | `docker-compose.yml:560-569`, `deploy/compose/secrets/*.example`; `test_secrets_are_files_with_examples_and_gitignored` | evidenced |
| 6b | реальні файли у `.gitignore` | `.gitignore` (+5 рядків), `git ls-files` — лише `*.example` і `init-secrets.sh` | evidenced |
| 6c | жодного секрету в image layer або `ARG` | мої `docker history`/`env` — 0 збігів; у `environment` лише `*_FILE` (`test_services_reference_only_declared_secrets_and_no_secret_env`) | evidenced |
| 7a | healthcheck кожного сервісу = process + критична dependency | `docker-compose.yml:88-93,163-168,207-225,261-266,343-355,375-380,412-417,433-438,455-460,489-494`; `tests/unit/test_compose_config_adversarial.py::test_application_healthchecks_name_a_critical_dependency`, `::test_projector_export_check_mongo_fetch_parse_export_check_minio` | evidenced |
| 7b | `scheduler` — singleton, placeholder, що тримає процес живим і логує нереалізовану lease-логіку | `:358-383` (`replicas: 1`, `collector.scalable=false`), `cli.py:330-333` + `placeholder_process`; `test_scheduler_is_singleton`, `test_scheduler_command_is_placeholder` | evidenced |
| 8 | документована команда clean-host start (`gui` може бути відсутнім у PR2) | `docs/runbooks/clean-host-start.md`, `docker-compose.yml:11-16`; моя перевірка `up -d --wait` exit 0 для `core+workers` | evidenced (для `core`+`workers`, як дозволяє картка) |
| 9a | CI `docker compose config --quiet` | `.github/workflows/ci.yml:123-127` (двічі: схема і всі profiles) | evidenced (конфіг), operationally unverified (job не виконувався) |
| 9b | CI `docker build` image `collector` | `ci.yml:129-135` + assert `Config.User` | evidenced (конфіг), operationally unverified |
| 9c | SBOM (`syft`/`docker sbom`) | `ci.yml:137-144` (`anchore/sbom-action@v0.24.2`, SPDX artifact); локально — `docker scout sbom` (149 пакетів) | partial (CI-крок не виконувався; локальний інструмент інший) |
| 9d | vulnerability scan (`trivy`) | `ci.yml:148-166` — CRITICAL без `ignore-unfixed` (блокує) + HIGH з `ignore-unfixed`; `test_ci_trivy_critical_without_ignore_unfixed_and_high_with` | partial (CI-крок не виконувався; локально Docker Scout: 0 CRITICAL, 2 unfixed HIGH з датованим acceptance) |

### 1.3. §7.5 — таблиця profiles і список вимог (кожен bullet окремим рядком)

| Пункт §7.5 | Доказ | Статус |
|---|---|---|
| Таблиця profiles: `core` = postgres, mongo, minio, migrate-postgres, ensure-mongo, api, scheduler | `test_services_match_spec_7_5_profile_table`; рендер `--profile core config --services` (звіт реалізатора) | evidenced |
| Таблиця profiles: `workers` = 7 ролей | ті самі тести; 7 сервісів `*-worker` у profile `workers` | evidenced |
| Таблиця profiles: `browser` = browser-worker, 0/1 replica | `:513-526`, `replicas: 0` | evidenced |
| Таблиця profiles: `gui` | сервіс — PR3; профіль зарезервовано коментарем | not applicable (PR3) |
| Таблиця profiles: `observability` (otel, prometheus, grafana, loki) | зарезервовано коментарем; сервіси — WP-12 | not applicable (WP-12) |
| Таблиця profiles: `tools` (admin, release verifier, DuckDB) | зарезервовано коментарем; сервіси — WP-11A/WP-11B/WP-14 | not applicable (WP-11A/WP-14) |
| Один pinned image `collector` для API/scheduler/controller/CLI/worker roles через різні commands | один `Dockerfile`, 12 app-сервісів з `x-collector-image` і різними `command` | evidenced |
| Browser worker — окремий image із pinned Playwright | TODO у compose; owner WP-02 PR3 | not applicable (WP-02 PR3) |
| GUI multi-stage image, non-root Nginx | PR3 | not applicable (PR3) |
| Vendor images pinned digest (PostgreSQL, MongoDB, MinIO, telemetry) | `:140,175,233` — усі три з `@sha256:`; telemetry — WP-12 | evidenced (3 з 3 наявних) |
| worker services без `container_name`/host ports/local persistent state; `worker_instance_id` на boot | `x-worker`; `worker_instance_id` — WP-01D | evidenced (Compose-частина); not applicable (`worker_instance_id` — WP-01D) |
| назовні публікується тільки GUI ingress; DB/object/telemetry не bind-яться на public host interface | base не публікує нічого; override — лише `127.0.0.1`; GUI ingress — PR3 | evidenced (частина «нічого не публікується»); partial (GUI ingress — PR3) |
| named volumes лише stateful; application images read-only, non-root, tmpfs | `test_named_volumes_only_for_stateful`, `test_application_services_are_read_only_non_root_with_tmpfs`; runtime inspect | evidenced |
| окремі мережі ingress/backend/source-egress/provider-egress/telemetry; discovery/fetch/browser — source egress, translation — provider egress | `:538-550`, `test_service_network_placement` | evidenced |
| API — лише OIDC egress; GUI бачить тільки API | `api` у `ingress` (не internal) → необмежений egress; прийнято з owner/датою (gate 3 CR-14/SEC L-2, ADR-0002 «Прийняті знахідки gate 3», рядок у картці PR3) | partial (accepted, owner WP-00 PR3 / WP-11A, 2026-09-22) |
| secrets через Docker secrets/files, не ARG/layer/committed `.env` | `:560-569`; `docker history`/`env` чисті; `.gitignore` | evidenced |
| healthcheck = process + критична dependency; readiness false до migrations/validators | healthchecks усіх сервісів + `depends_on: service_completed_successfully`; health 503 до готовності (`test_health*`) | evidenced (для PR2-обсягу; перевірка версії схеми/validators — WP-01A/WP-01B) |
| scheduler/controller мають singleton advisory lease | `scheduler` — `replicas: 1` + placeholder; сам lease — WP-01D (картка вимога 7 це прямо фіксує) | partial (singleton є; lease — WP-01D) |
| `stop_grace_period` довший за bounded task shutdown; SIGTERM → drain | `120s` у workers, `90s` scheduler; `placeholder_process` ставить handler і виходить 0 по SIGTERM (`cli.py:108-132`); лог `placeholder.stopped` у звіті реалізатора | evidenced |
| images: immutable tag + digest, OCI labels, SBOM, vulnerability scan | labels ✓, SBOM/scan — CI-кроки (unverified); **app image має лише mutable tag** `collector:dev`/`collector:ci` без публікації digest (rollback через `COLLECTOR_IMAGE=<img>@sha256:…` лише задокументовано) | partial (знахідка F-3) |
| stateful services не масштабуються worker controls; topology — окремий runbook/ADR | label `collector.scalable` лише у workers (`true`) і scheduler (`false`); `ensure-mongo` відмовляє при іншій назві RS із посиланням на runbook/ADR (`cli.py:168-172`) | evidenced |
| команди зміни replicas (`up --scale`, `scale`) | мій прогін `--scale fetch-worker=4 → 1` — обидва exit 0, healthy; runbook «Масштабування» | evidenced |
| Docker socket у web/API не монтується | 16/16 контейнерів без `docker.sock`; `test_no_docker_socket_mount_anywhere` + integration-рендер | evidenced |
| Swarm replicated services, controller на manager node | WP-01D (Q-013) | not applicable (WP-01D) |

### 1.4. §7.6 — default replicas × concurrency vs compose

| Роль | §7.6 | compose | Статус |
|---|---|---|---|
| discovery | 1 × 4 | `replicas: 1` | evidenced (replicas) |
| fetch | 2 × 8 | `replicas: 2` | evidenced |
| browser | 0 × 1 | `replicas: 0`, profile `browser` | evidenced |
| parse | 2 × CPU count | `replicas: 2`, `cpus: "2.00"` | evidenced |
| projector | 1 × 8 | `replicas: 1` | evidenced |
| translation | 1 × 4 | `replicas: 1` | evidenced |
| export | 1 × 2 | `replicas: 1` | evidenced |
| maintenance | 1 × 1 | `replicas: 1` | evidenced |
| `desired_concurrency`, `worker_pools`/`worker_instances`/`scale_commands`, drain barrier, origin limiter | — | у PR2 відсутні свідомо | not applicable (WP-01D/WP-01A) |
| parse без network egress | «без network egress» | `parse-worker` лише `backend` (internal) — перевірено рендером і `test_service_network_placement` | evidenced |

Звірка тестом: `tests/unit/test_compose_config.py:44-53` (`SPEC_7_6_REPLICAS`) + `test_worker_is_scalable`.

### 1.5. §13 (контейнери, secrets, socket, ролі БД)

| Пункт §13 | Доказ | Статус |
|---|---|---|
| Контейнери non-root | app 10001, postgres `postgres`, mongo 999; **minio root** — прийняте відхилення vendor image з `cap_drop: ALL`, `no-new-privileges`, `read_only` (ADR-0002, owner WP-01D, 2026-09-22) | partial (accepted, dated) |
| read-only root filesystem «де можливо» | усі 12 app-сервісів + minio `read_only: true`; postgres/mongo — ні (vendor data dirs) | evidenced |
| окремі network policies й egress allowlist у production | `backend`/`telemetry` internal; allowlist egress — production (SEC I-5, owner WP-12/WP-01D) | partial (accepted, production) |
| Secrets скануються в pre-commit/CI | `.pre-commit-config.yaml` (gitleaks) + job `secrets` (`ci.yml:92-106`); `git status` чистий, реальні секрети не трекаються | evidenced |
| logs приховують Authorization/Cookie/API keys | `src/collector/core/logging.py` (`redact_secrets`, PR1); у PR2 секрет читається через `env_or_file` і не логується (`test_cli_compose_commands.py` — секрет з `*_FILE` не потрапляє в лог) | evidenced |
| Raw bucket шифрується; writer/parser/auditor roles | WP-02/WP-13 | not applicable |
| Облікові дані БД розділені за компонентами; migration role не в runtime | ролі — WP-01A/WP-01B; у PR2 Mongo root-credentials має лише `mongo` і `ensure-mongo` (SEC I-4), health працює без credentials | not applicable (WP-01A/WP-01B); підтверджено відсутність надлишкових credentials у PR2 |
| Operator API OIDC/RBAC/audit | WP-11A | not applicable |
| GUI same-origin BFF, CSRF, CSP | PR3/WP-11C | not applicable |
| GUI/API/worker images не отримують Docker socket | 16/16 без `docker.sock` (мій inspect), unit+integration тести | evidenced |
| Dependency та image scanning — **щотижня** і на кожен PR | на кожен PR — є (trivy + gitleaks + SBOM); **щотижневого розкладу (`schedule: cron`) у `.github/workflows/` немає** | partial (знахідка F-2) |
| critical CVE блокує release або має датоване risk acceptance | trivy CRITICAL без `ignore-unfixed`, `exit-code: 1`; 2 unfixed HIGH з датованим acceptance (CVE-2026-82560 perl, CVE-2026-85091 zlib; owner WP-13, 2026-09-22, тригери: оновлення digest, злиття WP-02, дедлайн 2026-12-22) | evidenced |

### 1.6. §16.2 (Docker-команди) і §16.3 (дотичні пункти)

| Команда/пункт | Доказ | Статус |
|---|---|---|
| `docker compose config --quiet` | мій прогін exit 0 (і з усіма profiles) | evidenced |
| `docker compose build --pull` | runbook крок 3; звіт реалізатора (`Built`, exit 0); мій `docker compose build` з HEAD — Built, image відповідає HEAD SHA | evidenced |
| `docker compose --profile core --profile workers --profile gui up -d --wait` | виконано без `gui` (PR3, дозволено карткою): exit 0, 26.6 с | partial (`gui` — PR3) |
| `docker compose up -d --no-recreate --scale fetch-worker=4 --scale parse-worker=2` | мій прогін `--scale fetch-worker=4` → 4 healthy, потім `=1` → 1 healthy; звіт реалізатора — той самий сценарій із `parse-worker` | evidenced |
| §16.3 «чистий Docker host підіймає core/workers/gui однією documented командою; migrations/validators завершуються до readiness; restart не втрачає named-volume data» | clean-host — вище; readiness — `depends_on: service_completed_successfully` (api/workers стартують після one-shots); restart+маркери — вище | partial (`gui` — PR3; реальні migrations/validators — WP-01A/WP-01B) |
| §16.3 «scale fetch 1→4→1 … без дублів, сумарний origin rate не перевищує source policy» | replica-частина перевірена (4 → 1, усі healthy); origin rate — WP-01D/WP-02 | partial (за межами PR2 — обмеження джерела) |
| §16.3 «GUI/API не мають Docker socket» | `docker inspect` 16/16; тести | evidenced |
| §16.3 «Compose mode повертає audited scale command» | WP-01D | not applicable |

### 1.7. §1 п.8/10, §17.2, FR

| Пункт | Доказ | Статус |
|---|---|---|
| §1 п.8 — Compose на одному Linux-хості (PostgreSQL, Mongo RS, S3-compatible) | `docker-compose.yml` core profile; ADR-0002 фіксує рішення | evidenced |
| §1 п.10 — усі application-компоненти постачаються OCI images; Docker Engine/OIDC/translation provider — інфраструктура, не контейнери | один image `collector` для api/scheduler/workers/CLI; жодного сервісу під OIDC/translation provider у compose | evidenced (для наявних компонентів; GUI — PR3, observability — WP-12) |
| §17.2 WP-00: «multi-stage images, Compose profiles/networks/volumes/secrets, migrations, CI, SBOM; clean-host stack smoke green» | images/profiles/networks/volumes/secrets/CI — вище; migrations — стаб (owner WP-01A); SBOM — CI-крок unverified; clean-host smoke — зелений для core+workers | partial (межі PR2: `gui`/web locks — PR3, реальні migrations — WP-01A, SBOM — unverified) |
| FR-030 — versioned OCI images, health/readiness, resource limits, non-root, Compose definition, state лише у named volumes/external stores | labels+версія у `collector version`; health/readiness; limits для 15/15; non-root app; workers без volumes; state лише у 4 named volumes | evidenced |
| FR-031 — stateless role-based pools, масштабуються незалежно без зміни image | один image + `command`; `--scale fetch-worker=4→1` без rebuild; workers без volumes/container_name | evidenced (placeholder-рівень: черга/lease — WP-01D) |
| FR-035 — GUI не має Docker socket/DB credentials; у Compose replicas змінюються CLI | socket відсутній усюди; GUI ще немає (PR3); replicas — CLI `--scale` | evidenced (частина «socket»); not applicable (GUI — PR3) |
| FR-013 — secrets лише з environment/secret store, не в logs/raw/fixtures | Docker secrets/files, `env_or_file`, gitleaks, `history`/`env` чисті | evidenced |

## 2. DoD §18

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| 1 | реалізація відповідає одному issue/WP, без сторонніх змін | diff — 39 файлів, усі в межах PR2 (Docker/Compose/health/CLI/CI/тести/звіти); `docs/research/**`, `TECHNICAL_SPECIFICATION.md`, `REVIEW.md` не змінені (перевірено `git diff --stat`) | evidenced (з процесною заувагою F-4: частина файлів поза списком «Owned files» PR2, але необхідна для вимог) |
| 2 | formatter, lint, types, unit/contract/integration tests пройшли | мій прогін: `pytest -m "not live"` → 234 passed, 1 skipped; реалізатор: ruff check/format, `mypy src` і `mypy tests` — clean, pre-commit 11 hooks | evidenced |
| 3 | зміна схеми має migration і compatibility evidence | схем БД у PR2 немає (migrate — стаб) | not applicable |
| 4 | timestamp/matching/release contract evidence | не зачіпалися | not applicable |
| 5 | новий адаптер: manifest/fixtures/golden/coverage | адаптерів немає | not applicable |
| 6 | документація, метрики й runbook оновлені | ADR-0002, `docs/runbooks/clean-host-start.md`, `deploy/compose/README.md`; кореневий `README.md` не оновлено (етап docs, не owned у PR2); метрики — WP-12 | partial (етап 5 docs попереду; метрики — WP-12) |
| 7 | secret scan чистий; контакти не в logs/fixtures | gitleaks у pre-commit і CI job `secrets`; реальні секрети gitignored і не трекаються; `*.example` більше не містять паролів (`GENERATED:…`) | evidenced |
| 8 | findings рев'юерів — `fixed` / `accepted with owner/date` / `not applicable` з аргументом | розділ 6: gate 2 — 8 fixed / 2 accepted (owner+дата) / 1 info accepted; gate 3 — 11 CR fixed / 2 accepted (owner+дата) / 1 SEC medium fixed, 4 low fixed, 1 accepted, 5 info accepted/n-a. Розбіжностей не знайдено | evidenced |
| 9 | PR злитий тільки після CI та required review; SHA зафіксовані | SHA у звітах є; job `docker` на GitHub Actions ще не виконувався (push заборонений) — merge-gate попереду | partial (очікувано до merge) |

## 3. Додаток C — рядки, які покриває PR2

| Ціль | Що покрито | Статус |
|---|---|---|
| Docker і масштабування (FR-030—FR-033, §7.5—§7.6): clean-host start, profiles, volume restart tests | clean-host `core+workers` зелений; profiles/мережі/volumes/secrets за §7.5; restart зберігає дані; replica/scale 4→1 | evidenced (у межах PR2) |
| Docker і масштабування: replica/drain/kill, global rate-limit | drain barrier, lease recovery, kill-replica і origin limiter — WP-01D/WP-02 | not applicable (PR2) |
| Технічна безпека (FR-013, §13): secret/container scans | Docker secrets/files, non-root/read-only/cap_drop/pids, gitleaks, trivy/SBOM у CI (unverified), без Docker socket | partial (сканери CI не виконувались; щотижневий розклад відсутній) |
| Незалежна реалізація (§17, §18): WP acceptance, CI | CI job `docker` додано; acceptance PR2 закрито; owner-межі (WP-01A/B/D, WP-11A, WP-02, WP-12) зафіксовані в коді коментарями й ADR | evidenced |
| Operator GUI (FR-034—FR-037) | PR3/WP-11C | not applicable |

## 4. Регресія REVIEW.md

| R | Суть | Перевірка в коді/тестах (не лише в ТЗ) | Статус |
|---|---|---|---|
| R-51 (високий) | контейнерна поставка всіх application-компонентів і відтворюваний clean-host start | `Dockerfile` (один image для api/scheduler/workers/CLI/one-shots), `docker-compose.yml` (profiles/мережі/volumes/secrets/healthcheck/readiness), pinned digests vendor images, SBOM/scan кроки CI, runbook clean-host; **фактичний прогін**: `up -d --wait` exit 0, 16/16 healthy/exited-0, restart зберігає дані, `down -v` чистий | evidenced (обсяг PR2; `gui` — PR3) |
| R-55 (критичний) | Docker socket у GUI/API = host compromise | жодного bind `docker.sock` у 16 контейнерах (мій `docker inspect`), `tests/unit/test_compose_config.py::test_no_docker_socket_mount_anywhere`, `tests/integration/test_compose_render.py::test_no_docker_socket_and_no_published_ports`, `test_ci_docker_job_profiles_and_no_socket_or_hardcoded_secrets`; масштабування — лише CLI (`--scale`), Swarm controller — WP-01D | evidenced |

## 5. Q-питання §20

| Q | Default ТЗ | Як зафіксовано | Статус |
|---|---|---|---|
| Q-006 (інфраструктурний бюджет/SLO) | один хост MVP, SLO §2.4 | ADR-0002 «Context» прямо називає Q-006 і default; реалізація — single-host Compose з лімітами ресурсів (runbook рахує 16 CPU / 16 ГіБ) | evidenced (safe default + ADR) |
| Q-013 (production deployment mode) | Compose для local/single-host MVP; Swarm для GUI-керованих replicas | ADR-0002 «Context»/«Consequences»: Compose у PR2, Swarm/secrets — окремий ADR при WP-01D; у compose немає жодного Swarm-специфічного елемента, крім label `collector.scalable` (allowlist §7.5) | evidenced (safe default + ADR) |
| ADR-0002 формальні поля §20 (Context, Decision, Consequences, Date, Owner, Status) | — | `docs/decisions/0002-docker-compose-single-host.md:1-8` (Date 2026-09-22, Owner WP-00, Status accepted) + розділи Context/Decision/Consequences/Related | evidenced |
| Risk acceptance у ADR (CVE ID / дата / owner / тригер) | §13 | `0002-…md:164-175`: CVE-2026-82560 (perl), CVE-2026-85091 (zlib), дата 2026-09-22, owner WP-13, тригери — кожне оновлення digest `python:3.13-slim` і злиття WP-02 fetch, дедлайн 2026-12-22. Додатково датовані acceptance: MinIO root (owner WP-01D), права секретів 0644 (owner WP-01D), `api` в `ingress` (owner WP-00 PR3/WP-11A), `trivyignores` (owner WP-13) | evidenced |
| Q-010 (Mongo topology) | single-member RS у MVP | реалізовано single-member `rs0`; зміна topology заблокована перевіркою назви RS із посиланням на runbook/ADR | evidenced (дотично) |

## 6. Перевірка статусів знахідок gate 2 і gate 3 (кожен «fixed» — у коді)

| Знахідка | Заявлено | Перевірено мною |
|---|---|---|
| gate 2 H-1 (mongo init-фаза) | fixed | `docker-compose.yml:207-225` — healthcheck по IPv4 контейнера + `isreplicaset\|\|setName`; `test_mongo_healthcheck_is_robust_to_entrypoint_init_phase`; мій `up --wait` — 26.6 с, `RestartCount=0` ✓ |
| gate 2 low (COPY chmod) | fixed | `Dockerfile:77` `--chmod=0644` ✓ |
| gate 2 low (trivy ignore-unfixed для CRITICAL) | fixed | `ci.yml:148-166` — два кроки ✓ |
| gate 2 low (профілі у runbook/README) | fixed | runbook «Масштабування»/«Типові проблеми», `README.md` compose, коментар `:506-511` ✓ |
| gate 2 low (api exit 143), low (`name: collector`), info ×2 | accepted | ADR-0002 «Прийняті знахідки gate 2» — owner (WP-11A / WP-00) і дата 2026-09-22 ✓ |
| gate 3 CR-1 (`hostname -i` на dual-stack) | fixed | `:218` — `hostname -I \| tr \| grep -m1 IPv4` + `test -n` ✓ (але ADR усе ще цитує стару форму — знахідка F-1) |
| gate 3 CR-2 (`grep -vc healthy`) | fixed | `ci.yml:177` + `deploy/compose/check-healthy.py` + `tests/unit/test_check_healthy.py`; мій прогін проти живого стека — `all 16 containers healthy or exited 0` ✓ |
| gate 3 CR-3 (skip render-тестів) | fixed | `tests/integration/test_compose_render.py:21,44` — skip лише без docker CLI/plugin ✓ |
| gate 3 CR-4/5 (race 23, AutoReconnect, timeouts) | fixed | `cli.py:149-185,218-228`; `test_gate3_fixes.py` ✓ |
| gate 3 CR-6 (depends_on ↔ healthcheck) | fixed | fetch/parse ← minio, projector ← mongo, export ← mongo+minio ✓ |
| gate 3 CR-7 (ротація логів stateful) | fixed | anchor `x-logging` у 15/15 сервісів ✓ |
| gate 3 CR-8 (runbook 16 CPU/16 ГіБ) | fixed | runbook «Передумови» ✓ |
| gate 3 CR-9 (відновлення signal handlers) | fixed | `cli.py:112-131` + тест ✓ |
| gate 3 CR-10 (`http.client.HTTPException`) | fixed | `health.py:121` + тест ✓ |
| gate 3 CR-12 (lazy imports) | fixed | `cli.py:41-42,149,209,257,312`; `health.py:44-46,227`; `pymongo` лишається module-level у health — accepted з owner WP-11A ✓ |
| gate 3 CR-13 (`directConnection` у README) | fixed | `deploy/compose/README.md:65-68` ✓ |
| gate 3 CR-11/CR-14 | accepted | ADR-0002 + рядок у картці PR3 (owner, дата) ✓ |
| gate 3 SEC M-1 (minio cap_drop/read_only) | fixed | `:236-243` (`read_only`, `cap_drop: ALL`, `no-new-privileges`, tmpfs, `MC_CONFIG_DIR`); мій inspect — `RO=true`, MinIO пише у volume ✓ |
| gate 3 SEC L-1 (pids) | fixed | `deploy.resources.limits.pids` 256/1024; runtime ✓ |
| gate 3 SEC L-3 (default credentials) | fixed (паролі) / accepted (0644) | `init-secrets.sh:20-56` генерує випадкові значення; `*.example` містять `GENERATED:…`, не паролі; 0644 — датований acceptance ✓ |
| gate 3 SEC L-4 (health розкриває деталі) | fixed | `health.py:104-132,169-173,193` — у `detail` лише код/клас; `server_header=False` (`cli.py:326`); мій прогін: `detail="tcp reachable (no SQL check yet; owner WP-01A)"`, `"writable primary of replica set"` — без host:port ✓ |
| gate 3 SEC L-5/L-6 | fixed | ротація логів; CVE ID + тригер WP-02 в ADR ✓ |
| gate 3 SEC I-1…I-5 | accepted / not applicable | зафіксовані в ADR з owner ✓ |

## 7. Оцінка відхилень реалізатора

| Відхилення | Оцінка |
|---|---|
| `worker`/`scheduler` — placeholder-процеси (exit 0) замість стабів exit 2 | **відповідає ТЗ/картці**: вимога 7 прямо вимагає placeholder-команду `collector scheduler`, а acceptance — «workers живі й не падають» (exit 2 зробив би `up --wait` неможливим). Стаб-рядок `not implemented: owned by WP-01D` у stderr збережено (правило ADR-0001), покриття перенесено у `test_cli_compose_commands.py` (параметризовано на всі 8 ролей §7.6). Зафіксовано в ADR-0002 |
| MinIO від root (vendor image), тепер із `cap_drop: ALL` + `read_only` | **відповідає ТЗ** із застереженням: §13 вимагає non-root «де можливо», read-only rootfs «де можливо»; ADR-0002 містить датований risk acceptance (2026-09-22, owner WP-01D) саме на залишковий uid 0. Достатньо для MVP; ADR для production вже призначено |
| `--profile workers` без `core` невалідний | **відповідає ТЗ**: §16.2/§16.3 і картка використовують форму з усіма профілями (`--profile core --profile workers [--profile gui]`), яка працює (перевірено). Обмеження задокументовано в runbook, `deploy/compose/README.md`, коментарі compose і ADR. Не потребує ADR понад наявний |
| Правки тестів PR1 (`test_cli.py`, `test_cli_adversarial.py`, `test_foundation_config.py`) | **відповідає ТЗ**: зміни лише відображають нову (вимагану карткою) поведінку `db migrate`/`ensure-mongo`/`api`/`scheduler`/`worker` і додавання fastapi/pymongo/uvicorn; жодне покриття не втрачене — enum ролей §7.6, usage-errors, `--help` = §16.2 лишилися, а placeholder-поведінка покрита новими тестами. `FORBIDDEN_FOUNDATION_DEPS` посилено доданням `psycopg`/`asyncpg` (щоб WP-01A обирав драйвер сам) |
| `.gitattributes` (`*.sh text eol=lf`) | **відповідає ТЗ**: технічно необхідно (Windows `core.autocrlf` ламав би `init-secrets.sh` у Linux-контейнері/CI); файл поза списком «Owned files» PR2 — процесна заувага F-4, не блокуюча |
| Права секретів 0644 замість 0600 (обґрунтування EACCES для non-root) | **прийнятне відхилення з датованим acceptance** (ADR-0002 «Секрети», runbook «Секрети: права файлів», `deploy/compose/README.md`; owner WP-01D, 2026-09-22). §7.5 вимагає «secrets через Docker secrets/files», що виконано; права файлу — операційний ризик single-host MVP, задокументований у трьох місцях. Зауваження на майбутнє (не блокуюче): альтернатива «chown файлу секрету на uid контейнера + 0640» не розглянута в ADR поряд із `secrets.*.environment` — варто згадати при Swarm-ADR |
| Правка `docs/plan/cards/WP-00.md` (рядок до вимоги 4 PR3) | процесно поза owned files, але змістовно коректна: фіксує accepted-знахідку CR-14/SEC L-2 для власника PR3. Прийнято як частину «accepted with owner/date» (DoD п. 8) |

## 8. Знахідки пострев'ю

Усі — `low`/`info`, жодна не блокує merge.

| # | Severity | Файл:рядок | Знахідка | Рекомендація |
|---|---|---|---|---|
| F-1 | low (документація) | `docs/decisions/0002-docker-compose-single-host.md:96` | ADR цитує healthcheck як `--host "$(hostname -i)"`, тоді як після gate 3 (CR-1) у `docker-compose.yml:218` використовується `hostname -I` + вибір першої IPv4 з `test -n`. Розбіжність ADR ↔ код у деталі, яку ADR спеціально пояснює | Оновити рядок ADR (етап docs) |
| F-2 | low (§13) | `.github/workflows/ci.yml` | §13 вимагає dependency/image scanning «щотижня і на кожен PR»; є лише per-PR (`pull_request`/`push: main`), розкладу `schedule: cron` немає в жодному workflow | Додати щотижневий прогін trivy/gitleaks (окремий workflow) — природний owner WP-13; або зафіксувати в ADR-0002 як свідоме відкладення з owner/датою |
| F-3 | low (§7.5) | `docker-compose.yml:31` | §7.5 «images мають immutable tag + digest»: vendor images pinned, але application image використовує mutable tag (`collector:dev`/`collector:ci`) без публікації/pin digest. Відкат за digest лише описаний у звіті/runbook PR3 | Зафіксувати owner (release/registry — WP-14 або PR3 разом із `rollback-image.md`) в ADR-0002 |
| F-4 | info (процес) | `docs/plan/cards/WP-00.md` (Owned files PR2) | PR2 змінив файли поза своїм списком owned: `tests/**`, `pyproject.toml`, `uv.lock`, `.gitignore`, `.gitattributes`, `docs/runbooks/clean-host-start.md`, `docs/plan/cards/WP-00.md`. Усі зміни необхідні для вимог 1–9 і Docs PR2; forbidden-файли (`docs/research/**`, `TECHNICAL_SPECIFICATION.md`, `REVIEW.md`) не змінювались | Розширити список owned files PR2 у картці при наступній правці (координатор) |

## 9. Підсумок

- `missing`: **0** (жодного acceptance- або DoD-пункту без доказу).
- `partial`: **11** — profiles `gui`/`observability`/`tools` (PR3/WP-12/WP-11A), SBOM і trivy `operationally unverified`, `api` egress ширший за §7.5 (accepted, owner PR3/WP-11A), MinIO root (accepted, owner WP-01D), production egress allowlist (WP-12/WP-01D), щотижневий скан (F-2), immutable digest app-image (F-3), clean-host без `gui`, scale без origin-rate частини, singleton lease (WP-01D), DoD «docs/метрики» і «merge після CI».
- `not applicable`: 12 (усі з аргументом «owner іншого WP/під-PR»).
- Знахідок critical/high без статусу `fixed`: **0**.

Вердикт — **`accept`**. Рядки додано до `docs/acceptance/traceability.md`.
