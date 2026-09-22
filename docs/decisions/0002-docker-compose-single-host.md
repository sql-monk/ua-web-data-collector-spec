# ADR-0002: Single-host Docker Compose MVP (WP-00 PR2)

| Поле | Значення |
|---|---|
| Date | 2026-09-22 |
| Owner | WP-00 |
| Status | accepted |

## Context

ТЗ §1 п.8/10 фіксує, що MVP працює через Docker Compose на одному Linux-хості, а всі
application-компоненти постачаються OCI images. §7.5 задає profiles, мережі, вимоги до
контейнерів (read-only, non-root, secrets через files, healthcheck + readiness, pinned
digests, SBOM/scan), §7.6 — default replicas ролей, §13 — non-root/без Docker socket, §16.2 —
команди-контракт (`docker compose config --quiet`, `build --pull`, `up -d --wait`,
`--scale`), §16.3 — clean-host start і restart без втрати named volumes. REVIEW.md R-51
(контейнерна поставка, clean-host start) і R-55 (без Docker socket у GUI/API) — регресії, які
цей PR закриває для foundation.

Відкриті питання §20, для яких PR2 приймає default: **Q-006** (інфраструктурний бюджет/SLO →
один хост MVP, SLO §2.4) і **Q-013** (production deployment mode → Compose для
local/single-host MVP; Swarm mode для GUI-керованого масштабування — WP-01D, поза PR2).

Рішення ухвалювалися й перевірялися на Windows 11 + Docker Desktop 29.8 (Linux containers,
Compose v5.5.1); еталон acceptance — Linux CI (`ubuntu-latest`, job `docker`).

## Decision

### Один image `collector`, різні `command`

`Dockerfile` — multi-stage: builder `python:3.13-slim` + бінарник `uv` (обидва pinned
tag@digest) робить `uv sync --frozen --no-dev` у `/opt/collector`; runtime — той самий
`python:3.13-slim` без uv/pip/ensurepip (їх vendored-пакети лише додавали CVE у scan), користувач
`10001:10001`, `HOME=/tmp`, `HEALTHCHECK collector version`, OCI labels з Git SHA
(`org.opencontainers.image.revision`) і schema version (`ua.collector.schema-version`,
placeholder до WP-01C). API, scheduler, controller, CLI, one-shots і всі worker roles — один
image з різними `command`; browser worker — окремий image (WP-02 PR3), у PR2 —
`browser-worker` з `replicas: 0` на image `collector` і TODO.

Image також містить `docs/research/source-registry.yaml` за шляхом
`/app/config/source-registry.yaml` (`ENV COLLECTOR_SOURCE_REGISTRY`) — approved dependency
WP-01C: контракти валідують `source_id` проти реєстру.

### Compose profiles і мережі за §7.5

`docker-compose.yml` (`name: collector`) оголошує profiles `core`, `workers`, `browser`;
`gui` (PR3), `observability` (WP-12) і `tools` (WP-11A/WP-14) — зарезервовані коментарем,
бо Compose не має декларації профілю без сервісу. **Profile `core` обов'язковий** для
будь-якого запуску: workers залежать від `postgres`/`migrate-postgres`, тому
`--profile workers` без `core` — невалідний проєкт (задокументовано у runbook;
`COMPOSE_PROFILES=core,workers` у shell).

Мережі: `backend` (internal, усі сервіси), `ingress` (лише `api`; `gui` у PR3),
`source-egress` (discovery/fetch/browser), `provider-egress` (translation), `telemetry`
(internal, WP-12; поки без сервісів — `docker compose config` її не рендерить). Parse,
projector, export, maintenance, scheduler, one-shots і stateful — лише `backend`, тобто без
egress.

### Application containers

Спільний фрагмент `x-collector-runtime`: `read_only: true`, `tmpfs /tmp` (64 МБ, 1777),
`user 10001:10001`, `cap_drop: [ALL]`, `no-new-privileges`, `init: true`, json-file logging з
ротацією, `deploy.resources.limits` для кожного сервісу (small 0.5 CPU/512M, medium 1/1G,
large 2/2G). Workers (`x-worker`): без `container_name`/`ports`/`volumes`, label
`collector.scalable=true` (allowlist Swarm controller, §7.5), `stop_grace_period: 120s`
(≥ 90 с), `restart: unless-stopped`, replicas за §7.6 (discovery 1, fetch 2, parse 2,
projector 1, translation 1, export 1, maintenance 1, browser 0). `scheduler` — `replicas: 1`,
`collector.scalable=false`.

### Stateful services

`postgres:18`, `mongo:8.0`, `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` — pinned
multi-arch index digest, named volumes (`postgres-data`, `mongo-data`, `mongo-config`,
`minio-data`), без published ports у base-файлі; `deploy/compose/dev.override.yml` публікує
5432/27017/9000/9001/8000 лише на `127.0.0.1`. PostgreSQL і MongoDB працюють non-root
(`postgres`, `999:999`) із `cap_drop: ALL`. **MinIO лишається root** (прийняте відхилення):
vendor image тримає `/data` під root, а non-root потребує окремого chown-init контейнера;
MinIO — не application image (§13 «де можливо»). MinIO Docker Hub образи більше не
оновлюються; використовується `quay.io`.

