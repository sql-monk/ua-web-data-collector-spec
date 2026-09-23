# WP-00 PR3 — пострев'ю за ТЗ (`wp/00-3-web-scaffold`)

| Поле | Значення |
|---|---|
| Branch / HEAD | `wp/00-3-web-scaffold` @ `f371700` (worktree `.worktrees/wp-00-3`) |
| Diff | `git diff main...HEAD` — 55 файлів, +9730/−53 |
| Дата | 2026-09-22 |
| Рев'юер | `wp-spec-reviewer` (read-only; записи лише в цей файл і `docs/acceptance/traceability.md`) |
| Обсяг | Картка `docs/plan/cards/WP-00.md` розділ PR3 (вимоги 1–5, acceptance, Docs) + «Спільні правила»; ТЗ §7.7, §8, §13, §16.2, §16.3, §17.2 (рядок WP-00), §18, Додаток C; FR-030, FR-034; REVIEW.md R-51, R-54, R-55; Q-006/Q-013 |
| Вхідні звіти | `implementation-pr3.md`, `testing-pr3.md` (pass), `code-review-pr3.md` (changes_requested → відповіді в impl-звіті), `security-pr3.md` (approve) |
| Метод | статичний аналіз diff + **власна верифікація**: web-ланцюжок §16.2 на чистому `npm ci`, збірка образу `collector-gui`, повний clean-host старт `core+workers+gui`, curl-проби, `pytest -m e2e` проти живого стека, перевірка мережевої ізоляції, `docker compose down -v` |
| **Вердикт** | **`changes_requested`** — 1 знахідка **high** (CI job `python` падатиме на кожному PR), 0 `missing` по acceptance/DoD, 3 `partial` |

---

## Власна верифікація (що я виконав сам, не з чужого звіту)

```text
$ cd web && npm ci                                        -> found 0 vulnerabilities
$ npm audit --omit=dev --audit-level=high                 -> found 0 vulnerabilities  (exit 0)
$ npm audit --audit-level=high                            -> found 0 vulnerabilities  (exit 0)
$ npm run lint                                            -> eslint 0 + "All matched files use Prettier code style!"
$ npm run test                                            -> Test Files 4 passed | Tests 28 passed
$ npm run build                                           -> dist/assets/{NotFoundPage,OverviewPage,index}-<hash>.js (+ .map)
$ npm run test:build                                      -> Test Files 1 passed | Tests 4 passed
$ npm run build:image                                     -> ті самі chunk-и, у dist/assets 0 файлів *.map

$ COMPOSE_PROFILES=core,workers,gui docker compose build gui   -> Image collector-gui:specrev Built
$ docker inspect collector-gui:specrev
  User=101:101  image.revision=f371700  image.title=collector-gui
$ docker run --rm collector-gui:specrev: у /usr/share/nginx/html/assets 0 *.map;
  env без pass/secret/token/key; /etc/nginx/conf.d/default.conf = наш конфіг (root:root 0644)

$ COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait --wait-timeout 420
  UP_EXIT=0 — усі контейнери Healthy, one-shots (migrate-postgres, ensure-mongo) Exited 0

$ curl -fsS http://localhost/            -> <div id="root">, <html lang="uk">, <title>UA Web Data Collector — operator GUI</title>
$ curl -fsSI http://localhost/           -> CSP default-src 'none'; script-src 'self'; connect-src 'self';
                                            base-uri/form-action/frame-ancestors/object-src 'none';
                                            X-Content-Type-Options, X-Frame-Options: DENY, Referrer-Policy,
                                            Permissions-Policy, COOP/COEP/CORP, HSTS; Server: nginx (без версії)
$ curl http://localhost/api/v1/health/components    -> 200 {"status":"ready"}   (проксі до api живий)
$ curl -sI -H 'Host: evil.example.com' …/components/ -> location: /api/v1/health/components  (SEC M-1 закрито)
$ curl -o /dev/null -w '%{http_code}' http://localhost/api  -> 404  (CR L-8 закрито)

$ CI=true uv run pytest -m e2e tests/e2e -q            -> 25 passed  (жодного skip)
$ docker inspect collector-gui-1  -> мережі: collector_frontend, collector_ingress; User=101:101; ReadonlyRootfs=true
$ docker inspect collector-api-1  -> мережі: collector_backend, collector_frontend  (з `ingress` прибрано)
$ docker exec collector-gui-1 wget http://postgres:5432 -> bad address 'postgres' (сегментація §13 діє)
$ docker compose down -v --remove-orphans              -> exit 0; жодного collector_* volume/network

$ docker compose --profile core --profile workers --profile browser --profile gui config --quiet -> OK
  єдиний `published:` у рендері — "80" (сервіс gui); жодного container_name, жодного docker.sock
```

