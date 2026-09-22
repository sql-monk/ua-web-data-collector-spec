# WP-00 PR2 — звіт тестування (`wp/00-2-docker-compose`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR2 «Docker images + Compose profiles» |
| Branch / worktree | `wp/00-2-docker-compose` / `.worktrees/wp-00-2`, HEAD `7f0126b` (реалізація) |
| Картка | `docs/plan/cards/WP-00.md`, розділ PR2 (вимоги 1–9, команди перевірки, acceptance), «Спільні правила» |
| ТЗ | §7.5, §7.6, §13, §16.1 п.14, §16.2, §16.3 (clean host / restart / scale / socket); REVIEW.md R-51, R-55 |
| Середовище | Windows 11, Docker Desktop 29.8.0 (Linux containers), Compose v5.5.1, uv 0.12; `syft`/`trivy`/`docker sbom` відсутні; `actionlint` відсутній |
| Compose project | `name: collector` задано у файлі (не з назви теки `wp-00-2`); на хості не було іншого проєкту `collector`; сторонній проєкт `puluj-g` не чіпався |
| Тестовий коміт | `150441e test(wp-00): PR2 adversarial tests — compose/Dockerfile/CI invariants, health 503, ensure-mongo` |
| Вердикт | **pass** (з однією знахідкою high — інтермітентний збій clean-host `up --wait`, 1 з 9 прогонів; див. «Знахідки») |

`implementation-pr2.md` прочитано лише після власного прогону (п. 5 інструкції).

## 1. Команди перевірки картки та дослівний вивід

### 1.1. `docker compose config`

```text
$ docker compose config --quiet
exit=0
$ docker compose --profile core --profile workers --profile browser config --quiet
exit_all_profiles=0
$ docker compose config | head -3        # без profiles усі сервіси відфільтровано
name: collector
services: {}
x-collector-image:
```

Машинна перевірка `docker compose --profile core --profile workers --profile browser config --format json`
(скрипт `check_config.py`, scratchpad):

```text
project name: collector
services: ['api', 'browser-worker', 'discovery-worker', 'ensure-mongo', 'export-worker', 'fetch-worker',
  'maintenance-worker', 'migrate-postgres', 'minio', 'mongo', 'parse-worker', 'postgres', 'projector-worker',
  'scheduler', 'translation-worker']
  api                  nets=['backend', 'ingress']        user=10001:10001 ro=True grace=30s  replicas=None limits={'cpus': 1,   'memory': '1073741824'}
  browser-worker       nets=['backend', 'source-egress']  user=10001:10001 ro=True grace=2m0s replicas=0    limits={'cpus': 2,   'memory': '2147483648'}
  discovery-worker     nets=['backend', 'source-egress']  user=10001:10001 ro=True grace=2m0s replicas=1    limits={'cpus': 0.5, 'memory': '536870912'}
  ensure-mongo         nets=['backend']                   user=10001:10001 ro=True grace=None replicas=None limits={'cpus': 0.5, 'memory': '536870912'}
  export-worker        nets=['backend']                   user=10001:10001 ro=True grace=2m0s replicas=1    limits={'cpus': 1,   'memory': '1073741824'}
  fetch-worker         nets=['backend', 'source-egress']  user=10001:10001 ro=True grace=2m0s replicas=2    limits={'cpus': 1,   'memory': '1073741824'}
  maintenance-worker   nets=['backend']                   user=10001:10001 ro=True grace=2m0s replicas=1    limits={'cpus': 0.5, 'memory': '536870912'}
  migrate-postgres     nets=['backend']                   user=10001:10001 ro=True grace=None replicas=None limits={'cpus': 0.5, 'memory': '536870912'}
  minio                nets=['backend']                   user=None        ro=None grace=30s  replicas=None limits={'cpus': 1,   'memory': '1073741824'}
  mongo                nets=['backend']                   user=999:999     ro=None grace=1m0s replicas=None limits={'cpus': 2,   'memory': '2147483648'}
  parse-worker         nets=['backend']                   user=10001:10001 ro=True grace=2m0s replicas=2    limits={'cpus': 2,   'memory': '2147483648'}
  postgres             nets=['backend']                   user=postgres    ro=None grace=1m0s replicas=None limits={'cpus': 2,   'memory': '2147483648'}
  projector-worker     nets=['backend']                   user=10001:10001 ro=True grace=2m0s replicas=1    limits={'cpus': 1,   'memory': '1073741824'}
  scheduler            nets=['backend']                   user=10001:10001 ro=True grace=1m30s replicas=1   limits={'cpus': 0.5, 'memory': '536870912'}
  translation-worker   nets=['backend', 'provider-egress'] user=10001:10001 ro=True grace=2m0s replicas=1   limits={'cpus': 0.5, 'memory': '536870912'}
networks: {'backend': True, 'ingress': None, 'provider-egress': None, 'source-egress': None}
volumes: ['minio-data', 'mongo-config', 'mongo-data', 'postgres-data']
secrets: {... усі 5 → file: <worktree>/deploy/compose/secrets/<name>}
PROBLEMS: none
exit=0
```

