# ADR-0004: Shared contracts ownership

| Поле | Значення |
|---|---|
| Date | 2026-09-22 |
| Owner | WP-01C |
| Status | accepted |

## Context

ТЗ §17 закріплює WP-01C як власника `src/collector/contracts/**` — «canonical UUID/source
identity, temporal axes, artifact, projection command/ack, resolution decision, domain event
і dataset release schemas» (§17.2). Картка `docs/plan/cards/WP-01C.md` додає до owned files
`schemas/{events,mongo,releases}/**` і закріплює формулювання «Owner: wp-implementer (єдиний
owner shared contracts до кінця проєкту — наступні зміни лише через dependency-запити)».
Механізм ТЗ (§17.1) не деталізує, як саме інший WP запитує зміну спільного контракту,
яка версія (`minor`/`major`) відповідає якій зміні, і що саме вважається *drift* між
JSON Schema snapshot і моделлю — ці рішення прийняті реалізацією і зафіксовані тут поза
буквою ТЗ (§20), одночасно з ADR-0003, який фіксує формат canonical bytes.

Під час реалізації виникли три відхилення від буквального переліку картки/ТЗ, оцінені
пострев'ю (`docs/plan/reports/WP-01C/spec-review.md`, розділ 7) як такі, що **не потребують**
зміни ТЗ, але варті явної фіксації в ADR, щоб наступні WP (WP-01A, WP-01B, WP-07, WP-09,
WP-11A) не повторювали дослідження того самого питання:

1. четверта група snapshot-ів `schemas/common/` — поза дослівним переліком картки
   `schemas/{events,mongo,releases}/**`;
2. `CurrentDocumentBase.schema_version` — `int` (major, YAML §9.2 буквально), а не
   `major.minor` рядок, як у решти контрактів;
3. семантика `decision_version`/`supersedes_decision_id` у `ResolutionDecision` (§9.8) —
   ТЗ не фіксує, чи `decision_version` — це нова версія *того самого* рішення, чи паралельне
   рішення, і чи ланцюжок supersession транзитивний.

## Decision

### Єдиний owner: `src/collector/contracts/**` і `schemas/**`

WP-01C — єдиний owner `src/collector/contracts/**`, усіх чотирьох груп `schemas/**`
(`common/`, `events/`, `mongo/`, `releases/`), `tests/contract/contracts/**`,
`tests/unit/contracts/**`, `tests/fixtures/contracts/**` і `docs/contracts.md` — до кінця
проєкту, без передачі частин пакета іншим WP навіть тоді, коли лише один споживач працює з
конкретною моделлю (наприклад, `ReleaseManifest` читає переважно WP-11A). Причина —
уникнути паралельних, дрейфуючих визначень тих самих понять (identity, часові осі, гроші,
осі стану) у різних WP, що й було прямою причиною регресій R-18/R-30/R-37/R-42/R-43/R-45/R-46
у REVIEW.md.

**Заборонено** (наслідок §5.5, зафіксовано `docs/contracts.md` §1, §5): інший WP не створює
власних enum «стану», паралельних моделей identity/temporal/money чи власного canonical
serialization — навіть якщо йому потрібне лише підмножина функціоналу. Єдиний шлях отримати
нове поле/enum-значення/модель — dependency-запит.

### Процедура dependency-запиту

Споживач, якому потрібне нове поле, enum-значення чи модель (типовий випадок — WP-07 vehicle
`core`, WP-09 catalog `core`, нові `EntityKind`), пише
`docs/plan/deps/<WP>-to-WP-01C.md` за форматом, уже використаним у
`docs/plan/deps/WP-01C-to-WP-00.md`: що саме (назва моделі/поля, тип, optional/required),
навіщо (розділ ТЗ), очікувана версія (`minor`/`major`) і хто споживач. WP-01C реалізує зміну
у власному branch за процедурою розділу 3 `docs/contracts.md`, оновлює snapshot-и, fixtures і
`docs/contracts.md`; споживач переходить на нову версію після merge. Запит переходить у стан
`resolved`, коли зміна злита; до того лишається `open`.

