# `web/` — operator GUI (React + TypeScript + Vite)

Український інтерфейс оператора UA Web Data Collector. Стан — **каркас WP-00 PR3**
(FR-034: лише каркас): маршрутизація, збірка, перевірки та контейнер. Дев'ять екранів §7.7,
generated OpenAPI client, cursor-таблиці й SSE — **WP-11C**.

## Швидкий старт

```bash
cd web
nvm use            # Node 24 LTS з .nvmrc (або будь-який Node 24.x)
npm ci
npm run dev        # http://127.0.0.1:5173, /api проксіюється на http://127.0.0.1:8000
```

Повний ланцюжок перевірок §16.2 ТЗ:

```bash
cd web && npm ci && npm run lint && npm run test && npm run build && npm run test:e2e
```

| Скрипт             | Що робить                                                                      |
| ------------------ | ------------------------------------------------------------------------------ |
| `npm run dev`      | Vite dev-сервер; `/api` → `COLLECTOR_API_URL` (типово `http://127.0.0.1:8000`) |
| `npm run lint`     | ESLint (type-aware, `--max-warnings 0`) + `prettier --check`                   |
| `npm run format`   | `prettier --write`                                                             |
| `npm run test`     | Vitest + Testing Library (jsdom), `tests/unit/**`                              |
| `npm run build`    | `tsc -b` (strict) + `vite build` → `dist/`                                     |
| `npm run preview`  | Роздача `dist/` на `http://127.0.0.1:4173`                                     |
| `npm run test:e2e` | Playwright: збирає, піднімає preview і ганяє `tests/e2e/**`                    |

`npm run test:e2e` першого разу потребує browser binary: `npx playwright install chromium`.

## Структура

```text
web/
  src/
    api/          транспорт і типи; generated OpenAPI client — WP-11C (src/api/README.md)
    components/   спільні UI-примітиви (AppShell, RouteFallback)
    features/     логіка екранів §7.7 — порожньо, owner WP-11C
    routes/       таблиця маршрутів і сторінки-межі lazy-chunk-ів
  tests/
    unit/         Vitest + Testing Library
    e2e/          Playwright smoke проти vite preview
```

Аліас `~/*` → `src/*` (`tsconfig.app.json` + `vite.config.ts`).

### Route-level code splitting

`src/routes/router.tsx` містить лише таблицю маршрутів; кожен листовий маршрут оголошено
через `lazy: async () => import(...)`, тому Vite емітить окремий chunk на маршрут
(`dist/assets/OverviewPage-*.js`, `NotFoundPage-*.js`). `createBrowserRouter` викликається у
`src/main.tsx`, а не в модулі маршрутів: створення data router-а стартує навігацію вже на
імпорті, і в тестах це давало б побічні ефекти.

Додаючи екран у WP-11C, додавайте запис у `routes` з таким самим `lazy` — початковий bundle
не росте.

## Заборона browser storage (§13)

`localStorage`/`sessionStorage` заборонені ESLint-правилами `no-restricted-globals`,
`no-restricted-properties` і `no-restricted-syntax` (`eslint.config.js`, константа
`STORAGE_BAN` з поясненням причини). Заборона покриває і обхідні форми:
`window['localStorage']`, `globalThis.sessionStorage`, `self.localStorage`.

Причина: сховище читається будь-яким JS на origin (XSS миттєво віддає токен або персональні
контакти), не має `HttpOnly`/`SameSite`, переживає вихід користувача і не інвалідується
сервером. Сесія живе у `HttpOnly`-cookie від FastAPI BFF (WP-11A), тимчасовий стан — у
пам'яті React/TanStack Query (кеш без persister-а).

Що правило справді працює, доводить `tests/unit/eslint-no-browser-storage.test.ts`: він бере
конфіг саме для `src/main.tsx`, лінтує зразки коду і вимагає помилку на кожній формі, а
також перевіряє, що самі вихідники GUI чисті.