Перевірені інваріанти: жодного `container_name` у workers; жодного `docker.sock` /
`docker_engine` / `/var/run/docker` у JSON; жодного `ports` у base-файлі; `read_only: true` +
`user: 10001:10001` + tmpfs `/tmp` для всіх 12 application-сервісів; мережі за §7.5
(discovery/fetch/browser → `source-egress`, translation → `provider-egress`, api → лише
`backend`+`ingress`, parse/projector/export/maintenance/scheduler/one-shots/stateful → лише
`backend`); `gui` відсутній (PR3); `stop_grace_period` workers = 120s ≥ 90s;
`deploy.resources.limits` у всіх 15; stateful images `tag@sha256:` (postgres:18, mongo:8.0,
quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z). Мережа `telemetry` оголошена, але не
рендериться (без сервісів до WP-12) — очікувано.

### 1.2. `docker compose build --pull`, image

```text
$ COLLECTOR_GIT_SHA=$(git rev-parse HEAD) docker compose --profile core --profile workers build --pull
 Image collector:dev Built   (×11 сервісів, той самий image; кеш BuildKit)
real 0m47.658s   exit=0

$ docker inspect collector:dev --format 'User={{.Config.User}} Entrypoint={{.Config.Entrypoint}} Cmd={{.Config.Cmd}} Healthcheck={{.Config.Healthcheck.Test}}'
User=10001:10001 Entrypoint=[] Cmd=[collector --help] Healthcheck=[CMD collector version]
Labels: org.opencontainers.image.revision=7f0126be63deafa8b0571c560e32d04ddc6b3774,
  org.opencontainers.image.version=0.1.0, org.opencontainers.image.created=1970-01-01T00:00:00Z,
  ua.collector.schema-version=0.0.0-placeholder, org.opencontainers.image.base.name=docker.io/library/python:3.13-slim

$ docker run --rm --read-only --tmpfs /tmp collector:dev sh -c 'id; env | sort; collector version; ls -la /app/config; sha256sum /app/config/source-registry.yaml; test -e /var/run/docker.sock && echo SOCKET_PRESENT || echo no-socket; which pip || echo "no pip"; python -c "import ensurepip"; touch /app/x'
uid=10001(collector) gid=10001(collector) groups=10001(collector)
COLLECTOR_GIT_SHA=7f0126be63deafa8b0571c560e32d04ddc6b3774
COLLECTOR_SOURCE_REGISTRY=/app/config/source-registry.yaml
GPG_KEY=<id публічного ключа base image>  HOME=/tmp  PATH=/opt/collector/bin:…  PYTHONDONTWRITEBYTECODE=1
PYTHONUNBUFFERED=1  PYTHONUTF8=1  PYTHON_SHA256=…  PYTHON_VERSION=3.13.15  TMPDIR=/tmp
package_version=0.1.0
git_sha=7f0126be63deafa8b0571c560e32d04ddc6b3774
schema_version=0.0.0-placeholder
-rwxr-xr-x 1 root root 9922 Sep 22 06:44 source-registry.yaml
05604c0257cd6c252d241ffc2fb6e0bdd983d417440839ef95246d807455624e  /app/config/source-registry.yaml
no-socket
no pip
ModuleNotFoundError: No module named 'ensurepip'
touch: cannot touch '/app/x': Read-only file system

$ sha256sum docs/research/source-registry.yaml        # у репо
05604c0257cd6c252d241ffc2fb6e0bdd983d417440839ef95246d807455624e   → збігається (сценарій 7)
```

### 1.3. Секрети в image (сценарій 5)

```text
$ docker history --no-trunc collector:dev > history.txt; docker inspect collector:dev > inspect.json
# grep -F значень кожного з deploy/compose/secrets/{postgres_password,mongo_root_password,minio_root_password,minio_root_user,mongo_keyfile}
secret-leak-found=0
$ grep -ic "password\|secret" history_cmds.txt
0
ARG/ENV у history: лише COLLECTOR_CREATED/VERSION/SCHEMA_VERSION/GIT_SHA, PATH, PYTHON_*, GPG_KEY (base image)
$ docker run --rm collector:dev sh -c 'grep -rlF "change-me-" /opt /app /etc || echo "no change-me in fs"'
no change-me in fs
$ git ls-files deploy/compose/secrets
deploy/compose/secrets/init-secrets.sh
deploy/compose/secrets/{minio_root_password,minio_root_user,mongo_keyfile,mongo_root_password,postgres_password}.example
$ git status --short --ignored deploy/compose/secrets        # реальні файли — ignored (!!)
!! deploy/compose/secrets/minio_root_password  (…усі 5)
$ gitleaks detect --source . --redact
42 commits scanned … no leaks found
```

### 1.4. `up -d --wait` core+workers, health (сценарій 2)

