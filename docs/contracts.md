# Shared data contracts (WP-01C)

Єдине джерело versioned контрактів даних — пакет `collector.contracts`
(`src/collector/contracts/**`) і згенеровані з нього JSON Schema snapshots у `schemas/**`.
Контракти — чисті Pydantic v2 моделі без I/O; персистенція (SQLAlchemy/PyMongo mapping,
міграції, Mongo validators) живе у WP-01A/WP-01B і **генерується з цих схем**, а не навпаки.

| Поле | Значення |
|---|---|
| Owner | WP-01C (єдиний owner до кінця проєкту) |
| Версія набору | `collector.contracts.CONTRACTS_VERSION` = `1.0` |
| ТЗ | §5.1, §5.4, §5.5, §7.3, §9.2, §9.3, §9.4, §9.6, §9.8, §9.9, §10 п.5–10 |
| Регенерація snapshot-ів | `uv run collector contracts export`; перевірка drift — `uv run collector contracts export --check` |

## 1. Ownership і процедура dependency-запиту

- Будь-яка зміна у `src/collector/contracts/**`, `schemas/**`, `tests/contract/contracts/**`,
  `tests/unit/contracts/**`, `tests/fixtures/contracts/**` робиться **лише** WP-01C.
- Інший WP, якому потрібне нове поле, enum-значення чи модель (наприклад, WP-07 — vehicle
  `core`, WP-09 — catalog `core`, нові `EntityKind`), пише
  `docs/plan/deps/<WP>-to-WP-01C.md`: що саме (назва моделі/поля, тип, optional чи required),
  навіщо (посилання на розділ ТЗ), очікувана версія (`minor` чи `major`) і хто споживач.
- WP-01C реалізує зміну у власному branch за процедурою розділу 3, оновлює snapshot-и,
  fixtures і `docs/contracts.md`; споживач переходить на нову версію після merge.
- **Заборонено** створювати в інших WP власні enum «стану» або паралельні моделі
  identity/temporal/money: єдині осі стану — `collector.contracts.enums` (§5.5, R-18).

## 2. Склад пакета

| Модуль | Контракти | ТЗ |
|---|---|---|
| `_base` | `ContractModel` (frozen, `extra="forbid"`, base64 bytes), `VersionedDocument` (поле `schema_version`), `SchemaVersion`, `JsonObject` | §9.4 |
| `canonical` | `canonical_json_bytes`, `canonical_sha256`, `format_utc_datetime`, `format_decimal` | §7.3, R-37/R-42 |
| `enums` | `SourceState`, `RouteState`, `EntityLifecycle`, `ContentAccess`, `FetchOutcome` (+ `map_research_access_state`), `TimePrecision`, `EffectiveAtBasis`, `DataDomain`, `EntityKind`, `ContactKind`, `UploadClaimStatus`, `ObservationReason`, `ResolutionAction`, `ReleaseState` | §5.5, §9.2, §9.6, §9.8, §9.9 |
| `identity` | `EntityId` (UUIDv7) + `new_entity_id`/`Uuid7Generator`, `SourceIdentity`, `NormalizedUrl`, `identity_hash_v1`, `fetch_idempotency_key`, `planned_at_bucket`, `translation_idempotency_key`, `raw_object_key` | §5.1, §9.3 |
| `source_registry` | read-only loader `docs/research/source-registry.yaml` (`load_source_registry`, `known_source_ids`) | §9.3 п.1 |
| `temporal` | `UtcDatetime`, `SourceTime`, `SystemTime`, `EntityTime`, `EffectiveTime` + `derive_effective_time`, `BitemporalInterval`, `VersionTimes`/`VersionInterval` + `build_intervals` | §5.1, §9.6, R-43/R-49 |
| `values` | `Money`, `ContactValue` (+ `normalize_phone`/`normalize_email`/`normalize_contact`), `MeasuredValue` | §5.1, §9.3 п.8, §12.2 |
| `artifacts` | `ArtifactRef`, `RawArtifactRef`, `NormalizedArtifactRef`, `UploadClaim` + `can_commit` | §7.3, §9.1, §10 п.5/7 |
| `projection` | `ProjectionCommand`, `AppliedProjectionReceipt`, `ProjectionAcknowledgement`, `should_emit_domain_changed` | §7.3 кроки 2–4, §9.1, §9.2 |
| `events` | `DomainChangedEvent`, `EncodedEvent`, `encode_event`/`decode_event`, `EVENT_INLINE_LIMIT_BYTES` | §7.3 п.4, R-30/R-37/R-42 |
| `current` | `CurrentDocumentBase` (`schema_version: int` major за §9.2), `SourceRef`, `Lineage`, `compute_state_hash_v1` | §9.2, §9.4 |
| `resolution` | `ResolutionDecision`, `ResolutionSnapshot`, `project_groups` | §9.8, R-45 |
| `release` | `ReleaseManifest`, `ReleasePart`, `ReleaseWatermark`, `EntityVersionRef`, `SourceInclusion`, `ComponentVersions`, `RELEASE_TRANSITIONS`, `can_transition`, `transition_release`, `validate_manifest_update` | §9.9, R-46 |
| `schema_export` | `EXPORTED_CONTRACTS`, `export_schemas`, `check_schemas`, `check_compatibility` | §9.4 |

