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
MinIO — не application image (§13 «де можливо»). Gate 3 (SEC M-1): root, але **без
capabilities** (`cap_drop: ALL`, `no-new-privileges`) і з **read-only rootfs** + tmpfs `/tmp`
(`MC_CONFIG_DIR`/`MINIO_CONFIG_DIR` у tmpfs) — запис лише у volume `/data`; перевірено
`up --wait` + `mc mb/pipe/cat`. Залишкове відхилення — лише uid 0 (owner WP-01D, ADR для
Swarm/production, 2026-09-22). MinIO Docker Hub образи більше не оновлюються; використовується
`quay.io`.

MongoDB — single-member replica set `rs0` з `--keyFile` (auth). Keyfile — Docker secret;
file-secrets у Compose bind-mount-яться з правами хоста (на Docker Desktop — 0777, `mode`
у long syntax ігнорується), тому entrypoint копіює його в tmpfs `install -m 0400` перед
`docker-entrypoint.sh mongod`. Root user створює entrypoint образу з
`MONGO_INITDB_ROOT_PASSWORD_FILE`; `replSetInitiate` виконує one-shot `ensure-mongo`.

**Healthcheck mongo стійкий до init-фази entrypoint** (gate 2, знахідка H-1: 1/9 clean-host
`up --wait` падав, бо healthcheck `mongosh … ping` на loopback потрапляв у тимчасовий
init-mongod, зависав до timeout → exit 137 → mongod die 48 → restart, а Compose трактує exit
залежності як фатальний). Init-mongod слухає лише `127.0.0.1` і запущений без `--replSet`,
тому healthcheck (а) з'єднується з першою IPv4-адресою контейнера — `--host "$ip"`, де `ip`
явно вибирається з `hostname -I | tr ' ' '\n' | grep -m1 -E '^[0-9]+(\.[0-9]+){3}$'` (gate 3,
знахідка CR-1: `hostname -i` на dual-stack хості/мережі віддає кілька адрес, IPv6 першою, що
робить `--host` невалідним; `hostname -I` явно фільтрується на першу IPv4 з перевіркою
`test -n "$ip"`), — цю адресу слухає лише фінальний mongod з `--bind_ip_all`, і (б) вимагає
від `hello` ознаки члена RS
(`isreplicaset` до `replSetInitiate` або `setName` після); `start_period 40s`, `interval 10s`,
`timeout 10s`, `retries 10`. Свідоме відхилення від рекомендованого gate-ом
`isWritablePrimary || secondary`: до `replSetInitiate` член не є ні primary, ні secondary,
а `ensure-mongo` чекає `service_healthy` — така перевірка дала б deadlock. Healthy = «фінальний
mongod з --replSet приймає команди»; primary перевіряють `ensure-mongo` і health api.
Відтворення: 10/10 циклів `down -v → up -d --wait` зелені (`implementation-pr2.md`,
«Виправлення після gate 2»). Залишковий ризик (дата 2026-09-22, owner WP-00/WP-01B):
на Linux CI не прогнано; якщо збій повториться, повторний `up -d --wait` — штатна
ідемпотентна дія (runbook, «Типові проблеми»).

### One-shots і readiness

`migrate-postgres` = `collector db migrate` — у PR2 TCP-перевірка PostgreSQL і exit 0 з
«no migrations yet; owner WP-01A» (без credentials/SQL: драйвер обирає WP-01A).
`ensure-mongo` = `collector db ensure-mongo` — реально й ідемпотентно ініціалізує RS
(`replSetGetStatus` → код 94 → `replSetInitiate` → чекає `hello.isWritablePrimary`);
`--validators/--indexes` після ініціалізації дають стаб WP-01B (код 2), тому Compose
викликає команду без прапорців до WP-01B. `api` і workers стартують лише після
`service_completed_successfully` one-shots і `service_healthy` stateful — це readiness §7.5.

### Approved dependency WP-01A (після gate 3, дата 2026-09-22)