```text
$ export COMPOSE_PROFILES=core,workers; time docker compose up -d --wait --wait-timeout 300
 Container collector-migrate-postgres-1 Exited
 Container collector-ensure-mongo-1 Exited
 Container collector-{postgres,mongo,minio,api,scheduler,discovery-worker,fetch-worker-1,fetch-worker-2,
   parse-worker-1,parse-worker-2,projector-worker,translation-worker,export-worker,maintenance-worker}-1 Healthy
real 1m17.026s   exit=0

$ docker compose ps -a
NAME                             SERVICE              STATUS                        PORTS
collector-api-1                  api                  Up 25 seconds (healthy)       8000/tcp
collector-discovery-worker-1     discovery-worker     Up 40 seconds (healthy)
collector-ensure-mongo-1         ensure-mongo         Exited (0) 40 seconds ago
collector-export-worker-1        export-worker        Up 26 seconds (healthy)
collector-fetch-worker-1         fetch-worker         Up 23 seconds (healthy)
collector-fetch-worker-2         fetch-worker         Up 31 seconds (healthy)
collector-maintenance-worker-1   maintenance-worker   Up 35 seconds (healthy)
collector-migrate-postgres-1     migrate-postgres     Exited (0) 42 seconds ago
collector-minio-1                minio                Up About a minute (healthy)   9000/tcp
collector-mongo-1                mongo                Up About a minute (healthy)   27017/tcp
collector-parse-worker-1         parse-worker         Up 36 seconds (healthy)
collector-parse-worker-2         parse-worker         Up 37 seconds (healthy)
collector-postgres-1             postgres             Up 59 seconds (healthy)       5432/tcp
collector-projector-worker-1     projector-worker     Up 27 seconds (healthy)
collector-scheduler-1            scheduler            Up 32 seconds (healthy)
collector-translation-worker-1   translation-worker   Up 33 seconds (healthy)
(PORTS = expose; host-published ports: none — `docker ps` не показує 0.0.0.0/127.0.0.1 binding)

$ docker compose logs --no-log-prefix migrate-postgres
{"detail": "tcp postgres:5432 reachable (no SQL check yet; owner WP-01A)", "latency_ms": 1.1, "event": "migrate.postgres_reachable", ...}
no migrations yet; owner WP-01A
$ docker compose logs --no-log-prefix ensure-mongo
{"replica_set": "rs0", "member": "mongo:27017", "initiated_now": true, "event": "ensure_mongo.replica_set_ready", ...}

$ docker compose exec -T api python -c '…urlopen("http://127.0.0.1:8000/api/v1/health/components")…'
HTTP 200
{"ready": true, "components": [
  {"name": "postgres", "ok": true, "latency_ms": 1.2, "detail": "tcp postgres:5432 reachable (no SQL check yet; owner WP-01A)"},
  {"name": "mongo", "ok": true, "latency_ms": 25.6, "detail": "writable primary of replica set 'rs0'"},
  {"name": "minio", "ok": true, "latency_ms": 1.7, "detail": "liveness HTTP 200"}],
 "version": {"package_version": "0.1.0", "git_sha": "7f0126be…", "schema_version": "0.0.0-placeholder"}}

$ docker compose exec -T api env | sort        # лише COLLECTOR_* хости/порти; жодного *_PASSWORD, жодного *_FILE
COLLECTOR_API_HOST=0.0.0.0 COLLECTOR_API_PORT=8000 COLLECTOR_GIT_SHA=… COLLECTOR_LOG_LEVEL=INFO
COLLECTOR_MINIO_URL=http://minio:9000 COLLECTOR_MONGO_HOST=mongo COLLECTOR_MONGO_PORT=27017
COLLECTOR_MONGO_REPLICA_SET=rs0 COLLECTOR_POSTGRES_HOST=postgres COLLECTOR_POSTGRES_PORT=5432
COLLECTOR_SOURCE_REGISTRY=/app/config/source-registry.yaml GPG_KEY=… HOME=/tmp HOSTNAME=… PATH=… PYTHON*… TMPDIR=/tmp
$ docker compose exec -T api sh -c 'ls /run/secrets'
ls: cannot access '/run/secrets': No such file or directory
$ docker inspect collector-ensure-mongo-1 --format '{{range .Mounts}}{{.Type}}:{{.Destination}} {{end}}'
bind:/run/secrets/mongo_root_password
$ docker compose exec -T mongo sh -c 'ls -l /run/mongo-keyfile/ /run/secrets/'
/run/mongo-keyfile/: -r-------- 1 mongodb mongodb 1024 keyfile          (tmpfs 0400 — entrypoint)
/run/secrets/:       -rwxrwxrwx root root mongo_keyfile, mongo_root_password   (права хоста Docker Desktop)
```

Runtime-налаштування контейнерів (`docker inspect`):

```text
collector-api-1                  collector_backend collector_ingress        | ro=true user=10001:10001 capdrop=[ALL] init=true cpus=1e9   mem=1073741824 stop=30  secopt=[no-new-privileges:true]
collector-discovery-worker-1     collector_backend collector_source_egress  | ro=true user=10001:10001 capdrop=[ALL] init=true cpus=5e8   mem=536870912  stop=120 secopt=[no-new-privileges:true]
collector-fetch-worker-{1,2}     collector_backend collector_source_egress  | ro=true user=10001:10001 capdrop=[ALL] init=true cpus=1e9   mem=1073741824 stop=120 …
collector-parse-worker-{1,2}     collector_backend                          | ro=true user=10001:10001 capdrop=[ALL] init=true cpus=2e9   mem=2147483648 stop=120 …
collector-projector-worker-1     collector_backend                          | ro=true … stop=120 ;  export-worker: backend, stop=120 ;  maintenance-worker: backend, stop=120
collector-translation-worker-1   collector_backend collector_provider_egress| ro=true user=10001:10001 capdrop=[ALL] init=true cpus=5e8   mem=536870912  stop=120 …
collector-scheduler-1            collector_backend                          | ro=true user=10001:10001 capdrop=[ALL] init=true stop=90 …
collector-postgres-1             collector_backend | ro=false user=postgres capdrop=[ALL] init=true stop=60 secopt=[no-new-privileges:true]
collector-mongo-1                collector_backend | ro=false user=999:999  capdrop=[ALL] init=true stop=60 secopt=[no-new-privileges:true]
collector-minio-1                collector_backend | ro=false user=         capdrop=<no value> init=true stop=30 secopt=[no-new-privileges:true]
$ docker network inspect collector_backend --format '{{.Internal}}'        → true
$ docker network inspect collector_source_egress --format '{{.Internal}}'  → false
# egress-проба (TCP 1.1.1.1:53, timeout 3s):
parse-worker: external blocked OK: OSError          (лише backend, internal)
fetch-worker: external reachable OK                  (source-egress)
```