Стороннього стану не лишив: образ `collector-gui:specrev` видалено, `git status` чистий,
контейнери/мережі/волюми інших проєктів на хості не чіпались.

---

## 1. Acceptance criteria (§17.2 рядок WP-00 + acceptance картки PR3 + дотичні §16.3)

### 1.1. Acceptance картки PR3

| Пункт | Доказ | Статус |
|---|---|---|
| усі команди `npm ci && lint && test && build && test:e2e` зелені | власна верифікація: lint 0, 28 тестів, build з окремими route-chunk-ами; `test:e2e` — `testing-pr3.md` §1.1 (4 passed) і `implementation-pr3.md` §614+ | `evidenced` (e2e — з чужого прогону, у мене не ганявся: Playwright browser download; логіку конфігу перевірено — `web/playwright.config.ts` піднімає `vite preview` на 127.0.0.1:4173) |
| `gui` container `healthy` | власний `up -d --wait` exit 0; `docker inspect collector-gui-1` | `evidenced` |
| `curl http://localhost/` віддає сторінку | власний curl — HTML українською з `<div id="root">` | `evidenced` |
| `curl http://localhost/api/v1/health/components` проксіюється до api | власний curl → `{"status":"ready"}`; гілка `not_ready` — `tests/e2e/test_gui_api_down_branch.py` (3 тести, у мене passed) | `evidenced` |
| CSP/security headers у відповіді | власний `curl -I` (повний набір §13); `tests/e2e/test_gui_runtime_contract.py::test_security_headers_present_on_every_location` (6 типів location) | `evidenced` |
| container non-root | `User=101:101`, `ReadonlyRootfs=true` — і образ, і контейнер | `evidenced` |
| clean-host §16.2 `--profile core --profile workers --profile gui up -d --wait` зелена | власний прогін, exit 0, усі healthy/exited-0 | `evidenced` |

### 1.2. Вимоги PR3 (1–5)

