# План реалізації субагентами: UA Web Data Collector

| Поле | Значення |
|---|---|
| Версія плану | 1.0 |
| Дата | 2026-09-22 |
| Базовий контракт | `TECHNICAL_SPECIFICATION.md` v1.4, `REVIEW.md`, `docs/research/*` |
| Модель виконання | Один оркестратор (основна сесія Claude Code) + короткоживучі спеціалізовані субагенти на кожен work package |
| Обов'язковий конвеєр на кожен WP | Картка → Реалізація → Тестування → Код-рев'ю → Пострев'ю (приймання за ТЗ) → Документування → Інтеграція |

## 1. Результати аналізу теки

### 1.1. Що є

| Файл | Роль у реалізації |
|---|---|
| `TECHNICAL_SPECIFICATION.md` (1102 рядки) | Контракт реалізації: 37 FR, архітектура, контракти даних §9, алгоритм §10, тести §16, 34 work packages §17.2, DoD §18, відкриті питання Q-001—Q-014 |
| `REVIEW.md` | 57 закритих знахідок R-01—R-57; кожна знахідка є регресійним критерієм для пострев'ю (не повторити виправлену помилку) |
| `docs/research/source-registry.yaml` | 70 канонічних `source_id` (58 news + 4 vehicles + 8 catalogs); адаптери не вигадують інших ID |
| `docs/research/news-*.md`, `ua-marketplaces.md` | Live-паспорти джерел: URL, redirects, robots/RSS/sitemap, схеми сторінок, блокування, `access_state`, стратегія адаптера, acceptance-fixtures |
| `.markdownlint-cli2.jsonc`, `.markdown-link-check.json` | Уже наявний docs-lint: MD013/MD060 вимкнені, link-check приймає 403 |
| `.gitignore` | Натякає на стек: `uv`/`.venv`, pytest/mypy/ruff cache, Playwright report, `dist/`, `build/` |

### 1.2. Чого немає (створюється в WP-00)

`pyproject.toml`, `uv.lock`, `src/`, `web/`, `schemas/`, `migrations/`, `deploy/`, `tests/`, `sources/`, `docs/{adr,runbooks,decisions}/`, `dashboards/`, `.github/`, `.claude/agents/`, CI, pre-commit/secret scan.

### 1.3. Ключові обмеження, які визначають форму плану

1. **Порядок злиття зафіксований у §17.1:** спочатку WP-00 і WP-01C, потім WP-01A/01B/01D і WP-02—04, далі адаптери та GUI на versioned fixtures/OpenAPI.
2. **Єдине ownership shared-артефактів:** WP-01C — contracts/schemas, WP-01A — SQL migrations, WP-01B — Mongo validators/indexes. Інші WP подають зміну як dependency-задачу власнику, не редагують паралельно.
3. **Один WP — один owner — одна branch/PR.** Інтегратор не виправляє чужий код мовчки (§17.1).
4. **Мережа заборонена у звичайних тестах** (§8, §16.1); live smoke — максимум 3–10 URL, явний прапорець, без CI schedule.
5. **Команди §16.2 є контрактом CI**; назви CLI можуть змінитися лише один раз у foundation PR.
6. **Чотири незалежні осі стану §5.5** — research-позначення `access_state`/`free`/`body_unavailable` мапляться в `content_access` і не переносяться в код як власні enum.
7. **Докази не переносяться між джерелами** (§17.1) — кожен адаптер має власні fixtures, coverage report і smoke.
8. Деякі джерела з research мають стартувати як `blocked_anonymous` або `metadata_only` (PAP, SME, article-fetch iROZHLAS; France 24, RFI, ZEIT, AP; Le Monde body challenge; Der Standard consent gate). Адаптер зобов'язаний закодувати це явно, а не «домислити» body.

## 2. Принципи конвеєра

- **Свіжий контекст для кожної ролі.** Тестувальник, код-рев'юер і пострев'юер не бачать міркувань реалізатора; вони отримують лише diff, картку WP і посилання на розділи ТЗ. Це усуває підтвердження власних припущень.
- **Докази у файлах, не в контексті.** Кожен етап пише звіт у `docs/plan/reports/<WP>/<stage>.md`. Оркестратор читає підсумок, а не увесь tool output.
- **Ізоляція через git worktree.** Кожен реалізатор працює у власному worktree на branch `wp/<id>`; оркестратор не змішує робочі дерева.
- **Failed gate повертає задачу власнику,** а не наступному етапу. Рев'юер не виправляє код; він фіксує знахідку зі статусом і рядком.
- **Нічого не вважається зробленим без виконаних команд §16.2.** Звіт «тести пройшли» без виводу команди не приймається.
- **Розмір diff обмежений.** PR понад ~800 змінених рядків продуктивного коду розбивається на послідовні PR того самого owner (див. §6, стовпець «Розбиття»).
- **Live-доступ до джерел — лише з дозволу користувача**, окремим subagent-run із прапорцем `--live`, зафіксованим User-Agent і лімітом URL.

## 3. Ролі субагентів