Adversarial — `docker compose stop postgres` → health має бути 503:

```text
$ docker compose stop postgres
$ docker compose exec -T api python -c '…'
HTTP 503
ready= False
  postgres False gaierror: [Errno -2] Name or service not known
  mongo True writable primary of replica set 'rs0'
  minio True liveness HTTP 200
$ docker compose exec -T fetch-worker python -m collector.api.health postgres minio
postgres: error (gaierror: [Errno -5] No address associated with hostname)
minio: ok (liveness HTTP 200)
exit=1
$ docker compose start postgres; sleep 20; …  → HTTP 200; ps: усі healthy

# без postgres довше за retries×interval (100 с): усі application-сервіси unhealthy, stateful healthy
api/discovery/export/fetch×2/maintenance/parse×2/projector/scheduler/translation  (unhealthy)
minio/mongo (healthy)
# після start postgres + 50 с: non-healthy = 0
```

### 1.5. Named volumes: restart / down / down -v (сценарій 3)

Маркери: postgres `CREATE TABLE wp00_marker … INSERT (1,'restart-marker')` (psql, пароль з
`/run/secrets`), mongo `wp00.marker {_id:1, note:'restart-marker'}` (mongosh, root
credentials з secret), MinIO bucket `wp00-marker` + object `marker.txt` через S3 API
(`curl --aws-sigv4`, credentials з secret).

```text
CREATE TABLE / INSERT 0 1 / true / bucket PUT 200 / object PUT 200
--- read back
postgres: restart-marker
mongo:    restart-marker
minio:    restart-marker (HTTP 200)

$ time docker compose restart                      real 0m7.584s  exit=0
(після 45 с) усі 14 довгоживучих healthy; one-shots: ensure-mongo Exited (0), migrate-postgres Exited (0)
ensure-mongo повторно: {"initiated_now": false, "event": "ensure_mongo.replica_set_ready", ...}
fetch-worker лог: {"signal": "SIGTERM", "event": "placeholder.stop_requested"} → {"event": "placeholder.stopped"}
postgres: restart-marker / mongo: restart-marker / minio: restart-marker (HTTP 200)

$ docker compose down                              # без -v
 Network collector_backend/provider_egress/source_egress Removed
volumes: collector_minio-data collector_mongo-config collector_mongo-data collector_postgres-data   (лишилися)
$ time docker compose up -d --wait --wait-timeout 300    real 0m52.402s exit=0; ensure-mongo initiated_now=false
postgres: restart-marker / mongo: restart-marker / minio: restart-marker (HTTP 200)

$ docker compose down -v --remove-orphans
 Volume collector_postgres-data/mongo-config/minio-data/mongo-data Removed; Networks Removed
volumes left: 0
$ docker compose up -d --wait   (fresh)  → маркери відсутні:
postgres: ERROR:  relation "wp00_marker" does not exist
mongo:    MongoServerError: node is not in primary or recovering state   (знято до завершення ensure-mongo; далі RS ok)
minio:    <Error><Code>NoSuchBucket</Code>…
```

### 1.6. Scale `fetch-worker` 2→1→4→1 (сценарій 4)

```text
$ docker compose up -d --no-recreate --scale fetch-worker=1
 Container collector-fetch-worker-2 Stopping/Stopped/Removing/Removed        exit=0
$ time docker compose up -d --no-recreate --wait --scale fetch-worker=4
 Container collector-fetch-worker-{4,2,3} Creating/Created/Starting/Started; fetch-worker-{1..4} Healthy
real 0m25.558s  exit=0
collector-fetch-worker-1..4  Up (healthy)  ports=[]     duplicate names: 0     parse-worker count unchanged: 2
$ docker compose up -d --no-recreate --scale fetch-worker=1
 Container collector-fetch-worker-{2,3,4} Stopping → Stopped → Removed      exit=0  elapsed=14s (< stop_grace_period 120s)
docker events: die collector-fetch-worker-3 exitCode=0 / -4 exitCode=0 / -2 exitCode=0
$ docker stop collector-fetch-worker-2     → 2151 ms, ExitCode=0 OOM=false
```

Профіль `browser` (COMPOSE_PROFILES=core,workers,browser): `up -d --wait` → контейнерів
browser-worker 0 (replicas 0); `--scale browser-worker=1` → `collector-browser-worker-1 Healthy`,
`shm=536870912 nets=collector_backend collector_source_egress`; `--scale browser-worker=0` → Removed.
Примітка: `docker compose --profile browser up …` при `COMPOSE_PROFILES=core,workers` у env
дає `service "browser-worker" depends on undefined service "postgres"` — прапорець `--profile`
**замінює** env, а не доповнює (див. знахідку L-2).