| Вимога | Доказ | Статус |
|---|---|---|
| 1. Vite + React + TS strict, ESLint + Prettier, Vitest + Testing Library, Playwright, `package-lock.json`, Node 24 у `.nvmrc` | `web/package.json` (react 19.1.1, vite 7.3.6, vitest 4.1.11, @playwright/test 1.63.0, typescript 5.9.2), `web/.nvmrc` = `24`, `web/tsconfig.app.json` (`strict`), `web/eslint.config.js`, `web/.prettierrc.json`, `web/package-lock.json` (4976 рядків, `npm ci` чистий) | `evidenced` |
| 2. Структура `src/{api,components,features,routes}`, `tests/{unit,e2e}`, placeholder українською, route-level code splitting, заготовка generated OpenAPI client | `web/src/routes/OverviewPage.tsx` (заголовок «UA Web Data Collector — operator GUI»), `web/src/routes/router.tsx` (обидва маршрути через `lazy: async () => import(...)`), `web/src/api/README.md` (генерація у WP-11C + інваріанти), `web/src/api/{schemaVersion,queryClient}.ts`; збірка дає окремі `OverviewPage-*.js` / `NotFoundPage-*.js` (власний `npm run build`); `web/tests/unit/build-contract.test.ts` фіксує це як контракт | `evidenced` |
| 3. Заборона `localStorage`/`sessionStorage` з коментарем-причиною | `web/eslint.config.js:91-121` — `no-restricted-globals` (+`indexedDB`/`caches` після SEC L-2), `no-restricted-properties` (window/globalThis), `no-restricted-syntax` (computed-доступ, `self.*`); повідомлення `STORAGE_BAN`/`PERSIST_BAN` посилаються на §13; плюс runtime-guard `web/src/browserStorageGuard.ts` і `web/tests/unit/{eslint-no-browser-storage,browser-storage-guard}.test.ts` | `evidenced` (перевищує вимогу: картка просила лише ESLint) |
| 4. Multi-stage image node → `nginx-unprivileged` (pinned digest), non-root, security headers + restrictive CSP, same-origin proxy `/api` → `api:8000` | `web/Dockerfile` (обидва base — tag+digest; `USER 101:101`; HEALTHCHECK; без Node у runtime), `deploy/compose/gui/nginx.conf`; власна перевірка образу і живих заголовків | `evidenced` |
| 4a. Мережевий gate 3 (CR-14/SEC L-2): gui↔api окрема **internal** мережа, у `ingress` лише gui, `api` з `ingress` прибрано | `docker-compose.yml` — мережа `frontend` (`internal: true`), `api: [backend, frontend]`, `gui: [ingress, frontend]`; власний `docker inspect` обох контейнерів; `gui` не резолвить `postgres` | `evidenced` |
| 4b. `/api/v1/health/components` назовні — лише ready/not-ready до OIDC | `nginx.conf` `auth_request` → `@api_ready`/`@api_not_ready`; власний curl → `{"status":"ready"}` без деталей; `test_public_health_exposes_only_ready_flag`, `test_internal_auth_subrequest_is_not_reachable_from_outside`, `test_health_detail_not_reachable_through_path_normalization` | `evidenced` |
| 5. Скрипти `lint`, `test`, `build`, `test:e2e`; E2E — smoke проти `vite preview` | `web/package.json` scripts; `web/playwright.config.ts` (`webServer: npm run build && npm run preview`, `baseURL 127.0.0.1:4173`); `web/tests/e2e/smoke.spec.ts` (4 тести) | `evidenced` |

### 1.3. §17.2, рядок WP-00 — «repo layout, Python/web locks, multi-stage images, Compose profiles/networks/volumes/secrets, migrations, CI, SBOM; clean-host stack smoke green»

| Складова | Де закрито | Статус |
|---|---|---|
| repo layout (Додаток A) | PR1 | `evidenced` (`spec-review-pr1.md`) |
| Python lock | PR1 — `uv.lock` + `UV_FROZEN` | `evidenced` |
| **web lock** | **PR3** — `web/package-lock.json`, `npm ci` у CI job `web`; власний чистий `npm ci` | `evidenced` |
| multi-stage images | PR2 `collector`, **PR3 `collector-gui`** — обидва pinned tag+digest, non-root, OCI labels | `evidenced` |
| Compose profiles/networks/volumes/secrets | PR2 + **PR3** (`gui` профіль став реальним, додано `frontend`) | `evidenced` |
| migrations | PR2 — one-shot `migrate-postgres` з `service_completed_successfully` перед readiness (реальні міграції — WP-01A, вже на `main`) | `evidenced` як foundation-wiring |
| CI | PR1 (python) + PR2 (docker) + **PR3 (job `web`, gui-кроки в job `docker`)** | `partial` — див. знахідку **S-1**: у поточному вигляді job `python` падатиме |
| SBOM | PR2 (`collector`) + **PR3 (`collector-gui`)** — `anchore/sbom-action`, SPDX JSON як artifact для обох образів | `evidenced` структурно, `operationally unverified` фактично (workflow ще не виконувався) |
| **clean-host stack smoke green** | **PR3** — `--profile core --profile workers --profile gui up -d --wait`, власний прогін exit 0 | `evidenced` |

### 1.4. Дотичні пункти §16.3

