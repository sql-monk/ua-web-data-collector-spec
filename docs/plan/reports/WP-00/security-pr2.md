# WP-00 PR2 — security review (Docker images + Compose profiles)

| Поле | Значення |
|---|---|
| Branch / HEAD | `wp/00-2-docker-compose` @ `5b3cb6a` (worktree `.worktrees/wp-00-2`) |
| Diff | `git diff main...HEAD` — 32 файли |
| Дата | 2026-09-22 |
| Рев'юер | `wp-security-reviewer` (read-only; єдиний запис — цей файл) |
| Обсяг | ТЗ §13 (контейнери, secrets, socket, ролі, логи), §7.5, FR-013, FR-035; REVIEW.md R-51/R-55; ADR-0002 |
| Вердикт | **approve** — 0 critical, 0 high, 1 medium, 6 low, 5 info |

## Знахідки

Формат: `severity | file:line | клас проблеми | сценарій | verdict`.

### Medium

**M-1** | `docker-compose.yml:185-214` (`minio`) | надлишкова привілейованість stateful-контейнера |
`minio` працює від root (ADR-0002 приймає це), але на відміну від `postgres`/`mongo` не має
`cap_drop: [ALL]` і `read_only`; runtime `docker inspect`: `CapDrop=[]`, `RO=false`, `User=""`.
Сценарій: компрометація MinIO з будь-якого контейнера мережі `backend` (усі application
services її бачать) дає root із повним default capability set у контейнері, що тримає raw
bucket — вища ймовірність lateral movement/escape порівняно з іншими stateful services.
ADR-0002 обґрунтовує лише root (chown `/data`), не збереження capabilities. |
**non-blocking, виправити у follow-up (PR3 або WP-01D ADR)**: додати `cap_drop: [ALL]`
(MinIO як root пише у volume `/data` без capabilities; перевірити `up --wait`), за можливості
`read_only: true` + `tmpfs /tmp` (`MC_CONFIG_DIR=/tmp/.mc`), або зафіксувати в ADR-0002
явне risk acceptance для capabilities з датою/owner.

### Low

**L-1** | `docker-compose.yml:43-67` (`x-collector-runtime`), усі сервіси | відсутній `pids_limit` |
Жоден сервіс не обмежує кількість процесів (`HostConfig.PidsLimit=<nil>`); `mem_limit`/`cpus` є.
Сценарій: fork-exhaustion у скомпрометованому/помилковому worker (parse у WP-03 запускає
сторонні парсери) вичерпує PID-таблицю хоста. | **non-blocking**: `pids_limit: 256` для
application services, ~1024 для stateful.

**L-2** | `docker-compose.yml:246-251` (`api.networks: [backend, ingress]`) | egress ширший за §7.5 |
`ingress` не `internal`, тому api має необмежений вихід в Інтернет (runtime: з `api`
доступні `1.1.1.1:53` і DNS `pypi.org`; з `parse-worker`/`scheduler` — заблоковано, як і
має бути). §7.5: «API — лише OIDC egress». У PR2 api — стаб без вихідних запитів, ризик
теоретичний; §13 відносить egress allowlist до production. | **non-blocking, врахувати у PR3/
WP-11A**: gui↔api через окрему internal-мережу (наприклад `frontend`), а `ingress` лишити лише
gui; OIDC egress api — окрема мережа/allowlist, коли з'явиться OIDC (WP-11A).

**L-3** | `deploy/compose/secrets/init-secrets.sh:28-31`, `*.example` | клас «default credentials» + права файлів |
Паролі PostgreSQL/Mongo/MinIO копіюються з прикладів (`change-me-*-local-dev`), файли
`chmod 0644` (на Linux-хості readable для всіх локальних користувачів). Скрипт лише друкує
попередження. Сценарій: операційна помилка — clean-host start за runbook без заміни значень →
відомі облікові дані у stateful services (мітигація: порти не публікуються, `backend`
internal, потрібна компрометація контейнера). | **non-blocking**: генерувати всі секрети
випадково (як keyfile: `openssl rand -base64 32`), а не копіювати з прикладів; права
`0640` + group, або задокументувати вимогу для non-local у runbook. Виправляє клас
проблеми повністю, коштує кілька рядків.