MongoDB — single-member replica set `rs0` з `--keyFile` (auth). Keyfile — Docker secret;
file-secrets у Compose bind-mount-яться з правами хоста (на Docker Desktop — 0777, `mode`
у long syntax ігнорується), тому entrypoint копіює його в tmpfs `install -m 0400` перед
`docker-entrypoint.sh mongod`. Root user створює entrypoint образу з
`MONGO_INITDB_ROOT_PASSWORD_FILE`; `replSetInitiate` виконує one-shot `ensure-mongo`.

### One-shots і readiness

`migrate-postgres` = `collector db migrate` — у PR2 TCP-перевірка PostgreSQL і exit 0 з
«no migrations yet; owner WP-01A» (без credentials/SQL: драйвер обирає WP-01A).
`ensure-mongo` = `collector db ensure-mongo` — реально й ідемпотентно ініціалізує RS
(`replSetGetStatus` → код 94 → `replSetInitiate` → чекає `hello.isWritablePrimary`);
`--validators/--indexes` після ініціалізації дають стаб WP-01B (код 2), тому Compose
викликає команду без прапорців до WP-01B. `api` і workers стартують лише після
`service_completed_successfully` one-shots і `service_healthy` stateful — це readiness §7.5.

### Health

`collector.api.health` — стаб FastAPI лише з `GET /api/v1/health/components`
(`openapi/docs` вимкнені; контракт §9.10 — WP-11A): `postgres` — TCP; `mongo` — `hello` без
auth, `ok` лише для writable primary (health не потребує Mongo credentials, §13); `minio` —
`GET /minio/health/live`. `ready` = усі `ok`; HTTP 200/503 — саме це читає Docker healthcheck
`api` і `up --wait`. Той самий модуль (`python -m collector.api.health <components>`) —
healthcheck workers/scheduler «process + критична dependency» (fetch/parse: postgres+minio;
projector: postgres+mongo; export: усі три; решта: postgres).

### Placeholders замість стабів для довгоживучих команд

Acceptance картки вимагає, щоб workers були «живі й не падають», а `--wait` не завершується
для контейнера з exit 2. Тому `collector worker <role>` і `collector scheduler` у PR2 —
placeholder-процеси: друкують той самий стаб-рядок `not implemented: owned by WP-01D` у
stderr при старті (скрипти відрізняють placeholder від реалізації, правило ADR-0001), далі
логують heartbeat і завершуються кодом 0 по SIGTERM/SIGINT (handler встановлюється явно, бо
PID 1 ігнорує SIGTERM). `controller` лишається стабом (Swarm mode, поза Compose MVP).

### Секрети

`deploy/compose/secrets/*.example` у git; реальні файли — `.gitignore`;
`init-secrets.sh` копіює приклади і генерує keyfile (`openssl rand -base64 756`). Жодного
секрету в `ARG`/`ENV`/image layer (`docker history`, `env`), у `environment` лише `*_FILE`.
Файли мають бути readable для uid 10001/999 (0644 для локальної розробки; production —
Swarm secrets, WP-01D).

### CI

Job `docker` у `.github/workflows/ci.yml`: `init-secrets.sh` → `docker compose config
--quiet` (без і з усіма profiles) → `docker build` (перевірка `Config.User`) →
SBOM `anchore/sbom-action` (syft, SPDX artifact) → `aquasecurity/trivy-action`
(`severity: CRITICAL`, `ignore-unfixed`, `exit-code: 1` — §13 «critical CVE блокує») →
`docker compose up -d --wait` core+workers → `ps`/health → `down -v`. Локально SBOM/CVE —
`docker scout sbom`/`docker scout cves` (0 critical, 2 high без fix у Debian trixie:
perl, zlib — прийнято, дата 2026-09-22, переглядати при оновленні digest).

## Consequences

- Clean-host старт core+workers — одна команда після `init-secrets.sh`; `gui` додається у
  PR3 і закриває acceptance §16.3 повністю.
- Власники WP-01A/WP-01B/WP-01D/WP-11A замінюють тіла `db migrate`, `db ensure-mongo
  --validators --indexes`, `worker`, `scheduler`, `api`/`collector.api.health` без зміни
  Compose (лише `command` one-shots за потреби: `alembic upgrade head`, прапорці).
- `fastapi`, `uvicorn`, `pymongo` увійшли до runtime-залежностей у PR2 (тест
  `FORBIDDEN_FOUNDATION_DEPS` оновлено); драйвер PostgreSQL не обирався — TCP-перевірка
  свідомо не валідує credentials, це робить WP-01A міграціями.
- MinIO root і bind-mount секретів з правами хоста — відомі відхилення; Swarm/production
  вимагають окремого ADR (WP-01D) із Docker secrets і non-root MinIO.
- `docker compose --profile workers` без `core` не працює — свідомо, щоб не дублювати
  stateful у кількох profiles.
- Windows: локальні перевірки потребують `MSYS_NO_PATHCONV=1` у Git Bash для `/tmp` у
  `docker run --tmpfs`; файли з Windows-контексту потрапляють у image з mode 0755 — на
  Linux CI 0644.

## Related

- Реалізація: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `deploy/compose/**`,
  `src/collector/api/health.py`, `src/collector/cli.py`, `.github/workflows/ci.yml`.
- Тести: `tests/unit/test_compose_config.py`, `tests/unit/test_health.py`,
  `tests/unit/test_cli_compose_commands.py`, `tests/integration/test_compose_render.py`,
  `tests/integration/test_health_loopback.py`.
- Документи: `docs/runbooks/clean-host-start.md`, `deploy/compose/README.md`,
  `docs/plan/reports/WP-00/implementation-pr2.md`; ADR-0001 (стаби, логування).