| Пункт §16.3 | Статус |
|---|---|
| «чистий Docker host підіймає core/workers/gui однією documented командою; migrations/validators завершуються до readiness, restart не втрачає named-volume data» | `evidenced` — власний прогін; readiness через `depends_on: service_completed_successfully` (PR2, `test_readiness_waits_for_one_shots`); named-volume restart доведено у PR2 маркерним записом |
| «GUI/API не мають Docker socket» | `evidenced` — `test_no_docker_socket_mount_anywhere`, власний `docker compose config` без `docker.sock` |
| «GUI E2E покриває pause/resume source, bounded backfill, dead-letter replay, worker scale/drain, matching block/unmerge, release publish і compaction dry-run з RBAC/audit evidence» | `not applicable` для WP-00 — це WP-11C (картка PR3 явно: «E2E проти Docker stack — у WP-11C»); PR3 свідомо не заходить у ці flow |
| решта §16.3 (pilot, quality gates, temporal, matching, release, capacity, DuckDB, translation QA) | `not applicable` — інші WP |

---

## 2. Definition of Done §18 (дев'ять пунктів)

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| 1 | реалізація відповідає одному issue/WP, без сторонніх змін | diff обмежений `web/**`, `deploy/compose/gui/**`, `docker-compose.yml`, `.github/workflows/ci.yml` + задокументоване розширення (`tests/**`, `.gitattributes`, README/runbooks); forbidden-файли (`docs/research/**`, `TECHNICAL_SPECIFICATION.md`, `REVIEW.md`) не змінювались — перевірено `git diff --name-only` | `evidenced` (знахідка **S-3**: таблиця розширення не називає `tests/e2e/**`) |
| 2 | formatter, lint, types, unit/contract/integration tests пройшли | власні `npm run lint` (eslint+prettier), `npm run test` 28/28, `npm run build` (tsc strict); python-гейти — `implementation-pr3.md` §614 (`ruff`, `mypy`, 627 passed) і `testing-pr3.md` §1.2 | `evidenced` |
| 3 | зміна схеми має migration і compatibility evidence | `not applicable` — PR3 схем не змінює | `not applicable` |
| 4 | зміна timestamp/matching/release contract → temporal/replay evidence | `not applicable` | `not applicable` |
| 5 | новий адаптер → manifest/fixtures/golden/coverage | `not applicable` | `not applicable` |
| 6 | документація, метрики й runbook оновлені | `web/README.md` (Docs PR3 картки), `README.md` (розділ GUI + команда §16.2), `deploy/compose/README.md` (профіль/мережі/`nginx.conf`/два health-и), `docs/runbooks/clean-host-start.md`, `docs/runbooks/rollback-image.md` (розділ про gui-image). Метрики GUI — WP-12 | `evidenced` |
| 7 | secret scan чистий; контакти не в logs | gitleaks у pre-commit і CI job `secrets` (PR1); у образі `collector-gui` секретів немає (власна перевірка `env`); `log_format gui_no_query` прибирає query string з access-логу (SEC M-2) | `evidenced` |
| 8 | findings позначені `fixed` / `accepted with owner/date` / `not applicable` | `implementation-pr3.md` розділи «Виправлення після gate 2» і «Відповіді на код-рев'ю»: gate2 H-1/M-1/L-2 `fixed`; CR H-1, M-1..M-4, L-2, L-3, L-4, L-8, L-9(×2) `fixed`; CR L-1, L-5, L-7, L-9(dockerignore, trivy-кеш) `accepted` з owner+датою; SEC M-1, M-2, L-1, L-2, L-4 `fixed`; SEC M-1(444), L-3 `accepted` (owner WP-13, 2026-09-22). **Кожен `fixed`, названий у завданні, я перевірив у коді і на живому стеку** — див. §4.1 нижче | `evidenced` |
| 9 | PR злитий лише після CI та required review | `partial` — CI ще не виконувався жодного разу; знахідка **S-1** каже, що у поточному вигляді він і не стане зеленим | `partial` |

---

## 3. Рядки Додатка C, які покриває PR3

