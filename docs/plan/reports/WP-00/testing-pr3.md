# WP-00 PR3 — звіт незалежного тестування (`wp/00-3-web-scaffold`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR3 «React/TypeScript/Vite scaffold + GUI image» |
| Branch / worktree | `wp/00-3-web-scaffold` / `.worktrees/wp-00-3`, HEAD `0778439` |
| Картка | `docs/plan/cards/WP-00.md`, розділ «PR3» + «Спільні правила» |
| Розділи ТЗ | §7.7, §8, §13, §16.1 (рівень 14 Docker, 1 Unit), §16.2, §16.3; REVIEW.md R-51, R-54, R-55 |
| Середовище | Windows 11, Node 24.19.0 + npm 11.17.0, Docker Desktop 29.8.0, Compose v5.5.1, uv/CPython 3.13 |
| Порядок | Картка і ТЗ прочитані першими; `implementation-pr3.md` — **після** власного прогону |
| Вердикт | **pass** (з однією знахідкою `high`, яку треба закрити до merge/релізу) |

---

## 1. Команди та дослівний вивід

### 1.1. Web-ланцюжок §16.2 на чистій теці

`rm -rf node_modules dist test-results playwright-report`, далі один ланцюжок.

```text
### npm ci
npm warn deprecated whatwg-encoding@3.1.1: Use @exodus/bytes instead ...
npm warn deprecated eslint@9.36.0: This version is no longer supported ...
added 305 packages, and audited 306 packages in 17s
6 vulnerabilities (1 moderate, 4 high, 1 critical)
npm warn allow-scripts 1 package has install scripts not yet covered by allowScripts:
npm warn allow-scripts   esbuild@0.25.12 (postinstall: node install.js)
EXIT=0

### lock after
1f8f3793bf4bbb436797e9e111e5bfea *package-lock.json      ← md5 до npm ci: той самий
### git status lock
(порожньо — `npm ci` не переписав lockfile)

### npm run lint
> eslint . --max-warnings 0 && prettier --check .
Checking formatting...
All matched files use Prettier code style!
EXIT=0

### npm run test
 ✓ tests/unit/eslint-no-browser-storage.test.ts (11 tests) 5628ms
 ✓ tests/unit/routes.test.tsx (5 tests) 255ms
 Test Files  2 passed (2)
      Tests  16 passed (16)
EXIT=0

### npm run build
> tsc -b --force && vite build
✓ 94 modules transformed.
dist/index.html                       0.80 kB │ gzip:  0.54 kB
dist/assets/index-C6SdZp5L.css        0.64 kB │ gzip:  0.36 kB
dist/assets/NotFoundPage-EZJrWIPl.js  0.52 kB │ gzip:  0.37 kB │ map:     0.86 kB
dist/assets/OverviewPage-CWQ785UW.js  2.44 kB │ gzip:  1.35 kB │ map:     4.56 kB
dist/assets/index-CyBfb2hD.js       291.60 kB │ gzip: 93.20 kB │ map: 1,450.42 kB
✓ built in 2.90s
EXIT=0

### npm run test:e2e
Running 3 tests using 3 workers
  ok 3 [chromium] › smoke.spec.ts:15:3 › невідомий маршрут віддає SPA-сторінку 404 … (438ms)
  ok 1 [chromium] › smoke.spec.ts:24:3 › застосунок нічого не пише у browser storage (§13) (451ms)
  ok 2 [chromium] › smoke.spec.ts:3:3  › головна сторінка рендериться українською (5.5s)
  3 passed (3.3m)
EXIT=0
```

**Мережеві сюрпризи `npm ci`:** немає. `md5sum package-lock.json` до і після — ідентичні,
`git status package-lock.json` порожній → lockfile актуальний, `npm ci` його не оновлює.
Ставиться рівно 305 пакетів за lockfile. Окремо зафіксовано рядок `6 vulnerabilities
(1 moderate, 4 high, 1 critical)` — знахідка H-1 нижче.

### 1.2. Python-гейти (регресія після моїх тестів)

```text
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
135 files already formatted
$ uv run mypy src
Success: no issues found in 40 source files
$ uv run pytest -m "not live" -rs -q
613 passed, 1 skipped, 8 warnings in 141.00s
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
```

