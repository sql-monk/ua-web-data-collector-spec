# WP-00 PR3 — security review (GUI scaffold + Nginx image)

| Поле | Значення |
|---|---|
| Branch / HEAD | `wp/00-3-web-scaffold` @ `1901d6e` (worktree `.worktrees/wp-00-3`) |
| Diff | `git diff main...HEAD` — 52 файли (3 коміти PR3) |
| Дата | 2026-09-22 |
| Рев'юер | `wp-security-reviewer` (read-only; єдиний запис — цей файл) |
| Обсяг | ТЗ §13 (CSP, заголовки, browser storage, контейнери, сканування), §7.5 (єдиний публічний ingress), §7.7 (межі GUI), FR-013; REVIEW.md R-54/R-55 |
| Метод | статичний аналіз diff + **живий стек** (`COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`), curl-проби всіх типів відповідей, Playwright/Chromium проти контейнера, окремий gui-контейнер без `api`, `gitleaks`, `npm audit`, `docker inspect`/`exec` |
| Вердикт | **approve** — 0 critical, 0 high, 2 medium, 4 low, 4 info |

Обидва medium — латентні: сьогодні не експлуатуються (API — стаб без автентифікації), але
спрацюють рівно тоді, коли WP-11A додасть OIDC BFF. Фікс обох — локальний, у файлі, який
цей PR і створює (`deploy/compose/gui/nginx.conf`), тому рекомендую закрити їх у PR3.

## Знахідки

Формат: `severity | file:line | клас проблеми | сценарій | verdict`.

### Medium

**M-1** | `deploy/compose/gui/nginx.conf:12-14,96-111` (`server_name _` + `proxy_set_header Host $host`) |
Host header injection → attacker-controlled absolute URL у backend |
Сервер приймає будь-який `Host` (catch-all `server_name _`, немає `default_server`-блока з
`return 444`), а `/api/` віддає цей `Host` в api дослівно. Підтверджено на живому стеку:

```text
curl -H "Host: evil.example.com" http://localhost/api/v1/health/components/
→ HTTP/1.1 307 Temporary Redirect
  location: http://evil.example.com/api/v1/health/components
```

(з нормальним `Host` — `location: http://localhost/...`; статика з `Host: attacker.test`
теж віддається 200). Сьогодні наслідок обмежений: FastAPI `redirect_slashes` будує
absolute-Location з `Host`, тобто маємо open redirect на trailing-slash варіантах, який
браузер сам ініціювати не може (JS не встановлює `Host`). Клас проблеми стає серйозним у
двох очікуваних конфігураціях: (а) WP-11A OIDC BFF будує `redirect_uri`/absolute-посилання з
`Host` запиту — тоді authorization code може піти на домен атакуючого; (б) WP-13 ставить
TLS termination/кеш перед gui — тоді це web cache poisoning. |
**non-blocking для PR3, обов'язково до WP-11A; рекомендую закрити зараз**: додати
`server { listen 8080 default_server; return 444; }` і/або валідацію `$host` через `map` з
allowlist (`GUI_PUBLIC_HOST`), або передавати в api фіксований `Host` замість `$host`.
Інваріант зафіксувати в `tests/unit/test_compose_config.py` поряд з
`test_gui_nginx_proxies_api_same_origin_and_hides_health_detail`.

**M-2** | `deploy/compose/gui/nginx.conf` (немає власного `log_format`/`access_log` у server-блоці
→ діє дефолтний `combined` з образу; пор. `:52` `access_log off` лише для `/healthz`) |
секрет у логах (§13 «logs приховують Authorization/Cookie/API keys») |
`combined` пише повний request line разом із query string. Підтверджено на живому стеку:

```text
gui-1 | 172.23.0.1 - - [...] "GET /api/v1/status?access_token=SECRETQUERY789 HTTP/1.1" 404 22 "-" "UA-SECRET" "-"
```

Заголовки `Authorization`/`Cookie` у лог **не** потрапляють (перевірено тим самим запитом) —
це коректно. Проблема саме в query string: WP-11A додає OIDC callback
(`/api/v1/auth/callback?code=…&state=…`), і authorization code осяде в json-file логу gui на
хості (ротація `20m × 5`, тобто зберігається довго), у той час як Python-логи вже мають
`redact_secrets` (WP-00 PR1). Логи gui — єдиний компонент стека без redaction. |
**non-blocking для PR3, обов'язково до WP-11A**: власний `log_format`, що використовує `$uri`
замість `$request` (query відкидається), або `access_log off` для auth-шляхів.