| Ціль Додатка C | Що саме покрив PR3 | Статус |
|---|---|---|
| **Docker і масштабування** (FR-030—FR-033, §7.5—§7.6) — «clean-host start, profiles, replica/drain/kill, global rate-limit і volume restart tests» | clean-host start із `gui` (закриває останню частину перевірки для WP-00); profile `gui` реальний; другий multi-stage non-root image; мережа `frontend` internal | `evidenced` для clean-host/profiles/images; `partial` загалом (replica/drain/kill і global rate-limit — WP-01D/WP-02) |
| **Технічна безпека** (FR-013, §13) — «SSRF/XXE/secret tests and scans» | CSP/security headers на кожному типі location (доведено runtime-тестами і моїм curl); browser-storage заборона (ESLint + runtime guard + E2E); non-root/read-only/cap_drop/no-new-privileges для єдиного публічного сервісу; Host-injection і spoofable XFF закриті; query string не потрапляє в логи; source maps не постачаються; npm audit + SBOM + trivy для `collector-gui` у CI | `evidenced` (сканування — `operationally unverified` до першого прогону CI) |
| **Operator GUI** (FR-034—FR-037, §7.7, §9.10) — «OpenAPI contract, RBAC/CSRF, cursor/SSE recovery, preview/idempotency, accessibility і Playwright E2E» | лише каркас: стек §8 (React+TS+Vite, TanStack Query, React Router), route-level code splitting, українська мова, заготовка generated client із зафіксованими інваріантами (same-origin, cookie-сесія, CSRF, cache key з версією схеми, cursor pagination, SSE окремо), Playwright як інструмент | `partial` — і це правильний стан: §7.7 екрани, RBAC, SSE, cursor tables, preview/idempotency і a11y належать WP-11C |
| **Незалежна реалізація** (§17, §18) | web-частина CI-контракту §16.2 (`cd web && npm ci && npm run lint && npm run test && npm run build && npm run test:e2e`) як job `web`; web lock; clean-host acceptance усього WP-00 у job `docker` | `partial` — структура є, але job `python` у поточному вигляді падатиме (**S-1**) |

---

## 4. Регресія REVIEW.md

| R | Що вимагає | Доказ у коді/тестах | Статус |
|---|---|---|---|
| **R-51** (високий) | контейнерна поставка всіх application-компонентів і відтворюваний clean-host start | GUI тепер теж контейнер (`collector-gui`, pinned digest, OCI labels, HEALTHCHECK); clean-host команда §16.2 у повному складі `core+workers+gui` зелена (власний прогін); SBOM для обох образів; runbook `clean-host-start.md` оновлено | `evidenced` — **саме PR3 закриває R-51 повністю** |
| **R-54** (високий) | GUI не мав scope, API boundary, RBAC і перевірюваних operator flows | ТЗ §7.7 закриває R-54 змістовно; PR3 дає інфраструктурну частину: дев'ять екранів зафіксовані як контракт у коді (`web/src/routes/screens.ts` — усі 9 у порядку §7.7, українською), API boundary реалізований (same-origin `/api` proxy, детальний health не виходить назовні), заготовка generated client описує RBAC/CSRF/SSE як інваріанти WP-11C | `partial` — і це відповідає карточному scope: «реальні екрани GUI — out of scope WP-00» |
| **R-55** (критичний) | Docker socket у GUI/API = host compromise | жодного socket-mount у рендері `docker compose config` (власна перевірка, 17 сервісів); `test_no_docker_socket_mount_anywhere`; `gui` додатково не має доступу до `backend` (`frontend` internal), не резолвить `postgres` | `evidenced` |

### 4.1. Перевірка `fixed`, названих у завданні