Усі публічні моделі експортуються у `schemas/<group>/<name>.v<major>.json`
(групи `common`, `events`, `mongo`, `releases`; опис у `schemas/README.md`).

## 3. Версіонування та процедура змін (§9.4)

Кожна модель має `contract_version` класу (`major.minor`); документи й повідомлення
(`VersionedDocument`) додатково несуть поле `schema_version` з тим самим значенням за
замовчуванням. Виняток — `CurrentDocumentBase`: за YAML §9.2 його `schema_version` — **int major**
(`schema_version: 1`), minor несуть `contract_version` класу і snapshot `x-contract-version`;
validator вимагає рівності major. Модель приймає документ, якщо його `major` збігається, а `minor` не більший за
minor моделі. Зміна валідаторів без зміни shape (жорсткіша перевірка наявних полів) — теж
breaking для писачів; трактуйте її як major, якщо існуючі дані можуть її не пройти.

### 3.1. Додати optional поле — minor

1. Додайте поле з default (`None`, `[]`, `{}`) у модель; підніміть `contract_version` і default
   `schema_version` на `major.(minor+1)` — обидва мають збігатися (перевіряється при визначенні
   класу).
2. `uv run collector contracts export` — snapshot `<name>.v<major>.json` оновлюється на місці
   (major не змінився).
3. Додайте fixture попередньої minor-версії, якщо його ще немає:
   `tests/fixtures/contracts/documents/<name>.v<major>.<old_minor>.json` — тест
   `tests/contract/contracts/test_compatibility.py` перевіряє, що старий документ валідується
   новою моделлю. Наявні fixtures старих minor **не змінюються**.
4. `check_compatibility(old_snapshot, new_snapshot)` має повернути `[]` (тест
   `test_repository_snapshot_is_compatible_with_current_model`); переконайтесь, що
   `uv run collector contracts export --check` і `uv run pytest -m "not live"` зелені.

### 3.2. Breaking change — major

Видалення/перейменування поля, зміна типу, нове required поле, видалення enum-значення:

1. Підніміть `contract_version`/`schema_version` на `(major+1).0`. Snapshot з'явиться як новий
   файл `<name>.v<major+1>.json`; старий `<name>.v<major>.json` **лишається в репозиторії**
   поки існують дані/споживачі попередньої версії (для `check_schemas` він буде `stale` —
   додайте старий файл у `EXPORTED_CONTRACTS` як legacy-запис із замороженим класом або
   видаліть після завершення міграції; рішення фіксується у PR).
2. PR обов'язково містить (§9.4, §18): PostgreSQL migration (WP-01A) або Mongo
   reprojection/migration plan (WP-01B), JSON Schema diff (`git diff schemas/`), новий fixture
   `<name>.v<major+1>.0.json`, compatibility test і backward-compatible reader у споживачів.
3. Якщо змінюється алгоритм hash (identity/state hash, canonical serialization) — це нова
   версія алгоритму (`identity_hash_v2`, `v2:` prefix), стара функція лишається для перевірки
   існуючих значень; golden fixtures старої версії не змінюються.

### 3.3. Що вважається drift

`uv run collector contracts export --check` порівнює згенерований текст (sorted keys, indent 2,
LF, відсортований `required`) з файлом у `schemas/`. Будь-яка розбіжність — включно зі зміною
docstring (він потрапляє у `description`) — це drift: перегенеруйте snapshot і закомітьте.
CI-тест `tests/contract/contracts/test_schema_snapshots.py` робить те саме.

## 4. Ідентичність (§9.3)

### 4.1. `EntityId` — UUIDv7

RFC 9562 layout: 48 біт unix-time (мс) | version 7 | 12 біт лічильник (`rand_a`) | variant |
62 біти random. `Uuid7Generator` монотонний у межах процесу: у тій самій мілісекунді лічильник
інкрементується; при переповненні або кроці годинника назад timestamp зсувається на +1 мс.
Порівняння як `int`/рядок дає порядок створення. Python 3.13 не має `uuid.uuid7`, тому
генератор власний, без зовнішніх залежностей.