| Роль | Призначення | Модель (рекомендація) | Інструменти | Вхід | Вихід | Заборонено |
|---|---|---|---|---|---|---|
| `orchestrator` | Основна сесія: формує картки, запускає субагентів, звіряє gates, зливає, веде ledger | opus | усі | ТЗ, план, ledger | оновлений ledger, злиті PR | писати продуктивний код WP; «дотягувати» чужий PR |
| `wp-implementer` | Реалізує один WP у worktree | opus | Read/Write/Edit/Bash/Grep/Glob | картка WP, розділи ТЗ, owned files | код + тести + `implementation.md` звіт | редагувати shared schema/migrations без dependency-задачі; live-мережа; secrets у fixtures |
| `wp-tester` | Незалежно перевіряє реалізацію: запускає контракт команд, пише відсутні adversarial-тести, fault injection | opus | Read/Write/Bash/Grep/Glob (Edit лише в `tests/`) | diff, картка, §16.1 рівні для WP | `testing.md` з виводом команд, coverage, списком нових тестів | змінювати продуктивний код; вимикати/skip-ити тести |
| `wp-code-reviewer` | Код-рев'ю на коректність, гонки, ідемпотентність, спрощення, ефективність | opus | Read/Grep/Glob/Bash (read-only) | diff, картка | `code-review.md`: знахідки з file:line, severity, verdict | правити код; оцінювати відповідність ТЗ (це пострев'ю) |
| `wp-spec-reviewer` | **Пострев'ю**: приймання за ТЗ після реалізації — acceptance §17.2, DoD §18, traceability Додаток C, регресії R-01—R-57 | opus | Read/Grep/Glob/Bash (read-only) | diff, картка, ТЗ, REVIEW.md, звіти попередніх етапів | `spec-review.md`: матриця вимога → доказ → статус | правити код; приймати «на слово» без посилання на тест/файл |
| `wp-docs-writer` | README/ADR/runbook/docstrings/CHANGELOG, метрики й алерти в docs; docs-lint | sonnet | Read/Write/Edit/Bash | злитий або approved diff, звіти етапів | doc-файли + `docs.md` | змінювати код або тести; вигадувати неперевірені команди |
| `wp-security-reviewer` | Threat model §13 для fetch/API/GUI/Docker PR; WP-13 повністю | opus | Read/Grep/Bash (read-only, scanners) | diff, §13 | `security.md` | правити код |
| `source-canary` | Bounded live smoke одного джерела (≤10 URL) з явного дозволу | sonnet | Bash + мережа | manifest, список URL | датований snapshot у `tests/fixtures/<source_id>/live/`, `canary.md` | обходити CAPTCHA/challenge/login; більше ліміту URL |

Пропоновані визначення для `.claude/agents/*.md` — у Додатку A. Роль виконує субагент із **власною** інструкцією; параметр `subagent_type: "fork"` для рев'ю не використовується, бо fork успадковує контекст реалізатора.

## 4. Конвеєр одного WP

```text
[0] Картка WP (orchestrator) ─► [1] Реалізація (implementer, worktree)
      │                                   │
      │                          [2] Тестування (tester)  ──fail──► [1]
      │                                   │ pass
      │                          [3] Код-рев'ю (code-reviewer) ──findings──► [1] ──► [3'] re-review diff
      │                                   │ approve
      │                          [4] Пострев'ю (spec-reviewer) ──gap──► [1] або ADR/ТЗ-зміна
      │                                   │ accept
      │                          [5] Документування (docs-writer) ──► docs-lint
      │                                   │
      └──────────────────────────► [6] Інтеграція (orchestrator): CI, merge, SHA у ledger
```

### 4.1. Етап 0 — Картка WP (Definition of Ready)

Оркестратор створює `docs/plan/cards/<WP>.md` за шаблоном §17.3 ТЗ. Картка готова лише коли має:

- scope / out-of-scope і посилання на конкретні §/FR ТЗ;
- **owned files** (глоби) і **forbidden files** (shared contracts, чужі адаптери);
- input contract + version (напр. `schemas/events/projection.command@1`), output contract + version;
- fixtures: які існують, які треба створити, provenance;
- команди перевірки (підмножина §16.2);
- acceptance criteria — дослівно з §17.2 + §16.3, де застосовно;
- залежності (WP + стан у ledger — має бути `merged`);
- rollback/disable plan;
- для адаптера: `source_id` з registry, research-секція, очікувані `source_state`/`route_state`/`content_access`.

### 4.2. Етап 1 — Реалізація

- Оркестратор запускає `wp-implementer` з `isolation: "worktree"`, branch `wp/<id>`.
- Реалізатор працює **лише** в owned files. Потреба змінити shared contract → створює `docs/plan/deps/<WP>-to-<owner>.md` і зупиняється на цій частині; оркестратор маршрутизує задачу власнику.
- Виходом є `docs/plan/reports/<WP>/implementation.md`: що зроблено, які команди виконано з фактичним виводом, що не перевірено, ризики, як вимкнути.
- Реалізатор **не** оголошує «done» — це робить gate.

### 4.3. Етап 2 — Тестування

`wp-tester` у тому самому worktree (після завершення реалізатора):

1. Виконує повний контракт команд картки (`uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy src`, `pytest -m "not live"`, `docker compose config --quiet`, для web — `npm ci && npm run lint && npm run test && npm run build`).
2. Звіряє рівні §16.1, призначені WP (див. §7), і **дописує відсутні тести** в `tests/` — особливо adversarial: out-of-order, crash між кроками, expired lease, дублікат idempotency key, порожня відповідь ≠ видалення.
3. Перевіряє, що тести фактично тестують (mutation-check: тимчасово ламає ключову гілку і бачить червоний тест; повертає).
4. Пише `testing.md` з дослівним виводом команд і таблицею «рівень §16.1 → тести → результат».

Gate: усі команди зелені; кожен acceptance-пункт картки має щонайменше один тест або явну позначку `not testable offline` з обґрунтуванням.

### 4.4. Етап 3 — Код-рев'ю

`wp-code-reviewer` отримує лише `git diff main...wp/<id>`, картку і `testing.md`. Чек-лист:

- коректність: гонки, ідемпотентність, транзакційні межі (одна task = одна Mongo-транзакція; parser не пише у дві БД), CAS/монотонні версії, lease/expiry;
- обробка помилок: retryable vs permanent, `fetch_outcome` ≠ `content_access`, нескінченні retry заборонені;
- дані: `amount_minor BIGINT + currency`, UTC `timestamptz`, source time nullable і ніколи не підміняється fetch time;
- безпека вхідних даних (для fetch/parse): SSRF, розмір body, XXE, secrets у логах;
- спрощення/повторне використання: адаптер не створює власний HTTP client, не дублює SDK;
- типізація `mypy strict`, відсутність `# type: ignore` без причини.