До моїх тестів — `594 passed, 1 skipped` (збігається зі звітом реалізації).
Compose-тести на `gui` **реально виконуються**, не skip-ляться:

```text
$ uv run pytest tests/integration/test_compose_render.py -q -rs
6 passed in 1.34s
```

### 1.3. Compose і clean-host старт §16.2/§16.3

```text
$ COMPOSE_PROFILES=core,workers,gui docker compose config --quiet
CONFIG_OK (exit=0)

$ COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait --wait-timeout 600
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
EXIT=0

$ docker compose --profile core --profile workers --profile gui up -d --wait --wait-timeout 300
EXIT=0                      ← дослівна команда §16.2/§16.3 теж зелена
```

### 1.4. Заголовки, проксі й обидві гілки health

```text
$ curl -sI http://localhost/
HTTP/1.1 200 OK
Server: nginx
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

$ curl -s http://localhost/ | head -3
<!doctype html>
<html lang="uk">

$ curl -s -w "\nHTTP=%{http_code}\n" http://localhost/api/v1/health/components
{"status":"ready"}
HTTP=200
```

Гілка `not_ready` (реалізатор стверджує, що виправив баг rewrite-фази через
`try_files` → named location — **підтверджено, див. mutation M3**):

```text
$ docker compose stop api
$ curl -s -o /dev/null -w "root=%{http_code}\n" http://localhost/
root=200                                   ← статика продовжує роздаватись
$ curl -s -w "\nHTTP=%{http_code}\n" http://localhost/api/v1/health/components
{"status":"not_ready"}
HTTP=503
$ curl -sI http://localhost/api/v1/health/components | head -8
HTTP/1.1 503 Service Temporarily Unavailable
Content-Security-Policy: default-src 'none'; ...   ← заголовки §13 не зрізані error_page
X-Content-Type-Options: nosniff
$ curl -s -o /dev/null -w "api_other=%{http_code}\n" http://localhost/api/v1/whatever
api_other=502                              ← звичайний /api/ проксі теж чесно падає

$ docker inspect collector-gui-1 --format '{{.State.Health.Status}} fails={{...}}'
healthy fails=1  → healthy fails=2 → unhealthy fails=5   (через ~45 с, 3 retries × 15 с)

$ docker compose start api ; (очікування ready)
{"status":"ready"} HTTP=200
$ (полінг) gui healthy after ~5s ; healthy fails=0
```

**Висновок щодо «gui сам лишається healthy»:** ні, не лишається — за ~45 с після зупинки
`api` контейнер `gui` переходить у `unhealthy`. Це **свідома і правильна** поведінка за §7.5
(«Healthcheck кожного сервісу перевіряє process + критичну dependency»), закріплена тестом
`test_application_healthchecks_name_a_critical_dependency`. Процес nginx при цьому живий,
статика віддається 200, `restart: unless-stopped` не перезапускає контейнер через unhealthy
(нема restart-loop), відновлення автоматичне за ~5 с. Зафіксовано як L-2 (наслідок для
`up --wait`), не як дефект.

### 1.5. Ізоляція мереж (gate 3 CR-14/SEC L-2)

```text
$ docker compose exec -T gui sh -c 'nslookup postgres/mongo/minio/parse-worker'
postgres       *** Can't find postgres: No answer
mongo          *** Can't find mongo: No answer
minio          *** Can't find minio: No answer
parse-worker   *** Can't find parse-worker: No answer
$ docker compose exec -T gui wget --spider http://api:8000/api/v1/health/components
api_OK
```

### 1.6. Контейнер і image `gui`