### 1.7. One-shots повторно (сценарій 6)

```text
$ docker compose run --rm ensure-mongo
{"replica_set": "rs0", "member": "mongo:27017", "initiated_now": false, "event": "ensure_mongo.replica_set_ready", ...}
exit=0
$ docker compose run --rm ensure-mongo collector db ensure-mongo --validators --indexes
{"…", "initiated_now": false, …}
not implemented: owned by WP-01B
exit=2
$ docker compose run --rm migrate-postgres
{"detail": "tcp postgres:5432 reachable (no SQL check yet; owner WP-01A)", …}
no migrations yet; owner WP-01A
exit=0
$ bash deploy/compose/secrets/init-secrets.sh     # ідемпотентно
skip  minio_root_password (exists) … skip  postgres_password (exists);  файл LF
```

### 1.8. Clean-host повторюваність (9 циклів `down -v` → `up -d --wait`)

```text
run 0 (перший після серії run/restart): exit=1 elapsed=26s   ← ЗБІЙ (див. H-1)
   docker events: start collector-mongo-1 → exec_die collector-mongo-1 exit=137 (healthcheck timeout)
                  → die collector-mongo-1 exit=48 → start collector-mongo-1 (restart policy) → health_status: healthy
   ps -a: migrate-postgres Exited (0); api/workers/scheduler/ensure-mongo — Created (не стартували)
   повторний `docker compose up -d --wait` (без down): exit=0, усі healthy
run 1: exit=0 elapsed=58s   run 2: exit=0 51s   run 3: exit=0 55s   run 4: exit=0 51s
run 5: exit=0 60s  run 6: exit=0 44s  run 7: exit=0 38s  run 8: exit=0 39s   (RestartCount mongo/postgres = 0)
```

### 1.9. Python-контракт, lint, pre-commit

```text
$ uv sync --frozen                Checked 51 packages
$ uv run ruff check .             All checks passed!
$ uv run ruff format --check .    71 files already formatted
$ uv run mypy src                 Success: no issues found in 25 source files
$ uv run pytest -m "not live"     (до моїх тестів) 170 passed, 1 skipped   (після) 210 passed, 1 skipped, 8 warnings in 42.02s
$ uv run pytest tests/unit/test_compose_config.py tests/integration/test_compose_render.py tests/unit/test_health.py \
    tests/unit/test_cli_compose_commands.py tests/integration/test_health_loopback.py    76 passed
$ uv run pre-commit run --all-files     11 hooks Passed (incl. gitleaks, markdownlint-cli2)
$ python -c "yaml.safe_load(ci.yml)"    jobs: ['python', 'pre-commit', 'secrets', 'docker']; docker env: COMPOSE_PROFILES=core,workers, COLLECTOR_IMAGE=collector:ci
$ grep -niE "password|token|secret|key" ci.yml | grep -v 'secrets\.'    → лише cache key і назва job
```

### 1.10. Teardown

```text
$ docker compose down -v --remove-orphans   (COMPOSE_PROFILES=core,workers,browser)
containers: 0   volumes: 0 (label) / 0 (collector_*)   networks collector_*: 0
```

Сторонній проєкт `puluj-g` не чіпався; його контейнери за час прогону були перезапущені
власником незалежно (в т.ч. `processor`/`analytics` Exited 137) — не наслідок команд
цього прогону (усі команди — лише project `collector`).

## 2. Acceptance-пункт → тест / сценарій → результат