### Low

**L-1** | `web/vite.config.ts:20` (`sourcemap: true`) + `web/Dockerfile:60` | information disclosure на
єдиному публічному ingress |
Source maps потрапляють у runtime-образ і віддаються публічно. Перевірено:
`ls /usr/share/nginx/html/assets | grep -c '\.map$'` → `3`;
`GET /assets/index-CgGEjsJW.js.map` → `200`, 1 552 314 байт, у мапі `sourcesContent` з 34
файлів, з них 6 — власні TS-вихідники (`src/main.tsx`, `src/browserStorageGuard.ts`,
`src/api/queryClient.ts`, `src/routes/router.tsx`, …). Секретів і абсолютних шляхів хоста в
мапах **немає** (перевірено: усі `sources` відносні, жодного `C:\`/`/home/`/`file://`), тож
сьогодні це не витік. Ризик наростаючий: з WP-11C у мапи піде вся клієнтська логіка,
коментарі, імена внутрішніх endpoint-ів і RBAC-gating — готова карта атаки для
неавтентифікованого відвідувача. |
**non-blocking**: `sourcemap: 'hidden'` (мапи будуються для CI-артефактів, але без
`//# sourceMappingURL` у бандлі) і/або не копіювати `*.map` у runtime-шар Dockerfile;
альтернатива — свідомо зафіксувати відкриті мапи як рішення в картці WP-11C.

**L-2** | `web/src/browserStorageGuard.ts:1-21,38-67` | межа контролю ширша, ніж стверджує docstring |
Live-перевірка в Chromium проти контейнера (`http://localhost/`):

| вектор | результат |
|---|---|
| `localStorage.setItem` | blocked (§13 TypeError) |
| `window.localStorage` | blocked |
| alias (`const w = window; w['localStorage']`) | blocked |
| `globalThis.sessionStorage` | blocked |
| `Object.getOwnPropertyDescriptor(Window.prototype,'localStorage')` | геттера на прототипі немає — вектор недоступний |
| `delete window.localStorage` | blocked (`undefined`) |
| **same-origin iframe** (`document.createElement('iframe')` → `f.contentWindow.localStorage.setItem`) | **NOT BLOCKED** — ключ реально записано (`storageState` → `[{"name":"bypass_iframe","value":"1"}]`) |
| `indexedDB` | не покрито (`typeof indexedDB === 'object'`) |

Гард ставиться лише на top-level realm; свіжий realm (iframe/`about:blank`) віддає
незаймані `Storage`-акцесори того самого origin, і CSP цьому не заважає (`frame-src` не
поширюється на `about:blank`/`srcdoc`). IndexedDB і Cache API не покриті взагалі — саме туди
за замовчуванням пише `@tanstack/query-persist-client-idb`, якщо його колись додадуть.
Тому формулювання docstring «обхід через alias помирає на першому ж зверненні — … у
власному коді й **у будь-якій залежності, що потрапила в bundle**» — надто сильне. |
**non-blocking, виправити docstring + доповнити ESLint**: гард лишається валідним засобом
проти *випадкового* використання (саме це і є його threat model — реальний XSS і так має
повний доступ до origin), але межу слід назвати чесно: top-level realm, без iframe, без
IndexedDB/Cache API. У `STORAGE_BAN` додати `indexedDB`/`caches` до заборонених ідентифікаторів.

**L-3** | `docker-compose.yml:577,581,613-614` (`ingress` не `internal`) | egress ширший за §13
(«окремі network policies й egress allowlist у production») |
Публікація порту вимагає не-`internal` мережі, тому gui має вихід в Інтернет. Підтверджено з
контейнера: `wget http://example.com/` → успіх. Для nginx, що віддає статику і проксіює на
`api`, egress не потрібен; при RCE в nginx це готовий канал exfiltration. Всередину
сегментація виконана правильно (див. «Що перевірено», п. 5). |
**non-blocking, owner WP-13**: питання не вирішується в Compose без host firewall — внести в
egress-allowlist разом з TLS termination.

**L-4** | `deploy/compose/gui/nginx.conf:101` (`X-Forwarded-For $proxy_add_x_forwarded_for`) |
spoofable client attribution на межі довіри |
`$proxy_add_x_forwarded_for` **додає** `$remote_addr` до значення, надісланого клієнтом, тобто
довільний неавтентифікований клієнт може підставити перші елементи ланцюга. gui — межа
довіри (перший hop від Інтернету), перед ним нічого немає. Наслідок з'явиться, коли WP-11A
почне писати IP в audit log (§13 «audit log усіх mutating actions») або застосує
rate limiting за IP. `X-Request-ID $request_id` натомість генерує nginx — клієнт його
підмінити не може (коректно). |
**non-blocking, до WP-11A**: на межі довіри коректно `proxy_set_header X-Forwarded-For $remote_addr;`
(а `$proxy_add_x_forwarded_for` повернути, коли перед gui з'явиться довірений proxy WP-13).

### Info

**I-1** | `deploy/compose/gui/nginx.conf:31` | CSP hardening | Політика сильна (див. «Що
перевірено», п. 1), але без `require-trusted-types-for 'script'`/`trusted-types` і без
`report-to`/`report-uri` — порушення CSP у продакшені невидимі для оператора. Окремо варто
зафіксувати для WP-11C: `style-src 'self'` без `'unsafe-inline'` забороняє inline
`style={{…}}`-атрибути в React — це правильна строгість, але її треба знати заздалегідь. |
**optional**.

**I-2** | `deploy/compose/gui/nginx.conf:60-71` | поведінка маскованого endpoint-а |
`location = /api/v1/health/components` віддає `200 {"status":"ready"}` на POST/PUT/DELETE/PATCH/
OPTIONS (`auth_request` завжди робить GET-підзапит). Інформації не розкриває, але маска
поводиться не так, як реальний API, і може заплутати діагностику. |
**optional**: `limit_except GET HEAD { deny all; }`.

**I-3** | `deploy/compose/gui/nginx.conf` | немає rate limiting на єдиному публічному ingress |
Ані `limit_req`, ані `limit_conn`; §14.2 має SEV-1 «неконтрольований request rate». §13 цього
для WP-00 не вимагає (стаб без автентифікації), але ingress з'явився саме в цьому PR. |
**optional, owner WP-11A/WP-13**.

**I-4** | `.github/workflows/ci.yml` (немає `schedule:`) | supply chain — щотижневий скан |
§13: «Dependency та image scanning — **щотижня** і на кожен PR». На кожен PR — виконано і для
нового образу (див. «Що перевірено», п. 6); щотижневого розкладу немає. Це вже зафіксована
знахідка F-2 (owner WP-13), але PR3 розширює її обсяг: додано другий образ `collector-gui` і
npm-дерево на 295 пакетів. |
**accept (існуючий owner WP-13)**, повторно підтверджено.

## Що перевірено

1. **CSP — реальні відповіді, не конфіг.** Політика
   `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'`
   присутня **на всіх** перевірених типах відповідей: `200` html (`/`), SPA-fallback
   (`/some/spa/route`), `200` js, `200` css, `404` статики (`/assets/nope.js`), `404` від api
   через проксі, `200`/`307` проксі `/api`, `405` (TRACE), `413` (тіло 3 МБ),
   `414` (URI 9000 символів), `502` (api недоступний), `503` `not_ready` гілка.
   Немає `unsafe-inline`/`unsafe-eval` у `script-src`; `frame-ancestors`, `base-uri`,
   `form-action`, `object-src` — усі `'none'`. Vite-білд inline-скриптів не потребує:
   `dist/index.html` містить лише `<script type="module" crossorigin src="/assets/…">` і
   `<link rel="stylesheet">`, жодного `<script>` з тілом і жодного inline `style` (перевірено
   на зібраному `dist`, не лише на джерелі). CDN-посилань у бандлі немає — усі зовнішні URL у
   `dist` це XML-namespace-константи React і посилання на документацію в текстах помилок.
2. **Інші заголовки** (на кожній з перевірених відповідей): `X-Content-Type-Options: nosniff`,
   `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (camera/
   microphone/geolocation/payment/usb/interest-cohort = `()`), `Cross-Origin-Opener-Policy:
   same-origin`, `Cross-Origin-Embedder-Policy: require-corp`, `Cross-Origin-Resource-Policy:
   same-origin`, `Strict-Transport-Security`. `server_tokens off` працює — у відповідях і на
   дефолтних сторінках помилок лише `Server: nginx` без версії. `X-Powered-By` відсутній.
   Використання `expires` замість `add_header` у `location`-блоках справді зберігає
   успадковані заголовки — перевірено на `/assets/`, `/index.html`, `@api_ready`, `@api_not_ready`.
3. **Проксі `/api` — спроби обходу.** Маску `location = /api/v1/health/components` (назовні лише
   `ready`/`not_ready`, детальний звіт із `git_sha`, latency і станом postgres/mongo/minio
   назовні не виходить — звірено з прямою відповіддю api всередині мережі) не вдалося обійти
   жодним з перевірених варіантів: `//api/…`, `/api//v1/…`, `/api/v1/./health/components`,
   `/api/v1/health/../health/components`, `/api/..%2fapi/v1/…`, `?x=1`, `co%6Dponents` — усі
   нормалізуються в exact-location і віддають `ready`; `/api/v1/health/components/` і `…%2f`
   дають `307` на канонічний шлях (тобто знову на маску); `;x`, `.`, регістрові варіанти
   (`/api/V1/…`, `/api/v1/Health/…`, `/api/v1/health/Components`) і подвійне кодування —
   `404 {"detail":"Not Found"}` від api, без деталей. Path traversal не проходить:
   `/api/../etc/passwd` → SPA `200`; `..%2f..%2f`, `%2e%2e/`, `/../../../etc/passwd`,
   `/assets/../../etc/passwd` → `400` від nginx; `%00` → `400`. Внутрішніх адрес/версій
   health-проксі не розкриває: `Location` будується з `$host` (не з `api:8000`), тіла
   `{"status":"ready"}`/`{"status":"not_ready"}` порожні від деталей, `502`-сторінка — дефолтна
   nginx без версії. Ім'я upstream `api` фігурує лише в stderr самого контейнера
   (`api could not be resolved`), клієнту не віддається. `Authorization`/`Cookie` у логи не
   потрапляють (див. M-2 щодо query string). Внутрішній `/internal-api-health` позначено
   `internal` — ззовні недосяжний.
4. **Storage guard у живому браузері** (Playwright/Chromium проти контейнера, не jsdom) —
   таблиця в L-2. Гард не dev-only: він викликається в `src/main.tsx:14` до першого рендеру і
   присутній у production-бандлі (перевірено на зібраному `dist`, який віддає nginx).
   `queryClient` без persister-а — кеш лише в пам'яті (`web/src/api/queryClient.ts:13-31`).
   Токенів/контактів у storage немає взагалі (`storageState` порожній до моїх проб).
5. **Контейнер `gui`** (live `docker exec`/`inspect`): `uid=101(nginx) gid=101(nginx)`,
   `CapPrm=0000000000000000`, `CapEff=0000000000000000`, `NoNewPrivs: 1`, rootfs read-only
   (запис у `/usr/share/nginx/html` і `/etc` відбито), пишуться лише tmpfs `/tmp` і
   `/var/cache/nginx`, `pids: 256`, `/var/run/docker.sock` відсутній (і в усьому compose
   немає жодного socket mount, `privileged`, `cap_add`). Публічний порт один на весь стек —
   `gui` (`80:8080`); решта сервісів без `ports` (`docker compose config --format json`:
   лише `gui` має `published`). Мережі: `gui ∈ {ingress, frontend}`; з контейнера
   `postgres`/`mongo`/`minio` — **NXDOMAIN**, резолвиться лише `api` — тобто мережевого шляху
   до БД і об'єктного сховища немає (§7.7, R-55). `api ∈ {backend, frontend}` — з `ingress`
   прибрано. У runtime-образі немає ні Node, ні npm, ні вихідників; `Config.Env` без
   секретів; `docker history` без credentials.
6. **Ланцюг постачання.** `npm audit --omit=dev` → `found 0 vulnerabilities`;
   `npm audit` (повний) → `found 0 vulnerabilities`. CI блокує: `npm audit --omit=dev
   --audit-level=high` і `npm audit --audit-level=high` (`ci.yml:122-125`) — обидва після
   `npm ci`, тобто по lockfile. Образ `collector-gui` сканується нарівні з `collector`:
   syft SBOM + trivy `CRITICAL` **без** `ignore-unfixed` + trivy `HIGH` з `ignore-unfixed`
   (`ci.yml:221-271`), плюс перевірка `User=101:101` після `docker compose build gui`.
   Базові образи pinned tag+digest (`web/Dockerfile:14-15`: `node:24.19-alpine@sha256:d32cdf…`,
   `nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d5…`). `package-lock.json`
   (lockfileVersion 3, 347 записів): **жодного** `resolved` поза `https://registry.npmjs.org/`,
   жодного git/github/file/tarball-URL, `integrity` є в усіх записах. Постінсталяційних
   скриптів у prod-дереві немає — `hasInstallScript` лише у трьох dev-залежностях
   (`esbuild`, два `fsevents`), причому репозиторій уже працює з npm allow-scripts
   (`npm ci` друкує попередження про неухвалений `esbuild` postinstall). `gitleaks detect`
   по всій історії (89 комітів) — `no leaks found`.
7. **Збірка.** `dist/index.html` без inline-скрипта і без CDN (п. 1). Source maps — L-1.
   Route-level code splitting працює (`NotFoundPage-*.js`, `OverviewPage-*.js` окремими
   chunk-ами), тобто ліниві маршрути WP-11C не роздують початковий бандл.
8. **Межі GUI (§7.7).** Прямого доступу до БД/Docker немає (п. 5). RBAC у PR3 свідомо
   відсутній — `AppShell` без auth, це WP-11C/WP-11A; поточний стаб api не має ані
   автентифікації, ані mutating endpoint-ів, тому неавторизованого доступу до дій немає.
   Детальний health замаскований саме до появи OIDC/RBAC (п. 3).

## Зміни в робочому дереві після зрізу (не входять у вердикт)

Рев'ю зафіксоване на коміті `1901d6e`. На момент запису звіту в worktree були
**незакомічені** правки паралельного код-рев'ю, які частково перетинаються з моїми
знахідками — фіксую, щоб не було подвійної роботи, і додаю зауваження там, де правка має
безпековий наслідок:

- `web/vite.config.ts` — `sourcemap: mode !== 'image'`, тобто `build:image` (єдиний скрипт,
  який виконує `web/Dockerfile`) мапи більше не генерує. Це **закриває L-1** у кращому
  варіанті, ніж я пропонував (мапи лишаються для локальної розробки й CI-артефактів).
  Перевірити на зібраному образі: `ls /usr/share/nginx/html/assets | grep -c '\.map$'` → `0`.
- `web/src/browserStorageGuard.ts` — docstring доповнено секцією «Чим це НЕ є» з чесною межею
  (guard не є контролем проти активного зловмисника; `configurable: true` → `delete` повертає
  нативний геттер), а `console.error` замінено на одноразовий `console.warn`. Це **знімає
  частину L-2** (перебільшення в docstring). Залишається відкритим: iframe-realm і
  IndexedDB/Cache API у тексті не названі, а `indexedDB`/`caches` не додані до `STORAGE_BAN`.
  Окремо: `react-router` справді читає `sessionStorage` на кожному `initialize()` — тобто
  guard кидає на штатному шляху застосунку при кожному завантаженні; варто переконатись, що
  всі такі читання в бандлі дійсно обгорнуті в `try/catch` (інакше це availability-ризик).
- `.github/workflows/ci.yml` — повний `npm audit` (з dev) послаблено з `--audit-level=high`
  до `--audit-level=critical` плюс інформаційний `npm audit || true`; runtime-аудит
  (`--omit=dev --audit-level=high`) лишився блокуючим. **З §13 це узгоджено** («critical CVE
  блокує»), обґрунтування в коментарі коректне (dev-залежності не їдуть ні в bundle, ні в
  образ). Зауваження: після цієї зміни HIGH у toolchain збірки (`vite`/`esbuild`/`jsdom`)
  проходить мовчки — варто, щоб крок `npm audit || true` лишався видимим у виводі job, а не
  перетворився на no-op, і щоб build-toolchain потрапив у щотижневий скан (I-4, WP-13).
  Також додано перевірку OCI-label `revision` GUI-образу і окремий CI-крок з runtime-тестами
  gui — обидва посилюють регресійний захист §13-інваріантів nginx.

## Примітки до прогону

- Стек піднімався і зупинявся тричі; фінально `docker compose down -v --remove-orphans`
  виконано, `docker volume ls --filter name=collector` порожній, контейнерів і мереж
  `collector_*` не лишилось.
- Під час прогону контейнер `collector-gui-1` двічі зникав сторонньо (зупинка/видалення не
  мною; стек ділився з паралельним рев'ю). На результати це не вплинуло: критичні перевірки
  повторено на окремому контейнері з того самого образу у власній мережі.
- `ensure-mongo` при повторному запуску поверх уже ініціалізованого volume впав з
  `AuthenticationFailed` — це артефакт мого середовища (перегенеровані секрети поверх
  старого volume), а не дефект PR3; після `down -v` чистий старт пройшов успішно.