```text
$ docker inspect collector-gui-1 --format '...'
User=101:101
ReadonlyRootfs=true
CapDrop=[ALL]
CapAdd=<no value>
PidsLimit=256
SecurityOpt=[no-new-privileges:true]
Tmpfs=map[/tmp:mode=1777,size=16m /var/cache/nginx:mode=0700,uid=101,gid=101,size=32m]
Binds=<no value>
Mounts=                      ← жодного bind/volume, отже й жодного docker socket
Privileged=false
Memory=536870912  NanoCpus=500000000
Ports=map[8080/tcp:[{0.0.0.0 80} {:: 80}]]
Networks=collector_frontend collector_ingress

$ docker inspect collector-gui:dev --format '{{.Config.User}}'   → 101:101
$ docker exec collector-gui-1 id  → uid=101(nginx) gid=101(nginx) groups=101(nginx)

$ docker compose ps -a --format '{{.Service}}\t{{.Ports}}'
gui   0.0.0.0:80->8080/tcp, [::]:80->8080/tcp
(решта — без published ports; minio/mongo/postgres лише expose)

$ for c in $(docker compose ps -aq); do docker inspect $c --format '{{.Name}} {{.Mounts}}'; done | grep docker.sock
NO DOCKER SOCKET ANYWHERE

$ docker history collector-gui:dev --no-trunc | grep -iE 'password|secret|token|key='
(порожньо)
$ docker run --rm --entrypoint env collector-gui:dev
PATH=... HOSTNAME=... NGINX_VERSION=1.29.8 PKG_RELEASE=1 DYNPKG_RELEASE=1
NJS_VERSION=0.9.6 NJS_RELEASE=1 HOME=/var/cache/nginx          ← жодного секрету
```

### 1.7. Build determinism і code splitting

```text
$ npm run build ; md5sum dist/assets/*        (прогін 1)
d029377091c0c0d929a14168be4c6e59 *dist/assets/NotFoundPage-EZJrWIPl.js
cef5e74d558d430a004db0190c245bff *dist/assets/NotFoundPage-EZJrWIPl.js.map
e629522d126d7150b9975c3357cbd281 *dist/assets/OverviewPage-CWQ785UW.js
a901872133f30607cd84fca4b78f47fe *dist/assets/OverviewPage-CWQ785UW.js.map
29f595f18bcfe2f2f1777ee7bf5c3bb1 *dist/assets/index-C6SdZp5L.css
1cb9213296874e3d9ae4554c11abd822 *dist/assets/index-CyBfb2hD.js
0bdca2c7d9789c1838667f78097827ae *dist/assets/index-CyBfb2hD.js.map

$ rm -rf dist && npm run build ; md5sum dist/assets/*   (прогін 2)
(байт-у-байт ті самі 7 рядків — і імена з хешами, і вміст)
```

Code splitting: у `dist/assets` три JS-chunk-и — спільний `index-*.js` і **по одному на
маршрут** (`OverviewPage-*.js`, `NotFoundPage-*.js`). Це не один bundle.

---

## 2. Acceptance-пункт → тест → результат

| Acceptance картки PR3 | Тест / перевірка | Результат |
|---|---|---|
| `npm ci && lint && test && build && test:e2e` зелені на чистій теці | §1.1, CI job `web` + `test_ci_runs_web_pipeline_from_spec_16_2` | pass |
| `docker compose config --quiet` | §1.3, `tests/integration/test_compose_render.py` | pass |
| `gui` container `healthy` | §1.3 (`up --wait`), `test_gui_is_the_only_ingress_and_depends_on_api` | pass |
| `curl http://localhost/` віддає сторінку | §1.4; **нове** `test_gui_runtime_contract.py::test_spa_fallback_returns_index_not_404` | pass |
| `curl /api/v1/health/components` проксіюється до api | §1.4 (обидві гілки); **нове** `test_public_health_exposes_only_ready_flag`, `test_gui_api_down_branch.py` | pass |
| CSP/security headers у відповіді | §1.4; `test_gui_nginx_has_restrictive_csp_and_security_headers` (статика) + **нове** `test_security_headers_present_on_every_location` (runtime, 6 location-ів) | pass |
| container non-root | §1.6; `test_gui_container_is_non_root_read_only_without_secrets`, `test_application_services_read_only_non_root` | pass |
| clean-host `--profile core --profile workers --profile gui up -d --wait` | §1.3 — обидві форми (env і `--profile`) | pass |
| Вимога 1: Vite+React+TS strict, ESLint/Prettier, Vitest/TL, Playwright, lock, `.nvmrc`=24 | `test_gui_build_uses_web_context_and_deploy_conf`; `tsconfig.app.json` strict + 8 додаткових прапорців; §1.1 | pass |
| Вимога 2: структура, укр. placeholder, route-level splitting, заготовка OpenAPI-клієнта | `tests/unit/routes.test.tsx`; **нове** `web/tests/unit/build-contract.test.ts`; `src/api/README.md` | pass |
| Вимога 3: ESLint-заборона storage з коментарем-причиною | `eslint-no-browser-storage.test.ts` (12 тестів) + §3 нижче | pass (з межею M-1) |
| Вимога 4: multi-stage, pinned digest, non-root nginx, CSP, same-origin `/api`, internal `frontend`, `api` без `ingress`, health без деталей | `test_gui_image_is_multistage_pinned_non_root`, `test_gui_sees_only_api_and_api_left_ingress`, §1.5, §1.6; **нове** `test_internal_auth_subrequest_is_not_reachable_from_outside`, `test_health_detail_not_reachable_through_path_normalization` | pass |
| Вимога 5: скрипти `lint/test/build/test:e2e` | §1.1 | pass |
| R-55 «без Docker socket» | §1.6, `test_no_docker_socket_mount_anywhere` | pass |
| R-51 «clean-host start» | §1.3 | pass |

