# WP-00 PR3 — код-рев'ю (`wp/00-3-web-scaffold`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-00 / PR3 «React/TypeScript/Vite scaffold + GUI image» |
| Branch / worktree | `wp/00-3-web-scaffold` / `.worktrees/wp-00-3`, HEAD `1901d6e` |
| Diff | `git diff main...HEAD` без `docs/plan/reports/**` (52 файли, +8488/−53) |
| Вхід | картка `docs/plan/cards/WP-00.md` (розділ PR3), `docs/plan/reports/WP-00/testing-pr3.md`, ТЗ §7.7, §8, §13, §16.2 |
| Режим | read-only; стек не піднімався (паралельно працює security-рев'юер) |
| Вердикт | **`changes_requested`** — 1 high, 4 medium, 9 low |

---

## Знахідки

### HIGH

#### H-1 — тести, що охороняють §13-інваріанти nginx і контракт збірки, у CI ніколи не виконуються

`severity: high | .github/workflows/ci.yml:130-134, 276-288 + tests/e2e/test_gui_runtime_contract.py:87-97 + tests/e2e/test_gui_api_down_branch.py:46-53 + web/tests/unit/build-contract.test.ts:23 | verdict CONFIRMED`

**Claim.** Усі 19 pytest-тестів, доданих gate 2 саме для того, щоб ловити nginx-пастки, і всі
4 тести контракту збірки в CI гарантовано пропускаються. Реальне CI-покриття §13-заголовків —
один `curl -I http://localhost/` з перевіркою 3 заголовків з 9 на одному location з шести.

**Failure scenario (конкретний).**

1. `tests/e2e/test_gui_runtime_contract.py` має module-level
   `pytest.mark.skipif(not _gui_is_up(), …)`, де `_gui_is_up()` б'є у `http://127.0.0.1:80`.
   `test_gui_api_down_branch.py` — `skipif(not _image_exists())` для `collector-gui:dev`.
2. У CI pytest запускає **тільки** job `python` (`uv run pytest -m "not live" -rs`,
   ci.yml:61-62). У цьому job немає ні стека, ні GUI-образу → обидва модулі skip.
3. Стек піднімає job `docker` (ci.yml:274-275), але pytest у ньому не запускається жодного
   разу — лише `curl` (ci.yml:277-288) і `check-healthy.py`.
4. `web/tests/unit/build-contract.test.ts:23` — `describe.skipIf(!built)`, де `built` =
   існування `dist/assets`. У CI порядок кроків `npm run test` (ci.yml:131) → `npm run build`
   (ci.yml:134), а `dist/` у `.gitignore`. На чистому checkout `dist/` немає → всі 4 тести
   skip; після build тести вдруге не запускаються.

Перевірено локально: `npm run test` у цьому worktree дає `4 files / 28 tests` **лише тому,
що `dist/` уже лежить на диску**; на чистому checkout буде 3 файли / 24 тести — і жодного
падіння, жодного сигналу.

**Що це коштує.** Регресія, яку ловить лише пропущений тест і яку CI пропустить зеленою:
хтось додає `add_header Cache-Control "public"` всередину `location /assets/`
(deploy/compose/gui/nginx.conf:116) — за правилом успадкування nginx це **скидає весь
успадкований набір** CSP/`nosniff`/`X-Frame-Options`/COOP/CORP на кожній відповіді з JS і CSS.
`test_security_headers_present_on_every_location[hashed-asset]` це ловить, але він skip;
`curl -I http://localhost/` у ci.yml:280-285 б'є тільки в `/` і лишається зеленим.
Те саме для `index.html` без inline-скрипта (`build-contract.test.ts:47`) — контракт, від
якого залежить `script-src 'self'` без nonce.

Автор конфігу сам двічі документує ці пастки в коментарях (nginx.conf:62-65, 75-76) — тобто
знає, що вони тонкі; тим гірше, що охорона від них у CI мертва.

**Мінімальний фікс (будь-який один рядок кожного пункту).**

- job `docker`, після `up -d --wait`: `uv run pytest -m e2e tests/e2e -rs` (образ і стек уже
  є; `COLLECTOR_GUI_IMAGE=collector-gui:ci` вже в env job-а);
- job `web`: перенести `npm run test` після `npm run build`, або додати другий прогін
  `npx vitest run tests/unit/build-contract.test.ts` після build (ланцюжок §16.2 у картці
  фіксує порядок команд, тому другий прогін — менш інвазивний варіант);
- додатково варто зробити skip гучним у CI (`-p no:randomly … --strict-markers` не допоможе;
  простіше — окремий крок з явним `pytest --co -q tests/e2e | wc -l` або `-W error` на skip),
  щоб «усе зелено» ніколи не означало «нічого не виконувалось».

---

### MEDIUM

#### M-1 — `browserStorageGuard` друкує `console.error` §13 на кожному завантаженні сторінки (хибна тривога)

`severity: medium | web/src/browserStorageGuard.ts:50 + web/src/main.tsx:16 | verdict CONFIRMED (відтворено в Chromium на реальному dist/)`

**Claim.** Guard спрацьовує не на порушенні, а на штатному коді React Router, і кожен старт
застосунку пише в консоль червоне «Заборонено §13: browser storage не використовується…».

**Failure scenario.** `react-router` 7.18.4 у `createBrowserRouter().initialize()` виконує
безумовно (node_modules/react-router/dist/development/chunk-OB3PAWPO.mjs:1698-1703):

```js
if (isBrowser) {
  restoreAppliedTransitions(routerWindow, appliedViewTransitions);   // → _window.sessionStorage.getItem(...)
  routerWindow.addEventListener("pagehide", _saveAppliedTransitions);
}
```

`isBrowser` — це просто `typeof window !== "undefined"`, жодного зв'язку з `viewTransition`.
`restoreAppliedTransitions` обгорнута в `try { … } catch {}` (там же, :5801-5815), тому
застосунок **не ламається** — але наш геттер до кидка робить `console.error(MESSAGE)`
(browserStorageGuard.ts:50), і цей лог нічим не глушиться.

Відтворено: зібраний `dist/` подано статикою, відкрито Chromium —

```text
--- console on load ---
[error] Заборонено §13: browser storage не використовується в operator GUI (XSS-читання, …)
--- after navigation ---
[error] Заборонено §13: browser storage не використовується в operator GUI (XSS-читання, …)
```

У мінімізованому бандлі це видно як 4 звертання до `sessionStorage` з `react-router`
(`dist/assets/index-*.js`), усі — alias-форми `n.sessionStorage`, тобто рівно те, що guard
задуманий ловити.

**Чому це medium, а не косметика.**

- Повідомлення про порушення §13 з'являється там, де порушення немає. Через тиждень
  розробник навчиться його ігнорувати — і пропустить справжнє.
- Будь-який майбутній E2E-асерт «на сторінці немає console errors» (звичайна практика для
  operator GUI) впаде одразу.
- Це водночас доказ, що вибір «кидати замість повертати no-op» справді зачіпає сторонні
  бібліотеки. Сьогодні пронесло лише тому, що React Router має `catch {}`. Бібліотека без
  `try/catch` (наприклад `ScrollRestoration` того ж React Router, або devtools TanStack
  Query) впаде вже не тихо.

**Пропозиція.** Розділити діагностику і заборону: `throw` лишити (він і так несе текст), а
`console.error` або прибрати, або зробити «раз на ключ» через прапорець, або понизити до
`console.warn` з поміткою, що виклик міг прийти з бібліотеки. Компроміс «гучно ≫ тихо»
правильний — але гучність має вмикатись від порушення, а не від завантаження сторінки.

#### M-2 — production-образ публічно віддає повні source maps

`severity: medium | web/vite.config.ts:20 + web/Dockerfile:54 + deploy/compose/gui/nginx.conf:116-119 | verdict CONFIRMED`

**Claim.** `build.sourcemap: true` діє й для образу; `COPY --from=builder /build/dist
/usr/share/nginx/html` кладе `.map` у образ, а `location /assets/` віддає їх будь-кому з
`expires 1y`.

**Failure scenario.** `ls dist/assets` → `index-CgGEjsJW.js.map` 1 552 314 байт (плюс дві
сторінкові). `gui` — єдиний сервіс з публічним портом (`${GUI_PORT:-80}:8080`), автентифікації
до WP-11A немає. Анонімний `GET /assets/index-*.js.map` віддає повне оригінальне TS-дерево
GUI (`sourcesContent`), включно зі структурою каталогів і коментарями. Плюс ~1.5 МБ мертвої
ваги в образі і в кеші браузера на рік.

**Пропозиція.** `sourcemap: false` (або `'hidden'` + вивантаження мап в артефакт CI) для
збірки образу — напр. окремий режим у `build:image`; або, як мінімум,
`location ~ \.map$ { return 404; }` у nginx. Зауважу, що `build-contract.test.ts:41` уже
свідомо виключає `.map` з перевірки content hash — тобто наявність мап у `dist/` помічена,
але наслідок для образу не розглянутий.

#### M-3 — на єдиному edge `X-Forwarded-For` доклеюється до клієнтського значення

`severity: medium | deploy/compose/gui/nginx.conf:99-101 | verdict CONFIRMED`

**Claim.** `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` — правильна форма
для проксі **за** довіреним проксі. `gui` за §7.5 тепер єдиний ingress, перед ним нічого
немає (TLS termination — WP-13), тому це форма підробки.

**Failure scenario.** `curl -H 'X-Forwarded-For: 10.0.0.1' http://<host>/api/v1/...` → api
отримує `X-Forwarded-For: 10.0.0.1, <реальний IP>`. Будь-який споживач, який бере
**перший** елемент (звичайна конвенція, і саме так роблять типові helper-и FastAPI/starlette
та rate limiter-и), отримає підконтрольне клієнту значення. Наслідок для WP-11A: audit log
§13 «хто, коли і що змінив» фіксує підроблений IP; per-IP rate limit обходиться зміною
заголовка на кожному запиті.

Суміжне (той самий рядок 99): `proxy_set_header Host $host` при `server_name _` пропускає
будь-який Host усередину. Starlette з `redirect_slashes` побудує 307 `Location` з цього
Host — керований ззовні редирект. Імпакт низький (браузер не надішле чужий Host сам), але
разом з XFF це одна й та сама причина: edge довіряє клієнтським заголовкам.

**Пропозиція.** На edge: `proxy_set_header X-Forwarded-For $remote_addr;` (замінити, не
доклеювати), і повернутись до `$proxy_add_x_forwarded_for` тільки коли перед `gui` з'явиться
довірений termination WP-13. Для Host — або `$host` з `server_name` за allowlist, або
`proxy_set_header Host $proxy_host`.

#### M-4 — новий e2e-тест має busy-loop замість очікування і буде flaky на повільнішому хості

`severity: medium | tests/e2e/test_gui_api_down_branch.py:111-121 | verdict CONFIRMED`

**Claim.** `_wait_until_serving` крутить `for _ in range(60)` без жодної паузи між спробами.

**Failure scenario.** Одразу після `docker run -d` порт ще не слухається → `urlopen` падає
`ConnectionRefusedError` **миттєво** (refused, не timeout). 60 ітерацій відпрацьовують за
десятки мілісекунд — тобто фактичне вікно очікування старту nginx становить ~0.05 с замість
очікуваних «60 спроб по 2 с». На хості, де контейнер стартує довше за 50 мс (а це норма під
навантаженням і на CI-runner-і), тест падає `AssertionError: gui не піднявся` без жодного
зв'язку з предметом перевірки. Локально пройшло випадково.

Дрібніше в тому ж файлі: `_free_port()` (:104-107) звільняє порт до `docker run` — класичний
TOCTOU, під паралельним прогоном дасть `port is already allocated`.

**Пропозиція.** `time.sleep(0.5)` у хвості циклу (або `attempts` × явний deadline за
`time.monotonic()`), і публікувати порт як `-p 127.0.0.1::8080` з читанням реального порту
через `docker port`.

---

### LOW

#### L-1 — `npm audit` продубльовано, і dev-only HIGH блокує будь-який PR

`severity: low | .github/workflows/ci.yml:122-125 | verdict CONFIRMED`

`npm audit --omit=dev --audit-level=high` і наступний `npm audit --audit-level=high` — другий
є строгим надмножинним варіантом першого з тим самим порогом, тому перший ніколи не змінює
результат job-а (лише дає окреме повідомлення). Практичний наслідок: HIGH у `vite`/`esbuild`/
`jsdom` — тобто в коді, що **ніколи не потрапляє в браузер і не потрапляє в образ** —
зупиняє кожен PR репозиторію до апдейту upstream. §13 вимагає сканування на кожен PR, але не
вимагає однакового порогу для runtime і dev.

Пропозиція: `--omit=dev --audit-level=high` лишити блокуючим; повний аудит зробити
інформаційним (`|| true` з друком) або підняти йому поріг до `critical`. Зараз
`test_ci_web_job_audits_npm_dependencies` (tests/unit/test_compose_config.py) закріплює саме
поточну форму, тож змінювати доведеться разом з тестом.

Поточний стан залежностей перевірено: обидві команди — `found 0 vulnerabilities`, тобто H-1
gate 2 закрито реально, а не ігнором. `vitest` 4.1.11 — стабільна мінорна в мажорі 4.x,
типи не зламані (`tsc -b --force` і `eslint . --max-warnings 0` зелені, 28 тестів проходять).

#### L-2 — глобальні таймаути Vitest підняті заради одного повільного тесту

`severity: low | web/vite.config.ts:49-50 | verdict CONFIRMED`

`testTimeout: 60_000` / `hookTimeout: 120_000` застосовуються до **всіх** 28 тестів, хоча
причина одна — `eslint-no-browser-storage.test.ts`, який запускає ESLint програмно (локально
~180 с на весь файл проти ~0.3 с на `routes.test.tsx`). Наслідок: зависання будь-якого
іншого тесту тепер коштує хвилину замість 5 с. Точніше — `describe(..., { timeout })` або
`it(..., 60_000)` саме у тому файлі.

#### L-3 — `resolver` без `resolver_timeout`

`severity: low | deploy/compose/gui/nginx.conf:24 | verdict CONFIRMED`

`proxy_connect_timeout`/`proxy_read_timeout` не покривають фазу DNS. При зависанні
вбудованого docker-резолвера запит до `/api/` висітиме до дефолтних 30 с (і health-підзапит
теж — при `proxy_connect_timeout 3s`, що виглядає як гарантія, але нею не є). Додати
`resolver_timeout 3s;`.

#### L-4 — «tripwire»-тести перевіряють текст файлів, а не поведінку

`severity: low | web/tests/unit/eslint-no-browser-storage.test.ts:101-113 | verdict CONFIRMED`

`expect(guard).toMatch(/Object\.defineProperty/)`, `expect(spec).toMatch(/NOT BLOCKED/)`,
`expect(main).toMatch(/installBrowserStorageGuard\(\)/)` — це асерти на реалізацію: вони
впадуть від нешкідливого рефакторингу (перейменування рядка-проби у Playwright-тесті,
перехід на `Reflect.defineProperty`) і не впадуть, якщо guard перестане працювати. Намір
(«не дати прибрати страховку непомітно») зрозумілий і легітимний, але поведінку вже
перевіряють `browser-storage-guard.test.ts` і E2E; тут достатньо перевіряти факт імпорту
модуля в `main.tsx`, а не форму його внутрішнього коду.

#### L-5 — `auth_request` + healthcheck зламаються, коли WP-11A закриє health автентифікацією

`severity: low | deploy/compose/gui/nginx.conf:60-70 + docker-compose.yml (healthcheck gui) | verdict PLAUSIBLE`

`auth_request` пересилає заголовки клієнтського запиту у підзапит. `error_page 500 502 504`
покриває тільки випадок, коли nginx сам перетворив не-2xx у 500. Але 401 і 403 від підзапиту
nginx **віддає клієнту як є**, повз `error_page` — отже, щойно WP-11A поставить
`/api/v1/health/components` за OIDC/RBAC, публічний endpoint почне віддавати 401 з HTML-тілом
замість `{"status":"not_ready"}`, а docker healthcheck сервісу `gui`, який б'є рівно в цей
шлях, стане постійно failing. Варто лишити коментар-попередження в конфігу або одразу
розвести liveness/readiness (`/healthz` для liveness уже є).

Ширше зауваження про спрощення: уся конструкція (`auth_request` → `try_files` у неіснуючий
файл → named location → `error_page`-ремап) існує, щоб приховати тіло відповіді API. Штатне
місце для цього — окремий публічно-безпечний endpoint на боці API (`GET /api/v1/health` →
`{"status": …}`), який WP-11A все одно робитиме. Тоді в nginx лишається один звичайний
`proxy_pass`, і три коментарі про фази nginx стають не потрібні. Це не зауваження до PR3
(без API-змін інакше не вийшло), а рекомендація не носити цю конструкцію далі.

#### L-6 — guard знімається одним `delete`, а документація описує його як контроль проти XSS

`severity: low | web/src/browserStorageGuard.ts:56-60 та шапка файлу | verdict CONFIRMED`

Дескриптор ставиться з `configurable: true` (потрібно для ідемпотентності й тестів), тому
`delete window.localStorage` прибирає підміну і повертає нативний геттер з `Window.prototype`.
Для зловмисника з XSS guard не існує. Це нормально — це anti-footgun для своїх, а не
security-контроль. Але шапка файлу і `eslint.config.js` подають його в одному абзаці з
«XSS миттєво віддає токен», через що наступний рев'юер може на нього спертись. Варто одним
реченням зафіксувати: guard захищає від власної необережності й від залежностей у бандлі,
але не від активного зловмисника — там працює тільки CSP і відсутність токена в JS.

#### L-7 — image-збірка змушена копіювати `tsconfig.test.json` для файлів, яких у контексті немає

`severity: low | web/Dockerfile:36-41 + web/tsconfig.json | verdict CONFIRMED`

`build:image` виконує `tsc -b tsconfig.app.json` і кореневий solution-конфіг не читає — але
`vite build` резолвить `references` кореневого `tsconfig.json`, тому в контекст доводиться
класти конфіг проєкту, чиї `include: ["tests/unit"]` у контексті свідомо відсутні. Працює,
задокументовано, але це неочевидний зв'язок, який зламається при будь-якій зміні набору
проєктів. Простіша альтернатива — не тримати solution-references у кореневому `tsconfig.json`
(або дати Vite окремий `tsconfig` через `esbuild.tsconfigRaw`).

Розділення `tsconfig.app.json` / `tsconfig.test.json` саме по собі зроблено правильно:
типізація не втрачена (`tsc -b --force` покриває всі три проєкти, `include: ["src"]` і
`include: ["tests/unit"]` не перетинаються, `strict` + 8 додаткових прапорців успадковуються
тестовим проєктом). Єдина дрібниця — `tsconfig.app.json` тягне `"types": ["vitest/globals",
"@testing-library/jest-dom"]` у простір типів `src`, тобто `describe()` у продуктивному коді
типізується без помилки.

#### L-8 — `/api/` без trailing slash і без cache-directives

`severity: low | deploy/compose/gui/nginx.conf:96, 126-128 | verdict CONFIRMED`

- `location /api/` не матчить рівно `/api` — такий запит падає у SPA-fallback і віддає
  `index.html` з кодом 200 замість 404 від API. Плюс до вже зафіксованої тестувальником I-1
  (регістр: `/API/v1/...` теж іде у fallback).
- Для `/api/` не задано жодної cache-директиви: `proxy_cache off` вимикає кеш **nginx**, а не
  браузера. Персональні дані (§7.7 «контакти не кешуються») лишаються на дисковому кеші
  браузера, якщо API не поставить `Cache-Control: no-store` сам. Правильне місце — API
  (WP-11A), але `expires -1;` у цьому location дав би дешевий страхувальний шар (і, на
  відміну від `add_header`, не скинув би успадковані заголовки §13).

#### L-9 — дрібні залишки

`severity: low | verdict CONFIRMED`

- `deploy/compose/gui/nginx.conf:103` — `proxy_set_header Connection ""` без блоку
  `upstream{} … keepalive` нічого не вмикає (та й `proxy_pass` зі змінною keepalive-пул не
  використовує); коментар поруч створює хибне враження.
- `.github/workflows/ci.yml:207-209` — `docker compose build gui` не отримує
  `COLLECTOR_GIT_SHA`/`COLLECTOR_VERSION`/`COLLECTOR_CREATED` (на відміну від кроку
  `docker build collector`, ci.yml:196-201), тому `org.opencontainers.image.revision` у
  GUI-образі завжди `unknown`. `COLLECTOR_GIT_SHA` уже є в env workflow (ci.yml:22) —
  достатньо прокинути решту.
- `web/.dockerignore` виключає `tests/e2e`, але не `tests/unit` (в образ вони все одно не
  потрапляють — `COPY` перелічений явно; питання лише розміру контексту й консистентності).
- Чотири прогони trivy (2 образи × 2 політики) не дублюють роботу логічно — політики різні
  (`CRITICAL` без `ignore-unfixed` і `HIGH` з ним), але кожен крок заново тягне БД
  вразливостей; кеш trivy (`cache-dir` + `actions/cache`) зекономив би 3 завантаження.

---

## Що перевірено окремо

**Виконано (read-only):**

| Команда | Результат |
|---|---|
| `cd web && npx tsc -b --force` | exit 0 (усі три проєкти) |
| `cd web && npx eslint . --max-warnings 0` | exit 0 |
| `cd web && npm run test` | 4 files / 28 tests passed (з наявним `dist/` — див. H-1) |
| `cd web && npm audit --audit-level=high` та `--omit=dev` | `found 0 vulnerabilities` обидва |
| `docker compose config --quiet` | OK |
| `docker compose --profile core --profile workers --profile browser --profile gui config --quiet` | OK |
| Chromium проти зібраного `dist/` (статикою, порт 45173) | відтворено M-1 |

**Прочитано і розібрано:** `deploy/compose/gui/nginx.conf` (фази rewrite/access/content,
`auth_request`, `try_files` → named location, `error_page`-ремап, успадкування `add_header`
vs `expires`, кешування, SPA-fallback, `server_tokens`, `client_max_body_size`),
`web/src/**`, усі чотири `tsconfig*`, `vite.config.ts`, `eslint.config.js`, `package.json`,
`web/Dockerfile`, `docker-compose.yml` (сервіс `gui`, мережі `frontend`/`ingress`),
`.github/workflows/ci.yml`, всі додані тести (`web/tests/**`, `tests/e2e/**`,
`tests/unit/test_compose_config.py`), `node_modules/react-router/dist/**` (для M-1).

**Перевірено і підтверджено правильним (без знахідок):**

- **Пастка `add_header` у вкладеному location** — обійдена свідомо: жоден з шести
  location-ів не використовує `add_header`, кешування задається через `expires`, який
  успадкування не ламає. Це головна тонкість конфігу, і вона зроблена правильно.
- **`try_files` → named location для health** — коректно: `return` у rewrite-фазі
  відпрацював би до `auth_request`; мутація M3 звіту тестування це відтворює. Повільний api
  не ламає гілку: `proxy_connect_timeout 3s` + `proxy_read_timeout 5s` → 504 у підзапиті →
  `auth_request` → 500 → `error_page` → 503 `not_ready`.
- **Приховування детального звіту** — `location = /internal-api-health` має `internal`;
  нормалізація (`//api/`, `/api//v1/`, `..`, `?query`, trailing slash, `%2f`) не виводить на
  `location /api/` в обхід exact-match, бо матчинг іде по нормалізованому `$uri`.
- **`proxy_buffering off` + `proxy_read_timeout 300s`** для SSE (§7.7, WP-11C) — доречно;
  `gzip_proxied` за замовчуванням `off`, тож проксійовані відповіді не стискаються (без
  BREACH-поверхні).
- **`server_tokens off`, `client_max_body_size 2m`** — присутні й адекватні для GUI.
- **SPA fallback не маскує помилки API**: `location /api/` (prefix) специфічніший за `/`,
  тому 404/502 від API доходять як є; `/assets/` має `try_files $uri =404`; `index.html`
  віддається з `Cache-Control: no-cache` (через `expires -1`, у т.ч. на fallback-шляху —
  внутрішній redirect `/index.html` повторно матчить `location = /index.html`).
- **`browserStorageGuard` не ламає бібліотеки в поточному складі**: єдиний споживач
  storage у бандлі — `react-router` (4 звертання), і всі в `try/catch`. TanStack Query без
  persister storage не чіпає (`queryClient.ts` явно без persister). SSR у проєкті немає,
  guard викликається лише з `main.tsx`; ідемпотентність реалізована коректно (мітка на
  функції-геттері, `Reflect.get` замість `descriptor.get` через `unbound-method`).
  Компроміс «кидати, а не повертати no-op» вважаю виправданим; єдине зауваження — M-1 про лог.
- **Chunk-стратегія**: route-level splitting через `lazy` у таблиці маршрутів, окремий chunk
  на маршрут (перевірено у `dist/assets`), нічого зайвого в бандлі (291 КБ — це React +
  React DOM + Router + Query, без CSS-in-JS і без полізаповнювачів). `build` детермінований
  (підтверджено звітом тестування побайтово; імена з content hash).
- **`build:image` не ховає тести від перевірок**: `npm run lint` проходить по всьому дереву,
  а `npm run build` (розробник + job `web`) перевіряє всі три TS-проєкти; `build:image`
  вужчий лише всередині образу, де тестів фізично немає.
- **Compose**: `api` прибрано з `ingress`, `frontend` internal, `gui` не бачить `backend`;
  one-shots і health це не зламало (`depends_on` усередині `backend`, healthcheck api
  локальний). `gui` — `read_only` + `101:101` + `cap_drop ALL` + `no-new-privileges` + tmpfs,
  без volumes/secrets/environment.
- **`spec-mismatch`**: не виявлено. Усі 5 вимог картки PR3 реалізовані, включно з окремою
  internal-мережею і видаленням `api` з `ingress`.

**Оцінка виправлень gate 2:** H-1 закрито по суті (версії підняті, `npm audit` 0, SBOM+trivy
для `collector-gui` з тією ж політикою, що для `collector`) — зауваження лише до форми
(L-1). M-1 закрито змістовно (runtime-guard дійсно ловить alias-форми, які ESLint ловити не
може) — але з побічним ефектом M-1 цього звіту. L-2 закрито документацією, що адекватно.

---

## Вердикт

**`changes_requested`.**

Блокує merge одна знахідка — **H-1**: тести, які цей PR сам називає головним доказом
§13-інваріантів, у CI не виконуються жодного разу, тому конфіг nginx фактично не має
регресійного захисту. Фікс — рядок-два у `ci.yml`.

Medium-знахідки (M-1 хибна тривога guard-а на кожному завантаженні, M-2 публічні source maps,
M-3 підроблюваний `X-Forwarded-For` на єдиному edge, M-4 flaky busy-loop у новому e2e)
merge формально не блокують, але M-1 і M-3 варто закрити разом з H-1 — вони дешеві й обидві
стосуються саме тих контрактів (§13, аудит), заради яких PR писався.

Сам каркас — React/TS/Vite, розділення tsconfig-ів, route-level splitting, nginx-конфіг з
правильно обійденими пастками успадкування і фаз — зроблений акуратно і суттєвих
архітектурних зауважень не має.