### 4.2. `SourceIdentity`

`(source_id, source_item_id)` — первинний природний ключ. `source_id` має синтаксис
`<kind>_<cc>_<slug>` (`news|vehicle|catalog`, ISO-3166 alpha-2 у lowercase) і **має існувати** у
`docs/research/source-registry.yaml` (70 записів). Loader read-only, кешований; шлях — env
`COLLECTOR_SOURCE_REGISTRY` або пошук `docs/research/source-registry.yaml` угору від пакета/cwd.
У Docker image `docs/research/source-registry.yaml` має бути скопійований або env заданий
(див. dependency-запит до WP-00 PR2 у звіті).

### 4.3. Алгоритм `identity_hash_v1`

Використовується, коли джерело не дає стабільного `source_item_id` (§9.3 п.2).

```text
input: canonical_url: str, stable_attributes: Mapping[str, Any]
1. url  := NFC(canonical_url).strip()                     # case не змінюється
2. attrs := {}
   for key, value in stable_attributes:
       if value is None: skip
       v := casefold(collapse_ws(NFC(str(value))))       # collapse_ws: split() + " ".join
       if v == "": skip
       attrs[casefold(collapse_ws(NFC(key)))] := v
3. payload := {"attributes": attrs, "url": url, "v": 1}
4. bytes   := canonical_json_bytes(payload)              # розділ 6
5. result  := "v1:" + hex(sha256(bytes))
```

Стабільні атрибути обирає адаптер джерела (WP-06/08/10) і **документує в manifest джерела**;
типово: `brand`, `mpn`/`gtin` для каталогів; `vin`, `make`, `model`, `year` для авто. Атрибути,
що змінюються з часом (ціна, статус, пробіг), у hash не входять. Golden-приклади з точним
`canonical_json` — `tests/fixtures/contracts/identity_golden.json`.

### 4.4. Ключі ідемпотентності

| Ключ | Формула | Функція |
|---|---|---|
| fetch | `sha256(canonical_json({source_id, normalized_url, planned_at_bucket, request_variant}))`; `planned_at_bucket` — aware UTC, формат `YYYY-MM-DDTHH:MM:SS.ffffffZ`; `planned_at_bucket(planned_at, bucket)` округлює вниз | `fetch_idempotency_key` |
| translation | `sha256(canonical_json({article_version_id, target_language(lower), provider, model_version, glossary_version}))` | `translation_idempotency_key` |
| raw object | `hex(sha256(body))` | `raw_object_key` |

`normalized_url` — вхід (нормалізацію робить WP-02); контракт `NormalizedUrl(original, normalized)`
фіксує лише, що в `normalized` немає tracking-параметрів (`utm_*`, `fbclid`, `gclid`, `yclid`,
`msclkid`, `mc_cid`, `mc_eid`, `_ga`, `_gl`, `igshid`, `dclid`), а `original` зберігається.

## 5. Осі стану (§5.5, R-18)

П'ять закритих `StrEnum` з точними значеннями ТЗ: `SourceState`, `RouteState`,
`EntityLifecycle`, `ContentAccess`, `FetchOutcome`. `map_research_access_state()` переводить
research-позначення: `free→full`, `body_unavailable→metadata_only`,
`retryable→FetchOutcome.retryable`, решта — однойменні `ContentAccess`; невідома позначка —
`ValueError`. Інші WP не додають власних enum стану; нове значення — dependency-запит (major,
якщо споживачі читають enum як закритий).

## 6. Canonical serialization

`canonical_json_bytes(value)` — детерміновані UTF-8 bytes для hash (`identity_hash`,
`state_hash`, idempotency keys) і для `domain.changed` event bytes (R-37/R-42):

- об'єкти: ключі відсортовані за code point, `separators=(",", ":")`, `ensure_ascii=False`;
- рядки та ключі: Unicode NFC;
- `datetime`: лише aware UTC → `YYYY-MM-DDTHH:MM:SS.ffffffZ` (завжди 6 цифр); naive або offset ≠ 0 —
  `CanonicalEncodingError`; `date` → ISO `YYYY-MM-DD`;