---

## 3. ESLint-заборона browser storage: прогін на зразках

Створено `web/src/__storage_probe.ts` з чотирма формами, які вимагає задача, і запущено
`npx eslint src/__storage_probe.ts`. **Кожна падає:**

```text
 2:3   error  Unexpected use of 'localStorage'. Заборонено §13: ...        no-restricted-globals
 5:3   error  'window.localStorage' is restricted from being used. ...     no-restricted-properties
 8:3   error  'globalThis.sessionStorage' is restricted from being used.   no-restricted-properties
11:3   error  'window.localStorage' is restricted from being used. ...     no-restricted-properties
11:10  error  ["localStorage"] is better written in dot notation           @typescript-eslint/dot-notation
11:10  error  Заборонено §13: ...                                          no-restricted-syntax
✖ 6 problems (6 errors, 0 warnings)       EXIT=1
```

**Обхід через змінну-alias — правило НЕ спрацьовує** (зафіксовано як межа M-1):

```text
export function aliasWindow(t: string): void {
  const w = window;
  w.localStorage.setItem('access_token', t);        ← жодної помилки storage-правил
}
export function aliasStore(t: string): void {
  const store: Storage = window.top!.localStorage;  ← лише no-non-null-assertion
  store.setItem('access_token', t);
}
export function viaGlobalRef(t: string): void {
  const g = globalThis as unknown as { localStorage: Storage };
  g.localStorage.setItem('access_token', t);        ← жодної помилки storage-правил
}
✖ 2 problems  (no-non-null-assertion; no-restricted-properties на `window.top.localStorage`)
```

Тобто з чотирьох alias-форм ловиться одна (`window.top!.localStorage` — бо `object.name`
там усе ще `window`), три проходять. Файл-зразок видалено (`git status` чистий).

Це **межа синтаксичних правил ESLint**, а не помилка реалізації: картка вимагає саме
`no-restricted-globals`/`no-restricted-properties`, і вони на місці з розгорнутим
коментарем-причиною. Страховка існує на іншому рівні — E2E перевіряє в реальному браузері,
що після рендеру `localStorage.length === sessionStorage.length === 0`. Щоб цю страховку не
прибрали непомітно, додано тест (див. §5).

---

## 4. Рівень §16.1 → тести

| Рівень §16.1, призначений картці | Тести | Стан |
|---|---|---|
| 14 «Docker» (compose/контейнери/мережі/образи) | `tests/unit/test_compose_config.py` (6 нових GUI-тестів), `tests/unit/test_compose_config_adversarial.py`, `tests/integration/test_compose_render.py` (profile `gui`, `test_gui_sees_only_api_and_api_left_ingress`); **додано** `tests/e2e/test_gui_runtime_contract.py` (16), `tests/e2e/test_gui_api_down_branch.py` (3) | покрито; було покрито лише статикою конфігів, тепер і живим контейнером |
| 1 «Unit» (CLI/конфігурація) | `web/tests/unit/routes.test.tsx`, `web/tests/unit/eslint-no-browser-storage.test.ts`; **додано** `web/tests/unit/build-contract.test.ts` | покрито |
| E2E браузера (каркас) | `web/tests/e2e/smoke.spec.ts` (3) | покрито |

---

## 5. Додані тести

Edit робився **лише** у `tests/**` і `web/tests/**`; продуктивний код не змінювався.