**L-4** | `src/collector/api/health.py:118-131,170-185,230-236` | розкриття внутрішньої інформації неавтентифікованим endpoint |
`GET /api/v1/health/components` без auth повертає повний git SHA, `package_version`,
`schema_version`, `detail` з внутрішніми host:port (`tcp postgres:5432 reachable`), ім'я RS,
а при помилці — текст винятку до 200 символів (перевірено: `ServerSelectionTimeoutError …
servers: [<ServerDescription ('mongo-x…`, `[Errno 111] Connection refused`). Заголовок
`server: uvicorn`. §7.7 (таблиця RBAC) відносить health до ролі `viewer` і вище — тобто за
auth; у PR3 nginx проксіює `/api` → `api:8000` назовні до появи OIDC (WP-11A). Секретів у
відповіді немає. | **non-blocking, owner WP-11A/PR3**: для неавтентифікованого liveness —
лише `ready`/`ok` per component; версії та `detail` — після RBAC; у PR3 не публікувати
`/api/v1/health/components` через nginx без auth (або обмежити `detail`/`version`);
`server_header=False` в uvicorn.

**L-5** | `docker-compose.yml:113-214` (`postgres`, `mongo`, `minio`) | відсутня ротація логів stateful |
`logging.options.max-size/max-file` задано лише в `x-collector-runtime`; stateful services
пишуть json-file без обмеження. Сценарій: log flood (наприклад auth-failure loop) заповнює
диск хоста — DoS усього стека. | **non-blocking**: винести `logging` у спільний фрагмент і
застосувати до stateful.

**L-6** | `docs/decisions/0002-docker-compose-single-host.md:151-158` (risk acceptance) | неповний/обмежений у часі risk acceptance |
Дата (2026-09-22), owner (WP-13), дедлайн перегляду (2026-12-22) і тригер (оновлення digest)
присутні — форма відповідає §13. Локальний `docker scout cves`: 0 CRITICAL, 2 HIGH без fix
(`perl` CVE-2026-82560, `zlib` CVE-2026-85091) — збігається. Два зауваження: (а) для zlib
не вказано CVE ID; (б) обґрунтування «zlib — лише для довірених даних у PR2» перестає бути
чинним у WP-02 (fetch розпаковує недовірений gzip sitemap/body через zlib, §13 decompression
bomb). | **non-blocking**: додати CVE-2026-85091 і тригер перегляду «до merge WP-02 fetch»
(не лише digest/дата).

### Info

**I-1** | `src/collector/cli.py:277-289` (`access_log=False`) | Логи uvicorn без access log →
заголовки запиту не логуються взагалі; runtime-запит із `Authorization: Bearer …`,
`Cookie`, `X-Api-Key`, `?token=` не залишив жодного сліду в `docker compose logs`
(grep за маркерами = 0). Redaction structlog з PR1 (`redact_secrets`) активна у всіх
процесах контейнера (JSON-логи api/workers/one-shots через `configure_logging`), але цим
запитом не навантажувалась. Коли WP-11A увімкне access log — processor редагує лише за
ключами; URL/query з токенами мають фільтруватись викликачем (докстрінг `logging.py` це
фіксує). Перевірити у security-рев'ю WP-11A.

**I-2** | `docker-compose.yml:143-155` (mongo entrypoint) | Keyfile: у контейнері
`/run/mongo-keyfile/keyfile` `-r-------- 999:999` (0400, owner mongod) — перевірено; bind-mount
`/run/secrets/*` на Docker Desktop має `0777 root` (відоме відхилення, ADR-0002); на Linux
права хоста (0644 з init-скрипта). Auth увімкнено: неавтентифікований `listDatabases` →
`Unauthorized`, `getCmdLineOpts` → «requires authentication». Bind — лише `backend`
(`internal: true`), `--bind_ip_all` не виходить за межі контейнера.

**I-3** | Сканери | `gitleaks` (локально): `git main..HEAD` — 0 leaks; `dir` — 4 false
positive у `.venv/…/dns/dnssecalgs/eddsa.py` (не в git). `trivy`, `syft` — відсутні локально
(CI має обидва). `docker scout cves collector:dev`: 149 packages, 0C/2H/0M/0L — збіг з ADR.