Формат знахідки: `severity (critical/high/medium/low) | file:line | claim | failure scenario | verdict CONFIRMED/PLAUSIBLE`. Реалізатор відповідає на кожну: `fixed` (з commit) / `accepted with owner/date` / `not applicable` (аргумент). Re-review лише по інкрементальному diff. Можна використати `/code-review high` як інструмент рев'юера; результати все одно оформлюються у `code-review.md`.

### 4.5. Етап 4 — Пострев'ю (приймання за ТЗ)

`wp-spec-reviewer` перевіряє **не код, а відповідність контракту**. Вихід — матриця у `spec-review.md`:

| Вимога (FR/§/acceptance) | Доказ (файл, тест, звіт) | Статус |
|---|---|---|

Обов'язкові блоки:

1. Acceptance criteria WP з §17.2 і релевантні пункти §16.3 — кожен із доказом.
2. DoD §18 — усі 9 пунктів.
3. Traceability Додаток C — рядки, які покриває WP.
4. **Регресія REVIEW.md:** для R-знахідок, дотичних до WP, перевірити, що виправлення не втрачене (напр. R-25 out-of-order для WP-01B, R-53 aggregate rate для WP-01D/02, R-19 browser fallback для WP-02/08B, R-20 nullable body для WP-05/06).
5. Розбіжність між кодом і ТЗ → або знахідка реалізатору, або пропозиція ADR/зміни ТЗ (ТЗ змінюється **до** коду, §0).
6. Незакриті Q-питання, від яких залежить WP: перевірити, що використано safe default §20 і що default конфігурується.

Gate: жодного `critical/high` без статусу `fixed`; `accepted` має owner і дату.

### 4.6. Етап 5 — Документування

`wp-docs-writer` після approve пострев'ю:

- README адаптера (§11: smoke command, ліміти, rollback/disable), README модуля, docstrings публічних інтерфейсів;
- ADR у `docs/decisions/NNNN-title.md` (Context/Decision/Consequences/Date/Owner/Status) для кожного рішення, яке WP прийняв поза ТЗ або за Q-default;
- runbook у `docs/runbooks/` для операційних дій WP (§14.2: pause source, replay from raw, reconcile, drain pool, rollback image/parser);
- перелік нових метрик/алертів у `docs/observability/metrics.md` (WP-12 зводить);
- оновлення `README.md` кореня і, за потреби, `TECHNICAL_SPECIFICATION.md` §16.2 (лише через foundation PR);
- `markdownlint-cli2` + `markdown-link-check` зелені (конфіги вже в репозиторії).

### 4.7. Етап 6 — Інтеграція

Оркестратор: rebase на `main`, CI зелений, required review = approve code-reviewer + accept spec-reviewer, merge, запис у ledger `merged <sha>`. Після злиття WP, від якого залежать інші, оркестратор розблоковує картки наступної хвилі.

## 5. Хвилі виконання

| Хвиля | WP | Паралельність | Умова старту | Вихід хвилі |
|---|---|---|---|---|
| 0 | WP-00 → WP-01C | 1, потім 1 (01C може стартувати після появи repo layout у branch WP-00) | — | clean-host stack smoke green; schemas v1 + compatibility fixtures |
| 1 | WP-01A, WP-01D, WP-02, WP-04 паралельно; WP-01B після merge WP-01A | до 4 реалізаторів + рев'ю-агенти | WP-00, WP-01C `merged` | SQL/Mongo з нуля green, limiter/scale tests green, fetch SSRF/rate green, всі language pairs green |
| 2 | WP-03, WP-05, WP-07, WP-09 | 4 | WP-01A/01B/02/04 `merged` | discovery fixtures green; news SDK + coverage report; vehicle/catalog contracts + matching golden |
| 3 | WP-06A–G (7), WP-08A–D (4), WP-10A–H (8), WP-11A | адаптери безконфліктні: 8–10 одночасно; WP-11A окремо | WP-05 (news), WP-03+07 (vehicles), WP-03+09 (catalogs); WP-11A після 01A/01B/04/07/09 | кожне джерело має доказаний `source_state`/`route_state`/`content_access`; OpenAPI §9.10 |
| 4 | WP-11B, WP-11C, WP-12 | 3 | WP-11A (11B/11C), WP-01B/01D/02/04/11A (12) | DuckDB kit offline green; GUI E2E green; dashboards/alerts/runbooks |
| 5 | WP-13 → WP-14 | 1, потім 1 | WP-02—12 `merged` | findings triaged; 7-day pilot, acceptance report |

Правила паралельності:

- у хвилях 1–2 не більше 4–5 реалізаторів одночасно — усі торкаються ядра, і рев'ю має встигати;
- у хвилі 3 адаптери запускаються пакетами по 8–10 (обмеження — пропускна здатність рев'ю та оркестратора, не конфлікти файлів);
- реалізатор наступної хвилі стартує лише після `merged` залежностей — не після `approved`;
- рев'ю/тест-агенти живуть один етап і не перевикористовуються між WP (свіжий контекст).

Оцінка загального навантаження: 34 WP × (1 реалізатор + 1 тестер + 1–2 код-рев'ю + 1 пострев'ю + 1 docs) ≈ 170–200 запусків субагентів плюс канарки. Orchestrator може виконувати конвеєр вручну через `Agent` або, за явної згоди користувача, через `Workflow` (pipeline `implement → test → review → spec-review → docs`) — за замовчуванням ручний режим.

## 6. Каталог work packages

Розмір: S (< 300 рядків), M (300–800), L (800–2000), XL (> 2000 — обов'язкове розбиття на послідовні PR одного owner).

### 6.1. Хвиля 0

| WP | Розмір | Owned files | Розділи ТЗ | Тести §16.1 | Особливі перевірки пострев'ю | Docs | Розбиття |
|---|---|---|---|---|---|---|---|
| WP-00 Foundation | L | `pyproject.toml`, `uv.lock`, `.python-version`, `docker-compose.yml`, `deploy/**`, `Dockerfile*`, `.github/**`, `web/` scaffold, `src/collector/{__init__,cli}.py`, `tests/conftest.py`, `.pre-commit-config.yaml`, `.claude/agents/**` | §7.5, §8, §16.2, §18, Додаток A, FR-030, FR-013 | 14 Docker | назви CLI §16.2 зафіксовано (єдина дозволена зміна); profiles/networks/secrets; non-root/read-only; без `container_name` у workers; pinned digests; SBOM; secret scan у pre-commit/CI; Q-006 default | README quickstart, `docs/runbooks/clean-host-start.md`, ADR-0001 стек | PR1 python+CI, PR2 Docker/Compose, PR3 web scaffold |
| WP-01C Shared contracts | M | `src/collector/contracts/**`, `schemas/{events,mongo,releases}/**`, `tests/contract/contracts/**` | §5.1, §5.5, §9.3, §9.4, §9.6, §9.8, §9.9, §7.3 (event shapes), R-18, R-30, R-42, R-43 | 2 Contract, 9 Temporal | чотири enum-осі + `fetch_outcome`; `projection.command` ≠ `domain.changed`; canonical UTF-8 event bytes + SHA-256; temporal precision/inferred; money `amount_minor`; minor/major versioning з compatibility test | `docs/contracts.md`: як додати поле, як підвищити версію | — |

### 6.2. Хвиля 1

| WP | Розмір | Owned files | Розділи ТЗ | Тести §16.1 | Особливі перевірки пострев'ю | Docs | Розбиття |
|---|---|---|---|---|---|---|---|
| WP-01A PostgreSQL | L | `migrations/postgres/**`, `src/collector/persistence/postgres/**`, `tests/integration/postgres/**` | §9.1, §7.2, §7.3 крок 2/4, §9.5, §13 (ролі), R-27, R-28, R-32, R-38, R-41 | 3 Integration | усі таблиці §9.1 + обов'язкові indexes; partitioning за місяцем; `FOR UPDATE SKIP LOCKED` claim із lease; upload claim `claim_generation` + commit predicate; без domain payload JSONB; окремі DB roles; forward-only migrations | `docs/runbooks/migrations.md`, схема ER | PR1 control/jobs/limiter, PR2 artifacts/projection/outbox, PR3 news/matching/release/capacity |
| WP-01B MongoDB | XL | `migrations/mongo/**`, `src/collector/persistence/mongo/**`, `src/collector/workers/projector.py`, `src/collector/workers/reconciler.py`, `src/collector/workers/compactor.py`, `tests/integration/mongo/**` | §7.3 кроки 3–5, §7.4, §9.2, §9.5, §9.7, R-24—R-26, R-31, R-34, R-36, R-37, R-40, R-44, R-50 | 3 Integration, 11 Compaction | `$jsonSchema` validators warn→error; всі unique indexes §9.2; одна транзакція на task; порядок `3,1,2` → current=3; crash між кроками 3–4 → replay без нової observation; `TransientTransactionError`/`UnknownTransactionCommitResult`; receipt із event bytes; compaction: locator публікується **до** delete; rollback window | `docs/runbooks/{reconcile,restore-mongo,compaction}.md`, ADR single-member RS (Q-010) | PR1 validators/indexes/repositories, PR2 projector+receipts, PR3 reconciler+ack, PR4 compaction/archive |
| WP-01D Worker pool control | L | `src/collector/workers/{base,pool,heartbeat,drain}.py`, `src/collector/orchestration/{compose,swarm}/**`, `src/collector/core/limiter.py`, `tests/integration/scaling/**` | §7.5 (scaling), §7.6, FR-031—FR-033, FR-035, R-52, R-53, R-55, R-57 | 15 Scaling | global PostgreSQL token bucket з leased permits; role-wide drain barrier; scale command states; Compose adapter повертає CLI без socket; Swarm adapter allowlist `collector.scalable=true`, лише replicas у min/max; killed replica → lease recovery; concurrency hot-change | `docs/runbooks/scale-drain-recover.md`, ADR deployment mode (Q-013) | PR1 pools/instances/heartbeat, PR2 limiter, PR3 drain+adapters |
| WP-02 Fetch core | L | `src/collector/fetch/**`, `tests/unit/fetch/**`, `tests/integration/fetch/**` | §3, §10 кроки 4–6, §13, FR-004, FR-005, R-16, R-19, R-33, R-38 | 1 Unit, 3, 8 Security | conditional GET; redirects із DNS/IP-перевіркою на кожному hop; body 20 МБ / sitemap 100 МБ; robots snapshot versioned; retry policy §10; browser fallback лише anonymous JS-rendering, CAPTCHA/challenge → route stop; upload claim протокол end-to-end; media binaries off (Q-002) | `docs/runbooks/pause-source.md`, fetch README | PR1 HTTP+SSRF+limits, PR2 raw upload claim, PR3 browser worker image |
| WP-04 Translation core | L | `src/collector/translation/**`, `tests/unit/translation/**`, `tests/fixtures/translation/golden/**` | §5.4, §8 (Google/NLLB), §10 кроки 11–12, §12.1 translation gates, §12.3 rubric, FR-016, FR-017, R-03—R-05, R-08, R-09, R-20 | 1 Unit, 7 Translation QA | segmenter не рве HTML; TM key = 5 компонентів; `uk` → `not_required`; змішані мови по сегментах; body nullable; preservation чисел/URL/імен 100%; budget + пріоритет title/lead → body → backfill; provider interface без мережі в тестах (respx) | ADR budget/segments (Q-008, Q-009), `docs/translation-qa.md` | PR1 segmenter+TM+glossary, PR2 provider interface+Google adapter+budget, PR3 golden corpus 16 мов |

### 6.3. Хвиля 2

| WP | Розмір | Owned files | Розділи ТЗ | Тести §16.1 | Особливі перевірки пострев'ю | Docs | Розбиття |
|---|---|---|---|---|---|---|---|
| WP-03 Discovery | M | `src/collector/discovery/**`, `tests/unit/discovery/**`, `tests/fixtures/discovery/**` | §10 кроки 1–3, FR-002, FR-003, §15 (streaming sitemap), Q-003 | 1, 2 | sitemap index/urlset streaming + gzip; RSS зі збереженням entry ID і raw XML; cursors persisted; idempotency key `source_id + normalized_url + planned_at_bucket + request_variant`; scheduler не запускає два несумісні повні обходи | discovery README, ADR sharding (Q-003) | — |
| WP-05 News adapter SDK | M | `src/collector/adapters/news/_sdk/**`, `src/collector/normalization/news/**`, `tests/unit/news_sdk/**`, `tests/fixtures/news/_template/**` | §5.4, §5.5, §11, §12.1, FR-019, research «Контракт, спільний для всіх адаптерів», parser order JSON-LD → OG → SSR state → selectors | 1, 2 | mapping research `access_state` → `content_access` §5.5; `metadata_only` без згенерованого body; coverage report generator (FR-019); fixture harness із provenance; canonical dedup (не `www`-варіант); fail-closed на зникнення body selector | «Як написати news adapter» гайд, шаблон README адаптера | — |
| WP-07 Vehicle contracts & matching | M | `src/collector/contracts/vehicles/**` (через dependency до WP-01C, якщо shared), `src/collector/normalization/vehicles/**`, `src/collector/matching/vehicles/**`, `tests/**/vehicles/**` | §5.3, §9.3, §9.8, §12.2, FR-026, Q-012 | 2, 10 Resolution | VIN точний → source ID; fuzzy лише candidate; decision versioned + supersedes; manual block блокує auto-merge; unmerge = replay history; E.164 телефони із збереженням raw | ADR thresholds (Q-012), dictionaries README | — |
| WP-09 Catalog contracts & matching | M | аналогічно для `catalogs` | §5.2, §9.3, §9.8, §12.2, FR-026 | 2, 10 | GTIN → brand+MPN → fuzzy candidate; reviews/questions `content_version` unique; category mapping benchmark | ADR thresholds, category mapping README | — |

### 6.4. Хвиля 3 — адаптери (шаблон однієї картки)

Кожен адаптер джерела — окрема картка й окремий субагент. Owned files: `sources/<source_id>/manifest.yaml`, `src/collector/adapters/<domain>/<source_id>/**`, `tests/fixtures/<source_id>/**`, `tests/contract/adapters/<source_id>/**`, README адаптера. Вхід: SDK/contracts `merged`, research-секція джерела, `source_id` з registry.

Обов'язковий вихід (§11, research acceptance):

- `manifest.yaml` за Додатком B з rating breakdown, anonymous evidence, allow/deny, schedule/rate, translation policy;
- discovery з persisted cursor; parser із `parser_version`;
- 3–10 raw fixtures (feed item, sitemap sample, list page, free article/картка, оновлення, blocked/partial/premium/404) — datovані, з provenance; **без** secrets і без вилучення публічних полів;
- golden normalized JSON + schema validation; URL identity/canonicalization tests; cursor tests;
- coverage report FR-019 і 30-record sample §12.3 (для новин — 30 перекладів за rubric або явний план human QA);
- явний `source_state`/`route_state`/`content_access` для кожного route і sample;
- README: smoke command, ліміти, rollback/disable.

Пострев'ю адаптера окремо перевіряє: використано лише спільний fetch layer; жодного власного HTTP client; порожня відповідь ≠ видалення; заблоковані джерела закодовані як `blocked_anonymous`/`metadata_only`, а не «працюють на fixture»; live-докази не запозичені з іншого джерела.

| Група WP | Джерела (source_id) | Стартовий режим за research |
|---|---|---|
| WP-06A UA/DE/AT | `news_ua_{suspilne,pravda,liga,ukrinform}`, `news_de_{tagesschau,dw,zeit}`, `news_at_{orf,derstandard,diepresse}` | ZEIT metadata_only; Der Standard consent gate → metadata; pravda backfill unverified |
| WP-06B FR/BE | `news_fr_{france24,rfi,lemonde}`, `news_be_{vrt,rtbf,brussels_times}` | France 24/RFI metadata_only; Le Monde mixed; BE — визначати країну й мову окремо |
| WP-06C GB/US | `news_gb_{bbc,guardian,sky}`, `news_us_{npr,ap,nyt}` | AP metadata_only; NYT/Sky metadata-first |
| WP-06D Baltics | `news_lt_*`, `news_lv_*`, `news_ee_*` (9) | Delfi ×3, TVNET, Postimees, 15min — fixture з явним `content_access` |
| WP-06E PL/HU/RO | `news_pl_*`, `news_hu_*`, `news_ro_*` (9) | PAP disabled до smoke; 444/Polskie Radio — обмежений архівний experiment |
| WP-06F CZ/SK/SI/HR | `news_cz_*`, `news_sk_*`, `news_si_*`, `news_hr_*` (12) | SME disabled; iROZHLAS лише RSS-ingestion |
| WP-06G IT/ES | `news_it_*`, `news_es_*` (6) | La Vanguardia приймати лише `isAccessibleForFree=true` |
| WP-08A–D | `vehicle_ua_auto_ria`, OLX Авто, RST, Automoto | OLX browser detail fallback; RST reveal `operationally_unverified`; RST charset fixture |
| WP-10A–H | Prom, Rozetka, Epicentr, Allo, Hotline, Comfy, Foxtrot, MOYO | Rozetka/Comfy HTTP challenge можливий → browser route з canary |

Групи WP-06 містять по 6–12 джерел; кожне джерело — **окремий PR** одного owner групи, щоб рев'ю лишалося малим. Якщо група > 9 джерел (06D/06E/06F), оркестратор може призначити двох owners з поділом за країною.

### 6.5. Хвилі 3–5 — платформа

| WP | Розмір | Owned files | Розділи ТЗ | Тести §16.1 | Особливі перевірки пострев'ю | Docs | Розбиття |
|---|---|---|---|---|---|---|---|
| WP-11A Operator API/releases | XL | `src/collector/api/**`, `src/collector/export/**`, `research/` manifest verifier CLI, `tests/integration/api/**` | §9.5, §9.9, §9.10, §7.7 (RBAC table), §13, FR-023, FR-027, FR-034, FR-036, FR-037, R-29, R-46, R-49, R-56 | 3, 12 Release, 16 (contract) | `problem+json`; cursor lists; `Idempotency-Key` + `If-Match`; preview→apply з expiry; SSE cursor + `resync_required`; two-step read → `409 projection_inconsistent`; release lifecycle immutable; deterministic Parquet (sort, codec, row-group); два builds → однакові hashes; OIDC BFF + CSRF; RBAC на кожному endpoint | OpenAPI опубліковано в `web/` як контракт; ADR cadence (Q-011) | PR по endpoint groups §9.10: system/sources, jobs/pools, data/lineage, translations/matching, releases/retention, audit/SSE, auth |
| WP-11B DuckDB research kit | S | `research/{sql,views}/**`, `deploy/compose` tools profile, `tests/e2e/research/**` | §9.9, FR-029, §8 DuckDB | 12 | verify manifest/hashes до SQL; read-only views/macros; без credentials до operational БД; `as_of_valid_time`/`as_known_at` macros | `docs/research-kit.md` | — |
| WP-11C Operator GUI | XL | `web/**` | §7.7, §9.10, FR-034—FR-037, §13 (CSP, no localStorage tokens), R-54, R-56, R-57 | 16 GUI | українська за замовчуванням; generated client з OpenAPI; server-side cursor tables; контакти не в browser storage; SSE gap → snapshot refresh; typed confirmation + reason + revision для destructive; RBAC gating лише UX; Playwright E2E з §16.3 списком flows | GUI README, a11y звіт | PR по екранах §7.7: shell/auth → огляд → джерела → jobs → workers → дані → matching → releases → retention → аудит |
| WP-12 Observability/lifecycle/runbooks | L | `src/collector/telemetry/**`, `dashboards/**`, `deploy/compose/observability/**`, `docs/runbooks/**`, `docs/observability/**` | §14, §15.1, FR-028, §7.4 (restore drill) | 13 Capacity, injected failures | усі метрики §14.1 без URL/exception/item ID у labels; SEV-1/2/3 alerts; capacity snapshot з golden формулами і 30% headroom gate; runbook на кожен пункт §14.2 | зведення runbooks, dashboards README | PR1 metrics/OTel, PR2 dashboards/alerts, PR3 capacity planner, PR4 runbooks |
| WP-13 Security review | M | `docs/security/**`, `.github/workflows/security.yml`, findings у ledger | §13, FR-013 | 8 Security + scanners | threat model §13 перевірено тестами; dependency/image scan щотижня і на PR; critical CVE блокує; GUI/API/workers без Docker socket; secrets не в fixtures | threat model doc, risk acceptance register | — |
| WP-14 Integration/release | L | `docs/acceptance/**`, `docs/plan/ledger.md` | §16.3, §2.4, Додаток C | 4 E2E offline, 5 Live smoke (з дозволу), 6 Load | усі пункти §16.3 з доказом; 19 країн × ≥1 `enabled` джерело з `content_access=full` + переклад; traceability matrix заповнена; 7-day pilot | acceptance report | — |

## 7. Тестування: рівні → власники → момент

| Рівень §16.1 | Хто пише | Хто запускає як gate | Коли | Мережа |
|---|---|---|---|---|
| 1 Unit | implementer | tester (кожен PR) | завжди | ні |
| 2 Contract (fixture → golden) | implementer адаптера/контракту | tester | кожен адаптер/contract PR | ні |
| 3 Integration (PG + Mongo RS + MinIO) | implementer 01A/01B/01D/02/11A | tester; CI через Docker services | хвилі 1–4 | ні (локальні контейнери) |
| 4 E2E offline | WP-14 owner + внески 01B/03/05 | tester WP-14; CI nightly | після хвилі 2, потім кожна хвиля | заблокована |
| 5 Live smoke | `source-canary` | лише з дозволу користувача, поза CI | адаптер перед пострев'ю; WP-14 | так, ≤10 URL, прапорець |
| 6 Load (2× прогноз) | WP-12/WP-14 | окремий run | хвиля 5 | ні |
| 7 Translation QA | WP-04 + WP-06x (по 30 зразків) | tester WP-04; human QA поза агентами | хвиля 1, потім кожен news adapter | ні (provider mock) |
| 8 Security | WP-02, WP-11A, WP-13 | security-reviewer | fetch/API/GUI PR, WP-13 | ні |
| 9 Temporal | WP-01C, WP-05, WP-07/09 | tester | contracts + adapters | ні |
| 10 Resolution | WP-07, WP-09 | tester | хвиля 2 | ні |
| 11 Compaction | WP-01B | tester | хвиля 1 | ні |
| 12 Release/analytics | WP-11A, WP-11B | tester | хвиля 3–4 | ні |
| 13 Capacity | WP-12 | tester | хвиля 4 | ні |
| 14 Docker | WP-00 | tester; CI | хвиля 0, потім кожна зміна deploy/ | ні |
| 15 Scaling | WP-01D | tester | хвиля 1 | ні |
| 16 GUI | WP-11C | tester; CI (Vitest) + E2E проти Docker stack | хвиля 4 | ні |

Правила для тестера:

- fault injection обов'язковий там, де ТЗ його називає: після Mongo commit до PG ack (§16.3), після S3 PUT до PG commit, killed replica, expired permit;
- тест не може бути «зеленим за замовчуванням»: перевіряється, що він падає на зламаній реалізації;
- flaky-тест — знахідка high, а не retry;
- coverage не є gate сам по собі; gate — покриття acceptance-пунктів картки.

## 8. Рев'ю: два різні гейти

| | Код-рев'ю | Пострев'ю |
|---|---|---|
| Питання | «Чи код правильний і простий?» | «Чи зроблено те, що вимагає ТЗ, і чи доведено?» |
| Вхід | diff | diff + ТЗ + REVIEW.md + звіти тест/рев'ю |
| Вихід | знахідки file:line з verdict | матриця вимога → доказ → статус |
| Інструмент | `/code-review high`, `/simplify` (лише рекомендації) | ручний чек-лист §4.5 |
| Блокує merge | critical/high не `fixed` | будь-який acceptance-пункт без доказу |
| Може змінити ТЗ | ні | так — через пропозицію ADR/зміни ТЗ до злиття коду |

Статуси знахідок (§18): `fixed` (commit SHA) / `accepted with owner/date` / `not applicable` (аргумент). Інші статуси не існують.

## 9. Документування

| Артефакт | Де | Хто | Коли |
|---|---|---|---|
| Картка WP | `docs/plan/cards/<WP>.md` | orchestrator | етап 0 |
| Звіти етапів | `docs/plan/reports/<WP>/{implementation,testing,code-review,spec-review,docs,security,canary}.md` | відповідна роль | кожен етап |
| Ledger | `docs/plan/ledger.md` — таблиця WP × стан (`ready/in_progress/testing/review/spec_review/docs/merged <sha>/blocked <reason>`) | orchestrator | після кожного gate |
| Dependency-запити до owners shared-артефактів | `docs/plan/deps/<WP>-to-<owner>.md` | implementer | за потреби |
| ADR | `docs/decisions/NNNN-title.md` | docs-writer (зміст — від implementer/spec-reviewer) | етап 5 |
| Runbooks | `docs/runbooks/*.md` | docs-writer; WP-12 зводить | етап 5 |
| README адаптера/модуля | поряд із кодом | docs-writer | етап 5 |
| Метрики/алерти | `docs/observability/metrics.md` | docs-writer, WP-12 | етап 5 |
| Docs-lint | `markdownlint-cli2 "**/*.md"` + `markdown-link-check` | docs-writer; CI | етап 5, CI |
| Traceability matrix | `docs/acceptance/traceability.md` (Додаток C → тести/файли) | spec-reviewer додає рядки; WP-14 фіналізує | етап 4 |

Правило: документ описує лише те, що виконано і перевірено; неперевірене позначається `operationally unverified` тим самим терміном, що в research.

## 10. Конвенції репозиторію для агентів

- Branch: `wp/<id>` (напр. `wp/06d-lt-lrt`), один PR на джерело/під-PR; base `main`.
- Commit: `<type>(<wp>): <summary>` — `feat(wp-02): raw upload claim with generation fencing`.
- PR body: шаблон з §17.1 — зміни, тести (з виводом), fixture provenance, ризики, як вимкнути/відкотити, що не перевірено live, посилання на звіти етапів.
- Worktree: `Agent(..., isolation: "worktree")`; оркестратор не запускає двох implementers на одному worktree.
- Заборонено у будь-якій ролі: обхід CAPTCHA/challenge/login, private cookies, source API keys, secrets у fixtures/логах, `--no-verify`, вимкнення тестів для проходження gate.

## 11. Відкриті рішення та ескалація до користувача

Питання §20 мають safe default і не блокують старт, але кожен default має бути конфігурованим і зафіксованим ADR тим WP, що його використовує:

| Q | Default | WP, який оформлює ADR | Дедлайн за ТЗ |
|---|---|---|---|
| Q-006 бюджет/SLO | один хост, SLO §2.4 | WP-00 | до WP-00 close |
| Q-002 media binaries | off | WP-02 | до WP-02 close |
| Q-003 шардінг каталогу | hash категорії | WP-03 | до WP-03 close |
| Q-008/Q-009 translation budget, segments | budget config, backfill paused; лише змінені сегменти | WP-04 | до WP-04 close/live |
| Q-012 auto-merge thresholds | лише deterministic IDs | WP-07, WP-09 | до close |
| Q-011 cadence releases | щотижня + on-demand | WP-11A | до close |
| Q-013 deployment mode | Compose MVP, Swarm для GUI-scaling | WP-01D | до production |
| Q-010 Mongo topology | single-member RS локально | WP-01B | до production |
| Q-014 autoscale | off | WP-01D | після pilot |
| Q-001, Q-004, Q-005, Q-007 | за §20 | WP-14 фіксує у acceptance report | pilot |

Оркестратор ескалює до користувача лише: (а) live smoke будь-якого джерела, (б) конфлікт ТЗ ↔ реалізація, який потребує зміни ТЗ, (в) вибір між кількома валідними default, що змінює обсяг роботи, (г) зовнішні витрати (Google Translation credentials/budget).

## 12. Шаблони промптів субагентів

Усі промпти містять: шлях до картки, шлях до ТЗ із конкретними §, owned/forbidden files, команди перевірки, куди писати звіт. Приклади нижче — каркаси, які оркестратор наповнює з картки.

### 12.1. `wp-implementer`

```text
Ти реалізуєш <WP> у worktree на branch wp/<id>. Контракт: docs/plan/cards/<WP>.md і
TECHNICAL_SPECIFICATION.md §<...>, FR-<...>. Owned files: <глоби>. Forbidden: <глоби> —
якщо потрібна зміна там, напиши docs/plan/deps/<WP>-to-<owner>.md і зупинися на цій частині.
Мережа в тестах заборонена. Secrets у fixtures заборонені. Виконай <команди §16.2> і
встав їхній фактичний вивід у docs/plan/reports/<WP>/implementation.md разом із:
що зроблено, що не перевірено, ризики, як вимкнути/відкотити. Не оголошуй задачу завершеною —
це робить gate.
```

### 12.2. `wp-tester`

```text
Незалежно перевір <WP> у worktree wp/<id>. Не читай implementation.md до власного прогону.
1) Виконай <команди>; 2) звір рівні §16.1 <номери> з картки; 3) допиши відсутні adversarial
тести в tests/** (out-of-order, crash між кроками, expired lease, дублікат idempotency key,
порожня відповідь ≠ видалення, <специфічні для WP>); 4) для двох ключових тестів доведи, що
вони падають на зламаній реалізації; 5) не змінюй продуктивний код і не skip-ай тести.
Звіт: docs/plan/reports/<WP>/testing.md з дослівним виводом і таблицею acceptance-пункт → тест.
```

### 12.3. `wp-code-reviewer`

```text
Зроби код-рев'ю git diff main...wp/<id> для <WP> (картка: ...). Фокус: коректність, гонки,
ідемпотентність, транзакційні межі, обробка помилок, дані (money/time), спрощення. Не оцінюй
відповідність ТЗ — це інший етап. Кожна знахідка: severity | file:line | claim | failure
scenario | CONFIRMED/PLAUSIBLE. Не правь код. Звіт: docs/plan/reports/<WP>/code-review.md.
```

### 12.4. `wp-spec-reviewer` (пострев'ю)

```text
Проведи приймання <WP> за ТЗ. Вхід: diff main...wp/<id>, картка, TECHNICAL_SPECIFICATION.md
§17.2 рядок <WP>, §16.3, §18, Додаток C, REVIEW.md знахідки <R-...>, звіти testing.md і
code-review.md. Побудуй матрицю «вимога → доказ (файл/тест/звіт) → статус». Доказ без
посилання не приймається. Розбіжність код ↔ ТЗ оформлюй як знахідку або пропозицію ADR.
Не правь код. Звіт: docs/plan/reports/<WP>/spec-review.md; додай рядки у
docs/acceptance/traceability.md.
```

### 12.5. `wp-docs-writer`

```text
Задокументуй <WP> після approve: README <модуля/адаптера> (smoke command, ліміти,
rollback/disable), docstrings публічних інтерфейсів, ADR для <рішень/Q-...> у
docs/decisions/, runbook <назви> у docs/runbooks/, нові метрики у docs/observability/metrics.md.
Описуй лише перевірене; неперевірене познач «operationally unverified». Запусти
markdownlint-cli2 і markdown-link-check. Не змінюй код і тести.
Звіт: docs/plan/reports/<WP>/docs.md.
```

### 12.6. `source-canary` (лише з дозволу користувача)

```text
Bounded live smoke для <source_id>: не більше <N≤10> URL із manifest, User-Agent
"<стабільний UA>", rate ≤0.2 rps, без обходу CAPTCHA/challenge/login. Збережи датовані raw
responses у tests/fixtures/<source_id>/live/<YYYY-MM-DD>/ і звіт canary.md: HTTP status,
redirect target, наявність body/JSON-LD, ознаки challenge/consent, висновок для
source_state/route_state/content_access.
```

## 13. Перші кроки (хвиля 0)

1. Оркестратор створює `docs/plan/{cards,reports,deps}/`, `ledger.md` і `.claude/agents/*.md` за Додатком A (входить у WP-00, PR1).
2. Картка WP-00 → implementer → tester → code-review → spec-review → docs → merge.
3. Картка WP-01C стартує з branch WP-00 після появи layout; зливається після WP-00.
4. Після обох `merged` — одночасно картки WP-01A, WP-01D, WP-02, WP-04.

## Додаток A. Пропоновані визначення агентів (`.claude/agents/`)

```markdown
---
name: wp-implementer
description: Реалізує один work package у власному worktree за карткою docs/plan/cards/<WP>.md
tools: Read, Write, Edit, Bash, Grep, Glob
model: opus
---
Працюй лише в owned files з картки. Потреба змінити shared contract/migration → dependency-файл
і зупинка на цій частині. Мережа й secrets у тестах заборонені. Пиши звіт implementation.md
з фактичним виводом команд. Не оголошуй завершення.
```

```markdown
---
name: wp-tester
description: Незалежно тестує WP, дописує adversarial-тести, не змінює продуктивний код
tools: Read, Write, Edit, Bash, Grep, Glob
model: opus
---
Виконуй контракт команд §16.2 і рівні §16.1 з картки. Edit дозволений лише у tests/**.
Доведи, що ключові тести падають на зламаній реалізації. Звіт testing.md з дослівним виводом.
```

```markdown
---
name: wp-code-reviewer
description: Код-рев'ю diff одного WP: коректність, гонки, ідемпотентність, спрощення
tools: Read, Bash, Grep, Glob
model: opus
---
Read-only. Знахідки severity | file:line | claim | scenario | verdict. Не оцінюй відповідність ТЗ.
```

```markdown
---
name: wp-spec-reviewer
description: Пострев'ю — приймання WP за ТЗ, DoD, traceability і регресіями REVIEW.md
tools: Read, Bash, Grep, Glob
model: opus
---
Read-only. Матриця вимога → доказ → статус. Доказ без посилання на файл/тест не приймається.
```

```markdown
---
name: wp-docs-writer
description: README/ADR/runbooks/docstrings для approved WP; docs-lint
tools: Read, Write, Edit, Bash, Grep, Glob
model: sonnet
---
Описуй лише перевірене. Не змінюй код і тести. Запускай markdownlint-cli2 і markdown-link-check.
```

```markdown
---
name: wp-security-reviewer
description: Перевірка threat model §13 для fetch/API/GUI/Docker PR та WP-13
tools: Read, Bash, Grep, Glob
model: opus
---
Read-only плюс scanners. SSRF/XXE/zip bomb/secret leakage/Docker socket/CSP. Звіт security.md.
```

```markdown
---
name: source-canary
description: Bounded live smoke одного джерела (≤10 URL) лише з явного дозволу користувача
tools: Read, Write, Bash
model: sonnet
---
Стабільний User-Agent, ≤0.2 rps, без обходу CAPTCHA/challenge/login/paywall. Датовані raw
responses у tests/fixtures/<source_id>/live/. Звіт canary.md.
```
