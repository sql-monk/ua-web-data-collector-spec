# UA Web Data Collector — технічне завдання

Репозиторій містить самодостатнє ТЗ на дослідницьку платформу збору українських каталогів та автобазарів, а також новин із 19 країн.

- [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) — вимоги, конкретні сайти, архітектура, технології, зберігання оригіналів і українських перекладів, контракти та поділ робіт між незалежними агентами.
- [docs/research/ua-marketplaces.md](docs/research/ua-marketplaces.md) — live-паспорти 12 українських каталогів і автобазарів: URL, sitemap, схеми сторінок, поля, блокування та рейтинги.
- [docs/research/news-central-baltic.md](docs/research/news-central-baltic.md), [news-western.md](docs/research/news-western.md), [news-southern.md](docs/research/news-southern.md) — live-паспорти 58 новинних джерел у 19 країнах.
- [docs/research/source-registry.yaml](docs/research/source-registry.yaml) — канонічні `source_id`, display names, домени та рейтинги всіх 70 джерел.
- [docs/contracts.md](docs/contracts.md) — shared data contracts (WP-01C): identity/temporal/canonical serialization, `state_hash`, resolution/release, версіонування схем і ownership.
- [docs/persistence/postgres.md](docs/persistence/postgres.md) — PostgreSQL-схема control plane (WP-01A): таблиці, transaction boundaries, партиціонування, ролі БД.
- [REVIEW.md](REVIEW.md) — результати критичного рев’ю ТЗ і виправлення.
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — план реалізації субагентами: конвеєр work packages, картки, gate-и.
- [docs/plan/ledger.md](docs/plan/ledger.md) — стан кожного work package/під-PR у конвеєрі.

## Швидкий старт розробника

Стан на PR3 (`wp/00-3-web-scaffold`): Python-каркас і CLI-контракт §16.2 з PR1, Docker image
`collector` та Compose-стек з PR2, operator GUI (`web/`, image `collector-gui`, profile `gui`)
з PR3. Доменна логіка й екрани GUI належать наступним WP.

### Вимоги

