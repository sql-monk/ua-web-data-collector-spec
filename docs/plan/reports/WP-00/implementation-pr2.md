# WP-00 PR2 — звіт реалізації (`wp/00-2-docker-compose`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR2 «images + Compose profiles» |
| Branch / worktree | `wp/00-2-docker-compose` / `.worktrees/wp-00-2` (від `main` після merge PR1) |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR2» + «Спільні правила» |
| Розділи ТЗ | §1 п.8/10, §7.5, §7.6, §8, §13, §16.1 п.14, §16.2, §16.3, FR-030, FR-035; REVIEW.md R-51, R-55; ADR-0001 |
| Середовище | Windows 11, Docker Desktop 29.8.0 (Linux containers), Compose v5.5.1, uv 0.12.13, CPython 3.13.9; Docker Scout 1.24 (SBOM/CVE локально); `syft`/`trivy` локально відсутні |
| Commits | `d0360e3 feat(wp-00): Docker image collector, Compose profiles, health stub, one-shots (PR2)` + commit зі звітом/дрібним фіксом |

## Що зроблено

### Image `collector` (вимога 1) — `Dockerfile`, `.dockerignore`

- Multi-stage: builder `python:3.13-slim@sha256:8d9d0b8b…` + `uv` з
  `ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65…` → `uv sync --frozen --no-dev` у
  `/opt/collector` (спершу залежності, потім пакет `--no-editable`); runtime — той самий
  `python:3.13-slim` (Debian 13 trixie, Python 3.13.15) без uv, **без pip/ensurepip** (їх
  vendored-пакети давали 3 HIGH у scan). Digests — multi-arch index
  (`docker buildx imagetools inspect`).
- Non-root `10001:10001` (`Config.User` у image), `HOME=/tmp`, `TMPDIR=/tmp` (tmpfs у Compose,
  rootfs read-only), `HEALTHCHECK collector version`, OCI labels: `revision` = Git SHA (build arg
  `COLLECTOR_GIT_SHA`, також `ENV` для `collector version`), `ua.collector.schema-version`
  (placeholder до WP-01C), `version`, `created`, `base.name`.
- Секретів у build немає: `ARG`/`ENV` без credentials; `.dockerignore` = `*` з allowlist
  `pyproject.toml, uv.lock, .python-version, README.md, src/, docs/research/source-registry.yaml`.
- **Approved dependency WP-01C** (повідомлення координатора під час PR2):
  `COPY docs/research/source-registry.yaml /app/config/source-registry.yaml` (root-owned,
  readable) + `ENV COLLECTOR_SOURCE_REGISTRY=/app/config/source-registry.yaml`. Перевірено
  `docker compose run --rm api python -c "…os.path.exists(os.environ['COLLECTOR_SOURCE_REGISTRY'])"`
  → `True` (вивід нижче); тест `test_dockerfile_ships_source_registry_for_wp_01c`.
- Browser image — не тут (WP-02 PR3): `browser-worker` тимчасово на image `collector` з TODO.

### `docker-compose.yml` (вимоги 2, 3, 4, 7, 8)

- `name: collector`; profiles `core`, `workers`, `browser` із сервісами точно за таблицею §7.5;
  `gui`/`observability`/`tools` — зарезервовані коментарем (Compose не має декларації профілю
  без сервісу; `gui` — PR3, решта — WP-12/WP-11A/WP-14).
- Спільні фрагменти `x-collector-runtime` (read_only, tmpfs `/tmp` 64M, user 10001, `cap_drop:
  ALL`, `no-new-privileges`, `init`, json-file log rotation, env `COLLECTOR_*`) і `x-worker`
  (profile `workers`, `stop_grace_period: 120s`, label `collector.scalable=true`, `depends_on`
  postgres healthy + `migrate-postgres` completed, healthcheck
  `python -m collector.api.health postgres`). Workers: без `container_name`/`ports`/`volumes`,
  `deploy.replicas` за §7.6 (discovery 1, fetch 2, parse 2, projector 1, translation 1, export 1,
  maintenance 1, browser 0); `deploy.resources.limits` для всіх 15 сервісів
  (small 0.5/512M, medium 1/1G, large 2/2G).
- Мережі: `backend` (internal, усі), `ingress` (лише `api`), `source-egress`
  (discovery/fetch/browser), `provider-egress` (translation), `telemetry` (internal, поки без
  сервісів — `config` її не рендерить). parse/projector/export/maintenance/scheduler/one-shots/
  stateful — лише `backend` (без egress).