- `UUID` → lowercase з дефісами; `Enum` → `.value`; `bool`/`int`/`null` як у JSON;
- `Decimal` → `format(normalize(), "f")` без експоненти (`1.50`→`"1.5"`, `1E+2`→`"100"`, `-0`→`"0"`);
- `float` — лише скінченні (`NaN`/`inf` відхиляються); гроші float не використовують (§5.1);
- `bytes` → base64 (standard, з padding); `set`/`frozenset` → відсортований список;
- Pydantic-моделі → `model_dump(mode="python", by_alias=True)` і далі рекурсивно.

`encode_event(event) -> EncodedEvent(event_id, event_bytes, event_media_type, event_sha256)`:
media type `application/vnd.ua-collector.domain-changed.v1+json`; якщо bytes > 256 KiB —
`EventTooLargeError`, викликач переносить payload в immutable artifact (`payload_artifact`) і
кодує подію без inline payload. Receipt зберігає готові bytes; reconciler/outbox копіюють їх без
повторної серіалізації.

## 7. `state_hash` v1 (§9.2, §9.4)

`compute_state_hash_v1(core, attributes, latest_state)` =
`"v1:" + sha256(canonical_json({"v": 1, "core": core, "attributes": attributes, "latest_state": latest_state}))`.
Детермінований незалежно від порядку полів, unicode-форми і локалі; три блоки хешуються як
окремі ключі (переміщення поля між блоками змінює hash). `CurrentDocumentBase` відхиляє
документ, у якому `state_hash` не збігається з обчисленим. Lineage/time/`projection_version`
у hash не входять — heartbeat без зміни state дає той самий hash.

## 8. Часова модель (§9.6)

- `UtcDatetime` відхиляє naive datetime і будь-який offset, крім нуля (не нормалізує —
  помилка джерела має бути видимою).
- `SourceTime.source_event_at/source_updated_at` лишаються `None`, якщо джерело не дало часу;
  `EntityTime` (блок `time` current document) додатково відхиляє значення, що дорівнюють
  `fetched_at` (регресія R-43). `derive_effective_time` дає `effective_at` з basis
  `source_event → source_updated → observed` і `source_time_inferred=True` для будь-якого
  basis, крім `source_event`; `fetched_at` як basis не використовується.
- `build_intervals(versions)` рахує обидві напіввідкриті осі незалежно: `valid_to` — наступний
  строго більший `effective_at` (версії з однаковим `effective_at` — backdated correction —
  ділять valid-інтервал), `known_to` — наступний більший `ingested_at`. Late arrival
  вставляється у valid-історію, не переписуючи known-вісь.

## 9. Projection / receipt / event (§7.3)

`ProjectionCommand` — внутрішня команда, не публікується (R-30). `AppliedProjectionReceipt`
несе event descriptor (inline bytes + media type + sha256, або `event_artifact`) **тоді й лише
тоді**, коли `should_emit_domain_changed(receipt) = applied_to_current and state_changed`;
inline bytes ≤ 256 KiB і `event_sha256 == sha256(event_bytes)`. `ProjectionAcknowledgement`
будується з receipt (`from_receipt`) без повторної серіалізації.

## 10. Resolution (§9.8) і release (§9.9)

- `project_groups(decisions)` — детермінований replay у порядку
  `(effective_at, recorded_at, decision_version, decision_id)`; рішення, на яке хтось посилається
  через `supersedes_decision_id`, пропускається разом із ефектом. `manual_block` блокує наступні
  auto `merge` для будь-якої пари members (доки блок не superseded); `manual_link` блоку не
  підлягає; `unmerge` виводить members із груп; `reject` груп не змінює.
- `ReleaseManifest`: переходи `draft→building→validating→published→superseded`, з будь-якого
  стану до `published` можливий `failed`; `failed`/`superseded` — термінальні. `published`
  immutable: `validate_manifest_update` дозволяє змінити лише `state` і `superseding_release_id`;
  `superseded` (колишній published) — жодне поле, крім ще не заданого `superseding_release_id`.
  `transition_release(..., **changes)` відхиляє `state`/`release_id` у `changes`.
  `published/superseded` вимагають `published_at`, непорожні `parts`, `quality_report`,
  `reconciliation_result`.

## 11. Тести

- `tests/unit/contracts/**` — рівні 1 (identity hash, money, E.164/e-mail) і 9 (temporal),
  replay resolution, release state machine, CLI export;
- `tests/contract/contracts/**` — рівень 2: drift snapshot-ів, compatibility fixtures,
  `check_compatibility`, відсутність I/O-імпортів у `collector.contracts`;
- `tests/fixtures/contracts/` — golden identity/keys (`identity_golden.json`), документи
  `documents/<name>.v<major>.<minor>.json`, `factories.py` (спільні фабрики тестів).