Той самий механізм застосовується і в зворотному напрямку — коли WP-01C потребує зміни поза
власними owned files (приклад: `docs/plan/deps/WP-01C-to-WP-00.md`, три пункти —
видимість CLI-групи `contracts`, `collector version` → `CONTRACTS_VERSION`, CI drift-крок —
усі `resolved` рішенням оркестратора).

### Minor/major versioning

Кожна модель (`ContractModel.contract_version`, `major.minor`) і, для документів/повідомлень,
поле `schema_version` (`VersionedDocument`) версіонуються незалежно за моделлю, не за пакетом
у цілому (`CONTRACTS_VERSION` — версія *набору*, для `collector version`/release manifest, не
для сумісності окремої моделі):

- **minor** — сумісне додавання: нове optional поле з default-ом, нове enum-значення (де
  споживач не читає enum як закритий список), послаблення обмеження. Приймається без зміни
  формату документа; snapshot оновлюється на місці (`<name>.v<major>.json`).
- **major** — breaking: видалення/перейменування поля, зміна типу, нове *required* поле,
  видалення enum-значення, посилення validator-а так, що існуючі дані можуть його не
  пройти. Новий snapshot `<name>.v<major+1>.json`; PR обов'язково несе PostgreSQL migration
  (WP-01A) або Mongo reprojection plan (WP-01B), JSON Schema diff, новий fixture,
  compatibility test і backward-compatible reader у споживачів (§9.4, §18).
- зміна алгоритму hash (identity/state hash, canonical serialization) — окрема категорія:
  нова версія алгоритму (`identity_hash_v2`, `v2:` prefix), стара функція лишається для
  перевірки вже записаних значень; golden fixtures старої версії не змінюються (ADR-0003).

Повна процедура з кроками — `docs/contracts.md`, розділи 3.1 (minor) і 3.2 (major).

### Snapshot drift check у CI

`uv run collector contracts export --check` (CLI-команда, owned by WP-01C, розширення
`src/collector/cli.py` за approved dependency-запитом) порівнює згенерований з поточних
Pydantic-моделей текст (sorted keys, indent 2, LF, відсортований `required`) з файлами у
`schemas/**`; будь-яка розбіжність — **включно зі зміною docstring**, бо він потрапляє в
JSON Schema `description`, — є drift. CI job `python` (`.github/workflows/ci.yml`) виконує
цю команду після `pytest`; той самий drift ловить
`tests/contract/contracts/test_schema_snapshots.py`. Наслідок: правка docstring публічної
моделі без регенерації snapshot-а ламає CI — дозволена процедура лише
«відредагувати docstring → `uv run collector contracts export` → закомітити разом».

### Чотири групи `schemas/`, включно з `schemas/common/`

Дослівний перелік картки — `schemas/{events,mongo,releases}/**`; фактична структура додає
четверту групу `schemas/common/` для value objects і shared блоків (identity, temporal,
money/contacts, artifact refs, upload claim, lineage), що не належать жодній із трьох груп,
але потребують власного snapshot-а за acceptance-вимогою «snapshot для кожної публічної
моделі». Пострев'ю (`spec-review.md`, знахідка 2, low) підтвердило: Додаток A ТЗ описує
«мінімальну структуру», четверта група їй не суперечить, альтернатива (перенести `common/*`
у `events/`) семантично гірша. Рішення: `schemas/common/**` лишається owned files WP-01C
нарівні з іншими трьома групами; `schemas/README.md` документує всі чотири.

### `schema_version` у `CurrentDocumentBase` — `int`, а не `major.minor`

Виняток із загального правила (вище): `CurrentDocumentBase.schema_version` — `int` (major,
`Field(default=1, ge=1, strict=True)`), а не рядок `major.minor`, як у решти
`VersionedDocument`. Причина — YAML §9.2 фіксує поле буквально як `schema_version: 1`
(ціле число), і Mongo `$jsonSchema` validators (WP-01B) генеруються прямо з цього snapshot-а
(`schemas/mongo/current_document_base.v1.json`, `"type": "integer"`) — рядковий
`major.minor` зламав би цю пряму відповідність без користі (validator однаково перевіряє
лише major на межі документа). Minor-версію документа несе `contract_version` класу
(`ContractModel`, не `VersionedDocument` — `CurrentDocumentBase` успадковує безпосередньо
`ContractModel`) і `x-contract-version` у JSON Schema snapshot; validator моделі вимагає
рівності `schema_version` (int) з major частиною `contract_version`. Приклад:
`SourceTime.contract_version` піднявся `1.0 → 1.1` при додаванні `source_locale_raw`
(minor, §9.6) — `CurrentDocumentBase.schema_version` як окреме поле цієї зміни не бачить,
бо `SourceTime` — вкладена модель, а не сам документ.