| Файл | Що перевіряє | Навіщо (чого бракувало) |
|---|---|---|
| `tests/e2e/test_gui_runtime_contract.py` (16 тестів) | CSP-директиви і 5 security headers **на кожному типі location** (index, hashed-asset, `index.html`, SPA-fallback, health, internal-404); відсутність `unsafe-inline`/`unsafe-eval`/`*`/`http:` у CSP; `Server: nginx` без версії; SPA fallback 200; публічний health віддає рівно `{"status": …}` без `components`/`git_sha`/`latency_ms`; `/internal-api-health` → 404; 4 форми нормалізації шляху (`?x=1`, `//api/...`, `/api//v1/...`, `/api/v1/health/../health/components`) не витягують детальний звіт; `/api/` справді доходить до FastAPI | Статичні тести читають `nginx.conf` як текст і не бачать, що `add_header` у `location` мовчки скидає весь успадкований набір заголовків `server` — класична пастка nginx, яку реалізація обходить через `expires`. Доки заголовки перевіряються лише регулярками по файлу, цей регрес непомітний |
| `tests/e2e/test_gui_api_down_branch.py` (3 тести) | З мертвим `api` health віддає **503 `not_ready`**, статика лишається 200, security headers переживають гілку `error_page` | Реалізатор сам зафіксував у ризиках: «окремого автотесту на “ready ≠ завжди” немає — він потребує живого стека». Тест піднімає **одноразовий контейнер з того самого image у власній мережі без імені `api`** (docker DNS → NXDOMAIN → `auth_request` → 500 → `error_page` → 503), тому не чіпає робочий стек і не робить `gui` unhealthy |
| `web/tests/unit/build-contract.test.ts` (4 тести) | У `dist/assets` кількість route-chunk-ів == кількості lazy-маршрутів і chunk-ів > 1; усі імена з content hash (інакше `expires 1y` у nginx отруює кеш); `index.html` без inline-`<script>`/`<style>`/`on*=` (CSP `script-src 'self'` без hash/nonce); усі `src`/`href` — root-relative, жодного CDN | `routes.test.tsx` перевіряє **намір** (`lazy` у таблиці маршрутів), а не результат Rollup. Заодно робить правдивим коментар `vite.config.ts:18`, який посилався на неіснуючий `tests/unit/build-contract.test.ts` (знахідка L-1). Набір `describe.skipIf(!dist)` — у ланцюжку §16.2 тести йдуть до `build` |
| `web/tests/unit/eslint-no-browser-storage.test.ts` (+1 тест) | Runtime-страховка проти alias-обходу існує: `tests/e2e/smoke.spec.ts` містить перевірку `{ local: 0, session: 0 }` | Межа M-1: статичне правило обходиться `const w = window`. Тест не дає прибрати єдиний бар'єр, який alias ловить |

---

## 6. Mutation-перевірка

| # | Мутація | Очікування | Фактично |
|---|---|---|---|
| M1 | `docker-compose.yml`: прибрано `read_only: true` у `gui` | compose-тести червоні | **червоно** — 2 failed, 56 passed |
| M2 | `nginx.conf`: `script-src 'self'` → `script-src 'self' 'unsafe-inline' https://cdn.example.com`, image перезібрано і `gui` перестворено | статичний і runtime-тести червоні | **червоно** — статичний 1 failed; runtime 7 failed, 12 passed |
| M3 | `nginx.conf`: повернено `return 200 '{"status":"ready"}'` у rewrite-фазі замість `try_files` → named location, image перезібрано | новий тест гілки `not_ready` червоний | **червоно** — `200: {"status":"ready"}` при повністю відсутньому `api` |

```text
# M1
FAILED tests/unit/test_compose_config.py::test_gui_container_is_non_root_read_only_without_secrets
FAILED tests/integration/test_compose_render.py::test_application_services_read_only_non_root
  E  KeyError: 'read_only'
2 failed, 56 passed in 3.78s

# M2 (статичний)
FAILED tests/unit/test_compose_config.py::test_gui_nginx_has_restrictive_csp_and_security_headers
  E  AssertionError: жодного inline/CDN-скрипта
  E  assert "'self' 'unsa...n.example.com" == "'self'"
1 failed, 51 deselected

# M2 (runtime, після `docker compose build gui && up -d --wait gui`)
FAILED tests/e2e/test_gui_runtime_contract.py::test_security_headers_present_on_every_location[index]
FAILED ... [hashed-asset] [index.html] [spa-fallback] [health] [internal-denied]
FAILED tests/e2e/test_gui_runtime_contract.py::test_csp_has_no_unsafe_sources
  E  AssertionError: 'unsafe-inline' у {'script-src': "'self' 'unsafe-inline' https://cdn.example.com"}
7 failed, 12 passed in 15.32s

# M3
FAILED tests/e2e/test_gui_api_down_branch.py::test_health_reports_not_ready_when_api_is_unreachable
  E  AssertionError: 200: {"status":"ready"}
  E  assert 200 == 503
1 failed, 2 passed in 5.31s
```