| Заявлено `fixed` | Де в коді | Чи справді |
|---|---|---|
| e2e-тести реально виконуються в CI | `.github/workflows/ci.yml` job `docker`: `setup-uv` → `uv sync --frozen` → `uv run pytest -m e2e tests/e2e -rs` між `up -d --wait` і `down -v`; `test_ci_runs_gui_runtime_tests_against_live_stack` фіксує порядок; `npm run test:build` після `npm run build` у job `web` | **так** — кроки є; я відтворив їх зміст локально (25 passed проти живого стека). **Але** механізм заборони skip (`CI=true`) створює новий дефект — знахідка **S-1** |
| `npm audit --audit-level=high` повернуто | `ci.yml:127-130` — обидва прогони (`--omit=dev` і повний) без зниження порогу | **так** — власний прогін обох команд: exit 0, `found 0 vulnerabilities` |
| `absolute_redirect off` | `deploy/compose/gui/nginx.conf`, блок «SEC M-1, друга половина» | **так** — і підтверджено live: `Host: evil.example.com` дає `location: /api/v1/health/components` |
| `log_format` без query | `nginx.conf` — `log_format gui_no_query` з `$uri` замість `$request`, `access_log … gui_no_query` у server-блоці | **так** — директива у файлі і в образі; `testing-pr3.md` §1.4 і `implementation-pr3.md` наводять live-доказ `grep -c SECRETQUERY789 → 0` |
| sourcemap вимкнено для образу | `web/vite.config.ts` — `sourcemap: mode !== 'image'`; `web/Dockerfile` виконує `npm run build:image`; страхувальний `location ~ \.map$ { return 404; }` | **так** — власний `npm run build:image` дав 0 `*.map`; у зібраному образі `/usr/share/nginx/html/assets` містить 0 `*.map` |

---

## 5. Q-питання §20

| Q | Стан | Доказ |
|---|---|---|
| **Q-006** (інфраструктурний бюджет/SLO; дедлайн — «до WP-00 close») | safe default зафіксовано ADR | `docs/decisions/0002-docker-compose-single-host.md` (PR2); PR3 нічого не змінює у топології, лише додає єдиний публічний ingress. **Оскільки PR3 закриває WP-00, Q-006 треба вважати закритим на рівні default — окремої відповіді замовника в репозиторії немає, і це прийнятно (default задокументований)** |
| **Q-013** (production deployment mode) | safe default (Compose для single-host MVP) зафіксовано в ADR-0002 | `evidenced` |
| нових Q PR3 не відкриває | `web/src/api/README.md` прямо відкладає вибір генератора OpenAPI-клієнта в ADR WP-11C — це не Q §20, а дизайн-рішення наступного WP | `evidenced` |

---

## Знахідки

### S-1 | **high** | `.github/workflows/ci.yml:56` + `tests/e2e/test_gui_runtime_contract.py:120`, `tests/e2e/test_gui_api_down_branch.py:55`, `tests/e2e/test_runtime_suite_is_enforced.py:33`

**Механізм заборони skip прив'язаний до універсальної змінної `CI`, тому job `python` падатиме на кожному PR.**

Виправлення CR H-1 зробило умову skip такою:

```python
CI = os.environ.get("CI", "").strip().lower() in {"1", "true", "yes", "on"}
pytest.mark.skipif(not available and not CI, reason=...)
```

GitHub Actions виставляє `CI=true` **в усіх job-ах**, не лише в `docker`. Job `python`
виконує `uv run pytest -m "not live" -rs` (`ci.yml:56`) — а маркер цих модулів `e2e`,
не `live`, тому вони **збираються** цим селектором:

```text
$ uv run pytest -m "not live" --collect-only -q | grep -c 'test_gui\|test_runtime_suite'
37
```

У job `python` немає ні піднятого стека, ні образу `collector-gui`, отже skip вимкнено, а
передумови не виконані. Відтворено локально (стек не піднято, `CI=true`):

```text
$ CI=true uv run pytest -m e2e tests/e2e -q -rs
21 failed, 4 passed in 61.52s
E  Failed: gui недоступний на http://127.0.0.1:80 …, тому тести
   ('test_gui_runtime_contract.py', 'test_gui_api_down_branch.py') були б пропущені.
```