| Acceptance (картка PR2, §16.3, R-51/R-55) | Тест / сценарій | Результат |
|---|---|---|
| Чистий host піднімає `core`+`workers` однією командою; всі healthy; workers живі | 1.4, 1.8 (9 циклів); CI job `docker` (`test_ci_runs_compose_config_and_image_build`, `test_ci_docker_job_order_build_sbom_scan_up_down`) | pass, з H-1 (1/9 інтермітентний збій, self-heal) |
| `restart` не втрачає дані named volumes (postgres/minio; + mongo) | 1.5 маркери restart → down → up → down -v | pass |
| `config` без `container_name` у workers | 1.1; `test_worker_is_scalable`, `test_no_container_name_anywhere`, render `test_workers_have_no_container_name_ports_or_volumes` | pass |
| без Docker socket у жодному сервісі | 1.1, 1.2 (`no-socket`), 1.4 mounts; `test_no_docker_socket_mount_anywhere`, render `test_no_docker_socket_and_no_published_ports`, `test_dev_override_only_adds_loopback_ports_and_environment` (M8) | pass |
| без public port крім gui/dev-override | 1.1, 1.4 (`docker ps` без host binding); `test_base_compose_publishes_no_ports`, `test_dev_override_binds_only_loopback` + render | pass |
| images non-root (`Config.User`), read-only rootfs у compose | 1.2, 1.4 inspect; `test_application_services_are_read_only_non_root_with_tmpfs`, `test_dockerfile_final_user_is_non_root_and_no_context_wide_copy` (M9), CI крок `docker inspect … User=10001:10001` | pass |
| жоден secret не потрапив у image (`history`, `run env`) | 1.3; `test_dockerfile_is_multistage_pinned_non_root_without_secrets`, `test_services_reference_only_declared_secrets_and_no_secret_env`, `test_each_secret_has_exactly_documented_consumers` (M6) | pass |
| Вимога 1: multi-stage, pinned digest, non-root, tmpfs, OCI labels, HEALTHCHECK | 1.2; `test_dockerfile_*`, `test_dockerfile_runtime_stage_has_no_uv_and_no_pip` | pass |
| Вимога 2: profiles/сервіси §7.5, workers без state, limits, grace ≥ 90 | 1.1; `test_services_match_spec_7_5_profile_table`, `test_every_service_has_resource_limits`, `test_worker_drain_contract` | pass |
| Вимога 3: мережі §7.5 | 1.1, 1.4 (runtime + egress-проба); `test_networks_match_spec_7_5`, `test_service_network_placement` | pass |
| Вимога 4: stateful pinned digest, named volumes, порти лише override 127.0.0.1 | 1.1, 1.5; `test_stateful_image_pinned_by_digest_and_named_volumes`, `test_named_volumes_only_for_stateful` | pass |
| Вимога 5: one-shots + readiness через `service_completed_successfully`; api 503 доки не ready | 1.4 (503 при stop postgres), 1.7; `test_readiness_waits_for_one_shots`, `test_health_endpoint_503_when_not_ready`, `test_endpoint_503_when_only_postgres_down` (M11) | pass |
| Вимога 6: secrets files, `.example` у git, реальні — ignored | 1.3; `test_secrets_are_files_with_examples_and_gitignored`, `test_secret_file_env_points_to_mounted_secret` | pass |
| Вимога 7: healthcheck = process + dependency; scheduler singleton placeholder | 1.4 (unhealthy без postgres → recovery); `test_application_healthchecks_name_a_critical_dependency` (M7), `test_scheduler_is_singleton`, `test_scheduler_command_is_placeholder` | pass |
| Вимога 8: документована clean-host команда (gui у PR3) | `docs/runbooks/clean-host-start.md`, 1.4/1.8 | pass |
| Вимога 9: CI `config --quiet`, `docker build`, SBOM, trivy | 1.9; `test_ci_*` (M10) | pass (YAML/структура); GitHub-прогін і syft/trivy — operationally unverified |
| §16.3 scale `fetch 1→4→1` без дублів, graceful stop | 1.6 | pass |
| §16.3 GUI/API не мають Docker socket (R-55) | 1.1, 1.2, 1.4 | pass |
| Сценарій 7: `COLLECTOR_SOURCE_REGISTRY` = файл, sha256 збігається з репо | 1.2 | pass |

## 3. Рівень §16.1 → тести

| Рівень | Тести |
|---|---|
| 1 Unit (CLI/конфігурація) | `tests/unit/test_compose_config.py` (33), `tests/unit/test_compose_config_adversarial.py` (21, додано), `tests/unit/test_health.py` (13), `tests/unit/test_health_adversarial.py` (20, додано), `tests/unit/test_cli_compose_commands.py` (21), оновлені `test_cli*.py`, `test_foundation_config.py` |
| 3 Integration (loopback) | `tests/integration/test_health_loopback.py` (5), `tests/integration/test_compose_render.py` (5; `docker compose config --format json`, тепер FAIL при невалідному проєкті) |
| 14 Docker | ручні сценарії 1.1–1.8 цього звіту (config, build, cold start/one-shots/health, named-volume restart, profile isolation `browser`, pinned digest, scale, socket, secrets) + CI job `docker` (`up -d --wait`, `down -v`) |
| 15 Scaling (частково: 1→4→1) | 1.6 (без rate-limit/lease — WP-01D) |
| 8 Security (secret leakage) | 1.3 (history/inspect/fs/gitleaks), `test_ensure_mongo_missing_secret_file_fails_without_leaking`, `test_db_ensure_mongo_cli_exit_1_when_set_name_mismatch` (секрет не в output) |

## 4. Додані тести (коміт `150441e`)

- `tests/integration/test_compose_render.py` — **посилено**: `docker compose config` з
  ненульовим кодом → `assert` (FAIL), skip лише якщо відсутній Compose plugin. Раніше
  невалідний проєкт (напр. M2 `container_name` на масштабованому worker) давав 5 skipped і був
  невидимий на цьому рівні.
- `tests/unit/test_compose_config_adversarial.py` (21): least-privilege мапа секретів
  (`postgres_password`→postgres, `mongo_root_password`→mongo+ensure-mongo, `mongo_keyfile`→mongo,
  `minio_*`→minio; api/workers/scheduler без secrets і без `*_FILE`); `*_FILE` env вказує на
  змонтований secret; healthcheck application-сервісів іменує критичну dependency (workers/
  scheduler — postgres; projector/export — mongo; fetch/parse/export — minio; api — HTTP 200 до
  health endpoint); drain-контракт workers (`restart: unless-stopped`, SIGTERM, `init`, залежність
  від `migrate-postgres`, обмежені логи); one-shots `restart: "no"`; `dev.override.yml` лише
  `ports`(127.0.0.1:`${DEV_*_PORT}`)/`environment`; Dockerfile (останній `USER` non-root, без
  `USER root`/`COPY . .`/`COPY deploy`/`--mount=type=secret`, `ENTRYPOINT []`, `HEALTHCHECK CMD [...]`,
  runtime без uv/pip); CI job `docker` (порядок config→build→sbom→trivy→up→down, `down -v`
  `if: always()`, `Config.User` перевірка, профілі core,workers, без docker.sock/privileged/
  hardcoded credentials, trivy `severity: CRITICAL` + `exit-code: 1`, `permissions: contents: read`).