**I-4** | `docker-compose.yml:227-241` (`ensure-mongo`) | One-shot отримує root-облікові дані
Mongo (`*_FILE`), лише він і `mongo`; `api`/workers секретів не мають (перевірено
`Config.Env` усіх контейнерів). Для ролей per-component (§13) owner WP-01A/WP-01B — у PR2
відсутність runtime-credentials в api/workers відповідає §13 «scheduler/fetcher не має
MongoDB credentials».

**I-5** | Мережа `backend` | Плоска: усі application services бачать `api:8000`,
`postgres`, `mongo`, `minio` (runtime). Для MVP очікувано; segmentation per role — §13
«окремі network policies … у production», поза PR2.

## Вердикт

**approve** — блокуючих (critical/high) знахідок немає. M-1 і L-1…L-6 — follow-up без
блокування merge; L-2/L-4 передати у PR3 (gui/nginx) і WP-11A; L-6 — правка ADR-0002 у
будь-якому наступному PR WP-00.

## Що перевірено

### Статично (diff, конфіги, тести)

1. **Docker socket / named pipe** (R-55, FR-035): `grep` по рендеру
   `docker compose --profile core --profile workers --profile browser config` — жодного
   `docker.sock`, `npipe`, `docker_engine`, `privileged`; runtime `inspect` усіх 16 контейнерів
   — mounts лише named volumes + bind секретів. Тести `test_no_docker_socket_mount_anywhere`,
   `test_no_docker_socket_and_no_published_ports` покривають.
2. **Published ports**: base-файл — 0 `ports`; `dev.override.yml` рендерить лише
   `127.0.0.1:{5432,27017,9000,9001,8000}`; `expose: 8000` у api — внутрішній.
3. **Мережі**: `backend`/`telemetry` `internal: true`; api — `backend`+`ingress` (без
   `source-egress`); discovery/fetch/browser — `source-egress`, translation —
   `provider-egress`, parse/projector/export/maintenance/scheduler/one-shots/stateful — лише
   `backend`. Runtime-підтвердження egress (I-5, L-2). `telemetry` без сервісів — не
   рендериться (очікувано).
4. **Secrets** (§7.5, FR-013): у git лише `*.example` + `init-secrets.sh`
   (`git ls-files`); реальні файли `!!` ignored (`.gitignore` `deploy/compose/secrets/*` з
   винятками). `environment` — лише `*_FILE` (`POSTGRES_PASSWORD_FILE`,
   `MONGO_INITDB_ROOT_PASSWORD_FILE`, `MINIO_ROOT_*_FILE`, `COLLECTOR_MONGO_ROOT_PASSWORD_FILE`).
   `docker inspect` усіх контейнерів: grep значень трьох паролів = 0 збігів; `Config.Env`/
   `Cmd`/`Entrypoint` без значень секретів. `docker history collector:dev` — лише
   базовий `GPG_KEY` (публічний fingerprint python image), `ARG`/`ENV` без credentials.
   Keyfile генерується (`openssl rand`/`urandom`), `.example` невалідний за задумом.
   `env_or_file` читає файл і не логує значення; `log.error("ensure_mongo.failed", error=…)`
   — тексти PyMongo без паролів.
5. **Image** (§7.5, §13): `Config.User=10001:10001`; runtime `id` = 10001, `CapEff=0`,
   `NoNewPrivs=1`, rootfs read-only (`touch /app/x`, `/opt/collector/x` → EROFS), `/tmp`
   tmpfs 64M (`size=` задано, `mode=1777`), `mem_limit`/`cpus` для всіх сервісів;
   `pids_limit` відсутній (L-1). Base images pinned `tag@sha256` (python:3.13-slim, uv,
   postgres:18, mongo:8.0, minio); `uv sync --frozen --no-dev --no-editable`; у runtime
   відсутні `pip`, `pip3`, `uv`, `gcc`, `cc`, `make`, `curl`, `wget`, `git`, `ensurepip`
   (`perl` є — базовий Debian, у ADR risk acceptance). `/app/config/source-registry.yaml`
   root:root 0644. `.dockerignore` — allowlist (`*` + `!…`): `.env*`, `.git`, `tests/`,
   `deploy/compose/secrets/` у контекст не потрапляють.