Після кожної мутації — `git checkout -- <file>` + перезбірка image; фінальна перевірка:

```text
$ git status --short
 M web/tests/unit/eslint-no-browser-storage.test.ts
?? tests/e2e/test_gui_api_down_branch.py
?? tests/e2e/test_gui_runtime_contract.py
?? web/tests/unit/build-contract.test.ts
      (продуктивний код без змін)
$ uv run pytest tests/e2e/ -q
19 passed in 20.23s
```

**M3 окремо підтверджує заяву реалізатора** про баг rewrite-фази: із поверненим `return`
у тій самій location endpoint віддає `ready` при повній відсутності `api` — тобто помилка
була реальна, а `try_files` → named location її справді закриває.

---

## 7. Знахідки

| # | Severity | Місце | Опис |
|---|---|---|---|
| H-1 | high | `web/package.json:35,44,47` (`react-router` 7.9.1, `vite` 7.1.6, `vitest` 3.2.4, `@playwright/test` 1.55.0), `.github/workflows/ci.yml:96-140` (job `web`) | `npm ci` рапортує **6 вразливостей: 1 critical, 4 high, 1 moderate**, і для **всіх є не-major фікс**: `react-router` → 7.18.4 (XSS через open redirect, arbitrary constructor injection, DoS — це **runtime-залежність**, яка їде у bundle до браузера), `vite` → 7.3.6, `vitest` → 3.2.7 (critical), `@playwright/test` → 1.63.0. При цьому в CI **немає жодного сканування npm-залежностей** (`npm audit`), а `trivy`/`syft` у job `docker` скановані тільки для образу `collector:ci` — образ `collector-gui:ci` не сканується взагалі. §13: «Dependency та image scanning — щотижня і на кожен PR; critical CVE блокує release або має датоване risk acceptance». Датованого risk acceptance немає — у звіті реалізації згадано лише відсутність SBOM/trivy для `collector-gui`, а про npm-CVE не сказано. Потрібно або підняти версії (всі фікси non-breaking за semver), або додати `npm audit --audit-level=high` до job `web` + trivy на `collector-gui` з датованим ADR-винятком |
| M-1 | medium | `web/eslint.config.js:88-112` | Заборона browser storage обходиться присвоєнням у проміжну змінну: `const w = window; w.localStorage.setItem(...)`, `const g = globalThis as unknown as {localStorage: Storage}; g.localStorage...` проходять lint чисто (доказ — §3). Ловляться лише прямі форми і `window.top!.localStorage`. Це принципова межа синтаксичних правил (для alias потрібне type-aware правило на тип `Storage`, напр. `no-restricted-types`/власний rule). Вимогу картки формально виконано; єдиний бар'єр проти alias — E2E-перевірка «storage порожній», яка покриває лише placeholder-сторінку. Власникові WP-11C з реальними екранами варто підсилити (type-aware rule або CSP-незалежний runtime-гард) |
| L-1 | low | `web/vite.config.ts:18` | Коментар посилався на `tests/unit/build-contract.test.ts`, якого в PR немає (dangling reference). Закрито доданим файлом з тією самою назвою і змістом, який коментар обіцяє |
| L-2 | low | `docker-compose.yml:592-604` (healthcheck `gui`) | Healthcheck `gui` б'є у `/api/v1/health/components`, тому падіння `api` робить `gui` `unhealthy` за ~45 с, хоча nginx живий і статику віддає (перевірено §1.4). Це свідома вимога §7.5 і як дефект не класифікується, але має два наслідки, які варто мати на увазі: (а) `docker compose up -d --wait` на стеку з деградованим `api` завершиться помилкою через `gui`; (б) у конфігу вже є чистий liveness-endpoint `/healthz` (використовується `HEALTHCHECK` в образі), тож за потреби розділити liveness і readiness інструмент уже є |
| I-1 | info | `deploy/compose/gui/nginx.conf:47` | `location = /api/v1/health/components` — exact match і регістрозалежний: `/API/v1/health/components` падає у SPA-fallback і віддає `index.html` з кодом 200. Витоку немає (перевірено: FastAPI теж регістрозалежний, `/api/openapi.json` → 404, api-стаб має рівно один маршрут), але при появі реальних endpoint-ів WP-11A цю асиметрію треба тримати в голові. Перевірено також `//api/...`, `/api//v1/...`, `/api/v1/health/../health/components`, `?x=1`, `%2f` і trailing slash — жодна форма не віддає детальний звіт (закріплено тестом `test_health_detail_not_reachable_through_path_normalization`) |