- [`uv`](https://docs.astral.sh/uv/) 0.12+ — керує версією Python і залежностями;
  окремо встановлювати Python 3.13 не потрібно, `uv sync` підтягує його сам
  (`.python-version` фіксує `3.13`);
- Node.js 24 LTS — для `web/` (версія у `web/.nvmrc`); потрібен лише для локальної роботи
  з GUI, у Docker-збірці Node приходить з образу;
- Docker Engine 27+ з Compose plugin v2.30+ — потрібен для запуску стека (`Dockerfile`,
  `docker-compose.yml`, PR2 нижче, розділ «Запуск стека»); для `uv run` команд не потрібен.

### Встановлення

```bash
uv sync --frozen
uv run pre-commit install
```

`uv sync --frozen` ставить залежності точно за `uv.lock` (без перерахунку) у
`.venv`. `pre-commit install` вмикає локальні git-hooks
(`.pre-commit-config.yaml`): ruff, ruff-format, gitleaks, markdownlint-cli2 —
ті самі перевірки, що й CI (`.github/workflows/ci.yml`).

### Контракт перевірки (§16.2 ТЗ, частина PR1)

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not live"
uv run collector --help
uv run pre-commit run --all-files
```

Команди §16.2 `docker compose …` — розділ «Запуск стека» нижче. Решта (`uv run alembic
upgrade head`, `duckdb …`, `cd web && npm …`) з'являються в PR3 і в подальших WP, коли
відповідні стаби замінюються реальною реалізацією.

### CLI `collector`

```bash
uv run collector --help
```

У PR1 усі команди контракту §16.2, крім `collector version`, — типізовані
стаби: вони друкують `not implemented: owned by WP-XX` у stderr і завершуються
з кодом 2, доки власник WP не замінить тіло команди (назва та параметри
команди фіксовані карткою й тестами):

| Команда | Owner-WP |
|---|---|
| `collector version` | WP-00 (реальна — package version, Git SHA, `schema_version` = `collector.contracts.CONTRACTS_VERSION`) |
| `collector db migrate` | WP-01A |
| `collector db ensure-mongo --validators --indexes` | WP-01B |
| `collector worker <role>` (`discovery\|fetch\|browser\|parse\|projector\|translation\|export\|maintenance`) | WP-01D |
| `collector scheduler` | WP-01D |
| `collector controller` | WP-01D |
| `collector api` | WP-11A |
| `collector release build --watermark <w> --output <dir>` | WP-11A |
| `collector release verify --manifest <path>` | WP-11A |
| `collector e2e --source <name> --offline` | WP-14 |

Детальніше про вибір `typer`, логування та мережеву політику тестів —
[docs/decisions/0001-foundation-stack.md](docs/decisions/0001-foundation-stack.md).

## Operator GUI (`web/`)

```bash
cd web && npm ci && npm run lint && npm run test && npm run build && npm run test:e2e
```

Український каркас інтерфейсу (React 19 + TypeScript strict + Vite, TanStack Query + React
Router, Vitest/Testing Library, Playwright). Деталі, структура і заборона browser storage
(§13) — [web/README.md](web/README.md).

## Запуск стека (Docker Compose)

```bash
./deploy/compose/secrets/init-secrets.sh
docker compose --profile core --profile workers --profile gui up -d --wait   # §16.2
docker compose ps
curl -s http://localhost/   # GUI — єдиний публічний порт стека
```

`init-secrets.sh` генерує локальні файли секретів (поза git) для Postgres/Mongo/MinIO.
Докладна покрокова інструкція для чистого хоста (передумови, перевірка health, масштабування,
restart без втрати даних, типові проблеми) —
[docs/runbooks/clean-host-start.md](docs/runbooks/clean-host-start.md); відкат image за
tag/digest — [docs/runbooks/rollback-image.md](docs/runbooks/rollback-image.md). Опис
profiles, мереж, secrets і dev-override з портами на `127.0.0.1` —
[deploy/compose/README.md](deploy/compose/README.md). Рішення й прийняті відхилення (MinIO
root, права секретів 0644, unfixed CVE) —
[docs/decisions/0002-docker-compose-single-host.md](docs/decisions/0002-docker-compose-single-host.md).

## План і стан робіт

- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — конвеєр
  work packages (картка → реалізація → тестування → код-рев'ю → пострев'ю →
  документування → інтеграція).
- [docs/plan/ledger.md](docs/plan/ledger.md) — поточний стан кожного
  work package/під-PR.

Платформа призначена для внутрішнього дослідження і збирає всі публічно доступні поля, включно з контактами продавців. V1 звертається до джерел тільки без реєстрації, входу й source API keys. Перед масовим запуском треба підтвердити відкриті питання Q-001—Q-014, насамперед бюджет перекладу, media download, глибину backfill, production topology MongoDB, cadence releases, matching thresholds і deployment mode.

Сховище гібридне: PostgreSQL керує jobs, source state, lineage, новинами та перекладами; MongoDB зберігає поточні документи й історію каталогів/авто; S3/MinIO — незмінні raw і normalized artifacts. Узгодження PostgreSQL і MongoDB виконується через versioned idempotent projection outbox та reconciler, без синхронного dual-write.

ТЗ також визначає окремі source/system timestamps, оборотний merge/unmerge сутностей, verified history compaction, immutable dataset releases, щомісячний capacity/cost forecast і read-only DuckDB research kit поверх Parquet.

Усі application-компоненти запускаються в Docker. Worker roles масштабуються незалежно через Docker Compose, а в production Swarm mode — з українського operator GUI через audited desired-state controller. GUI також керує джерелами, jobs/replay, matching, releases, retention і capacity без прямого доступу до БД або Docker socket.