- `tests/unit/test_health_adversarial.py` (20): повільний probe → `ok=False` «slow»; detail
  типізований і ≤ 200 символів; програмний виняток (RuntimeError) не маскується; endpoint 503
  при postgres down / усіх down, JSON, лише GET; `hello` `{}` / secondary / legacy `ismaster` →
  not ready; timeout з env і без credentials; `main()` дедуплікація/канонічний порядок,
  unknown → 2 без probe; `check_components` підмножина; ensure-mongo: відсутній secret file →
  ненульовий код без створення клієнта; `hello` без `isWritablePrimary` → TimeoutError;
  `replSetInitiate` failure → OperationFailure; CLI exit 1 при іншій назві RS без витоку
  пароля; `db migrate` без «not implemented» і без читання секретів;
  **`replSetGetStatus` code 13 → помилка без `replSetInitiate`** (закриває мутацію M13).

## 5. Mutation-перевірка (код тимчасово зламано → тест червоний → `git checkout --`)

| # | Мутація | Червоні тести | Статус |
|---|---|---|---|
| M1 | `parse-worker: read_only: false` | `test_application_services_are_read_only_non_root_with_tmpfs`, render `test_application_services_read_only_non_root` (2 failed, 36 passed) | відновлено |
| M2 | `fetch-worker: container_name: fetch-worker` | `test_worker_is_scalable[fetch-worker]`, `test_no_container_name_anywhere`; render-тести **skipped** (5) до фіксу → після фіксу 5 errors (fixture assert) | відновлено; фікс у коміті |
| M3 | `api: volumes: - /var/run/docker.sock:…` | `test_no_docker_socket_mount_anywhere`, `test_application_services_are_read_only…`, `test_named_volumes_only_for_stateful`, render `test_no_docker_socket_and_no_published_ports` (4 failed) | відновлено |
| M4 | `postgres: ports: - "5432:5432"` у base | `test_base_compose_publishes_no_ports`, render ×2 (3 failed) | відновлено |
| M5 | невідомий ключ у worker; `docker compose config --quiet` без profiles | exit=1 (schema validation працює і без profiles); container_name+replicas (M2) без profiles → exit=0 (семантична перевірка лише для активних profiles; CI задає `COMPOSE_PROFILES=core,workers` + крок з усіма profiles) | відновлено |
| M6 | `api: secrets: - mongo_keyfile` | `test_each_secret_has_exactly_documented_consumers`, `test_api_and_workers_have_no_secrets_in_wp00` | відновлено |
| M7 | `parse-worker` healthcheck → `collector version` (лише процес) | `test_application_healthchecks_name_a_critical_dependency`, `test_projector_export_check_mongo_fetch_parse_export_check_minio` | відновлено |
| M8 | `dev.override.yml`: `api: privileged: true` + volume `//./pipe/docker_engine` | `test_dev_override_only_adds_loopback_ports_and_environment` | відновлено |
| M9 | `USER root` наприкінці Dockerfile | `test_dockerfile_final_user_is_non_root_and_no_context_wide_copy` | відновлено |
| M10 | CI trivy `exit-code: "0"` | `test_ci_trivy_blocks_on_critical` | відновлено |
| M11 | health: `status_code = 200` завжди | `test_health_endpoint_503_when_not_ready`, `test_endpoint_503_when_only_postgres_down`, `test_endpoint_503_when_all_down_still_json` | відновлено |
| M12 | health mongo: `if False:` замість перевірки `isWritablePrimary` | `test_check_mongo_not_ok_before_replica_set_init`, `test_check_mongo_not_ready_unless_writable_primary[×3]` | відновлено |
| M13 | cli `ensure_mongo_replica_set`: `if exc.code != 94` → `if False` (будь-яка OperationFailure → replSetInitiate) | **до додавання тесту: 39 passed (мутація невидима)** — існуючі фейки кидають виняток на всіх командах; після `test_ensure_replica_set_does_not_initiate_on_non_94_status_error`: 1 failed | відновлено |

`git status` після кожної мутації — чистий (продуктивний код не змінено).

## 6. Знахідки