Тобто §16.2-гейт `uv run pytest -m "not live"` стає червоним у CI і на `main`, і на кожному
PR — включно з PR інших WP. Локально це не видно: розробник без `CI` отримує skip, а
імплементатор прогнав `CI=true pytest -m "not live"` **з піднятим стеком** (звіт,
«Команди перевірки після виправлень») — єдина комбінація, у якій дефекту не видно.
Структурний тест `test_ci_python_job_shows_skips` (`tests/unit/test_compose_config.py:578`)
фіксує саме `pytest -m "not live" -rs` у job `python`, тому розбіжність не ловиться.

Це не косметика: DoD §18 п. 9 («PR злитий лише після CI») стає недосяжним, а §17.2 вимагає
для WP-00 саме працездатний CI.

**Пропозиція (будь-який з варіантів, на розсуд імплементатора):**

1. розвести селектори: job `python` → `pytest -m "not live and not e2e" -rs`, job `docker` →
   `pytest -m e2e tests/e2e -rs` (як зараз). Тоді `CI=true` лишається достатнім сигналом саме
   там, де стек піднято. Потрібно оновити `test_ci_python_job_shows_skips`;
2. або прив'язати заборону skip до **власної** змінної, яку виставляє лише job `docker`
   (напр. `COLLECTOR_GUI_RUNTIME=1`), лишивши `CI` поза умовою; `test_runtime_suite_is_enforced`
   тоді теж керується нею.

Варіант 1 дешевший і зберігає «гучне падіння» рівно там, де воно задумане.

### S-2 | low | `.github/workflows/ci.yml:117-126`

Над кроком `npm audit` лишились **два** коментарні блоки, що починаються однаково
(«§13 «Dependency scanning — щотижня і на кожен PR; critical CVE блокує»»). Перший описує
політику, від якої відмовились під час відповіді на CR L-1 («moderate/low видно у виводі, але
не зупиняють PR»), другий — чинну. Читач CI побачить дві суперечливі версії правила.
Прибрати перший блок.

### S-3 | low | `docs/plan/reports/WP-00/implementation-pr3.md` (розділи «Що не перевірено» і «Розширення scope»)

Два місця звіту застаріли відносно власних же виправлень:

- «Що не перевірено» стверджує: «**SBOM/trivy для `collector-gui`** — не додавав: картка PR3
  цього не вимагає» — тоді як gate-2 фікс H-1(б) їх додав (`ci.yml`, кроки SBOM GUI і два
  trivy GUI). Абзац треба переписати на «структура є, фактичний прогін — `operationally
  unverified`», як це коректно сказано в пізнішому розділі;
- таблиця «Розширення scope» перелічує `tests/unit/**` і `tests/integration/**`, але не
  називає три **нові** файли `tests/e2e/test_gui_runtime_contract.py`,
  `tests/e2e/test_gui_api_down_branch.py`, `tests/e2e/test_runtime_suite_is_enforced.py`.
  Для «Спільних правил» картки (owned files) це саме те, що має бути задокументоване явно.

### S-4 | low | сканування образу `collector-gui`

Заявлено `operationally unverified`, бо «`syft`/`trivy` локально відсутні». Це прийнятно як
стан, але не як неможливість: обидва запускаються контейнером над збереженим образом
(`docker save` → `trivy image --input`), без Docker socket і без порушення §13. Оскільки
`collector-gui` — **єдиний публічний сервіс стека**, разовий локальний скан до merge вартий
зусиль. Owner: WP-00 (або WP-13 разом з рештою налаштування сканування).

### S-5 | info | rebase попереду

Branch не перебазований на `main`, який уже містить WP-01A PR1. Перетин змінених файлів:
`.github/workflows/ci.yml`, `README.md`, `tests/unit/test_compose_config.py` (на `main` —
коміт `9ed5ed8 «align WP-00 PR2 tests with real db migrate after rebase»`). Конфлікти
очікувані саме в них; `docker-compose.yml` на `main` після merge-base не змінювався, тож
мережевий gate 3 має пройти чисто. Після rebase знахідку S-1 треба перевірити ще раз:
`main` теж міг торкнутись job `python`.

---

## Підсумок: чи виконаний WP-00 як цілісний work package (§17.2)