- Stateful (`core`): `postgres:18@sha256:86c951e0…` (`user: postgres`, scram-sha-256,
  `pg_isready`), `mongo:8.0@sha256:4968f22d…` (`user: 999:999`, `--replSet rs0 --keyFile`,
  keyfile копіюється з `/run/secrets` у tmpfs `install -m 0400` — file-secrets Compose мають
  права хоста, на Docker Desktop 0777), `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z@sha256:14cea493…`
  (root — прийняте відхилення, див. ризики). Named volumes `postgres-data`, `mongo-data`,
  `mongo-config`, `minio-data`; **портів у base-файлі немає**; `deploy/compose/dev.override.yml`
  публікує 5432/27017/9000/9001/8000 лише на `127.0.0.1`.
- `scheduler` — singleton: `replicas: 1`, `collector.scalable=false`, `stop_grace_period: 90s`.
- Healthcheck кожного довгоживучого сервісу = процес + критична dependency: api → HTTP 200
  health endpoint; fetch/parse → postgres+minio; projector → postgres+mongo; export → усі три;
  discovery/translation/maintenance/scheduler → postgres; postgres → `pg_isready`; mongo →
  `ping`; minio → `/minio/health/live`.

### One-shots, health, CLI (вимоги 5, 7) — `src/collector/cli.py`, `src/collector/api/health.py`

- `migrate-postgres` = `collector db migrate`: TCP-перевірка `postgres:5432` → stdout
  `no migrations yet; owner WP-01A`, exit 0; недоступний → exit 1. **Чесно:** лише TCP, без
  credentials/SQL — драйвер (psycopg/asyncpg) і Alembic обирає WP-01A.
- `ensure-mongo` = `collector db ensure-mongo`: реальна ідемпотентна ініціалізація single-member
  RS (`replSetGetStatus` → код 94 → `replSetInitiate {_id: rs0, members: [mongo:27017]}` →
  очікування `hello.isWritablePrimary` до 60 с); root credentials з env/`*_FILE`. Інша назва RS →
  помилка (topology змінюється runbook/ADR, §7.5). `--validators/--indexes` після ініціалізації —
  стаб WP-01B (stderr `not implemented: owned by WP-01B`, exit 2), тому Compose викликає без
  прапорців.
- `api` = uvicorn з factory `collector.api.health:create_app` (`log_config=None` → логи через
  structlog): лише `GET /api/v1/health/components` (`openapi/docs` вимкнені; WP-11A):
  `postgres` TCP, `mongo` `hello` без auth (`ok` лише writable primary — health не потребує Mongo
  credentials, §13), `minio` `GET /minio/health/live`. `ready` = усі `ok` → HTTP 200, інакше
  503 (це читає healthcheck `api`/`--wait`). Readiness до one-shots — через
  `depends_on: service_completed_successfully` (`api`, workers, scheduler).
- `python -m collector.api.health [postgres] [mongo] [minio]` — healthcheck-режим (exit 0/1/2).
- **Placeholder замість стаба для довгоживучих команд:** `collector worker <role>` і
  `collector scheduler` друкують стаб-рядок `not implemented: owned by WP-01D` у stderr,
  логують `placeholder.started/heartbeat`, по SIGTERM/SIGINT — `placeholder.stopped`, exit 0.
  Причина: acceptance «workers живі й не падають» + `--wait` не проходить для exit 2.
  `controller`, `e2e`, `release *` — стаби без змін.
- Залежності (`pyproject.toml`, алфавітно; `uv.lock` перегенеровано, 51 пакет): `fastapi>=0.118`
  (0.141.1), `pymongo>=4.15` (4.18.1), `uvicorn>=0.37` (0.53.0). Тест
  `FORBIDDEN_FOUNDATION_DEPS` оновлено (`fastapi`/`pymongo` вилучено, додано
  `psycopg`/`asyncpg`, щоб WP-01A обирав драйвер сам).

### Секрети (вимога 6)

`deploy/compose/secrets/{postgres_password,mongo_root_password,mongo_keyfile,minio_root_user,minio_root_password}.example`
у git; реальні файли — `.gitignore` (`deploy/compose/secrets/*` + `!*.example`,
`!init-secrets.sh`); `init-secrets.sh` копіює приклади і генерує keyfile
(`openssl rand -base64 756`). У `environment` — лише `*_FILE` (тест). `.gitattributes`:
`*.sh text eol=lf` (core.autocrlf=true на Windows ламав би bash-скрипт).