| Severity | Де | Опис |
|---|---|---|
| **high** | `docker-compose.yml` (`mongo`: entrypoint + healthcheck), acceptance «clean host однією командою» | Інтермітентний збій `docker compose up -d --wait` на чистому host: 1 із 9 циклів `down -v → up`. `docker events`: `start mongo → exec_die mongo exit=137 (healthcheck `mongosh` timeout 5s) → die mongo exit=48 → start (restart: unless-stopped) → healthy`; Compose трактує exit контейнера-залежності як фатальний і завершує `up` з кодом 1, api/workers лишаються `Created`. Стек self-heal-иться, повторний `up -d --wait` → 0. Ймовірна причина: healthcheck `mongosh` на `127.0.0.1:27017` під час init-фази entrypoint (тимчасовий mongod з `--fork` на loopback → shutdown → `exec mongod`) — exit 48 у mongod = «failed to set up listener / address in use». Не відтворилося у 8 наступних циклах; лог зниклого контейнера втрачено (`down -v`). Рекомендація власнику: зробити healthcheck нечутливим до init-фази (наприклад, перевіряти `hello.setName`/`--replSet` або підключатися до не-loopback IP контейнера, який слухає лише фінальний mongod з `--bind_ip_all`), збільшити `healthcheck.timeout`, і/або у runbook/CI задокументувати повтор `up -d --wait` як штатний. Вважати `operationally unverified` на Linux CI до першого прогону. |
| medium | `tests/integration/test_compose_render.py` (виправлено у `150441e`) | Невалідний Compose-проєкт перетворювався на `pytest.skip` (M2: `container_name` на масштабованому worker → 5 skipped). Тепер — FAIL; skip лише без Compose plugin. |
| medium | `tests/unit/test_cli_compose_commands.py` (закрито новим тестом) | `test_ensure_replica_set_reraises_other_operation_failures` не відрізняв «reraise на replSetGetStatus» від «reraise на replSetInitiate»: мутація M13 (будь-який код → ініціалізація) лишалася зеленою. Додано тест, де `replSetGetStatus` → code 13, а `replSetInitiate` успішний: має бути помилка без initiate. |
| low | `src/collector/cli.py:api` / `docker compose stop api` | api завершується з кодом 143 (uvicorn ≥ 0.30 після graceful shutdown повторно піднімає SIGTERM), хоча логи показують «Application shutdown complete» за 3.8 с. Workers/scheduler — 0. Косметично, але для §7.5 «SIGKILL = fault case» варто нормалізувати (WP-11A при заміні стаба). |
| low | `docs/runbooks/clean-host-start.md` §«Масштабування», `docker-compose.yml` коментар | `docker compose up -d --no-recreate --scale browser-worker=1  # після canary, профіль browser` — не сказано, що профіль треба додати до `COMPOSE_PROFILES` (`core,workers,browser`); `--profile browser` як прапорець **замінює** env-профілі → `depends on undefined service "postgres"`. |
| low | `Dockerfile` (`COPY … source-registry.yaml`), збірка з Windows-контексту | Файл потрапляє в image як `0755` (коментар у Dockerfile обіцяє 0644); на Linux-контексті буде 0644. Не впливає на безпеку (root-owned, rootfs read-only); можна додати `--chmod=0644` у `COPY`. |
| low | `.github/workflows/ci.yml` trivy `ignore-unfixed: true` | §13 вимагає «critical CVE блокує release або має датоване risk acceptance»; unfixed CRITICAL пропускаються мовчки без датованого acceptance. Зафіксувати правило в ADR-0002/ризиках або прибрати `ignore-unfixed` для CRITICAL. |
| low | `docker-compose.yml` `name: collector`, фіксовані `name:` мереж `collector_*` | Проєкт не ізолюється назвою теки: два checkout-и на одному host (напр., паралельні worktrees) конфліктують за project/network names. Для CI/single-host прийнятно; для паралельних тестувальників — `COMPOSE_PROJECT_NAME` не допоможе, бо `name:` у файлі має пріоритет. |
| info | `docker compose config --quiet` без profiles | Усі сервіси мають profiles → без `COMPOSE_PROFILES` команда валідує лише схему (M5: невідомий ключ → exit 1, але `container_name`+replicas → exit 0). У CI env задає `core,workers` і є окремий крок з усіма profiles — достатньо; картка/§16.2 команду наводять без profiles. |
| info | SBOM / vulnerability scan | Локально `syft`/`trivy`/`docker sbom` відсутні; `docker scout` є, але потребує мережі (заборонено). CI-кроки `anchore/sbom-action@v0.24.2`, `aquasecurity/trivy-action@v0.36.0` — `operationally unverified` до першого прогону PR (як і зазначив реалізатор). |

## 7. Звірка з `implementation-pr2.md` (прочитано після прогону)

Заявлене підтверджується: config/build/up/ps/health/exec виводи збігаються за змістом;
non-root/read-only/cap_drop/StopTimeout за `docker inspect` — ідентичні; секретів у
history/env немає; restart із маркерами, scale 4×fetch, `down -v` → 0 volumes; 170 passed
до моїх тестів. Розбіжності: (1) реалізатор не спостерігав інтермітентний збій clean-host
`up --wait` (H-1) — у нього два прогони, у мене 1/9; (2) «render-тести skip без docker CLI або
секретів» — skip також ховав невалідний проєкт (виправлено); (3) exit-код api 143 при stop не
згадано (low). Ризики 1–9 реалізатора — адекватні; ризик 5 (2 HIGH unfixed) підтверджено
логікою CI (`ignore-unfixed`), локально не переперевірявся (потрібна мережа).

## 8. Вердикт

**pass** — усі acceptance-пункти PR2 підтверджені реальними сценаріями; 210 тестів зелені;
13 мутацій → червоні (2 прогалини закрито доданими тестами). Єдина суттєва знахідка — H-1
(інтермітентний збій `up --wait` через рестарт mongo на першому boot, 1/9, self-healing) —
рекомендується виправити/задокументувати до merge або зафіксувати як відомий ризик у
ADR-0002 з перевіркою на Linux CI.