### Межа статичних правил і runtime-guard

Усі три ESLint-правила **синтаксичні**: вони спрацьовують лише там, де ім'я сховища є в коді
буквально. Присвоєння в проміжну змінну вони не ловлять і не можуть зловити без type-aware
правила на тип `Storage` (знахідка M-1, gate 2):

```ts
const w = window; // lint чистий
w.localStorage.setItem('access_token', token); // lint чистий
```

Тому бар'єр подвійний: `src/browserStorageGuard.ts` підміняє `localStorage` і
`sessionStorage` геттером/сеттером, який кидає `TypeError`, а `src/main.tsx` встановлює його
**до першого рендеру**. Покрито `tests/unit/browser-storage-guard.test.ts` (7 тестів) і E2E
«guard блокує обхід ESLint через alias»; E2E також читає сховище повз сторінку
(`context.storageState()`) і вимагає, щоб воно було порожнє.

**Діагностика без шуму.** `react-router` під час `initialize()` безумовно читає
`sessionStorage` (усередині `try/catch`), тому кидок — штатна подія на кожному завантаженні.
Якби кожне звертання писало `console.error`, попередження про §13 з'являлось би там, де
порушення немає. Тому заборона (кидок) діє завжди, а лог — `console.warn` і лише **один раз
на сховище**, з поміткою, що виклик міг прийти з бібліотеки.

**Чого guard НЕ покриває** (перевірено у Chromium, security-рев'ю L-2 і код-рев'ю L-6):

| вектор                                                             | стан                                                                              |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `localStorage`/`sessionStorage` у головному realm, усі alias-форми | заблоковано                                                                       |
| `delete window.localStorage`                                       | заблоковано                                                                       |
| same-origin `iframe` (`iframe.contentWindow.localStorage`)         | **не заблоковано** — свіжий realm має незаймані акцесори                          |
| IndexedDB, Cache API                                               | **не покрито** — лише ESLint-правило (`indexedDB`, `caches` у списку заборонених) |
| активний XSS                                                       | не покрито — він і так має повний доступ до origin                                |

Тобто guard — anti-footgun проти власної необережності й залежностей у бандлі, а не
security-контроль. Проти зловмисника працюють CSP і те, що токена в JS немає взагалі.

Якщо WP-11C знадобиться браузерне сховище для не-чутливого стану, це свідома зміна
`browserStorageGuard.ts` + ADR, а не локальний `eslint-disable`.

## Контейнер (`Dockerfile`)

Multi-stage: `node:24.19-alpine` (pinned digest) збирає статику → `nginx-unprivileged:1.29-alpine`
(pinned digest) її роздає. У runtime-шарі немає ні Node, ні вихідників. Процес — uid 101,
rootfs read-only, запис лише в tmpfs (`/tmp`, `/var/cache/nginx`).

Конфіг nginx живе не тут, а в `deploy/compose/gui/nginx.conf` (deploy-шар) і приходить у
build окремим контекстом `gui-conf`. Він задає restrictive CSP і security headers, SPA
fallback та same-origin reverse proxy `/api` → `api:8000`. Публічний
`/api/v1/health/components` віддає лише `ready`/`not_ready` — деталі компонентів назовні не
виходять до появи OIDC (WP-11A).

Збірка і запуск — через Compose (profile `gui`):

```bash
docker compose --profile core --profile gui up -d --wait
curl -sI http://localhost/            # CSP і security headers
curl -s http://localhost/api/v1/health/components
```

## Що навмисно ще не зроблено

- екрани §7.7, cursor-таблиці, SSE, mutation-стани, RBAC-gating — WP-11C;
- generated OpenAPI client — WP-11C (`src/api/README.md` описує контракт генерації);
- E2E проти повного Docker stack, a11y-перевірки й локалізація понад `uk` — WP-11C;
- TLS termination перед `gui` — WP-13 (HSTS-заголовок уже віддається).