### CI (вимога 9) — `.github/workflows/ci.yml`, job `docker`

`init-secrets.sh` → `docker compose config --quiet` (без profiles і з core/workers/browser) →
`docker build` (+ перевірка `Config.User=10001:10001`) → SBOM `anchore/sbom-action@v0.24.2`
(syft, SPDX JSON artifact) → `aquasecurity/trivy-action@v0.36.0` (`severity: CRITICAL`,
`ignore-unfixed`, `exit-code: 1`) → `docker compose up -d --wait --wait-timeout 300`
(core+workers) → `ps` + `exec api python -m collector.api.health` + перевірка «усі healthy» →
`down -v` (`if: always()`).

### Документи

`docs/decisions/0002-docker-compose-single-host.md` (Q-006/Q-013 default, усі рішення вище),
`docs/runbooks/clean-host-start.md`, `deploy/compose/README.md` (profiles, мережі, секрети,
override, env). `README.md` (корінь) не змінювався — не в owned files PR2; pointer на PR2
додає етап docs.

### Тести (§16.1 п.14 + unit)

- `tests/unit/test_compose_config.py` (33) — base-файл через PyYAML без Docker: таблиця
  profiles §7.5, workers без container_name/ports/volumes + replicas §7.6 + grace ≥ 90 с,
  scheduler singleton, жодного `docker.sock`, жодного `ports` у base, override лише
  `127.0.0.1`, секрети = файли з `.example` + gitignored, env без секретів, application
  services read_only/non-root/tmpfs/cap_drop/init, limits для всіх, healthcheck + readiness
  depends_on, мережі §7.5 і розміщення сервісів, stateful pinned digest + named volumes,
  Dockerfile (pinned FROM, USER, HEALTHCHECK, labels, без секретів у ARG/ENV, source-registry),
  `.dockerignore`, CI-кроки.
- `tests/integration/test_compose_render.py` (5, marker `integration`, skip без docker CLI або
  секретів) — ті самі інваріанти на `docker compose config --format json` (+ dev override).
- `tests/unit/test_health.py` (13) — env/`*_FILE`, адреси, `hello` через фейк PyMongo,
  `ready`, 200/503, лише один route, `main()`.
- `tests/integration/test_health_loopback.py` (5) — реальні TCP/HTTP проти loopback-заглушок.
- `tests/unit/test_cli_compose_commands.py` (21) — `db migrate` 0/1, RS init (initiate /
  idempotent / інша назва / timeout / re-raise), `ensure-mongo` через фейковий клієнт (секрет
  з `*_FILE`, не в логах), `--validators` → стаб 2, placeholder loop до stop, `worker`/`scheduler`
  → placeholder, `api` → uvicorn factory.
- Оновлено `test_cli.py`, `test_cli_adversarial.py` (стабами лишаються `e2e`, `release *`,
  `controller`), `test_foundation_config.py` (forbidden deps).

## Команди та вивід

### Команди перевірки картки (PR2)

