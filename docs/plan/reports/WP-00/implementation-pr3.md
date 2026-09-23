# WP-00 PR3 — звіт реалізації (`wp/00-3-web-scaffold`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR3 «React/TypeScript/Vite scaffold + GUI image» |
| Branch / worktree | `wp/00-3-web-scaffold` / `.worktrees/wp-00-3` (від `main` після merge PR1, PR2, WP-01C) |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR3» + «Спільні правила» |
| Розділи ТЗ | §7.7, §8, §13, §16.2, §16.3, FR-034; REVIEW.md R-54; ADR-0001, ADR-0002 |
| Середовище | Windows 11, Node 24.19.0 + npm 11.17.0, Docker Desktop 29.8.0 (Linux containers), Compose v5.5.1, uv 0.12.13 / CPython 3.13.9 |
| База | `bcae1ad` |

## Що зроблено

### 1. `web/` — Vite + React 19 + TypeScript strict (вимога 1)

- `web/package.json`: React 19.1.1, TypeScript 5.9.2, Vite 7.1.6, TanStack Query 5.90.2,
  React Router 7.9.1; dev — ESLint 9.36 (flat config, typescript-eslint 8.44
  `strictTypeChecked` + `stylisticTypeChecked`), Prettier 3.6.2, Vitest 3.2.4 + Testing
  Library (`@testing-library/react` 16.3, `jest-dom` 6.8, `user-event` 14.6), Playwright
  Test 1.55, jsdom 27. Усі версії **exact** (без `^`), `package-lock.json` у git,
  `engines: node >=24 <25`, `.nvmrc` = `24`.
- TypeScript strict у трьох файлах: `tsconfig.json` (solution) → `tsconfig.app.json`
  (`src` + `tests/unit`, DOM lib, alias `~/*` → `src/*`) і `tsconfig.node.json` (конфіги +
  `tests/e2e`). Понад `strict` увімкнено `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`, `verbatimModuleSyntax`, `erasableSyntaxOnly`,
  `noUnusedLocals/Parameters`, `noImplicitOverride`, `noFallthroughCasesInSwitch`.
  `npm run build` = `tsc -b --force && vite build`, тож типова помилка ламає і збірку image.

### 2. Структура, placeholder-сторінка, route-level code splitting (вимога 2)

- `src/{api,components,features,routes}/`, `tests/{unit,e2e}/` — як у картці.
- `src/routes/router.tsx` — лише таблиця `routes`; кожен листовий маршрут через
  `lazy: async () => import(...)`. `vite build` реально емітить окремі chunk-и:
  `dist/assets/OverviewPage-*.js` (2.44 kB) і `NotFoundPage-*.js` (0.52 kB) поряд з
  `index-*.js`. `createBrowserRouter` викликається у `src/main.tsx`, а не в модулі
  маршрутів — інакше data router стартує навігацію вже на імпорті (це ламало unit-тести).
- `src/routes/OverviewPage.tsx` — сторінка українською з `<h1>UA Web Data Collector —
  operator GUI</h1>` і переліком дев'яти екранів §7.7 (`src/routes/screens.ts`) як плану
  WP-11C. `index.html` має `lang="uk"` і `<title>` тим самим текстом.
