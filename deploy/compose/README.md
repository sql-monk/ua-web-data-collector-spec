# deploy/compose — Docker Compose single-host MVP

Base-файл — `docker-compose.yml` у корені репозиторію (Compose шукає його там за
замовчуванням). Тут — override для розробки, секрети та їхні приклади. Рішення — ADR-0002
(`docs/decisions/0002-docker-compose-single-host.md`); покрокова інструкція —
`docs/runbooks/clean-host-start.md`.

## Profiles (§7.5)

| Profile | Сервіси | Стан |
|---|---|---|
| `core` | `postgres`, `mongo`, `minio`, one-shot `migrate-postgres`, `ensure-mongo`, `api`, `scheduler` | PR2 |
| `workers` | `discovery-`, `fetch-`, `parse-`, `projector-`, `translation-`, `export-`, `maintenance-worker` | PR2 (placeholder-процеси до WP-01D) |
| `browser` | `browser-worker`, 0 replicas | PR2 placeholder на image `collector`; окремий image — WP-02 PR3 |
| `gui` | `gui` | PR3 |
| `observability` | otel-collector, prometheus, grafana, loki | WP-12 |
| `tools` | admin one-shot, release verifier, DuckDB | WP-11A/WP-14 |

`core` потрібен завжди (workers залежать від `postgres`/`migrate-postgres`):
`export COMPOSE_PROFILES=core,workers`. Прапорець `--profile X` **замінює** `COMPOSE_PROFILES`
(не доповнює), тому для browser: `COMPOSE_PROFILES=core,workers,browser docker compose up -d
--no-recreate --scale browser-worker=1`.

## Мережі (§7.5)

| Мережа | internal | Хто |
|---|---|---|
| `backend` | так | усі сервіси (DB/object store, черга) |
| `ingress` | ні | `api` (і `gui` у PR3 — єдиний published port) |
| `source-egress` | ні | `discovery-`, `fetch-`, `browser-worker` |
| `provider-egress` | ні | `translation-worker` |
| `telemetry` | так | observability (WP-12); поки без сервісів |

Parse/projector/export/maintenance/scheduler/one-shots — лише `backend`, без egress.

## Секрети (§7.5, §13, FR-013)

`secrets/*.example` — у git; реальні файли (`secrets/<name>` без суфікса) — у `.gitignore`.
`./deploy/compose/secrets/init-secrets.sh` створює відсутні файли: паролі генерує
випадково (`openssl rand -hex 24`, fallback `python3 secrets`/`/dev/urandom`), `mongo_keyfile`
— `openssl rand -base64 756`; з прикладу копіюється лише не-секретне `minio_root_user`.
Файли отримують 0644 свідомо: Compose bind-mount-ить їх у `/run/secrets/<name>` з правами
хоста, а читають non-root uid контейнерів (10001 — collector, 999 — postgres/mongo);
0600 → `Permission denied` на Linux. У `environment` сервісів дозволені лише
`*_FILE`-посилання.

| Secret | Споживач |
|---|---|
| `postgres_password` | `postgres` (`POSTGRES_PASSWORD_FILE`) |
| `mongo_root_password` | `mongo` (`MONGO_INITDB_ROOT_PASSWORD_FILE`), `ensure-mongo` |
| `mongo_keyfile` | `mongo` (`--keyFile`, копія в tmpfs 0400) |
| `minio_root_user`, `minio_root_password` | `minio` (`MINIO_ROOT_*_FILE`) |

## Override для розробки

`dev.override.yml` публікує порти лише на `127.0.0.1`: 5432, 27017, 9000/9001 (MinIO API/console,
console вмикається), 8000 (health API):

```bash
docker compose -f docker-compose.yml -f deploy/compose/dev.override.yml --profile core up -d --wait
```

Base-файл портів не публікує; на shared/production host override не використовувати.

Клієнти MongoDB з хоста через override мають підключатися з `directConnection=true`
(`mongosh "mongodb://127.0.0.1:27017/?directConnection=true"`): конфігурація replica set
містить `members[0].host = mongo:27017`, і RS-aware клієнт після discovery спробує
з'єднатися з `mongo:27017`, який з хоста не резолвиться, — зависне на server selection.

## Змінні оточення Compose

| Змінна | Типово | Призначення |
|---|---|---|
| `COLLECTOR_IMAGE` | `collector:dev` | tag image для application-сервісів (CI: `collector:ci`) |
| `COLLECTOR_GIT_SHA`, `COLLECTOR_SCHEMA_VERSION`, `COLLECTOR_VERSION`, `COLLECTOR_CREATED` | `unknown`/placeholder | build args → OCI labels |
| `COLLECTOR_LOG_LEVEL` | `INFO` | рівень structlog у контейнерах |
| `POSTGRES_DB`, `POSTGRES_USER` | `collector` | ім'я БД/ролі; пароль — лише secret |
| `MONGO_ROOT_USERNAME` | `collector_root` | root user Mongo; пароль — лише secret |
| `DEV_*_PORT` | 5432/27017/9000/9001/8000 | порти override |

## Обмеження: фіксоване ім'я проєкту `collector`

`docker-compose.yml` задає `name: collector` і фіксовані назви мереж `collector_*`, тому
project name **не** залежить від назви теки і `COMPOSE_PROJECT_NAME` його не перекриває
(`name:` у файлі має пріоритет). Два checkout-и/worktree на одному host (напр. паралельні
тестувальники) ділять один project, ті самі volumes `collector_*` і конфліктують за мережі.
Для single-host MVP і CI (один runner = один checkout) це свідомо: стабільні назви volumes
і мереж потрібні runbook-ам. Для паралельних стеків на одному host — окремий override з
іншим `name:` та назвами мереж (`docker compose -f docker-compose.yml -f my.override.yml`),
або різні Docker context/host.

## Перевірки (tests)

- `tests/unit/test_compose_config.py` — інваріанти base-файлу без Docker;
- `tests/integration/test_compose_render.py` — `docker compose config --format json`
  (потрібен docker CLI, секрети з `init-secrets.sh`);
- CI job `docker` — build, SBOM, trivy, `up -d --wait`, `down -v`.