```text
$ docker compose config --quiet
exit=0

$ docker compose build --pull
 Image collector:dev Built
exit=0

$ time docker compose --profile core --profile workers up -d --wait
 Container collector-mongo-1 Healthy
 Container collector-postgres-1 Healthy
 Container collector-minio-1 Healthy
 Container collector-migrate-postgres-1 Exited
 Container collector-ensure-mongo-1 Exited
 Container collector-discovery-worker-1 Healthy
 Container collector-translation-worker-1 Healthy
 Container collector-scheduler-1 Healthy
 Container collector-api-1 Healthy
 Container collector-parse-worker-2 Healthy
 Container collector-parse-worker-1 Healthy
 Container collector-maintenance-worker-1 Healthy
 Container collector-projector-worker-1 Healthy
 Container collector-export-worker-1 Healthy
 Container collector-fetch-worker-2 Healthy
 Container collector-fetch-worker-1 Healthy
exit=0 elapsed=118s        (перший прогін того ж дня з готовими images — 33 с)

$ docker compose ps -a
SERVICE              STATUS                        PORTS
api                  Up 17 seconds (healthy)       8000/tcp
discovery-worker     Up 45 seconds (healthy)
ensure-mongo         Exited (0) 51 seconds ago
export-worker        Up 14 seconds (healthy)
fetch-worker         Up 12 seconds (healthy)
fetch-worker         Up 41 seconds (healthy)
maintenance-worker   Up 18 seconds (healthy)
migrate-postgres     Exited (0) 51 seconds ago
minio                Up About a minute (healthy)   9000/tcp
mongo                Up About a minute (healthy)   27017/tcp
parse-worker         Up 25 seconds (healthy)
parse-worker         Up 29 seconds (healthy)
postgres             Up About a minute (healthy)   5432/tcp
projector-worker     Up 15 seconds (healthy)
scheduler            Up 37 seconds (healthy)
translation-worker   Up 21 seconds (healthy)
(PORTS = expose всередині мереж; PortBindings порожні — див. docker inspect нижче)

$ docker compose exec api python -m collector.api.health
postgres: ok (tcp postgres:5432 reachable (no SQL check yet; owner WP-01A))
mongo: ok (writable primary of replica set 'rs0')
minio: ok (liveness HTTP 200)
exit=0

$ docker compose run --rm api python -c "import os; print(os.path.exists(os.environ['COLLECTOR_SOURCE_REGISTRY']))"
True

$ docker compose logs ensure-mongo migrate-postgres
ensure-mongo-1      | {"replica_set": "rs0", "member": "mongo:27017", "initiated_now": true, "event": "ensure_mongo.replica_set_ready", ...}
migrate-postgres-1  | no migrations yet; owner WP-01A
migrate-postgres-1  | {"detail": "tcp postgres:5432 reachable (no SQL check yet; owner WP-01A)", "latency_ms": 2.9, "event": "migrate.postgres_reachable", ...}

$ docker compose run --rm ensure-mongo            # повторно — ідемпотентно
{"replica_set": "rs0", "member": "mongo:27017", "initiated_now": false, "event": "ensure_mongo.replica_set_ready", ...}
$ docker compose run --rm ensure-mongo collector db ensure-mongo --validators --indexes
not implemented: owned by WP-01B
exit=2
```

### Restart без втрати named volumes (acceptance)

```text
# маркери (postgres таблиця wp00_marker, minio bucket wp00-marker/marker.txt, mongo wp00.marker)
CREATE TABLE
INSERT 0 1
restart-marker
restart-marker
restart-marker

$ docker compose restart
 Container collector-fetch-worker-2 Started
exit=0 elapsed=20s
SERVICE              STATUS
api                  Up 47 seconds (healthy)
... (усі 16 контейнерів healthy; translation-worker ще "health: starting" через 30 с, далі healthy)
# маркери після restart
restart-marker
restart-marker
restart-marker / PRIMARY

# SIGTERM drain у worker (placeholder завершився кодом 0 у межах stop_grace_period)
{"signal": "SIGTERM", "event": "placeholder.stop_requested", "logger": "collector.worker.fetch", ...}
{"component": "worker.fetch", "event": "placeholder.stopped", "logger": "collector.worker.fetch", ...}
```

### Non-root, read-only, без Docker socket, без секретів в image (acceptance)

```text
$ docker inspect collector:dev --format "{{.Config.User}}"
10001:10001
/collector-api-1          User=10001:10001 ReadonlyRootfs=true  CapDrop=[ALL] StopTimeout=30  Ports=map[]
/collector-fetch-worker-1 User=10001:10001 ReadonlyRootfs=true  CapDrop=[ALL] StopTimeout=120 Ports=map[]
/collector-scheduler-1    User=10001:10001 ReadonlyRootfs=true  CapDrop=[ALL] StopTimeout=90  Ports=map[]
/collector-postgres-1     User=postgres    ReadonlyRootfs=false CapDrop=[ALL] StopTimeout=60  Ports=map[]
/collector-mongo-1        User=999:999     ReadonlyRootfs=false CapDrop=[ALL] StopTimeout=60  Ports=map[]
/collector-minio-1        User=            ReadonlyRootfs=false CapDrop=[]    StopTimeout=30  Ports=map[]

$ docker ps --filter name=collector- -q | xargs docker inspect --format '{{.Name}} {{.HostConfig.Binds}} {{range .Mounts}}{{.Source}} {{end}}' | grep -c docker.sock
0
(mounts: лише named volumes і bind /run/secrets/* для stateful)

$ docker history --no-trunc collector:dev --format '{{.CreatedBy}}' | grep -iE "password|secret|token|api[_-]?key"
grep exit=1 (нічого не знайдено)

$ docker run --rm collector:dev env | sort
COLLECTOR_GIT_SHA=ea8d75e9d535b757e585954012eb533a95f761aa
COLLECTOR_SOURCE_REGISTRY=/app/config/source-registry.yaml
GPG_KEY=<id публічного ключа base image python; значення прибрано зі звіту через gitleaks>
HOME=/tmp  HOSTNAME=…  PATH=/opt/collector/bin:…  PYTHONDONTWRITEBYTECODE=1  PYTHONUNBUFFERED=1
PYTHONUTF8=1  PYTHON_SHA256=…  PYTHON_VERSION=3.13.15  TMPDIR=/tmp

$ docker run --rm collector:dev sh -c "ls /run/secrets /app"
ls: cannot access '/run/secrets': No such file or directory
/app/config/source-registry.yaml

$ docker compose --profile core --profile workers --profile browser config --format json | python -c ...
services: 15
container_name: []
ports: []
docker.sock: False
read_only+non-root app services: ['api', 'browser-worker', 'discovery-worker', 'ensure-mongo', 'export-worker',
  'fetch-worker', 'maintenance-worker', 'migrate-postgres', 'parse-worker', 'projector-worker', 'scheduler',
  'translation-worker']
networks: ['backend', 'ingress', 'provider-egress', 'source-egress']   (telemetry — без сервісів, не рендериться)
```