Flaky-тестів не виявлено: жоден тест не ретраївся, усі прогони відтворювані.

---

## 8. Звірка зі звітом реалізації (`implementation-pr3.md`)

Прочитано **після** власного прогону. Розбіжностей, які б спростовували заявлене, немає:

- заявлений вивід `npm ci/lint/test/build/test:e2e`, розміри chunk-ів і хеші імен
  (`index-CyBfb2hD.js`, `OverviewPage-CWQ785UW.js`, `NotFoundPage-EZJrWIPl.js`) збіглися
  з моїми **побайтово** — це ще й додатковий доказ детермінованості збірки;
- `594 passed, 1 skipped` до моїх тестів — збіглося;
- заявлені `Config.User=101:101`, `ReadonlyRootfs=true`, `CapDrop=[ALL]`, `PidsLimit=256`,
  `no-new-privileges`, відсутність секретів у `env`/`docker history` — підтверджено;
- заявлена ізоляція мереж (`gui` не бачить postgres/mongo/minio/workers, бачить `api`) —
  підтверджено;
- заявлене виправлення бага rewrite-фази — підтверджено мутацією M3, яка відтворює саме
  описаний симптом;
- у розділі «Ризики» чесно зафіксовано і `gui unhealthy` при падінні `api` (мій L-2), і
  відсутність автотесту на «ready ≠ завжди» (я цей тест додав), і відсутність SBOM/trivy
  для `collector-gui`;
- **не зафіксовано** у звіті реалізації: 6 CVE у npm-залежностях (H-1) і обхід ESLint-правила
  через alias (M-1).

---

## 9. Прибирання

```text
$ docker compose --profile core --profile workers --profile gui --profile browser \
      down -v --remove-orphans
 Container collector-postgres-1 Removed
 ... (усі 17 контейнерів)
 Volume collector_postgres-data Removed
 Volume collector_mongo-data Removed
 Volume collector_mongo-config Removed
 Volume collector_minio-data Removed
 Network collector_backend / collector_frontend / collector_ingress /
         collector_source_egress / collector_provider_egress Removed
EXIT=0

$ docker volume ls | grep -i collector    → жодного collector_* volume
$ docker network ls | grep -i collector   → жодної collector_* мережі
```

Сторонні контейнери хоста (`puluj-g-*`, `wp01a-*`) і worktree `.worktrees/wp-01a` не
чіпались; робота велася лише в `.worktrees/wp-00-3`. `docker events` за період прибирання
показує `destroy` виключно для `collector-*` / `collector_*`; `--remove-orphans` діє тільки
на контейнери з міткою `com.docker.compose.project=collector`, а ad-hoc контейнери
`wp01a-*` (`docker run postgres:18`) такої мітки не мають і були поза його досяжністю.
Одноразові контейнери/мережі власних тестів (`collector-test-gui-*`,
`collector-test-noapi-*`) прибираються фікстурою — перевірено, що не лишилось жодного.

---

## Вердикт

**pass.**

Усі acceptance-пункти картки PR3 (і закриваючий acceptance усього WP-00 — clean-host старт
`core + workers + gui`) відтворені незалежно й зелені. Три мутації доводять, що тести
справді кусаються, зокрема на найтоншому місці — фазах nginx. H-1 (CVE у web-залежностях без
жодного npm/gui-сканування в CI) треба закрити до merge або оформити датованим risk
acceptance за §13; це гейт поставки, а не дефект каркаса, тому вердикт не змінює.