### Семантика `decision_version` і `supersedes_decision_id` (стисло; повний текст — `docs/contracts.md` §10)

Дві дефіцитні в ТЗ семантики, зафіксовані реалізацією й підтверджені код-рев'ю (CR-09):

- **`decision_version`** — версія *того самого* рішення (`decision_id`): у `project_groups`
  replay бере участь лише рішення з найвищим `decision_version` для кожного `decision_id`;
  старі версії того самого `decision_id` не replay-яться (виправлення рішення = нова версія
  з тим самим `decision_id`, а не окреме рішення).
- **`supersedes_decision_id`** — скасування *іншого* рішення, окреме рішення з власним
  `decision_id`. Supersession діє лише від **ефективного** (не superseded) рішення і
  обчислюється як fixed point: у ланцюжку A ← B ← C (де C supersede B, B supersede A) B
  більше не діє (бо сам superseded), тому блок A **відновлюється**; додавання D, що supersede
  C (A ← B ← C ← D), знову знімає A. Цикл supersession — `ValueError`; посилання на
  неіснуючий `decision_id` (dangling) допускається і потрапляє в `superseded_decision_ids`
  снапшота без помилки.

## Consequences

- Наступні WP (WP-01A, WP-01B, WP-01D, WP-02, WP-04, WP-07, WP-09, WP-11A), яким потрібне
  нове поле чи модель, завжди йдуть через `docs/plan/deps/<WP>-to-WP-01C.md`, а не редагують
  `src/collector/contracts/**`/`schemas/**` напряму — навіть тривіальна зміна (нове optional
  поле) залишається виключно в branch WP-01C.
- Будь-яка редакція docstring публічної моделі вимагає regenerate + commit snapshot-а — це
  свідомий компроміс (description — частина контракту для споживачів схем), а не
  недогляд CI.
- `schemas/common/` лишається постійною четвертою групою; майбутні gate-и WP-01C не повинні
  вимагати трьох груп буквально за карткою — Додаток A і acceptance («snapshot для кожної
  публічної моделі») мають пріоритет.
- `CurrentDocumentBase.schema_version` (int) і `contract_version`/snapshot `x-contract-version`
  (`major.minor`) — два різні числа з різним призначенням; WP-01B, генеруючи Mongo
  `$jsonSchema` validators, і будь-який consumer, що логує/порівнює версію документа, мають
  розрізняти їх, а не припускати, що `schema_version` документа завжди дорівнює
  `CONTRACTS_VERSION`.
- Reprojection/replay-логіка resolution (WP-07/09/11A) спирається на факт, що
  `project_groups` не replay-ить старі `decision_version` того самого рішення і що
  supersession не транзитивна «на прохід» — зміна цієї семантики без нового ADR і
  compatibility-аналізу зламає вже записані `ResolutionSnapshot`.

## Related

- Картка: `docs/plan/cards/WP-01C.md` (Owner, Owned files, вимога 10).
- Документація: `docs/contracts.md` (розділи 1, 2, 3), `schemas/README.md`.
- Dependency-запити: `docs/plan/deps/WP-01C-to-WP-00.md` (resolved).
- Звіти: `docs/plan/reports/WP-01C/code-review.md` (CR-09), `docs/plan/reports/WP-01C/spec-review.md`
  (розділ 7 — оцінка відхилень), `docs/plan/reports/WP-01C/implementation.md`.
- Пов'язаний ADR: `docs/decisions/0003-canonical-event-serialization.md` (формат bytes і
  причина, чому вони не перераховуються повторно).
