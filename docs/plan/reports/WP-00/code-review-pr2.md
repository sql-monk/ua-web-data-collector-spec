# WP-00 PR2 — код-рев'ю (`wp/00-2-docker-compose`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR2 «Docker images + Compose profiles» |
| Branch / worktree | `wp/00-2-docker-compose` / `.worktrees/wp-00-2` |
| Рев'юваний commit | `5b3cb6a` (`git diff main...HEAD`, без `docs/plan/reports/**`; fix gate 2 — `71f5903`) |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR2» (вимоги 1–9, acceptance) |
| Вхідні звіти | `implementation-pr2.md`, `testing-pr2.md` (вердикт `pass`, H-1 → fixed у `71f5903`) |
| Розділи ТЗ | §7.5, §7.6, §13, §16.2 |
| Середовище | Windows 11, Docker Desktop 29.8 / Compose v5.5.1, uv 0.12.13, CPython 3.13; `docker compose up` НЕ запускався (паралельний security-рев'ю на тому ж worktree) |
| Рев'юер | wp-code-reviewer, read-only; єдиний запис — цей файл |

## Вердикт

**`approve`** — critical: 0, high: 0, medium: 3, low: 11 (з них 1 `spec-mismatch` для пострев'ю).

Код коректний для свого обсягу: `ensure-mongo` ідемпотентний і не ковтає помилок, `db migrate`
чесний стаб (TCP + явне «no migrations yet; owner WP-01A»), health-endpoint віддає 503/200 із
JSON і не блокує event loop (sync route → threadpool), placeholder-процеси коректно дренуються по
SIGTERM з кодом 0 і не маскують стаб-статус, Dockerfile без секретів/non-root/read-only з
правильним шаруванням кешу, Compose-інваріанти §7.5 підтверджені рендером. Три medium — усі
локальні (кожна правка ≤ 5 рядків) і варті закриття у цьому PR: (1) `hostname -i` у healthcheck
mongo ламається на dual-stack мережі (відтворено в контейнері), (2) CI-assert `grep -vc healthy`
рахує `unhealthy` як healthy, (3) integration render-тести ніколи не виконуються в CI через зайвий
skip на відсутні файли секретів.

## Оцінка рішення H-1 (healthcheck mongo через `hostname -i` + `hello().isreplicaset||setName`)

- **Семантика правильна.** Init-фаза `docker-entrypoint.sh` (перевірено у самому image:
  рядки 306–317 скрипта) запускає тимчасовий `mongod` з `--bind_ip 127.0.0.1`, без `--replSet`,
  `--auth`, `--keyFile`. Підключення на IP контейнера гарантує, що відповідає саме фінальний
  `mongod` з `--bind_ip_all` — той інтерфейс, який бачать `ensure-mongo`/api/workers; умова
  `isreplicaset || setName` гарантує режим replica set. Обидві частини потрібні: без `--host`
  «healthy» під час init неможливий (standalone → обидва ключі відсутні), але і не потрібен;
  без `hello` «healthy» до RS-режиму дав би `ensure-mongo` `NoReplicationEnabled`.
- **Справжню несправність не ховає.** Healthy ≠ primary свідомо: до `ensure-mongo` primary
  немає. Член, що застряг у `STARTUP2/RECOVERING/REMOVED`, буде healthy для Compose, але
  `ensure-mongo` впаде по `TimeoutError` (60 с), api → 503, projector/export → unhealthy
  (`isWritablePrimary`) — деградація видима на тому рівні, де вона має значення.
- **Root cause H-1 не підтверджено** (exit 48 після exec_die 137 — гіпотеза), фікс емпіричний
  (10/10 циклів); залишковий ризик задокументовано у runbook («повторний `up -d --wait`
  штатний») і ADR-0002. Прийнятно; рекомендую після першого прогону CI job `docker` зняти
  позначку «operationally unverified» лише за ≥ 5 зелених прогонів.
- **Крихкість `hostname -i`** — знахідка medium 1 нижче: на мережі з IPv6 (`enable_ipv6` або
  daemon `default-network-opts`) команда повертає два адреси, `mongosh --host` відхиляє рядок,
  mongo ніколи не стає healthy і весь `core` блокується (fail-closed, але повна зупинка стеку).

## Знахідки

Формат: `severity | file:line | claim | failure scenario | verdict`.

### medium

**1. medium | `docker-compose.yml:188-192` | `hostname -i` повертає кілька адрес на dual-stack мережі → `mongosh --host` невалідний → mongo ніколи не healthy**
`hostname -i` друкує УСІ адреси, у які резолвиться hostname контейнера (`/etc/hosts`); Docker
на мережі з IPv6 пише і A-, і AAAA-запис, glibc (RFC 6724) ставить IPv6 першою.
Failure scenario (відтворено у `mongo:8.0@sha256:4968…`, `--network none`, додано обидва
записи в `/etc/hosts`): `hostname -i` → `fd00::5 172.18.0.5`;
`mongosh --host "$(hostname -i)" --eval 1` → `MongoshInvalidInputError: [COMMON-10001] The
--host argument contains an invalid character`. Healthcheck → exit 1 на кожній спробі → після
`start_period` 40 с + 10×10 с mongo `unhealthy` → `ensure-mongo`, `api`, `projector`/`export`
не стартують, `up --wait` → 1. Без hostname у `/etc/hosts` (`--network none`, нестандартні
DNS-налаштування) — `Temporary failure in name resolution`, той самий результат. У базовій
конфігурації PR2 (IPv4-only user-defined network) не проявляється — тому medium, не high.
Фікс: брати першу IPv4-адресу явно, напр.
`ip="$(hostname -I | tr ' ' '\n' | grep -m1 -E '^[0-9]+(\.[0-9]+){3}$')"` або
`getent ahostsv4 "$(hostname)" | awk 'NR==1{print $1}'`, і додати unit-assert на це.
Verdict: **CONFIRMED** (сценарій відтворено в контейнері).

**2. medium | `.github/workflows/ci.yml:171` | `grep -vc healthy` рахує `unhealthy` як healthy — assert ніколи не спрацює**
`test "$(docker compose ps --status running --format '{{.Health}}' | grep -vc healthy)" = "0"`:
`-v` відкидає рядки, що МІСТЯТЬ підрядок `healthy`, а `unhealthy` його містить.
Failure scenario (відтворено у shell): `printf 'healthy\nunhealthy\nstarting\n' | grep -vc
healthy` → `1` (лише `starting`); стек із `unhealthy` api після `--wait` (напр. postgres впав між
`up` і перевіркою) проходить крок зеленим. `up -d --wait` перед цим справді перевіряє healthy,
тому крок зараз лише надлишковий, але його призначення — регресійний assert — не працює.
Фікс: `grep -vxc healthy` (або `-vw`).
Verdict: **CONFIRMED**.

**3. medium | `tests/integration/test_compose_render.py:58-67` | skip на відсутні файли секретів зайвий → render-тести ніколи не виконуються в CI**
`docker compose config` не читає файли `secrets.*.file` (відтворено: проєкт із
`file: ./does-not-exist` → `config --quiet` exit 0). Fixture `rendered` skip-ає, якщо
`deploy/compose/secrets/<name>` відсутні; у CI job `python` (`uv run pytest -m "not live"`)
`init-secrets.sh` не запускається → усі 5 тестів skip; job `docker` запускає `init-secrets.sh`,
але не pytest. Разом: єдина перевірка інтерполяції/merge (`<<:` у `depends_on`/`healthcheck`
workers, loopback-порти override) працює лише на машині розробника, який виконав
`init-secrets.sh`. Тестувальник уже закрив один «skip ховав невалідний проєкт» (M2), цей —
другий такого ж роду.
Фікс: прибрати guard на секрети (лишити skip лише без Compose plugin) або додати
`./deploy/compose/secrets/init-secrets.sh` у job `python` перед pytest; у CI також варто
`-rs`, щоб skip був видимим.
Verdict: **CONFIRMED** (probe `docker compose config` без файлів секретів + читання ci.yml).

### low

**4. low | `src/collector/cli.py:133-142` | паралельний `ensure-mongo` не ідемпотентний: другий отримує `AlreadyInitialized` (code 23) і завершується 1**
Обидва бачать code 94 → обидва `replSetInitiate`; другий → `OperationFailure(23)` → re-raise →
exit 1, хоча RS у потрібному стані. Сценарій: `docker compose run ensure-mongo` паралельно з
`up`, або `--scale ensure-mongo=2`. Фікс: ловити code 23, далі звичайна перевірка `set` +
очікування primary.
Verdict: PLAUSIBLE.

**5. low | `src/collector/cli.py:151-158,188-196` | wait-loop не толерує transient driver-помилки під час election; клієнт без `connectTimeoutMS`/`socketTimeoutMS`**
`client.admin.command("hello")` у циклі не обгорнуто в `try`: одиничний `AutoReconnect`
(з'єднання скинуто при переході SECONDARY→PRIMARY) → `PyMongoError` → exit 1 замість повтору
до `deadline`. Окремо: `MongoClient` має лише `serverSelectionTimeoutMS=10s`; `replSetInitiate`,
що завис, тримає one-shot без ліміту (в CI обмежує лише `--wait-timeout 300`). Фікс: у циклі
ловити `AutoReconnect`/`NotPrimaryError` і продовжувати до deadline; задати
`connectTimeoutMS`/`socketTimeoutMS` ≈ 30 с.
Verdict: PLAUSIBLE.

**6. low | `docker-compose.yml:80-84,362-367,376-381` | `depends_on` workers не містить залежностей, які перевіряє їхній healthcheck**
fetch/parse healthcheck → `minio`, projector/export → `mongo`, але `depends_on` — лише
`postgres` + `migrate-postgres` (+ `ensure-mongo` для projector/export, без `mongo:
service_healthy`). `docker compose up fetch-worker` не підніме minio; при холодному старті
`start_period` worker 15 с < `start_period` minio 20 с → транзиторний `unhealthy` (3 retries
покривають, тому не проявилось у тестах). Фікс: додати `minio`/`mongo: condition:
service_healthy` до відповідних сервісів.
Verdict: PLAUSIBLE.

**7. low | `docker-compose.yml:115-228` | stateful-сервіси без `logging` rotation**
`json-file` з `max-size/max-file` лише в `x-collector-runtime`; postgres/mongo/minio пишуть
без ліміту (mongo — найбалакучіший). На single-host з тривалим pilot (§16.3, 7 діб) диск
під `/var/lib/docker/containers` росте необмежено. Фікс: винести `logging` в окремий anchor і
додати до трьох stateful.
Verdict: CONFIRMED (відсутність), вплив — операційний.

**8. low | `docs/runbooks/clean-host-start.md:12-14` | «ліміти сумарно ≈ 12 CPU / 12 ГБ» — фактично 16 CPU / 16 ГіБ**
Сума `deploy.resources.limits` core+workers (без one-shots): postgres 2/2G + mongo 2/2G +
minio 1/1G + api 1/1G + scheduler 0.5/0.5G + discovery 0.5/0.5G + fetch 2×1/1G + parse 2×2/2G +
projector 1/1G + translation 0.5/0.5G + export 1/1G + maintenance 0.5/0.5G = 16 / 16G.
Verdict: CONFIRMED (арифметика).

**9. low | `src/collector/cli.py:105-108`, `tests/unit/test_cli_compose_commands.py:222-231` | `placeholder_process` ставить SIGINT/SIGTERM handlers і не відновлює їх; тест викликає його напряму в main thread pytest**
Після `test_placeholder_process_runs_until_stopped` handler SIGINT процесу pytest — замикання
на вже мертвий `Event`: Ctrl-C далі в сесії не дає `KeyboardInterrupt`, а мовчки виставляє
`stop_event`. Фікс: `previous = signal.getsignal(...)` і відновлення у `finally` (це ж
правильно і для production-коду при майбутньому вбудовуванні в worker runtime WP-01D).
Verdict: CONFIRMED (читання коду).

**10. low | `src/collector/api/health.py:111,170-179` | `http.client.HTTPException` не ловиться → 500 замість JSON 503**
`_timed` ловить `OSError/PyMongoError/ValueError/TimeoutError`; `urllib` кидає
`http.client.BadStatusLine`/`IncompleteRead` (не `OSError` — перевірено `issubclass`) якщо
на `minio:9000` відповідає не-HTTP/обірваний сервіс. Тоді endpoint віддає 500 без
`components`, а healthcheck workers падає з traceback. Фікс: додати
`http.client.HTTPException` до кортежу.
Verdict: PLAUSIBLE.

**11. low | `.github/workflows/ci.yml:143-160` | trivy CRITICAL без wired-механізму risk acceptance**
Політика «unfixed CRITICAL блокує до датованого acceptance в ADR» правильна за §13, але
`trivy-action` не має `trivyignores:` — коли Debian опублікує unfixed CRITICAL у
`python:3.13-slim` (типово раз на кілька тижнів), усі PR червоні, а прийняти ризик можна лише
правкою workflow. Фікс: `trivyignores: .trivyignore` з датованими записами
(`CVE-… # accepted until YYYY-MM-DD, ADR-0002`). Також: actions pinned tag, не SHA (як у PR1).
Verdict: PLAUSIBLE.

**12. low | `src/collector/cli.py:32-35` | module-level import `pymongo` та `collector.api.health` (FastAPI) робить кожен виклик CLI важким**
`python -X importtime -c "import collector.cli"` → 0.77 с, з них `collector.api.health` 0.32 с
(fastapi). Це ціна `collector --help`, `collector version` (image `HEALTHCHECK` кожні 30 с у
one-shots) і всіх workers-healthcheck. Фікс: lazy import у тілах `db_ensure_mongo`/`db_migrate`
(як уже зроблено для `uvicorn` в `api`), а `env_or_file`/`*_address` — у `collector.core`
(вони не належать API-модулю; після заміни health WP-11A cli втратить імпорт).
Verdict: CONFIRMED (виміряно).

**13. low | `deploy/compose/README.md:52-61`, `docs/runbooks/clean-host-start.md` | host-клієнти Mongo через dev.override потребують `directConnection=true` — не задокументовано**
RS config містить `members[0].host = mongo:27017`; з хоста `mongo` не резолвиться, RS-aware
клієнт (`mongosh mongodb://127.0.0.1:27017`) після discovery спробує `mongo:27017` і зависне.
Фікс: один рядок у README (`?directConnection=true`).
Verdict: PLAUSIBLE.

**14. low `spec-mismatch` | `docker-compose.yml:286-288,475-477` | `api` у non-internal `ingress` має необмежений egress; §7.5 — «API — лише OIDC egress»**
`ingress` не `internal` (потрібно для gui у PR3), тож api отримує default route в інтернет.
Для PR2 без OIDC це не функціональна вада, але контракт §7.5/§13 («egress allowlist у
production») вимагає окремої мережі api↔gui або egress-обмеження для api. Для пострев'ю /
PR3 (gui): розглянути внутрішню мережу `api-gui` + окремий `oidc-egress`.
Verdict: PLAUSIBLE (для пострев'ю, не блокує).

### Не знахідки (перевірено, окремо згадую, бо були у фокусі)

- `ensure-mongo`: ідемпотентність (повторний запуск → `initiated_now=false`, без
  `replSetInitiate`), відмова при іншому `set` (ValueError → 1), re-raise будь-якого
  `OperationFailure` ≠ 94 без initiate, keyfile/auth (`authSource=admin`, `directConnection`),
  секрет через `*_FILE`, не в логах (`error=...[:300]` — повідомлення PyMongo не містять
  credentials); `FileNotFoundError` на відсутній secret-файл — traceback з exit 1 (тест
  фіксує). Помилки не ковтаються.
- `db migrate`: стаб чесний — окремий рядок `no migrations yet; owner WP-01A`, exit 0 лише за
  TCP-reachable, без читання секретів; exit 1 інакше.
- Health endpoint: sync `def` route → FastAPI виконує у threadpool, event loop не блокується;
  worst-case 3×3 с послідовно (9 с) > healthcheck timeout 8 с — результат все одно «unhealthy»
  (коректно). 503 vs 200 — правильно; urllib у healthcheck api на 503 кидає `HTTPError` → exit ≠ 0.
  `MongoClient` створюється/закривається на кожен probe (прийнятно для стаба).
- Placeholders: `Event.wait` переривається сигналом, handler у main thread, `init: true` →
  docker-init форвардить SIGTERM; exit 0 = штатна зупинка; стаб-рядок у stderr + `warning
  placeholder.started` — статус не приховано. Compose «healthy» для placeholder = process +
  dependency (§7.5), як вимагає картка (п. 7).
- Dockerfile: шар залежностей keyed лише на `pyproject.toml`/`uv.lock`/`.python-version`;
  `--frozen --no-dev`; venv шлях `/opt/collector` однаковий у builder/runtime (shebang і
  `pyvenv.cfg` валідні); `USER 10001:10001` останній; `ENTRYPOINT []`; жодних ARG/ENV з
  секретами, `.dockerignore` allow-list (`*` + `!…`), `src/**/__pycache__` виключено;
  `COPY --chmod=0644` реєстру + `mkdir -m 0755` — правильно (chmod на COPY впливає і на
  створювані каталоги); digests pinned для python/uv/postgres/mongo/minio.
- Compose: `$(hostname -i)` не потрапляє під інтерполяцію (`$$` у виводі `config` — лише
  екранування при серіалізації, перевірено на scratch-файлі); `<<:` merge для `environment`
  (`*collector-env`) і повна заміна `depends_on`/`healthcheck`/`networks` у workers — рендер
  збігається з наміром; `stop_grace_period` 120 с ≥ 90 с → `StopTimeout` (підтверджено
  тестувальником `docker inspect`); `restart: "no"` one-shots; `deploy.resources.limits`
  застосовуються Compose v2 без swarm; `read_only` + tmpfs `/tmp` 64m достатньо для
  Python/uvicorn (`PYTHONDONTWRITEBYTECODE`, bytecode скомпільовано в builder, `HOME=/tmp`);
  tmpfs-опції `uid=999` для keyfile працюють (інакше `install` від uid 999 не зміг би писати —
  тестувальник бачив 0400 файл); мережі: `backend`/`telemetry` internal, egress-розкладка за §7.5;
  `--bind_ip_all` у entrypoint надлишковий (entrypoint додає сам), нешкідливий.
- dev.override: лише `ports` на `127.0.0.1` + `MINIO_BROWSER`; не послаблює `read_only`/user/caps.
- CI job `docker`: порядок config → build → SBOM → trivy CRITICAL (без ignore-unfixed) → trivy
  HIGH (ignore-unfixed, `skip-setup-trivy`) → `up --wait` → `down -v` (`if: always()`); жодного
  `continue-on-error`; `COLLECTOR_IMAGE=collector:ci` збігається з `-t`, тому `up` не перезбирає;
  buildx-кеш не експортується (холодна збірка на кожен прогін — прийнятно для MVP).
- Тести: unit compose-config перевіряють інваріанти на YAML (merge keys резолвляться PyYAML),
  adversarial-тести закривають мутації (initiate під code≠94, slow probe, 503 JSON, secret
  consumers); типізація `mypy --strict` чиста, `type: ignore[arg-type]` лише у тестових фейках.

## Що перевірено окремо

| Перевірка | Результат |
|---|---|
| `uv run pytest -m "not live"` (worktree) | 213 passed, 1 skipped (Windows loopback asyncio) |
| `uv run pytest tests/integration -rs` (секрети ініціалізовані локально) | 10 passed, 0 skipped |
| `uv run mypy src` / `uv run ruff check .` / `ruff format --check .` | чисто |
| `COMPOSE_PROFILES=core,workers,browser docker compose config --format json` | exit 0; рендер `depends_on`/`healthcheck`/`environment`/`deploy`/`networks`/`tmpfs` для mongo, one-shots, api, scheduler, fetch/projector — відповідає наміру |
| Scratch-проєкт: `$(hostname -i)` під інтерполяцією Compose | літерал збережено (`--no-interpolate` ідентичний) |
| Scratch-проєкт: `secrets.file` → неіснуючий файл | `config --quiet` exit 0 → підстава для medium 3 |
| `docker run --rm --network none mongo:8.0@…` | `hostname -i` з A+AAAA → `fd00::5 172.18.0.5`; `mongosh --host` → `COMMON-10001` (medium 1); entrypoint init-фаза знімає `--replSet/--keyFile/--auth`, bind 127.0.0.1 (рядки 306–317) |
| Shell: `grep -vc healthy` на `healthy/unhealthy/starting` | `1` → medium 2 |
| `python -X importtime -c "import collector.cli"` | 0.77 с (health/fastapi 0.32 с) → low 12 |
| `issubclass(http.client.BadStatusLine, OSError)` | False → low 10 |
| Не запускалось | `docker compose up/down/build` (заборона під час паралельного security-рев'ю); SBOM/trivy (мережа) |

Read-only: жодних змін у коді/конфігурації, коміти не створювались.