6. **Health endpoint**: відповідь 200 без секретів; розкриває версію/git SHA/host:port/
   тексти винятків (L-4); `/docs`, `/openapi.json` → 404; path traversal у роуті → 404.
7. **Логи**: JSON structlog у api/workers/one-shots (`configure_logging` викликається у
   `api`, `placeholder_process`, `db migrate`, `db ensure-mongo`); запит із
   `Authorization`/`Cookie`/`X-Api-Key`/`?token=` не залишив маркерів у `docker compose logs`
   (I-1). Ротація json-file лише для application services (L-5).
8. **ensure-mongo / stateful auth**: mongo з `--keyFile` (0400 у tmpfs, owner 999),
   `--replSet rs0`, auth enforced (I-2); PostgreSQL `--auth-host/--auth-local=scram-sha-256`,
   пароль з `POSTGRES_PASSWORD_FILE`; MinIO `MINIO_ROOT_*_FILE`, `MINIO_BROWSER=off` у base
   (`on` лише в dev-override на 127.0.0.1). Дефолтних vendor-паролів немає; значення прикладів
   — L-3.
9. **CI** (`.github/workflows/ci.yml`): `permissions: contents: read` на workflow;
   job `docker`: `init-secrets.sh` (значення не друкує) → `compose config` (без/з profiles)
   → build + перевірка `Config.User` → `anchore/sbom-action@v0.24.2` (SPDX artifact) →
   `trivy-action@v0.36.0` двічі: `CRITICAL` без `ignore-unfixed`, `exit-code: 1`
   (відповідає §13 «critical CVE блокує або має датоване risk acceptance»); `HIGH` з
   `ignore-unfixed: true` + risk acceptance в ADR-0002 (дата/owner/дедлайн є; зауваження L-6)
   → `up -d --wait` → `ps`/health → `down -v` (`if: always()`). Secrets у логах: жоден крок
   не друкує вміст secret-файлів; `GITLEAKS_LICENSE`/`GITHUB_TOKEN` — лише через `secrets.*`.
   gitleaks — окремий job з `fetch-depth: 0`. Тести `test_ci_workflow_permissions_are_read_only`,
   `test_ci_trivy_*` фіксують політику.
10. **`.dockerignore`**: allowlist-підхід; `src/**/__pycache__/`, `*.pyc` виключені.

### Runtime (Docker Desktop 29.8 / Compose v5.5.1, Windows 11)

- `docker compose --profile core --profile workers build && up -d --wait --wait-timeout 300`
  → rc 0, 14 running healthy + 2 one-shots `Exited (0)`.
- `docker inspect` усіх контейнерів: Privileged/CapAdd/CapDrop/ReadonlyRootfs/SecurityOpt/
  PidsLimit/Memory/User/Mounts (таблиця вище).
- Egress-проби (`socket.create_connection` до `1.1.1.1:53`, DNS `pypi.org`, внутрішні
  сервіси) з `api`, `parse-worker`, `fetch-worker`, `translation-worker`, `scheduler`.
- Health: запит із секретними заголовками; `python -m collector.api.health` з неіснуючими
  хостами (тексти `detail` при помилках).
- Mongo: `id`, права keyfile, неавтентифіковані команди.
- `docker scout cves collector:dev` (мережа доступна): 0C/2H.
- Teardown: `docker compose down -v --remove-orphans` → 0 контейнерів, 0 volumes
  `collector_*`, 0 мереж `collector_*` (перевірено `docker ps -a`, `volume ls`, `network ls`).
- Worktree після рев'ю: без змін, крім цього звіту (untracked
  `code-review-pr2.md` належить код-рев'юеру).

### Не перевірялось / обмеження

- `trivy`/`syft` локально відсутні — конфіг CI перевірено статично, результат scan —
  через `docker scout` (еквівалентний перелік CVE base image).
- Linux-хост (права bind-mount секретів 0644, `internal` мережі з iptables) — лише статично;
  Docker Desktop емулює `internal` коректно (egress-проби), права файлів секретів у контейнері
  показують `0777` через WSL bind.
- SSRF/XML/decompression пункти §13 — не стосуються diff (fetch/parse — WP-02/WP-03).
- GUI/OIDC/CSP/CSRF — не стосуються diff (PR3, WP-11A/WP-11C).