- `src/api/README.md` — заготовка: описує, що клієнт **генерується з OpenAPI у WP-11C**,
  крок генерації, drift-перевірку в CI і інваріанти (same-origin без base URL, cookie-сесія
  замість токена в коді, CSRF, cache keys з версією схеми, cursor pagination, SSE окремо від
  query cache). Поряд — `src/api/schemaVersion.ts` (`API_SCHEMA_VERSION` placeholder,
  `API_BASE_PATH`) і `src/api/queryClient.ts` (`QueryClient` **без persister-а** — кеш лише
  в пам'яті, §7.7 «контакти не кешуються в browser storage»).
- `src/features/README.md` — порожньо за задумом, owner WP-11C.

### 3. Заборона `localStorage`/`sessionStorage` (вимога 3)

- `web/eslint.config.js`: константа `STORAGE_BAN` з розгорнутим коментарем-причиною
  (§13: XSS-читання, немає `HttpOnly`/`SameSite`, не інвалідується сервером; сесія — у
  `HttpOnly`-cookie від BFF) використана у трьох правилах для `src/**` і `tests/unit/**`:
  `no-restricted-globals` (голі `localStorage`/`sessionStorage`), `no-restricted-properties`
  (`window.*`, `globalThis.*`) і `no-restricted-syntax` (обчислений доступ
  `window['localStorage']` та `self.localStorage` — вони обходять перші два правила).
- **Перевірка, що правило працює** — `web/tests/unit/eslint-no-browser-storage.test.ts`
  (11 тестів): бере конфіг через `ESLint.calculateConfigForFile('src/main.tsx')`, доводить,
  що правила ввімкнені як `error` і що повідомлення містить «§13» та «HttpOnly»; лінтує
  сім форм обходу і вимагає помилку на кожній; доводить, що чистий код не падає; і лінтує
  реальні `src/**` — жодного порушення. Додатково E2E перевіряє, що після завантаження
  сторінки `localStorage.length === sessionStorage.length === 0`.

### 4. GUI image і nginx (вимога 4)

- `web/Dockerfile` — multi-stage:
  `node:24.19-alpine@sha256:d32cdf61…` (builder, `npm ci` за lockfile) →
  `nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56a…` (runtime). Обидва pinned
  tag + digest (multi-arch index, `docker buildx imagetools inspect`). У runtime-шарі немає
  ні Node, ні вихідників, ні `node_modules` — лише `dist/`. `USER 101:101`, `EXPOSE 8080`,
  `HEALTHCHECK`, OCI labels (`revision` = Git SHA, `title=collector-gui`, `base.name`).
  Секретів у build немає (`ARG`/`ENV` без credentials; `web/.dockerignore` виключає
  `node_modules`, `dist`, `tests/e2e`).
- `deploy/compose/gui/nginx.conf` приходить у build **окремим контекстом** `gui-conf`
  (`build.additional_contexts`), бо належить deploy-шару, а не застосунку; копіюється в
  image як `/etc/nginx/conf.d/default.conf` — тому rootfs лишається read-only і конфіг не
  підмінити на хості bind-mount-ом.
- Конфіг задає: restrictive CSP (`default-src 'none'; script-src 'self'; style-src 'self';
  img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self';
  base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'`),
  security headers (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy`, COOP/COEP/CORP, HSTS), `server_tokens off`, SPA fallback
  `try_files … /index.html`, довгий кеш для `/assets/` через `expires` (а не `add_header`,
  який скинув би успадковані security headers), same-origin reverse proxy `/api` →
  `api:8000` з `proxy_buffering off` і `proxy_read_timeout 300s` під SSE у WP-11C.
- `listen 8080` (non-root не може <1024); `proxy_pass` через змінну + `resolver 127.0.0.11`,
  щоб перестворення контейнера `api` не ламало проксі назавжди закешованим IP.
- **Публічний `/api/v1/health/components`** (§13 / gate 3 CR-14): назовні віддається лише
  `{"status":"ready"}` або 503 `{"status":"not_ready"}`; детальний звіт компонентів не
  виходить до OIDC (WP-11A). Узагальнення робить `auth_request` до внутрішнього location.
- **Знайдено й виправлено на стенді:** перша версія формувала «ready» через `return` у тій
  самій location — `return` виконується у rewrite-фазі, тобто **до** access-фази з
  `auth_request`, і endpoint віддавав `ready` навіть при повністю зупиненому `api`.
  Виправлено: успішну відповідь формує named location через `try_files` (content-фаза),
  помилку — `error_page 500 502 504 = @api_not_ready`, оголошений усередині location, щоб
  500 від реального API (WP-11A) не маскувався під `not_ready`. Перевірено обидві гілки
  (див. «Команди та вивід», §5).
- **Мережі** (gate 3 CR-14/SEC L-2, §7.5 «API — лише OIDC egress»): додано internal-мережу
  `frontend`; `api` **прибрано з `ingress`** (тепер `backend` + `frontend`), `gui` — у
  `ingress` + `frontend`. `gui` не має доступу ні до `backend` (PostgreSQL/MongoDB/MinIO),
  ні до workers — перевірено з контейнера.

### 5. Сервіс `gui` у `docker-compose.yml`

`profiles: [gui]`, image `${COLLECTOR_GUI_IMAGE:-collector-gui:dev}`, `read_only: true`,
`user: "101:101"`, `cap_drop: [ALL]`, `no-new-privileges`, `init: true`, tmpfs `/tmp` і
`/var/cache/nginx` (mode 0700, uid/gid 101 — nginx створює там temp-каталоги), `pids_limit`
256 через `deploy.resources.limits` (cpus 0.50, memory 512M), json-file log rotation
(20m × 5) — як у решти сервісів, `stop_grace_period: 30s`,
`depends_on: api: service_healthy`, healthcheck exec-формою (`CMD wget --spider
http://127.0.0.1:8080/api/v1/health/components` — одночасно процес і критична dependency),
`ports: ${GUI_PORT:-80}:8080` — **єдиний published port усього стека**. Ні `volumes`, ні
`secrets`, ні `environment` сервіс не має.

### 6. Скрипти `npm` (вимога 5)

`lint` (ESLint `--max-warnings 0` + `prettier --check`), `test` (Vitest), `build`
(`tsc -b` + `vite build`), `test:e2e` (Playwright: сам збирає й піднімає `vite preview` на
`127.0.0.1:4173`), плюс `dev`, `preview`, `format`, `test:watch`.

E2E — 3 smoke-тести: сторінка українською з правильним `<title>`/`<h1>`/`lang` і дев'ятьма
пунктами; невідомий маршрут віддає SPA-сторінку 404 зі **статусом 200** (не помилку
сервера); browser storage порожній. E2E проти Docker stack — WP-11C.

### 7. CI (`.github/workflows/ci.yml`)

- Новий job **`web`**: `actions/setup-node@v6` з `node-version-file: web/.nvmrc` і
  `cache: npm` (`cache-dependency-path: web/package-lock.json`) → `npm ci` → `npm run lint`
  → `npm run test` → `npm run build` → `npx playwright install --with-deps chromium` →
  `npm run test:e2e` (окремим кроком, як вимагає картка) → upload `playwright-report` при
  падінні.
- Job **`docker`**: `COMPOSE_PROFILES=core,workers,gui`, `COLLECTOR_GUI_IMAGE=collector-gui:ci`;
  `config --quiet` для всіх profiles включно з `gui`; крок `docker compose build gui` +
  перевірка `Config.User == 101:101`; `up -d --wait` тепер піднімає core+workers+gui (це
  acceptance §16.3 усього WP-00); новий крок перевіряє сторінку, CSP/`nosniff`/`DENY` у
  заголовках і `{"status":"ready"}` через проксі.

### 8. Unit-тести на compose-конфіг для `gui`

У `tests/unit/test_compose_config.py` додано шість тестів: non-root/read-only/tmpfs/без
secrets і volumes; єдиний ingress + `depends_on api` + pids/healthcheck; multi-stage image з
pinned digests і `USER 101:101`; build-контексти (`./web` + `gui-conf`) і `.nvmrc`; CSP і
security headers у `nginx.conf`; проксі `/api` + `resolver` + SPA fallback + приховування
деталей health. Оновлено наявні: `gui` у таблиці profiles §7.5, мережа `frontend`,
`test_only_gui_publishes_a_port` (замість «портів немає взагалі»), `api` без `ingress`,
`test_ci_runs_web_pipeline_from_spec_16_2`. Те саме — у
`tests/integration/test_compose_render.py` (рендер через `docker compose config --format
json`): profile `gui`, `test_gui_sees_only_api_and_api_left_ingress`.

### 9. Документація

`web/README.md` (швидкий старт, скрипти, структура, code splitting, заборона storage,
контейнер, що навмисно не зроблено); розділ «Operator GUI» і оновлена команда запуску стека
у кореневому `README.md`; `deploy/compose/README.md` (profile `gui`, мережа `frontend`,
розділ про `gui/nginx.conf`, змінні `COLLECTOR_GUI_IMAGE`/`GUI_PORT`);
`docs/runbooks/clean-host-start.md` (повна команда §16.2, перевірки `curl`, два нові рядки
у «Типові проблеми»); `docs/runbooks/rollback-image.md` (розділ про image `collector-gui` —
як і передбачала картка).

## Команди та вивід

### 1. `cd web && npm ci && npm run lint && npm run test && npm run build && npm run test:e2e`

Виконано на **чистій** теці (`rm -rf node_modules dist`), один ланцюжок, `exit=0`:

```text
added 305 packages in 3m
npm warn allow-scripts 1 package has install scripts not yet covered by allowScripts:
npm warn allow-scripts   esbuild@0.25.12 (postinstall: node install.js)

> collector-web@0.1.0 lint
> eslint . --max-warnings 0 && prettier --check .
Checking formatting...
All matched files use Prettier code style!

> collector-web@0.1.0 test
> vitest run
 ✓ tests/unit/routes.test.tsx (5 tests) 209ms
 ✓ tests/unit/eslint-no-browser-storage.test.ts (11 tests) 4089ms
 Test Files  2 passed (2)
      Tests  16 passed (16)

> collector-web@0.1.0 build
> tsc -b --force && vite build
vite v7.1.6 building for production...
✓ 94 modules transformed.
dist/index.html                       0.80 kB │ gzip:  0.54 kB
dist/assets/index-C6SdZp5L.css        0.64 kB │ gzip:  0.36 kB
dist/assets/NotFoundPage-EZJrWIPl.js  0.52 kB │ gzip:  0.37 kB │ map:     0.86 kB
dist/assets/OverviewPage-CWQ785UW.js  2.44 kB │ gzip:  1.35 kB │ map:     4.56 kB
dist/assets/index-CyBfb2hD.js       291.60 kB │ gzip: 93.20 kB │ map: 1,450.42 kB
✓ built in 2.05s

> collector-web@0.1.0 test:e2e
> playwright test
Running 3 tests using 3 workers
  ok 3 [chromium] › smoke.spec.ts:15:3 › невідомий маршрут віддає SPA-сторінку 404… (333ms)
  ok 2 [chromium] › smoke.spec.ts:3:3  › головна сторінка рендериться українською (1.1s)
  ok 1 [chromium] › smoke.spec.ts:24:3 › застосунок нічого не пише у browser storage (§13) (6.5s)
  3 passed (1.6m)
```

`npm warn allow-scripts` — npm 11.17 за замовчуванням не виконує postinstall-скрипти;
для `esbuild` це нешкідливо (бінарник приходить окремим optional-пакетом
`@esbuild/win32-x64`), перевірено `esbuild.transformSync` і зеленою збіркою.

### 2. Python-перевірки (регресія PR1/PR2 після змін у compose і тестах)

```text
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
132 files already formatted

$ uv run mypy src
Success: no issues found in 40 source files

$ uv run pytest -m "not live" -q
594 passed, 1 skipped, 8 warnings in 80.78s

$ uv run pre-commit run --all-files
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

### 3. `docker compose config --quiet` і `docker compose build --pull`

```text
$ docker compose config --quiet
exit=0

$ docker compose build --pull
 Image collector:dev Built (×11)
 Image collector-gui:dev Built
```

### 4. Clean-host acceptance §16.2/§16.3 (усього WP-00)

```text
$ docker compose --profile core --profile workers --profile gui up -d --wait
 Container collector-postgres-1 Healthy
 Container collector-mongo-1 Healthy
 Container collector-minio-1 Healthy
 Container collector-migrate-postgres-1 Exited
 Container collector-ensure-mongo-1 Exited
 Container collector-api-1 Healthy
 Container collector-scheduler-1 Healthy
 Container collector-discovery-worker-1 Healthy
 Container collector-fetch-worker-1 Healthy
 Container collector-fetch-worker-2 Healthy
 Container collector-parse-worker-1 Healthy
 Container collector-parse-worker-2 Healthy
 Container collector-projector-worker-1 Healthy
 Container collector-translation-worker-1 Healthy
 Container collector-export-worker-1 Healthy
 Container collector-maintenance-worker-1 Healthy
 Container collector-gui-1 Healthy
exit=0

$ docker compose ps
SERVICE              STATUS                    PORTS
api                  Up 23 seconds (healthy)   8000/tcp
gui                  Up 16 seconds (healthy)   0.0.0.0:80->8080/tcp, [::]:80->8080/tcp
…                    Up … (healthy)
(gui — єдиний сервіс з published port)

$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 17 containers healthy or exited 0
exit=0
```

### 5. Acceptance-перевірки GUI

```text
$ curl -sI http://localhost/
HTTP/1.1 200 OK
Content-Type: text/html; charset=utf-8
Cache-Control: no-cache
Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
Strict-Transport-Security: max-age=31536000; includeSubDomains

$ curl -s http://localhost/ | head -16
<!doctype html>
<html lang="uk">
…
    <title>UA Web Data Collector — operator GUI</title>
    <script type="module" crossorigin src="/assets/index-CyBfb2hD.js"></script>
    <link rel="stylesheet" crossorigin href="/assets/index-C6SdZp5L.css">
  </head>
  <body>
    <div id="root"></div>
    <noscript>Для роботи інтерфейсу потрібен увімкнений JavaScript.</noscript>

$ curl -sD - http://localhost/api/v1/health/components
HTTP/1.1 200 OK
Content-Type: application/json
(ті самі CSP і security headers)

{"status":"ready"}

$ docker inspect collector-gui:dev --format '{{.Config.User}}'
101:101
$ docker inspect collector-gui-1 --format 'ReadonlyRootfs=… CapDrop=… PidsLimit=… SecurityOpt=…'
ReadonlyRootfs=true CapDrop=[ALL] PidsLimit=256 SecurityOpt=[no-new-privileges:true]
$ docker inspect collector-gui:dev --format '{{index .Config.Labels "org.opencontainers.image.revision"}} / {{…"title"}}'
bcae1adad91befd849e394f00e2ea67b4bf24439 / collector-gui

$ docker run --rm --entrypoint env collector-gui:dev | sort
DYNPKG_RELEASE=1
HOME=/var/cache/nginx
HOSTNAME=3ee2dbc1fc1e
NGINX_VERSION=1.29.8
NJS_RELEASE=1
NJS_VERSION=0.9.6
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PKG_RELEASE=1
(жодного секрету в image)
```

Ізоляція мереж і поведінка при недоступному `api`:

```text
$ docker compose exec gui: доступність backend-сервісів
  postgres: недоступний (очікувано)
  mongo: недоступний (очікувано)
  minio: недоступний (очікувано)
  nslookup postgres: *** Can't find postgres: No answer
  api: HTTP/1.1 200 OK

$ docker compose stop api
  curl /api/v1/health/components → status=503 body={"status":"not_ready"}
  curl / (статика)               → status=200
  очікуємо перехід gui в unhealthy:
    t+10s: Up About a minute (healthy)
    t+20s: Up About a minute (healthy)
    t+30s: Up About a minute (healthy)
    t+40s: Up About a minute (unhealthy)

$ docker compose up -d --wait   (відновлення api)
  gui: Up 2 minutes (healthy)
  curl /api/v1/health/components → status=200 body={"status":"ready"}
all 17 containers healthy or exited 0
```

### 6. `docker compose down -v`

```text
 Volume collector_mongo-config Removed
 Volume collector_minio-data Removed
 Volume collector_mongo-data Removed
 Network collector_frontend Removed
 Network collector_provider_egress Removed
 Network collector_backend Removed
 Network collector_ingress Removed
 Network collector_source_egress Removed
exit=0
```

## Відповідність acceptance картки

| Пункт acceptance | Стан | Доказ |
|---|---|---|
| усі команди зелені | так | §1–§4 «Команди та вивід» |
| `gui` container `healthy` | так | §4, `docker compose ps` |
| `curl http://localhost/` віддає сторінку | так | §5 |
| `curl http://localhost/api/v1/health/components` проксіюється до api | так | §5: `ready`/`not_ready` слідує за станом `api` (реальний `auth_request` до `api:8000`) |
| CSP/security headers у відповіді | так | §5, плюс `test_gui_nginx_has_restrictive_csp_and_security_headers` |
| container non-root | так | §5 (`Config.User=101:101`, `ReadonlyRootfs=true`) |
| clean-host команда §16.2 зелена | так | §4 (17 контейнерів healthy/exited 0) |

## Що не перевірено

- **E2E проти Docker stack** — за карткою це WP-11C; тут E2E ганяється проти
  `vite preview`.
- **CI-прогони** (`job web`, оновлений `job docker`) — локально не виконувались: перевірено
  лише валідність YAML (`yaml.safe_load`) і структурними тестами
  (`test_ci_runs_web_pipeline_from_spec_16_2`, adversarial-тести PR2). Фактичний прогон
  побачить перший PR — `operationally unverified`.
- ~~**SBOM/trivy для `collector-gui`**~~ — **втратило актуальність**: додано під час gate 2
  (SBOM syft + два trivy-скани з політикою `collector`), а локальний скан образу виконано
  на пострев'ю — див. «Локальний скан образу `collector-gui`» наприкінці звіту.
- **HSTS у дії** — заголовок віддається, але TLS termination перед `gui` з'явиться у WP-13;
  на plain HTTP браузер його ігнорує.
- **Accessibility, i18n понад `uk`, dark/light-тема** — не в scope PR3.
- **Прогін на не-Windows хості** — усе виконано на Windows 11 + Docker Desktop; Linux-шлях
  покриє CI.

## Ризики

- **npm 11.17 `allow-scripts`.** Постінстал-скрипти не виконуються за замовчуванням. Для
  поточного набору залежностей це нешкідливо (перевірено), але додавання пакета, який
  реально потребує postinstall, впаде тихо — з'ясовуватиметься вже у збірці.
- **`auth_request` і фази nginx.** Виправлена помилка (`return` у rewrite-фазі) —
  типовий грабель: будь-яка майбутня правка health-location має зберегти `try_files` →
  named location, інакше endpoint знову почне брехати. Закріплено коментарем у конфігу,
  тестом (`proxy_pass` не в тілі публічної location) і записом у
  `deploy/compose/README.md`; окремого автотесту на «ready ≠ завжди» немає — він потребує
  живого стека (кандидат у WP-11C/WP-14 E2E).
- **`gui` unhealthy при падінні `api`.** Свідомий вибір (§7.5 «process + критична
  dependency»): статика при цьому продовжує роздаватись, а `docker compose up -d --wait`
  під час недоступного `api` поверне помилку. Відновлення автоматичне (~20 с після
  повернення `api`) — перевірено.
- **Bundle 291 kB (93 kB gzip)** для каркаса — це React + Router + Query. З появою екранів
  у WP-11C варто заміряти бюджет; зараз ліміту не встановлено.
- **Один `GUI_PORT` за замовчуванням 80.** На хості з зайнятим 80 старт впаде; обхід
  задокументовано в runbook (`GUI_PORT=8081`).

## Як вимкнути або відкотити

- **Не запускати GUI:** прибрати `gui` з `COMPOSE_PROFILES` (`core,workers`) — стек
  працюватиме як у PR2, але **без публічного порту взагалі** (`api` більше не в `ingress`;
  для локального доступу — `deploy/compose/dev.override.yml`, порт 8000 на `127.0.0.1`).
- **Відкотити лише image GUI:** `docs/runbooks/rollback-image.md`, розділ
  «Image `collector-gui`» (`COLLECTOR_GUI_IMAGE`, `docker compose build gui`).
- **Відкотити PR цілком:** revert merge commit. Стану у GUI немає (ні volumes, ні БД), тому
  відкат безпечний; єдина зовнішньо видима зміна — зникає публічний порт 80.

## Dependency-запити

Немає. Усі зміни — у owned files картки PR3 плюс задокументоване нижче розширення.

### Розширення scope (за зразком PR2, знахідка F-4)

Owned files картки — `web/**`, `deploy/compose/gui/**`, `docker-compose.yml`,
`.github/workflows/ci.yml`. Додатково змінено, бо цього прямо вимагають вимоги PR3 і
«Спільні правила»:

| Файл | Навіщо |
|---|---|
| `tests/unit/test_compose_config.py` | прямо вимагає промпт задачі: unit-тест на compose-конфіг для `gui`; плюс оновлення інваріантів, які змінює вимога 4 (мережі, публічний порт) |
| `tests/integration/test_compose_render.py` | ті самі інваріанти на рівні рендеру; без оновлення тест став би червоним через profile `gui` |
| `tests/e2e/test_gui_runtime_contract.py`, `tests/e2e/test_gui_api_down_branch.py` | додані тестувальником на gate 2 (`a9beb94`); реалізатор змінював їх на gate 3 і пострев'ю (гейт skip, нові перевірки Host/sourcemaps/`/api`, дедлайн замість busy-loop) |
| `tests/e2e/test_runtime_suite_is_enforced.py` | новий: тривога проти мовчазного skip runtime-тестів там, де стек обіцяний (код-рев'ю H-1) |
| `tests/unit/test_compose_config_adversarial.py` | два інваріанти PR2 потребували уточнення: healthcheck `gui` (критична dependency — `api`, не БД) і перевірка privileged-режиму, яка хибно спрацьовувала на назві образу `nginx-unprivileged` (тепер regex на директиву, а не підрядок) |
| `.gitattributes` | `web/** text eol=lf` — інакше Windows `core.autocrlf` дає CRLF у робочій копії і `prettier --check` (`endOfLine: lf`) падає локально при зеленому CI |
| `web/README.md` | етап «Docs» картки для PR3 |
| `README.md`, `deploy/compose/README.md`, `docs/runbooks/clean-host-start.md`, `docs/runbooks/rollback-image.md` | документація PR2 явно посилалась на «`gui` з'явиться у PR3» і на команду без `--profile gui`; лишити її застарілою означало б залишити runbook, що не відтворює §16.2. `rollback-image.md` доповнено розділом про `gui`-image — як і передбачала картка («PR3 за потреби доповнить») |

Forbidden-файли (`docs/research/**`, `TECHNICAL_SPECIFICATION.md`, `REVIEW.md`) не
змінювались.

---

## Виправлення після gate 2

Вхід: `docs/plan/reports/WP-00/testing-pr3.md` (вердикт **pass** із знахідками), 24 тести
тестувальника в `a9beb94`. База виправлень — `0778439` + `a9beb94`.

### Відповідь на кожну знахідку

| # | Severity | Знахідка | Рішення | Що саме зроблено |
|---|---|---|---|---|
| H-1 (а) | high | 6 CVE у npm (1 critical, 4 high, 1 moderate); `react-router` 7.9.1 — **runtime**-залежність, яка їде у bundle | **fixed** | `react-router` 7.9.1 → **7.18.4**, `vite` 7.1.6 → **7.3.6**, `@playwright/test` 1.55.0 → **1.63.0**, `vitest` 3.2.4 → **4.1.11**. Після оновлення `npm audit --omit=dev` і повний `npm audit` — **`found 0 vulnerabilities`** обидва. Датоване risk acceptance не потрібне: не лишилось вразливостей жодного рівня |
| H-1 (б) | high | у CI немає сканування npm; образ `collector-gui` не сканується взагалі (§13 «на кожен PR») | **fixed** | Job `web`: крок `npm audit --omit=dev --audit-level=high` + `npm audit --audit-level=high` **після `npm ci`, до lint** (падає швидко; аудит іде по lockfile). Job `docker`: SBOM (`anchore/sbom-action`) для `collector-gui:ci` і **два trivy-скани** з тією самою політикою, що для `collector`: CRITICAL **без** `ignore-unfixed`, `exit-code: 1`; HIGH з `ignore-unfixed`. Закріплено тестами `test_ci_trivy_critical_without_ignore_unfixed_and_high_with` (тепер перевіряє **обидва** образи), `test_ci_generates_sbom_for_both_images`, `test_ci_web_job_audits_npm_dependencies` |
| M-1 | medium | заборона storage обходиться alias-ом (`const w = window; w.localStorage…`) | **fixed** | Обрано **runtime-guard** (сильніший з двох запропонованих варіантів: E2E «сховище порожнє» ловить лише факт запису на момент перевірки, guard блокує сам доступ). Додано `web/src/browserStorageGuard.ts` — `Object.defineProperty` на `localStorage`/`sessionStorage` з геттером і сеттером, що логують і кидають `TypeError` з текстом §13; ідемпотентний; `src/main.tsx` ставить його **до першого рендеру**. Межу синтаксичних правил зафіксовано в коментарі `eslint.config.js` (блок «Межа цих правил») і в `web/README.md` (розділ «Межа статичних правил і runtime-guard»). Покриття: `web/tests/unit/browser-storage-guard.test.ts` (5 тестів: throw на обох сховищах, setter, alias-форма, ідемпотентність) + E2E «guard блокує обхід ESLint через alias (§13, M-1)», який виконує саме ті форми, що проходили lint. E2E-перевірку «сховище порожнє» збережено, але вона тепер читає сховище **повз сторінку** (`context.storageState()`), бо всередині сторінки геттер заблоковано |
| L-2 | low | не задокументовано, що `gui` свідомо стає `unhealthy` при недоступному `api` | **fixed** | `deploy/compose/README.md` — новий розділ «Два різні health-и `gui`» з таблицею liveness (`/healthz`, не залежить від `api`) vs readiness (`/api/v1/health/components`, healthcheck Docker), поясненням вибору (§7.5), таймінгами (`unhealthy` за ~45 с = `interval 15s × retries 3`; автовідновлення ~20 с) і прямою вказівкою, що `up -d --wait` на деградованому стеку впаде. `docs/runbooks/clean-host-start.md` — розширено рядок про `gui unhealthy` і додано рядок про падіння `up -d --wait` |
| I-1 | info | `location =` регістрозалежний: `/API/v1/health/components` іде у SPA-fallback | **accepted** (owner WP-11A, 2026-09-22) | Витоку немає — підтверджено самим тестувальником (FastAPI теж регістрозалежний, api-стаб має рівно один маршрут; усі форми нормалізації шляху покрито `test_health_detail_not_reachable_through_path_normalization`). Робити `location ~*` зараз означало б додати регулярку в гарячий шлях заради неіснуючої загрози. Перегляд — разом з реальними endpoint-ами і OIDC у WP-11A |
| L-1 | low | dangling reference на `tests/unit/build-contract.test.ts` | **not applicable** | Закрито тестувальником у `a9beb94` — файл створено, коментар у `vite.config.ts` став правдивим. Дій реалізатора не потребує |

### Побічні виправлення, без яких «зелено» не виходило

Це не знахідки gate 2, а наслідки самих виправлень — фіксую, щоб рев'ю не шукало причину:

| Що | Чому |
|---|---|
| `web/tsconfig.test.json` (новий проєкт), `include` у `tsconfig.app.json` звужено до `src`, посилання у `tsconfig.json` | тести з `a9beb94` (`build-contract.test.ts` і доповнений `eslint-no-browser-storage.test.ts`) використовують `node:fs`/`node:path`/`process`, а `tsconfig.app.json` не має типів `node` — **`npm run build` був червоний** (9 помилок `TS2307`/`TS2591`/`TS7006`). Додати `node` у проєкт застосунку не можна: `src` має лишатись браузерним. Тому unit-тести винесено в окремий проєкт із типами `node` |
| `web/package.json` → скрипт `build:image`; `web/Dockerfile` → `npm run build:image` і `COPY tsconfig.test.json` | збірка образу впала: `tsc -b` солюшену вимагав файли тестових проєктів, а `vite build` — сам файл `tsconfig.test.json` (він резолвить `references` кореневого конфігу). У build-контексті образу тестів свідомо немає. `build:image` = `tsc -b tsconfig.app.json && vite build`; повний `npm run build` (§16.2) лишився в CI job `web` і в розробника |
| `vite.config.ts` → `testTimeout: 60_000`, `hookTimeout: 120_000` | Vitest 4 з дефолтними 5/10 с валив `beforeAll` тесту, який запускає ESLint програмно з type-aware конфігом (на холодному кеші TS це десятки секунд). Логіку тестів не змінено |
| `playwright.config.ts` → `timeout: 60_000`, `expect.timeout: 15_000` | дефолтні 30 с не витримували першого воркера на навантаженому хості одразу після `npm ci` (спостерігалось двічі: 33.6 с і 35.1 с при секундній логіці тесту). Прибирає реальний флакі-ризик і в CI |
| `web/tests/unit/eslint-no-browser-storage.test.ts` — тест тестувальника про runtime-страховку переписано | він grep-ом вимагав у E2E рівно `{ local: 0, session: 0 }`. Після появи guard-а ця перевірка змінилась (`context.storageState()`), тож тест оновлено **зі збереженням наміру**: тепер він не дає прибрати ні `src/browserStorageGuard.ts`, ні його встановлення в `main.tsx`, ні E2E-перевірку alias-обходу |

### Команди перевірки після виправлень

```text
$ cd web && npm ci && npm audit --omit=dev --audit-level=high && npm audit --audit-level=high \
    && npm run lint && npm run test && npm run build && npm run test:e2e      (чиста тека)
added 295 packages in 11s
found 0 vulnerabilities          <- npm audit --omit=dev (runtime, те що їде в браузер)
found 0 vulnerabilities          <- npm audit (усе, включно з dev)

> lint: eslint . --max-warnings 0 && prettier --check .
All matched files use Prettier code style!

> test: vitest run  (v4.1.11)
 Test Files  3 passed | 1 skipped (4)
      Tests  24 passed | 4 skipped (28)
  (skip: build-contract.test.ts — у ланцюжку §16.2 тести йдуть до build, dist/ ще немає)

> build: tsc -b --force && vite build
dist/assets/NotFoundPage-DnNC4SfP.js    0.52 kB
dist/assets/OverviewPage-0PRu27X3.js    2.44 kB
dist/assets/index-CgGEjsJW.js         305.64 kB | gzip: 98.21 kB

> test:e2e: playwright test
  ok 2 > невідомий маршрут віддає SPA-сторінку 404, а не помилку сервера (423ms)
  ok 3 > guard блокує обхід ESLint через alias (§13, M-1) (469ms)
  ok 1 > головна сторінка рендериться українською (579ms)
  ok 4 > застосунок нічого не пише у browser storage (§13) (518ms)
  4 passed (1.9m)
EXIT=0
```

```text
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 136 files already formatted
$ uv run mypy src                -> Success: no issues found in 40 source files

$ uv run pytest -m "not live" -q   (стек не піднято)
  599 passed, 17 skipped    (skip — runtime-тести gui, що потребують живого контейнера)
$ uv run pytest -m "not live" -q   (стек піднято)
  615 passed, 1 skipped     (усі 19 тестів тестувальника проти живого gui — зелені)

$ uv run pre-commit run --all-files
fix end of files / trim trailing whitespace / check yaml / check toml /
check for added large files / check for merge conflicts / detect private key /
ruff check / ruff format / Detect hardcoded secrets / markdownlint-cli2 ....... Passed
```

```text
$ docker compose config --quiet                          exit=0
$ docker compose build gui                               Image collector-gui:dev Built
$ docker compose --profile core --profile workers --profile gui up -d --wait
 Container collector-gui-1 Healthy        (і решта 16)
exit=0
$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 17 containers healthy or exited 0
exit=0
$ curl -sI http://localhost/
HTTP/1.1 200 OK
Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; ...
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
$ curl -s http://localhost/api/v1/health/components   {"status":"ready"}
$ curl -s http://localhost/healthz                    ok
$ docker compose down -v                              exit=0
```

### Що лишилось неперевіреним після виправлень

- прогін CI (`npm audit`, SBOM/trivy для `collector-gui`) — локально `syft`/`trivy` відсутні,
  як і в PR2; структура кроків закріплена тестами, фактичний прогін побачить перший PR
  (`operationally unverified`);
- `npm audit` чистий **на дату 2026-09-22**; нові адвізорі з'являються щодня — саме тому крок
  доданий у CI, а не лише прогнаний разово;
- Vitest 4 — major-стрибок з 3.2.x, обраний свідомо: у лінійці 3.x виправлення
  GHSA-82fw-gwwq-j7x9 не випущено, а `--audit-level=high` пропустив би цю moderate. Прогнано
  локально повністю; конфіг змін не потребував, крім таймаутів.

---

## Відповіді на код-рев'ю

Вхід: `docs/plan/reports/WP-00/code-review-pr3.md` (**changes_requested**: 1 high, 4 medium,
9 low) і `docs/plan/reports/WP-00/security-pr3.md` (**approve**: 0 critical/high, 2 medium,
4 low). База — `1901d6e`.

### Код-рев'ю

| # | Severity | Знахідка | Рішення | Що саме зроблено |
|---|---|---|---|---|
| H-1 | high | 19 pytest-тестів `tests/e2e/test_gui_*` і 4 тести `build-contract.test.ts` у CI гарантовано skip-аються → §13-інваріанти nginx без регресійного захисту | **fixed** | (а) job `docker` після `up -d --wait` отримав кроки `setup-uv` + `uv sync --frozen` + **`uv run pytest -m e2e tests/e2e -rs`** (між `up` і `down -v`); (б) job `web` — окремий крок **`npm run test:build`** після `npm run build` (новий npm-скрипт; у ланцюжку §16.2 `test` іде до `build`, тому контракт артефакту інакше не перевіряється); (в) **skip у CI заборонений**: у `test_gui_runtime_contract.py` і `test_gui_api_down_branch.py` умова стала `skipif(not available and not CI)`, у `build-contract.test.ts` — `skipIf(!built && !CI)`; (г) доданий тривожний тест `tests/e2e/test_runtime_suite_is_enforced.py`, який у CI падає з поясненням, якщо стек/образ відсутні. Перевірено обидва напрямки: `CI=true` зі стеком — **25 passed, 0 skipped**; `CI=true GUI_PORT=9` (імітація «стек не піднято») — тривожний тест **падає** з текстом про те, які модулі були б пропущені. Закріплено `test_ci_runs_gui_runtime_tests_against_live_stack` (порядок `up` → `pytest -m e2e` → `down -v` і `build` → `test:build`) |
| M-1 | medium | `browserStorageGuard` пише `console.error` §13 на кожному завантаженні (штатний `restoreAppliedTransitions` React Router) | **fixed** | Розділено заборону і діагностику: кидок лишився **завжди** (це і є enforcement), а лог став `console.warn` **один раз на сховище** за життя сторінки, з поміткою, що виклик міг прийти з бібліотеки і застосунок від цього не ламається. Хибної тривоги на завантаженні більше немає, майбутній E2E-асерт «немає console errors» не впаде. Закріплено тестом «попереджає в консоль один раз на сховище, а не на кожне звертання (M-1)». Зафіксовано в `web/README.md` (підрозділ «Діагностика без шуму») і в шапці модуля |
| M-2 | medium | production-образ публічно віддає source maps (1.5 МБ, `expires 1y`) | **fixed** | Два незалежні шари: (а) `vite build --mode image` (скрипт `build:image`, який виконує `web/Dockerfile`) вимикає `sourcemap` — `defineConfig(({ mode }) => …)`, `sourcemap: mode !== 'image'`; локальний `npm run build` мапи лишає, вони потрібні розробнику; (б) `location ~ \.map$ { return 404; }` у nginx — страхувальний шар на випадок, якщо мапи колись знову потраплять у образ. Перевірено на живому стеку: у `/usr/share/nginx/html/assets` **0** файлів `.map`, `GET /assets/index-*.js.map` → **404**. Закріплено `test_gui_nginx_does_not_serve_source_maps` (обидва шари) і runtime-тестом `test_source_maps_are_not_served` |
| M-3 | medium | `X-Forwarded-For $proxy_add_x_forwarded_for` на єдиному edge дозволяє підробити перший елемент | **fixed** | `proxy_set_header X-Forwarded-For $remote_addr;` — edge є джерелом істини. Коментар для WP-11A/WP-13: повертати `$proxy_add_x_forwarded_for` лише коли перед `gui` з'явиться довірений TLS-termination. Закріплено `test_gui_nginx_does_not_forward_spoofable_client_ip` (перевіряє і відсутність `$proxy_add_x_forwarded_for` серед директив — коментарі при цьому не рахуються) |
| M-3 (суміжне) | medium | `proxy_set_header Host $host` при `server_name _` пропускає будь-який Host усередину | **fixed** (разом із SEC M-1, див. нижче) | Клієнтський `Host` більше не пересилається зовсім |
| M-4 | medium | `_wait_until_serving` — busy-loop без пауз (фактичне вікно ~0.05 с); `_free_port()` — TOCTOU | **fixed** | Явний дедлайн за `time.monotonic()` + `time.sleep(0.5)` між спробами (параметр став `timeout: float = 60.0`, а не `attempts`). Порт більше не вибирається заздалегідь: `docker run -p 127.0.0.1::8080`, а реальний порт зчитується через `docker port` (`_published_port`) — TOCTOU зник |
| L-1 | low | `npm audit` продубльовано; dev-only HIGH блокує будь-який PR | **accepted** (owner WP-00, 2026-09-22) | Я був почав знижувати поріг повного аудиту до `critical`, але координатор справедливо зупинив: це послаблення прямо суперечить тому, за що закривалась H-1 gate 2 (HIGH у toolchain проходив би мовчки). Обидва прогони лишаються блокуючими на HIGH; перший (`--omit=dev`) дає окреме повідомлення саме про runtime-залежності. Якщо колись з'явиться dev-only HIGH без фіксу — точковий `--exclude <pkg>` з CVE ID, owner і датою в ADR, а не глобальне зниження порогу; це записано коментарем у `ci.yml`. `test_ci_web_job_audits_npm_dependencies` лишився без змін |
| L-2 | low | глобальні таймаути Vitest підняті заради одного повільного файлу | **fixed** | Таймаути прибрані з `vite.config.ts` і перенесені точково: `describe('…', { timeout: 120_000 }, …)` у `eslint-no-browser-storage.test.ts`. Зависання будь-якого іншого тесту знову коштує 5 с |
| L-3 | low | `resolver` без `resolver_timeout` | **fixed** | `resolver_timeout 3s;` — фаза DNS тепер обмежена (раніше зависання резолвера тримало б запит до дефолтних 30 с попри `proxy_connect_timeout 3s`) |
| L-4 | low | tripwire-тести перевіряють текст файлів, а не поведінку | **fixed** | Асерти на форму реалізації прибрані (`Object.defineProperty`, `throw new TypeError`, `NOT BLOCKED`, `storageState()`). Лишився один тест — факт існування модуля і його виклику в `main.tsx`; поведінку покривають `browser-storage-guard.test.ts` і E2E |
| L-5 | low | `auth_request` віддає 401/403 підзапиту повз `error_page` — зламається, коли WP-11A закриє health автентифікацією | **accepted** (owner WP-11A, 2026-09-22) | Виправити зараз нічим: поки в API немає публічно-безпечного `GET /api/v1/health`, розвести liveness/readiness інакше не можна. У конфіг доданий блок-попередження прямо над `auth_request`: що саме зламається (публічний endpoint почне віддавати 401, healthcheck `gui` стане постійно failing) і що робити (readiness спирати на публічно-безпечний endpoint API; `/healthz` для liveness уже є). Ширшу рекомендацію рев'ю — прибрати всю конструкцію `auth_request` → `try_files` → `error_page` на користь одного `proxy_pass`, коли API дасть такий endpoint — приймаю як план WP-11A |
| L-6 | low | guard знімається одним `delete`, а документація подає його поряд з «XSS миттєво віддає токен» | **fixed** (разом із SEC L-2) | Шапка модуля і `web/README.md` отримали таблицю точних меж: що заблоковано, що ні (same-origin iframe, IndexedDB/Cache API, активний XSS), і пряме речення, що guard — anti-footgun проти власної необережності, а не security-контроль; проти зловмисника працюють CSP і відсутність токена в JS |
| L-7 | low | image-збірка змушена копіювати `tsconfig.test.json` для файлів, яких у контексті немає | **accepted** (owner WP-00, 2026-09-22) | Зв'язок неочевидний, але задокументований коментарем у `Dockerfile`, і альтернативи гірші: прибрати solution-references з кореневого `tsconfig.json` зламало б `tsc -b` одним рядком для розробника, а `esbuild.tsconfigRaw` дублює конфіг у двох місцях. Ціна поточного рішення — один рядок `COPY`; перегляд доречний, якщо набір TS-проєктів зміниться (напр. WP-11C додасть свій) |
| L-8 | low | `/api` без слеша падає у SPA-fallback; для `/api/` немає cache-директив | **fixed** | `location = /api { return 404 '{"detail":"Not Found"}'; }` — більше не index.html з кодом 200 (перевірено live: **404**). У `location /api/` доданий `expires -1;` (`no-cache` для клієнта) — саме `expires`, а не `add_header`, щоб не скинути успадковані security headers; канонічне місце для `no-store` лишається за API (WP-11A). Закріплено `test_gui_nginx_resolver_has_timeout_and_api_without_slash_is_not_spa` і runtime-тестом `test_api_without_trailing_slash_is_not_spa_fallback` |
| L-9 (`Connection ""`) | low | директива без `upstream{} … keepalive` нічого не вмикає, коментар вводить в оману | **fixed** | Рядок прибраний разом із коментарем |
| L-9 (build args GUI) | low | `docker compose build gui` не отримує build args → `image.revision` завжди `unknown` | **fixed** | Крок передає `COLLECTOR_VERSION` і `COLLECTOR_CREATED` (`COLLECTOR_GIT_SHA` уже в env workflow) і **перевіряє** через `docker inspect`, що label `org.opencontainers.image.revision` дорівнює `github.sha` — тепер регресія впаде, а не пройде тихо |
| L-9 (`.dockerignore`) | low | виключено `tests/e2e`, але не `tests/unit` | **accepted** (owner WP-00, 2026-09-22) | У образ вони не потрапляють (`COPY` перелічений явно), а `tests/unit` тепер потрібен у контексті не більше, ніж раніше. Змінювати `.dockerignore` заради розміру контексту (десятки КБ) не варто ризику зламати збірку перед merge |
| L-9 (кеш trivy) | low | 4 прогони trivy тягнуть БД вразливостей заново | **accepted** (owner WP-13, 2026-09-22) | Кешування trivy — оптимізація CI-часу, не безпеки; природно робити разом із рештою налаштування сканування у WP-13. `skip-setup-trivy: true` на трьох із чотирьох кроків уже стоїть |

### Security-рев'ю

| # | Severity | Знахідка | Рішення | Що саме зроблено |
|---|---|---|---|---|
| SEC M-1 | medium | Host header injection: `server_name _` + `proxy_set_header Host $host` → `curl -H "Host: evil.example.com"` давав `location: http://evil.example.com/...` | **fixed** | Три зміни разом: (а) `set $api_host api:8000;` і `proxy_set_header Host $api_host;` — api більше ніколи не бачить клієнтський Host; (б) `proxy_redirect http://$api_host/ /;` — absolute-Location від api стає відносним, тому штатні редиректи в браузері лишаються робочими; (в) **`absolute_redirect off;`** — без цього nginx перетворював відносний Location назад на абсолютний, підставляючи `$host`, і підроблений Host з'являвся знову (виявлено на стенді вже після (а)+(б): `location: http://evil.example.com:8080/…`). `X-Forwarded-Host` свідомо не передаємо — це був би той самий підроблюваний Host під іншим іменем. Перевірено live: з `Host: evil.example.com` і з нормальним Host відповідь однакова — `location: /api/v1/health/components`. Закріплено `test_gui_nginx_does_not_forward_client_host_to_api` (статика) і runtime-тестами `test_forged_host_does_not_reach_api` + `test_forged_host_does_not_break_static` |
| SEC M-1 (catch-all 444) | medium | немає `default_server`-блока `return 444` | **accepted** (owner WP-13, 2026-09-22) | `server_name` allowlist + `default_server { return 444; }` вимагає знати публічне ім'я хоста, а воно з'являється лише з TLS-termination WP-13; зараз стек документовано доступний і як `localhost`, і як `127.0.0.1`, і за `GUI_PORT`, тому будь-який жорсткий allowlist зламав би runbook. Головне — підтверджений імпакт (attacker-controlled absolute URL у backend) закритий повністю пунктом вище: api Host не бачить, Location від Host не залежить. Вимогу записано коментарем у `nginx.conf` поруч із `proxy_set_header Host` |
| SEC M-2 | medium | дефолтний `combined` логує query string → `?access_token=…` осідає в json-file лозі хоста | **fixed** | Власний `log_format gui_no_query` (http-контекст) з `$request_method $uri $server_protocol` замість `$request` — query відкидається на рівні формату, а не маскується. `access_log /var/log/nginx/access.log gui_no_query;` у server-блоці. Перевірено live: запит `?access_token=SECRETQUERY789&code=AUTHCODE42` дає в лозі `"GET /api/v1/status HTTP/1.1"`, `grep` по всіх логах `gui` секрету не знаходить. Це закриває і майбутній OIDC callback WP-11A (`?code=…&state=…`). Закріплено `test_gui_nginx_access_log_drops_query_string` |
| SEC L-1 | low | source maps публічно | **fixed** | Той самий фікс, що код-рев'ю M-2 (див. вище) |
| SEC L-2 | low | guard обходиться через same-origin iframe і не бачить IndexedDB → docstring перебільшує охоплення | **fixed** | (а) Формулювання «обхід помирає … у будь-якій залежності, що потрапила в bundle» прибране; шапка модуля і `web/README.md` тепер містять таблицю з явним «не заблоковано» для same-origin iframe, IndexedDB/Cache API і активного XSS. (б) ESLint розширено: `indexedDB` і `caches` додані до `no-restricted-globals` і `no-restricted-properties` з окремим повідомленням `PERSIST_BAN`, яке прямо каже, що runtime-guard їх не покриває і правило — єдиний бар'єр |
| SEC L-3 | low | `ingress` не `internal` → gui має egress в Інтернет | **accepted** (owner WP-13, 2026-09-22) | Публікація порту технічно вимагає не-`internal` мережі; у Compose без host firewall це не вирішується. Внутрішня сегментація виконана (gui не бачить `backend`). Питання належить egress-allowlist разом із TLS termination — як і рекомендує звіт |
| SEC L-4 | low | spoofable `X-Forwarded-For` | **fixed** | Той самий фікс, що код-рев'ю M-3 (див. вище) |

### Побічні виправлення

| Що | Чому |
|---|---|
| `tests/unit/test_compose_config.py` — хелпери `_nginx_block` / `_nginx_directives` | нові assert-и перевіряють відсутність рядків (`$host`, `$proxy_add_x_forwarded_for`, `X-Forwarded-Host`), які згадуються у коментарях-поясненнях «чому ми так НЕ робимо». Без відсікання коментарів тести падали на власній документації |
| `tests/e2e/test_gui_runtime_contract.py` — `_get(..., headers=, follow_redirects=)` + `_NoRedirect` | тест Host injection має прочитати сам заголовок `Location`; за замовчуванням urllib пішов би за редиректом, тобто спробував би реальну мережу на `evil.example.com` (і був би заблокований pytest-socket, замаскувавши предмет перевірки) |
| `web/tests/unit/eslint-no-browser-storage.test.ts` — очікуваний перелік заборонених ідентифікаторів | після SEC L-2 у `no-restricted-globals` їх чотири (`localStorage`, `sessionStorage`, `indexedDB`, `caches`); перевірка на «§13» лишилась для всіх, перевірка на «HttpOnly» — для storage-повідомлення |

### Команди перевірки після виправлень

```text
$ cd web && npm ci && npm audit --omit=dev --audit-level=high && npm audit --audit-level=high \
    && npm run lint && npm run test && npm run build && npm run test:build && npm run test:e2e
found 0 vulnerabilities      <- runtime
found 0 vulnerabilities      <- увесь toolchain (поріг HIGH збережено)
lint: All matched files use Prettier code style!
test: Test Files 3 passed | 1 skipped (4) | Tests 24 passed | 4 skipped (28)
build: dist/assets/{NotFoundPage,OverviewPage,index}-*.js  (+ .map лише для локальної збірки)
test:build: Test Files 1 passed (1) | Tests 4 passed (4)      <- H-1: контракт dist/ тепер виконується
test:e2e: 4 passed (5.7m)
EXIT=0
```

```text
$ uv run ruff check .            -> All checks passed!
$ uv run ruff format --check .   -> 139 files already formatted
$ uv run mypy src                -> Success: no issues found in 40 source files

$ CI=true uv run pytest -m "not live" -q          (стек піднято)
  627 passed, 1 skipped
$ uv run pytest -m e2e tests/e2e -q -rs           (локально)
  23 passed, 2 skipped   (skip — лише тривожний модуль, він діє тільки в CI)
$ CI=true uv run pytest -m e2e tests/e2e -q -rs   (режим CI)
  25 passed            <- жодного skip: H-1 закрито
$ CI=true GUI_PORT=9 uv run pytest tests/e2e/test_runtime_suite_is_enforced.py -q
  1 failed, 1 passed   <- імітація «стек не піднято» у CI: тривога спрацьовує

$ uv run pre-commit run --all-files   -> усі hooks Passed
```

```text
$ docker compose build gui                     Image collector-gui:dev Built
$ docker compose --profile core --profile workers --profile gui up -d --wait   exit=0
$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 17 containers healthy or exited 0

# SEC M-1 (Host injection)
$ curl -sI -H "Host: evil.example.com" http://localhost/api/v1/health/components/
HTTP/1.1 307 Temporary Redirect
location: /api/v1/health/components          <- підробленого Host немає
$ curl -sI http://localhost/api/v1/health/components/
location: /api/v1/health/components          <- нормальний Host дає те саме

# SEC M-2 (query string у логах)
$ curl -s "http://localhost/api/v1/status?access_token=SECRETQUERY789&code=AUTHCODE42"
$ docker compose logs gui | tail -1
gui-1 | 172.19.0.1 - - [...] "GET /api/v1/status HTTP/1.1" 404 22 "-" "UA-PROBE"
$ docker compose logs gui | grep -c "SECRETQUERY789\|AUTHCODE42"   -> 0

# CR M-2 / SEC L-1 (source maps)
$ docker compose exec gui sh -c 'ls /usr/share/nginx/html/assets | grep -c "\.map$"'   -> 0
$ curl -s -o /dev/null -w '%{http_code}' http://localhost/assets/index-*.js.map        -> 404

# CR L-8 (`/api` без слеша)
$ curl -s -o /dev/null -w '%{http_code}' http://localhost/api                          -> 404

$ docker compose down -v                      exit=0
```

### Що лишилось неперевіреним

- фактичний прогін CI (нові кроки `pytest -m e2e`, `npm run test:build`, перевірка label
  `revision`) — локально відтворено їхній зміст, але сам workflow побачить перший PR;
- `default_server { return 444; }` і egress-allowlist — свідомо не робились (owner WP-13):
  без відомого публічного імені хоста allowlist зламав би задокументований доступ
  `http://localhost/`;
- поведінка `auth_request` після того, як WP-11A закриє health автентифікацією — за
  визначенням не перевіряється до появи OIDC; попередження лишене в конфігу (L-5).

---

## Відповіді на пострев'ю

Вхід: `docs/plan/reports/WP-00/spec-review-pr3.md` (**changes_requested**, 1 high + 3 low).
База — `f371700`.

| # | Severity | Знахідка | Рішення | Що саме зроблено |
|---|---|---|---|---|
| S-1 | high | заборона skip прив'язана до універсальної `CI`, яку GitHub Actions ставить в УСІХ job-ах → job `python` падав би на кожному PR (без стека e2e-модулі виконувались замість skip) | **fixed** | Знахідка підтверджена дослівно: `CI=true uv run pytest -m e2e tests/e2e` без стека → **21 failed, 4 passed**. Причина моєї помилки — я перевіряв `CI=true` лише з піднятим стеком. Два незалежні виправлення: (1) гейт тепер на власній змінній **`COLLECTOR_E2E_REQUIRED`**, яку виставляє **єдиний крок** job `docker` — той, що йде після `up -d --wait`; на рівні workflow і job-ів її немає (це теж перевіряється тестом); (2) job `python` більше не збирає runtime-модулі gui. Той самий клас помилки був і на боці web (`npm run test` іде до `build`, а `CI=true` вимикав би skip у `build-contract.test.ts`) — там гейт переведено на **режим Vite** (`vitest run … --mode build-contract`, `import.meta.env.MODE`), який не залежить від оточення взагалі й крос-платформний |
| S-1 (спосіб виключення) | — | рев'ю пропонувало селектор `-m "not live and not e2e"` | **fixed інакше, з обґрунтуванням** | Виключення зроблено **по шляху** (`--ignore=tests/e2e/test_gui_*.py --ignore=tests/e2e/test_runtime_suite_is_enforced.py`), а не за маркером, з двох причин: (а) §16.2 фіксує команду `uv run pytest -m "not live"` **дослівно**, і звужений селектор ламав чинний контрактний тест `test_ci_runs_spec_16_2_commands_without_hardcoded_secrets` (перевірено: падає); (б) маркер `e2e` за §16.1 носитимуть і **offline**-тести WP-14 (`collector e2e --source fixtures --offline`), які мають виконуватись саме в job `python` — виключення за маркером тихо прибрало б їх з CI назавжди, тобто відтворило б рівно ту помилку, за яку PR отримав H-1. Інваріант «runtime-модулі gui не збираються в job python» від способу не залежить і закріплений тестом |
| S-2 | low | суперечливі коментарні блоки над `npm audit` (залишок відкликаного послаблення) | **fixed** | Коментар переписаний: поріг HIGH в обох прогонах, виняток оформлюється точковим `--exclude <pkg>` з CVE ID, owner і датою в ADR. Формулювань про «не має зупиняти кожен PR» більше немає; назва кроку теж виправлена (`npm audit (HIGH блокує, §13)`) |
| S-3 | low | «Що не перевірено» застаріло; owned files не називають нові `tests/e2e/**` | **fixed** | Пункт про відсутність SBOM/trivy для `collector-gui` перекреслений із посиланням на фактичний результат; у таблицю розширення scope додані три файли `tests/e2e/**` (два від тестувальника + новий тривожний модуль) |
| S-4 | low | немає локального скану образу `collector-gui` | **fixed** — і знайшов блокер, див. нижче | `trivy`/`syft` локально відсутні, тому скан зроблено через `docker scout cves` (без потреби в registry). Результат змусив змінити `web/Dockerfile` |

### Локальний скан образу `collector-gui` (S-4) — знайдено і виправлено реальний блокер

`docker scout cves --only-severity critical,high collector-gui:dev` на образі, зібраному з
pinned digest `nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56a…`:

```text
   10C    19H     0M     0L  curl 8.17.0-r1
    3C    15H     0M     0L  openssl 3.5.6-r0
    0C     3H     0M     0L  util-linux 2.41.4-r0
    0C     2H     0M     0L  expat 2.7.5-r0
    0C     1H     0M     0L  libxml2 2.13.9-r0
    0C     1H     0M     0L  c-ares 1.34.6-r0

54 vulnerabilities found in 6 packages
  CRITICAL  13
  HIGH      41
```

**53 з 54 мали доступний фікс.** Новішого digest для тега `1.29-alpine` немає
(`docker buildx imagetools inspect` повертає той самий `0c79d56a…`) — тобто **сам vendor-образ
відстає від security-оновлень Alpine**. Наслідок, який пропустили всі попередні гейти: крок
trivy `CRITICAL` (`exit-code: 1`, без `ignore-unfixed`, §13), доданий мною ж на gate 2,
**блокував би кожен PR** — а єдиний публічний сервіс стека їздив би з 13 критичними CVE.

Виправлено в `web/Dockerfile`: `USER root` + `RUN apk upgrade --no-cache && rm -rf
/var/cache/apk/*` перед поверненням на `101:101`. Base image лишається pinned by digest
(вимога картки); змінюється лише набір apk-пакетів — свідомий обмін побайтової
відтворюваності у часі на §13 «critical CVE блокує release», записаний коментарем у
Dockerfile.

Після перезбірки:

```text
   0C     1H     0M     0L  libxml2 2.13.9-r1
  CRITICAL  0
  HIGH      1
    x HIGH CVE-2026-86140   Affected range : <=2.13.9-r1   Fixed version : not fixed
```

Єдиний HIGH, що лишився, **не має фіксу** upstream, тому крок trivy `HIGH`
(`ignore-unfixed: true`) його пропустить, а крок `CRITICAL` — зелений. Для порівняння,
`collector:dev` у тому ж прогоні: **0 CRITICAL / 2 HIGH** (обидва без фіксу).

### Доказ обох гілок гейта (вимога пострев'ю)

```text
# (а) без стека і без прапорця — точна команда job `python`
$ CI=true uv run pytest -m "not live" -rs \
    --ignore=tests/e2e/test_gui_runtime_contract.py \
    --ignore=tests/e2e/test_gui_api_down_branch.py \
    --ignore=tests/e2e/test_runtime_suite_is_enforced.py -q
606 passed, 1 skipped          <- job python зелений; `CI=true` більше ні на що не впливає

# (а2) без стека, модулі викликані напряму — skip, а не падіння
$ CI=true uv run pytest -m e2e tests/e2e -q -rs
3 passed, 22 skipped
SKIPPED tests/e2e/test_gui_runtime_contract.py: gui не відповідає на http://127.0.0.1:80 …
SKIPPED tests/e2e/test_runtime_suite_is_enforced.py: перевірка діє лише там, де стек обіцяний
                                                    (COLLECTOR_E2E_REQUIRED=1 …)

# (б) зі стеком і прапорцем — виконуються, skip заборонений
$ COLLECTOR_E2E_REQUIRED=1 uv run pytest -m e2e tests/e2e -q -rs
25 passed                      <- жодного skip

# (б-) прапорець без стека — падає гучно, а не зникає
$ COLLECTOR_E2E_REQUIRED=1 uv run pytest tests/e2e/test_runtime_suite_is_enforced.py -q
1 failed, 1 passed
E  Failed: gui недоступний на http://127.0.0.1:80 …, тому тести
   ('test_gui_runtime_contract.py', 'test_gui_api_down_branch.py') були б пропущені.
```

Структурні тести переписані на інваріанти, а не на дослівні рядки:
`test_ci_python_job_does_not_collect_gui_runtime_tests` (модулі виключені + прапорця в job
немає), `test_ci_docker_job_forbids_skipping_e2e_after_stack_is_up` (прапорець рівно на
одному кроці, після `up --wait`, і його ж читають самі модулі; `os.environ.get("CI"` у
гейті заборонено), `test_ci_python_job_keeps_spec_16_2_command_verbatim`,
`test_build_contract_gate_does_not_depend_on_universal_ci`.

### Команди перевірки після виправлень

```text
$ cd web && npm ci && npm audit --omit=dev --audit-level=high && npm audit --audit-level=high \
    && npm run lint && npm run test && npm run build && npm run test:build && npm run test:e2e
found 0 vulnerabilities (обидва прогони)
lint OK · test: 24 passed | 4 skipped · build OK · test:build: 4 passed · test:e2e: 4 passed
EXIT=0

$ uv run ruff check . / ruff format --check . / mypy src      -> чисто
$ uv run pytest -m "not live" -q                              -> 605 passed, 23 skipped
$ COLLECTOR_E2E_REQUIRED=1 uv run pytest -m "not live" -q     -> 628 passed (стек піднято)
$ uv run pre-commit run --all-files                           -> усі hooks Passed

$ docker compose config --quiet                               exit=0
$ docker compose build gui                                    Image collector-gui:dev Built
$ docker compose --profile core --profile workers --profile gui up -d --wait   exit=0
$ docker compose ps -a --format json | python deploy/compose/check-healthy.py
all 17 containers healthy or exited 0
$ COLLECTOR_E2E_REQUIRED=1 uv run pytest -m e2e tests/e2e -q   25 passed
$ docker compose down -v                                      exit=0
```

### Що лишилось неперевіреним

- фактичний прогін CI — як і раніше, `operationally unverified`; цього разу обидві гілки
  гейта відтворені локально саме тими командами, які виконує workflow;
- `trivy`/`syft` локально відсутні — скан зроблено `docker scout`; числа trivy у CI можуть
  відрізнятись (інша БД), але клас проблеми (застарілі apk-пакети базового образу) закрито
  на рівні збірки;
- `apk upgrade` робить збірку образу невідтворюваною побайтово у часі — свідомий обмін
  (§13); якщо це стане проблемою для release-пайплайну, альтернатива — власний base image з
  періодичним оновленням digest (owner WP-14).