### Scale, profile isolation, down -v

```text
$ docker compose up -d --no-recreate --scale fetch-worker=4 --scale parse-worker=1 --wait
 Container collector-fetch-worker-3 Healthy
 Container collector-fetch-worker-4 Healthy
 Container collector-parse-worker-2 Removed
      4 × fetch-worker Up (healthy);  1 × parse-worker Up (healthy)

$ docker compose --profile workers config --quiet          # без core — свідомо невалідно
service "discovery-worker" depends on undefined service "migrate-postgres": invalid compose project
$ docker compose --profile core config --services
api ensure-mongo migrate-postgres minio mongo postgres scheduler

$ docker compose down -v
 Volume collector_postgres-data Removed / collector_mongo-config Removed / collector_mongo-data Removed / collector_minio-data Removed
 Network collector_backend / collector_ingress / collector_provider_egress / collector_source_egress Removed
exit=0 elapsed=21s
volumes collector_* після down -v: 0
```

### SBOM / vulnerability scan (локально — Docker Scout; у CI — syft + trivy)

```text
$ docker scout sbom --format list collector:dev
Indexed 168 packages (до видалення pip) → 149 пакетів (deb/pypi/generic) після видалення pip/ensurepip
$ docker scout cves --only-severity critical,high collector:dev
   0C     1H     0M     0L  perl 5.40.1-6+deb13u1        CVE-2026-82560  Fixed version: not fixed
   0C     1H     0M     0L  zlib 1:1.3.dfsg+really1.3.1-1                  Fixed version: not fixed
2 vulnerabilities found in 2 packages   CRITICAL 0   HIGH 2   MEDIUM 0   LOW 0
(до видалення pip: 5 HIGH — msgpack ×2, setuptools ×1 у pip/_vendor; усунено)
```

### Python-контракт §16.2 і pre-commit

```text
$ uv sync --frozen                      Checked 51 packages
$ uv run ruff check .                   All checks passed!
$ uv run ruff format --check .          68 files already formatted
$ uv run mypy src                       Success: no issues found in 25 source files
$ uv run mypy tests                     Success: no issues found in 12 source files
$ uv run pytest -m "not live"           170 passed, 1 skipped (Windows loopback/asyncio), 8 warnings in 12.62s
$ uv run pre-commit run --all-files     усі 11 hooks Passed (eof, whitespace, yaml, toml, large files,
                                        merge conflict, private key, ruff check, ruff format, gitleaks, markdownlint)
$ uv run collector --help               Commands: version e2e worker api scheduler controller db release
$ python -c "yaml.safe_load(ci.yml)"    jobs: ['python', 'pre-commit', 'secrets', 'docker']
```

Свіжий clone гілки (без секретів): `docker compose config --quiet` → exit 0;
`uv sync --frozen && uv run pytest -m "not live"` → `165 passed, 6 skipped`
(5 skip — `test_compose_render.py`: «секрети не ініціалізовані (init-secrets.sh)», 1 — Windows).

## Що не перевірено

- **CI job `docker` на GitHub Actions** (`anchore/sbom-action@v0.24.2`, `aquasecurity/trivy-action@v0.36.0`,
  ubuntu-latest `up --wait`) — не запускався (push заборонений); YAML валідний, версії actions —
  latest releases на 2026-09-22 (`gh api …/releases/latest`). Позначка: `operationally unverified`
  до першого прогону PR.
