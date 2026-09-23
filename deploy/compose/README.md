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
| `gui` | `gui` (React SPA + nginx reverse proxy `/api`) | PR3 — єдиний публічний порт стека |
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
| `ingress` | ні | лише `gui` — єдиний published port стека |
| `frontend` | так | `gui` ↔ `api` (gui не має доступу до `backend`) |
| `source-egress` | ні | `discovery-`, `fetch-`, `browser-worker` |
| `provider-egress` | ні | `translation-worker` |
| `telemetry` | так | observability (WP-12); поки без сервісів |

Parse/projector/export/maintenance/scheduler/one-shots — лише `backend`, без egress.
`api` у `ingress` **не** входить (gate 3 CR-14/SEC L-2): назовні дивиться лише `gui`, а
`gui`↔`api` живуть в окремій internal-мережі `frontend`. Тому `gui` не має мережевого
доступу ні до PostgreSQL/MongoDB/MinIO, ні до workers.

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
| `postgres_dsn` | `migrate-postgres` (`COLLECTOR_POSTGRES_DSN_FILE`) — DSN міграційної ролі; будується з `postgres_password` (§13: migration role не у runtime-процесах) |

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
| `COLLECTOR_GUI_IMAGE` | `collector-gui:dev` | tag image GUI (CI: `collector-gui:ci`) |
| `GUI_PORT` | `80` | публічний порт хоста → 8080 у контейнері gui |
| `COLLECTOR_GIT_SHA`, `COLLECTOR_SCHEMA_VERSION`, `COLLECTOR_VERSION`, `COLLECTOR_CREATED` | `unknown`/placeholder | build args → OCI labels |
| `COLLECTOR_LOG_LEVEL` | `INFO` | рівень structlog у контейнерах |
| `POSTGRES_DB`, `POSTGRES_USER` | `collector` | ім'я БД/ролі; пароль — лише secret |
| `MONGO_ROOT_USERNAME` | `collector_root` | root user Mongo; пароль — лише secret |
| `DEV_*_PORT` | 5432/27017/9000/9001/8000 | порти override |

## `gui/nginx.conf` — конфіг operator GUI (WP-00 PR3)

`deploy/compose/gui/nginx.conf` не монтується у контейнер, а копіюється в image на збірці
(`web/Dockerfile`, additional build context `gui-conf`) як `/etc/nginx/conf.d/default.conf` —
тому rootfs лишається read-only і конфіг не можна підмінити на хості. Що він задає:

- restrictive CSP (`default-src 'none'`, `script-src 'self'`, `connect-src 'self'`) і
  security headers (`nosniff`, `DENY`, `no-referrer`, COOP/COEP/CORP, HSTS) — §13;
- SPA fallback `try_files … /index.html` (маршрути React Router віддають 200, не 404);
- same-origin reverse proxy `/api` → `api:8000` з вимкненою буферизацією (SSE у WP-11C);
- публічний `/api/v1/health/components` віддає лише `{"status":"ready"}` / 503
  `{"status":"not_ready"}`: детальний звіт компонентів назовні не виходить до OIDC (WP-11A).
  Узагальнення робить `auth_request` до внутрішнього location — і саме тому «успішну»
  відповідь формує named location через `try_files`, а не `return` (той спрацював би у
  rewrite-фазі, ще до auth_request, і endpoint завжди повертав би `ready`).

`nginx` слухає 8080 (non-root не може <1024); наверх публікується `${GUI_PORT:-80}:8080`.

### Два різні health-и `gui` — і чому `gui` падає разом з `api`

| Endpoint | Хто питає | Що означає |
|---|---|---|
| `GET /healthz` | людина, зовнішній балансувальник | **liveness**: nginx живий і віддає відповідь; про `api` нічого не каже |
| `GET /api/v1/health/components` | Docker healthcheck сервісу `gui` | **readiness**: nginx живий **і** `api` відповів 2xx на внутрішній `auth_request` |

Healthcheck у `docker-compose.yml` свідомо питає **другий** — це вимога §7.5 «healthcheck
кожного сервісу перевіряє process + критичну dependency». Наслідки, які треба знати
експлуатації:

- при недоступному `api` контейнер `gui` **стає `unhealthy`** приблизно за 45 с
  (`interval 15s × retries 3`), хоча статику далі роздає нормально (перевірено: `curl /`
  → 200, `curl /api/v1/health/components` → 503 `{"status":"not_ready"}`);
- тому `docker compose up -d --wait` на деградованому стеку **впаде** з
  `container collector-gui-1 is unhealthy` — це не проблема GUI, а сигнал про `api`;
- відновлення автоматичне: після повернення `api` в `healthy` `gui` стає `healthy` сам
  приблизно за 20 с, перезапуск не потрібен;
- якщо потрібен саме liveness (напр. зовнішній LB, який не має знімати трафік зі статики
  через збій API), використовуйте `GET /healthz` — він навмисно не залежить від `api`.

## `postgres/init` — SQL першого старту (WP-01A)

`deploy/compose/postgres/init/` монтується у `postgres` як
`/docker-entrypoint-initdb.d:ro`; entrypoint виконує звідти `*.sql`/`*.sh` **лише** коли
data directory порожня (перший `up` після `down -v`). NOLOGIN group-ролі §13
(`01-roles.sql`) належать WP-01A і з'являться після merge WP-01A PR1 — WP-00 сюди SQL не
копіює. Деталі — `deploy/compose/postgres/init/README.md`.

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