Рядок §17.2: «repo layout, Python/web locks, multi-stage images, Compose profiles/networks/
volumes/secrets, migrations, CI, SBOM; clean-host stack smoke green».

| Складова §17.2 | Стан після PR1+PR2+PR3 | Що лишилось і за ким |
|---|---|---|
| repo layout | **done** (PR1, Додаток A, owner-docstring у кожному підпакеті) | — |
| Python lock | **done** (PR1) | нові залежності додають власники WP у своїх PR (правило картки) |
| web lock | **done** (PR3) | — |
| multi-stage images | **done** — `collector` (PR2) і `collector-gui` (PR3), обидва pinned tag+digest, non-root, read-only, OCI labels, HEALTHCHECK | **browser image** — WP-02 PR3 (наразі `browser-worker` стоїть на `collector` з 0 replicas); **immutable registry digest** для app-образів — WP-14 (registry pipeline) |
| Compose profiles/networks/volumes/secrets | **done** для `core`/`workers`/`browser`/`gui` і мереж `ingress`/`frontend`/`backend`/`source-egress`/`provider-egress`/`telemetry` | **profile `observability`** — WP-12; **profile `tools`** — WP-11A/WP-14; **egress-allowlist і TLS termination** (SEC L-3, `default_server 444`) — WP-13 |
| migrations | **done як foundation-wiring** — one-shot `migrate-postgres`/`ensure-mongo` з readiness-бар'єром | реальні SQL-міграції — **WP-01A** (вже злиті у `main`); Mongo validators/indexes — **WP-01B** |
| CI | **структурно done**, але **не зелений** — знахідка **S-1** | закрити S-1 перед merge; **щотижневий розклад сканів** (F-2 з `spec-review-pr2.md`) — WP-13 |
| SBOM | **done** структурно — syft SPDX для обох образів | фактичний прогін — перший PR; **trivy/SBOM policy і кешування** — WP-13 |
| clean-host stack smoke green | **done** — `--profile core --profile workers --profile gui up -d --wait` exit 0, усі 17 контейнерів healthy/exited-0 (перевірено мною незалежно) | — |

**Висновок.** Змістовно WP-00 виконаний: усі вісім складових §17.2 реалізовані, closing
acceptance (clean-host старт із `gui`) відтворений незалежно і зелений, R-51 закритий
повністю, R-55 — повністю, R-54 — у межах, які WP-00 і мав покривати (каркас; екрани,
RBAC, SSE і operator flows §7.7 належать WP-11C). Жодного рядка `missing` в acceptance і
DoD немає.

Єдина причина `changes_requested` — **S-1**: у поточному вигляді CI не може стати зеленим,
а §17.2 і DoD §18 п. 9 вимагають працездатного CI саме від WP-00. Знахідка локальна
(один селектор або одна змінна), не зачіпає ні архітектури, ні жодного з acceptance-пунктів.

Після S-1 (і, за бажанням, S-2/S-3) + rebase на `main` WP-00 можна закривати цілком.

### Що НЕ входить у WP-00 і лишається за іншими owner-ами

- **WP-11C** — дев'ять екранів §7.7, generated OpenAPI client, cursor tables, SSE з
  reconnect/snapshot refresh, mutation states, impact preview/typed confirmation, RBAC-gating
  в UI, a11y, Playwright E2E проти Docker stack.
- **WP-11A** — реальний FastAPI BFF, OIDC Authorization Code + PKCE, `HttpOnly`/`SameSite`
  cookie, CSRF, RBAC на кожному endpoint, публічно-безпечний health-endpoint (від якого
  залежить прийнята знахідка CR L-5 про `auth_request`).
- **WP-13** — TLS termination перед `gui`, `server_name` allowlist + `default_server 444`,
  egress-allowlist для `ingress`, щотижневий розклад сканів, кеш trivy.
- **WP-01A / WP-01B / WP-01D / WP-02 / WP-12** — реальні міграції, Mongo validators,
  worker/lease/scale/drain, browser image, observability stack.
