# WP-01C — звіт документування (`wp/01c-contracts`)

| Поле | Значення |
|---|---|
| WP | WP-01C «Shared data contracts» |
| Branch / worktree | `wp/01c-contracts` / `.worktrees/wp-01c` |
| Вхід | `docs/plan/cards/WP-01C.md` (розділ «Docs»), звіти `implementation.md`/`testing.md`/`code-review.md`/`spec-review.md` (вердикт `accept`), `git diff main...HEAD` (HEAD `6957946`) |
| Етап | 5 (документування) конвеєра `docs/IMPLEMENTATION_PLAN.md`; код і тести не змінювались |

## Що зроблено

### 1. `docs/decisions/0003-canonical-event-serialization.md` — новий ADR

Формат canonical bytes (`encode_event`): sorted-keys compact JSON, UTF-8 без escape
не-ASCII, NFC для рядків/ключів, формат `datetime`/`UUID`/`Decimal`, SHA-256, ліміт inline
256 KiB → `event_artifact`; чому bytes зберігаються в Mongo receipt і копіюються
reconciler-ом без повторної серіалізації (R-37/R-42); strict `JsonValue` для
`payload`/блоків current document (CR-01, з наслідком для WP-07/WP-09); заборона
NFC/casefold-колізій ключів (CR-03/CR-04). Context/Decision/Consequences/Date
2026-09-22/Owner WP-01C/Status accepted — формат за зразком `docs/decisions/0001-foundation-stack.md`.
Джерело фактів — код (`canonical.py`, `events.py`, `_base.py`, `current.py`, `identity.py`),
`docs/contracts.md` і звіт код-рев'ю (CR-01, CR-03, CR-04, CR-06, CR-09).

### 2. `docs/decisions/0004-shared-contracts-ownership.md` — новий ADR

WP-01C — єдиний owner `src/collector/contracts/**` і всіх чотирьох груп `schemas/**` до
кінця проєкту; процедура dependency-запиту (`docs/plan/deps/<WP>-to-WP-01C.md`, приклад —
вже існуючий `docs/plan/deps/WP-01C-to-WP-00.md`); minor/major versioning (§9.4); snapshot
drift-check у CI (`collector contracts export --check`, docstring теж є drift); четверта
група `schemas/common/` поза буквальним переліком картки — обґрунтування зі spec-review
(знахідка 2, low); `CurrentDocumentBase.schema_version: int` (YAML §9.2) проти
`CONTRACTS_VERSION`/`contract_version` (`major.minor`) — два різні числа з різним
призначенням; стисла семантика `decision_version`/`supersedes_decision_id` (CR-09) з
посиланням на повний текст `docs/contracts.md` §10. Той самий формат заголовка, що й
ADR-0001/0003.

### 3. `docs/contracts.md` — перевірено, прогалин не знайдено

Звірено проти вимог задачі й картки: алгоритм `identity_hash_v1` (§4.3, з golden-прикладом),
часова модель (§8, включно з `source_locale_raw`, доданим у fix-коміті `6957946` після
пострев'ю), процедура змін minor/major (§3.1/§3.2) і drift (§3.3), ownership і
dependency-процедура (§1), заборона паралельних enum/моделей (§1, §5). Документ (259 рядків)
уже повний — правок не вносилось.

### 4. `schemas/README.md` — перевірено, прогалин не знайдено

Описує всі чотири групи (`common/events/mongo/releases`) з переліком вмісту й споживачів,
команди `uv run collector contracts export [--check]`, правило іменування
`<name>.v<major>.json` і те, що major/minor визначає файл. Правок не вносилось.

### 5. Docstrings публічних моделей/функцій `src/collector/contracts/**`

Перевірено всі 15 модулів пакета (`_base`, `artifacts`, `canonical`, `current`, `enums`,
`events`, `identity`, `projection`, `release`, `resolution`, `schema_export`,
`source_registry`, `temporal`, `values`, `__init__`) — кожен публічний клас, enum-значення
через docstring класу, кожна публічна функція вже має docstring (реалізатор писав їх з
самого початку, бо вони потрапляють у JSON Schema `description` і drift-check це форсує).
**Прогалин не знайдено** — жодного docstring не додано і не змінено; код і тести не
торкались. Оскільки жодна docstring-зміна не вносилась, drift не виник — `collector
contracts export --check`/`ruff`/`mypy` перезапускати з цієї причини не було потреби.

### 6. `README.md` кореня

Додано один рядок-посилання у список документів (після `docs/research/source-registry.yaml`,
перед `REVIEW.md`):

```markdown
- [docs/contracts.md](docs/contracts.md) — shared data contracts (WP-01C): identity/temporal/canonical serialization, `state_hash`, resolution/release, версіонування схем і ownership.
```

### 7. Цей звіт

`docs/plan/reports/WP-01C/docs.md`.

## Lint

```text
$ npx --yes markdownlint-cli2 README.md docs/decisions/0003-canonical-event-serialization.md docs/decisions/0004-shared-contracts-ownership.md docs/contracts.md schemas/README.md
markdownlint-cli2 v0.23.3 (markdownlint v0.41.1)
Finding: README.md docs/decisions/0003-canonical-event-serialization.md docs/decisions/0004-shared-contracts-ownership.md docs/contracts.md schemas/README.md
Linting: 5 files
Summary: 0 issues in 0 files
```

`markdown-link-check` не запускався: нові ADR і правка `README.md` не додають нових
зовнішніх (http/https) посилань — лише внутрішні repo-посилання (`docs/contracts.md`,
`docs/decisions/0001-foundation-stack.md`, `docs/plan/deps/WP-01C-to-WP-00.md`, звіти),
які markdownlint-cli2 (правило MD011/MD042 не спрацювало) і сам факт існування файлів у
цьому ж коміті вже підтверджують.

## Що не змінювалось

Код (`src/collector/contracts/**`), тести, `pyproject.toml`, CI — без змін; лише
документація. `uv run ruff check .` / `ruff format --check .` / `mypy src` / `collector
contracts export --check` не перезапускались, бо жодна модель чи docstring не редагувались
(вимога інструкції — перезапускати лише після docstring-змін).

## Файли

- Нові: `docs/decisions/0003-canonical-event-serialization.md`,
  `docs/decisions/0004-shared-contracts-ownership.md`,
  `docs/plan/reports/WP-01C/docs.md`.
- Змінені: `README.md` (один рядок).
- Перевірені без змін: `docs/contracts.md`, `schemas/README.md`,
  `src/collector/contracts/**` (15 модулів, докстрінги).