`docs/plan/deps/WP-01A-to-WP-00.md`, п.2–3 (ухвалено оркестратором, внесено **після** HEAD
`eb280a6`, на якому працював пострев'юер — змін цього розділу немає у `spec-review-pr2.md`):
`postgres` монтує `./deploy/compose/postgres/init:/docker-entrypoint-initdb.d:ro` (SQL ролей
§13 — власність WP-01A, з'явиться після merge його PR1; тека у WP-00 порожня, лише
`.gitkeep`/`README.md`); digest `postgres:18@sha256:86c951e0…` збігається з CI/testcontainers
WP-01A; `migrate-postgres` отримує Docker secret `postgres_dsn` через
`COLLECTOR_POSTGRES_DSN_FILE` — **лише цей one-shot** (§13: migration role не
використовується runtime-процесами; per-role DSN для api/workers додають WP-01A/WP-01D);
`init-secrets.sh` будує DSN із того самого згенерованого `postgres_password`. `Dockerfile`
копіює `alembic.ini` і `migrations/` опційним glob (`alembic.in[i]`, `migration[s]/`) і задає
`COLLECTOR_ALEMBIC_INI=/app/alembic.ini`: доки файлів немає, BuildKit робить крок no-op;
після merge WP-01A вони потрапляють в image без зміни Dockerfile. Команду `collector db
roles` не додано (її ще немає в main) — лише коментар біля `command` у compose.

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
`init-secrets.sh` генерує випадкові паролі (`openssl rand -hex 24`; gate 3 SEC L-3 — жодних
default credentials) і keyfile (`openssl rand -base64 756`); з прикладу копіюється лише
не-секретний `minio_root_user`. Жодного секрету в `ARG`/`ENV`/image layer (`docker history`,
`env`), у `environment` лише `*_FILE`.

**Права файлів секретів — 0644 (risk acceptance, 2026-09-22, owner WP-01D):** Compose
bind-mount-ить file-secrets у `/run/secrets/<name>` з правами хоста (перевірено: `uid/gid/mode`
long syntax ігнорується, Docker Desktop показує 0777), а читають їх non-root uid контейнерів
(postgres/mongo 999, collector 10001) — 0600 від користувача хоста дає EACCES на Linux.
Альтернатива `secrets.<name>.environment` (Compose копіює значення в контейнер як 0444 root,
перевірено) потребує секретів у env/`.env` хоста, що суперечить §7.5 «не committed .env» за
духом; прийнято 0644 для single-host MVP, production — Swarm secrets (Q-013).

### CI

Job `docker` у `.github/workflows/ci.yml`: `init-secrets.sh` → `docker compose config
--quiet` (без і з усіма profiles) → `docker build` (перевірка `Config.User`) →
SBOM `anchore/sbom-action` (syft, SPDX artifact) → `aquasecurity/trivy-action` двічі:
`severity: CRITICAL, exit-code: 1` **без** `ignore-unfixed` (§13: критичний CVE блокує, навіть
без fix — тоді потрібен датований risk acceptance тут, а не мовчазний skip) і
`severity: HIGH, ignore-unfixed: true, exit-code: 1` (HIGH із доступним fix блокує → оновити
digest/залежність; unfixed HIGH — risk acceptance нижче) → `docker compose up -d --wait`
core+workers → `ps`/health → `down -v`. Локально SBOM/CVE — `docker scout sbom`/`docker scout
cves`.

**Risk acceptance (дата 2026-09-22, owner WP-13 — security/log tests §16.1 п.8;
тригери перегляду: кожне оновлення digest `python:3.13-slim`; **злиття WP-02 fetch** (zlib
почне розпаковувати недовірений gzip sitemap/body — §13 decompression bomb, обґрунтування
«довірені дані» перестає діяти); не пізніше 2026-12-22):**
2 HIGH без fix у base image Debian 13 trixie — `perl 5.40.1-6+deb13u1` (CVE-2026-82560) і
`zlib 1:1.3.dfsg+really1.3.1-1` (CVE-2026-85091); 0 CRITICAL. Обидва — системні пакети base
image, не виконуються application-кодом (perl не викликається; zlib — через stdlib Python лише
для довірених даних у PR2). Прийнято до появи fix у Debian; CI `HIGH ignore-unfixed` їх не
блокує, `CRITICAL` без `ignore-unfixed` заблокує будь-яке підвищення severity. Механізм
датованих винятків для unfixed CRITICAL (`trivyignores: .trivyignore`, gate 3 CR-11) — owner
WP-13 разом із security-тестами; до того unfixed CRITICAL блокує PR, і acceptance вноситься
сюди правкою workflow.

### Прийняті знахідки gate 3 (дата 2026-09-22)

- `api` у `ingress` (не internal) має необмежений egress; §7.5 — «лише OIDC egress» (CR-14,
  SEC L-2). Owner **WP-00 PR3**: gui↔api через окрему internal-мережу, `ingress` лише gui
  (рядок додано до картки PR3); **WP-11A**: OIDC egress api — окрема мережа/allowlist.
- Health endpoint без auth повертає версії й `detail` (SEC L-4). Зараз: `detail` на помилці —
  лише клас/код без текстів винятків і host:port, `server_header=False`; версії/`detail` за
  RBAC і не проксіювати без auth у PR3 — owner **WP-11A / PR3**.
- `trivyignores` для датованих unfixed CRITICAL — owner **WP-13** (див. risk acceptance).
- Access log uvicorn вимкнено (`access_log=False`); коли WP-11A увімкне його — URL/query з
  токенами фільтрує викликач, redaction structlog діє лише за ключами (SEC I-1, owner
  **WP-11A** security-рев'ю). Плоска мережа `backend` — production network policies §13
  (SEC I-5, owner WP-12/WP-01D).
- pymongo лишається module-level у `collector.api.health` (healthcheck mongo його потребує);
  fastapi — lazy у `create_app`, cli — lazy усе (CR-12): `import collector.cli` 0.88 с → 0.44 с.

### Прийняті знахідки gate 2 (дата 2026-09-22)

- `api` завершується з кодом 143 при `docker compose stop` (uvicorn ≥ 0.30 після graceful
  shutdown повторно піднімає SIGTERM), хоч shutdown штатний (~4 с) — owner **WP-11A** при заміні
  стаба `collector.api.health` (нормалізувати до 0 або задокументувати як штатний код).
- `name: collector` і фіксовані назви мереж — паралельні checkout-и на одному host конфліктують;
  задокументовано в `deploy/compose/README.md` («Обмеження»), прийнято для single-host MVP/CI.
- `docker compose config --quiet` без profiles валідує лише схему (усі сервіси мають profiles);
  CI задає `COMPOSE_PROFILES=core,workers` і має окремий крок з усіма profiles — достатньо (info).

### Прийняті знахідки пострев'ю / spec-review (дата 2026-09-22)

- **F-2** (§13, low): dependency/image scanning вимагається «щотижня і на кожен PR», а
  `.github/workflows/ci.yml` job `docker` спрацьовує лише на `pull_request`/`push: main` —
  окремого `schedule: cron` немає. Owner **WP-13** (разом із `.trivyignore`-механізмом вище),
  не пізніше **2026-12-22**: додати щотижневий workflow або задокументувати тут, чому per-PR
  частота на активному репозиторії еквівалентна вимозі.
- **F-3** (§7.5, low): application image `collector` має лише mutable tag
  (`collector:dev`/CI `collector:ci`), без публікації в registry з immutable digest (vendor
  images — `postgres`/`mongo`/`minio` — уже `tag@digest`). Rollback за digest тому обмежений
  локальним image cache або детермінованим ребілдом із попереднього Git SHA
  (`docs/runbooks/rollback-image.md`). Owner **WP-14** (registry/release pipeline) разом із
  PR3: опублікувати `collector` у registry з immutable digest і оновити runbook.

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
- Документи: `docs/runbooks/clean-host-start.md`, `docs/runbooks/rollback-image.md`,
  `deploy/compose/README.md`, `docs/plan/reports/WP-00/implementation-pr2.md`,
  `docs/plan/reports/WP-00/spec-review-pr2.md`, `docs/plan/deps/WP-01A-to-WP-00.md` (approved
  dependency, розділ «Approved dependency WP-01A»); ADR-0001 (стаби, логування).