- **`syft`/`trivy` локально** відсутні — SBOM/CVE перевірено через Docker Scout (той самий
  клас інструментів); результат trivy у CI може відрізнятися базою CVE.
- **Clean Linux host**: усе виконано на Windows/Docker Desktop (Linux containers). Відмінності:
  file-secrets на Linux зберігають права хоста (потрібен 0644 — робить `init-secrets.sh`,
  umask 022), файли з Windows-контексту потрапляють у image як 0755 (на Linux 0644).
  Еталон — CI job `docker`.
- **`--profile gui`** — сервіс з'являється у PR3; acceptance §16.3 виконано для `core` + `workers`
  (дозволено карткою, вимога 8).
- **Реальні credentials PostgreSQL** health/`db migrate` не перевіряють (лише TCP) — свідомо,
  див. ризики; MinIO перевіряється лише liveness (без S3-автентифікації).
- **Windows loopback skip** (`test_network_blocked.py`) — успадковано з PR1, у CI (Linux) виконується.

## Ризики

| # | Ризик | Пом'якшення / owner |
|---|---|---|
| 1 | `db migrate`/health `postgres` — TCP без credentials: помилковий пароль не блокує старт `api`, доки WP-01A не додасть Alembic | WP-01A замінює команду `migrate-postgres` на `alembic upgrade head` (падає на bad credentials) і додає SQL-перевірку в health; задокументовано в `detail` («no SQL check yet; owner WP-01A») |
| 2 | Placeholder `worker`/`scheduler` (exit 0, живі) замість стаба exit 2 — відхилення від букви ADR-0001 для двох команд | stderr-рядок стаба зберігається (правило ADR-0001 «стаб визначається рядком»); тести WP-01D замінять |
| 3 | MinIO працює від root (vendor image, `/data` root-owned) | прийняте відхилення (§13 «де можливо»); non-root MinIO + Swarm secrets — ADR при WP-01D production enablement |
| 4 | File-secrets Compose = bind mount з правами хоста (0644 у репо, gitignored) — читаються будь-яким процесом хоста з доступом до каталогу | лише локальна розробка/single-host MVP; production — Docker Swarm secrets (Q-013) |
| 5 | 2 HIGH CVE без fix у Debian trixie base (perl, zlib), дата 2026-09-22 | §13: блокує лише CRITICAL; trivy у CI `ignore-unfixed`; переглянути при оновленні digest `python:3.13-slim` |
| 6 | MinIO Docker Hub образи більше не публікуються; `quay.io/minio/minio` RELEASE.2025-09-07 — останній community-реліз | pinned digest; заміна на managed S3 у prod (§8) або форк/оновлення — окремий ADR |
| 7 | `docker compose --profile workers` без `core` невалідний | документовано (runbook, README compose); `COMPOSE_PROFILES=core,workers` |
| 8 | `fastapi`/`pymongo`/`uvicorn` увійшли у foundation-залежності раніше за WP-11A/WP-01B | мінімальні pin-и `>=`; власники оновлюють версії; тест forbidden deps оновлено з поясненням |
| 9 | Windows dev: `core.autocrlf=true` міг зламати `init-secrets.sh` | `.gitattributes` `*.sh text eol=lf` |

## Як вимкнути або відкотити

- PR не має runtime-ефекту поза Docker: revert merge commit повертає PR1 (стаби exit 2 для
  `api`/`db *`/`worker`/`scheduler`, без fastapi/pymongo/uvicorn у `uv.lock`).
- Зупинити стек: `docker compose --profile core --profile workers down` (дані у volumes
  лишаються) або `down -v` (видалити дані).
- Вимкнути окремий profile/сервіс: не передавати `--profile`, або `--scale <svc>=0`.
- Відкат image: `COLLECTOR_IMAGE=<name>@sha256:<digest> docker compose up -d` (runbook
  `rollback-image.md` — PR3).

## Dependency-запити

- **Вхідний (approved) від WP-01C**: source-registry у image + `COLLECTOR_SOURCE_REGISTRY` —
  виконано (див. вище). Файл `docs/research/source-registry.yaml` не змінювався (forbidden).
- Вихідних `docs/plan/deps/*` немає. Очікування від власників:
  WP-01A — замінити `migrate-postgres` command/health SQL; WP-01B — `--validators --indexes`
  у `ensure-mongo` command; WP-01D — тіла `worker`/`scheduler`, Swarm ADR; WP-11A —
  `collector.api.health` → повний API; WP-02 PR3 — image `browser-worker`; WP-12 — сервіси
  profile `observability` у мережі `telemetry`.
