# WP-01C PR2 — implementation report (`wp/01c-2-payload-news-contracts`)

Картка: `docs/plan/cards/WP-01C.md`, розділ «PR2». Worktree `.worktrees/wp-01c-2`, base `626b7e4`.
Статус — на gate (етап 2+); цей звіт не є прийманням.

## Що зроблено

Коміти: `9d9f1de` feat (контракти + snapshots + fixtures), `6072363` test, `a7cc728` docs
(+ коміт цього звіту). Продуктивний код: ~660 доданих рядків у `src/collector/contracts/**`.

### Нові контракти (усі `contract_version`/`schema_version` = `1.0`)

| Вимога PR2 | Модель / функція | Модуль | Snapshot |
|---|---|---|---|
| п.1 | `NormalizedProjectionPayload`, `ObservedValues`, `check_payload_matches_artifact`, `PayloadArtifactMismatchError`, `ENTITY_KIND_DOMAINS` | `payload.py` | `events/normalized_projection_payload.v1.json`, `common/observed_values.v1.json` |
| п.2 | `EntityProjectionVersion`, `VersionSnapshot` | `records.py` | `mongo/entity_projection_version.v1.json`, `common/version_snapshot.v1.json` |
| п.3 | `ObservationRecord`, `SellerContactObservation`, `ReviewQuestionRecord` (+ enum `ReviewQuestionKind`) | `records.py`, `enums.py` | `mongo/{observation_record,seller_contact_observation,review_question_record}.v1.json` |
| п.4 | `TranslationStatus` (рівно 4), `TranslationQualityFlag` (3 обов'язкові + `language_unsupported`), `NewsTranslation`, `LanguageCode` | `enums.py`, `news.py` | `common/news_translation.v1.json` |
| п.5 | `NewsVersionCreatedEvent`; `encode_event` приймає його (`PublishableEvent`), `decode_news_version_created` | `news.py`, `events.py` | `events/news_version_created.v1.json` |
| п.6 | TM key — **не** додано (рішення О-2); задокументовано в `docs/contracts.md` §11.3 | — | — |
| bounded (п.1–3) | `BoundedJsonObject` (`MAX_JSON_DEPTH=8`, `MAX_JSON_ARRAY_ITEMS=256`, `MAX_JSON_OBJECT_KEYS=512`) | `_base.py` | (межі не входять у JSON Schema) |

Snapshot-ів: 32 → 41. PR1-моделі й наявні snapshots не змінені (drift-check зелений, compat-тест
PR1-snapshot-ів зелений). Fixtures v1.0 для всіх 7 нових `VersionedDocument` у
`tests/fixtures/contracts/documents/`; фабрики — `tests/fixtures/contracts/factories.py`.

### Рішення реалізатора (поза буквою картки) — на перевірку spec-reviewer

1. **Snapshot payload у `schemas/events/`**, не `mongo/`: payload — повідомлення parser → projector
   у bytes artifact, а не документ collection; WP-01B генерує validators із `schemas/mongo/*.json`,
   і payload там дав би хибний validator. Задокументовано: `docs/contracts.md` §11.1, `schemas/README.md`.
2. **`NewsTranslation` у `schemas/common/`**: це рядок PostgreSQL (`news_translations`) і
   результат WP-04, не Mongo-документ і не подія. Група `common` належить WP-01C (ADR-0004).
3. **`payload.source` — `SourceRef`** (identity + `canonical_url`), а не голий `SourceIdentity`:
   current document §9.2 вимагає `source.canonical_url`, і projector бере його з payload.
4. **Час payload — два поля `source_time: SourceTime` + `system_time: SystemTime`** (картка:
   «time: SourceTime + SystemTime»); `entity_time()` збирає `EntityTime` і вже на валідації
   відхиляє `source_*_at == fetched_at` (R-43).
5. **У observation-полях payload немає окремого `observed_at`**: час observation =
   `system_time.observed_at` (одне джерело правди, щоб значення не розійшлися).
6. **`_id: UUID` (alias) у всіх чотирьох нових Mongo records**: §9.2 прямо вимагає UUID `_id`
   для observations і reviews; для version/contact records узято так само, бо інакше Mongo
   ставить ObjectId, а `extra="forbid"` не прочитає документ назад. `AppliedProjectionReceipt`
   (PR1) `_id` не має — не змінював (див. «Ризики»).
7. **`NewsVersionCreatedEvent` має поля, яких нема в переліку картки:** `event_id`,
   `event_type` (const), `version_number` — без них не заповнити обов'язкові колонки
   `outbox_events` (`event_id`, `event_type`, `aggregate_version`, міграція `0004`).
   Body-правило: обов'язковий для `full`, заборонений для `metadata_only|blocked|challenge|gone`.
8. **`NewsTranslation` також має `article_id` і `source_language`** (§5.4 «article ID»; мова
   article-level — optional); `cost: Money | None` замість пари `cost_minor`/валюта (та сама
   семантика, спільний тип §5.1); `translation_idempotency_key` перевіряється проти
   `translation_idempotency_key(...)` з компонентів.
9. **Інваріанти `EntityProjectionVersion`** (CAS з картки WP-01B PR2 п.2):
   `applied_to_current ⇔ previous_version is None or previous_version < projection_version`,
   `state_changed ⇔ state_hash ≠ previous_state_hash`, snapshot і/або artifact (хоча б одне),
   `state_hash == hash(snapshot)`, `lineage.projection_task_id == projection_task_id`.
10. `ObservationRecord.entity_kind ∈ {catalog_offer, vehicle_listing}` (дві observation
    collections §9.2); розширення — minor (послаблення validator-а).

### Як WP-04 переходить на контракт

У `.worktrees/wp-04-1` на момент роботи `src/collector/translation/` містить лише `__init__.py`,
тож дублікатів не видно. Якщо WP-04 PR1 введе тимчасові типи: статус/прапорці перекладу →
`collector.contracts.TranslationStatus`/`TranslationQualityFlag` (власний enum заборонений,
§5.5); код мови → `collector.contracts.LanguageCode` (валідація формату; набори
`CORE_SOURCE_LANGUAGES`/`EXTRA_SOURCE_LANGUAGES` лишаються у WP-04); результат запису версії →
`NewsTranslation`; вхід handler-а → `decode_news_version_created(bytes)`; ключ ідемпотентності —
`translation_idempotency_key` (вже в PR1). TM key лишається у `collector.translation.memory`
(О-2). `src/collector/translation/**` цим PR не редагувався.

## Команди та вивід

```text
$ uv sync --frozen
Checked 66 packages in 4ms
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
277 files already formatted
$ uv run mypy src
Success: no issues found in 81 source files
$ uv run collector contracts export --check
schemas up to date: schemas
exit=0
```

```text
$ uv run pytest tests/contract tests/unit/contracts -q
........................................................................ [ 91%]
.......................................                                  [100%]
471 passed in 15.44s
```

```text
$ uv run pytest -m "not live" -q -rs
(хвіст виводу; кириличні skip-reason у консолі Windows cp1251 — нечитабельні, скорочено)
SKIPPED [6] tests\e2e\test_gui_runtime_contract.py:167: gui ... `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
... (ще 11 рядків тих самих e2e GUI skip-ів, разом 20)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49: ... (COLLECTOR_E2E_REQUIRED=1 ... job `docker` ... `up -d --wait`)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:62: ... (COLLECTOR_E2E_REQUIRED=1 ... job `docker` ... `up -d --wait`)
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback ... asyncio
1 failed, 1189 passed, 23 skipped, 10 warnings in 1673.39s (0:27:53)

$ cat .pytest_cache/v/cache/lastfailed
{
  "tests/unit/contracts/test_code_review_fixes.py::test_project_groups_scales_linearly_on_disjoint_merges": true
}

$ uv run pytest "tests/unit/contracts/test_code_review_fixes.py::test_project_groups_scales_linearly_on_disjoint_merges" -q   # ×3
1 passed in 0.67s
1 passed in 0.68s
1 passed in 0.64s

$ uv run pytest -m "not live and not integration and not e2e" -q
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback ... asyncio
895 passed, 1 skipped, 317 deselected, 8 warnings in 171.13s (0:02:51)
```

```text
$ uv run pre-commit run --all-files
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
```

### Acceptance → тести

| Пункт PR2 | Тест |
|---|---|
| snapshot drift = fail | `test_schema_snapshots.py::test_repository_snapshots_have_no_drift`, `test_adversarial_export.py` (41 snapshot), `collector contracts export --check` |
| compatibility fixtures v1.0 кожної нової моделі | `test_compatibility.py::test_every_versioned_document_has_a_fixture`, `test_fixture_validates_against_current_model[*.v1.0.json]`, `test_model_rejects_other_major` |
| `NewsTranslation` з `body=None` валідний, з naive — ні | `test_news.py::test_translation_with_body_none_is_valid_r20`, `test_translation_rejects_naive_created_at` |
| `TranslationStatus` рівно чотири (golden) | `test_news.py::test_translation_status_has_exactly_four_values_golden`, `test_pr2_snapshots.py::test_news_translation_body_is_nullable_and_status_closed` |
| payload з чужим `entity_uuid` відхиляється функцією | `test_payload.py::test_entity_uuid_mismatch_with_ref_is_rejected` (+ schema_version, domain) |
| `encode_event(NewsVersionCreatedEvent)` byte-equivalent після round-trip | `test_news.py::test_encode_news_event_byte_equivalent_after_round_trip` |
| «жодного I/O» покриває нові модулі | `test_no_io_imports.py::test_io_probe_covers_pr2_modules` + наявні probe-тести (walk_packages) |
| bounded core/attributes (§9.2) | `test_payload.py::test_blocks_are_bounded`, `test_nested_array_inside_array_is_bounded`, `test_records.py::test_snapshot_is_bounded` |
| `docs/contracts.md` — нові моделі і правило розширення | `docs/contracts.md` §11 (§11.2 «Правило розширення доменними WP») |

## Що не перевірено

- **Одне падіння в повному прогоні** — PR1 wall-clock тест
  `test_project_groups_scales_linearly_on_disjoint_merges` (поріг `large < small × 40`) під
  навантаженням машини (паралельно працювали 4 worktree і їхні PostgreSQL-контейнери, прогін
  28 хв). `resolution.py` у PR2 не змінювався; окремо тест зелений 3/3. Нестабільність
  timing-порогу — відомий клас (див. `docs/plan/reports/WP-01D/flaky-scaling-tests.md`); окремого
  виправлення в цьому PR не робив (поза scope PR2), gate може вимагати повторного прогону.
- 23 skip-и — e2e GUI/runtime без піднятого compose (очікувано локально) і Windows-only skip
  `test_network_blocked.py:27`; жодного skip у нових тестах.

- Генерація Mongo `$jsonSchema` з нових snapshot-ів (`_id` → `binData`, `date-time` → `date`,
  `$ref` інлайн) — робота WP-01B PR2; тут перевірено лише форму snapshot-ів
  (`test_pr2_snapshots.py`). `not testable offline` у межах WP-01C: генератора ще немає.
- Реальні payload-и адаптерів (WP-06/08/10) — ще не існують; межі `BoundedJsonObject`
  (8/256/512) обрані без вимірів на реальних даних.
- Linux-прогін: не виконано в цьому середовищі (Windows); зміни — чисті Pydantic-моделі без
  platform-залежного коду, canonical-формат не змінювався. Остаточно — CI job.

## Ризики

- **`AppliedProjectionReceipt` без `_id`** (PR1), нові records — з `_id`. WP-01B має вирішити
  `_id` receipt-а (наприклад, `_id = projection_task_id`) при генерації validator-а; якщо
  знадобиться поле в контракті — dependency-запит (minor, optional).
- **Межі `BoundedJsonObject` застосовано лише до нових моделей**; `CurrentDocumentBase` лишився
  з `JsonObject` (додавання validator-а — посилення, за §3 `docs/contracts.md` трактується як
  major). Projector, що будує current з payload, фактично успадковує межі payload-а.
- Межа 256 елементів масиву може виявитися тісною для vehicle equipment/options — розширення
  (послаблення) сумісне, minor.
- `NewsTranslation.target_language` — будь-який `LanguageCode`, не `Literal["uk"]` (§5.4 каже
  `uk`); обмеження до `uk` — відповідальність WP-04. Звуження пізніше було б major.
- ADR-0003 описує `encode_event` лише для `domain.changed`; формат bytes не змінився, змінився
  лише перелік подій і media type. ADR поза owned files PR2 — оновлення, якщо потрібне, на етапі docs.

## Як вимкнути або відкотити

Контракти не мають runtime-ефекту, доки WP-01B PR2 / WP-01A PR3b / WP-04 PR2 їх не імпортують.
Відкат — `git revert` трьох комітів PR2 до злиття споживачів; PR1-моделі й snapshots не
змінювались, тож revert не зачіпає вже злитих споживачів.

## Dependency-запити

- Закриваються цим PR: `WP-01B-to-WP-01C` (normalized payload, `EntityProjectionVersion`,
  observations, мінімальні `SellerContactObservation`/`ReviewQuestionRecord`) і
  `WP-04-to-WP-01C` п.1 (`NewsTranslation`, `TranslationStatus`, `TranslationQualityFlag`,
  payload `news.version_created`). Самостійних файлів `docs/plan/deps/WP-01B-to-WP-01C.md` /
  `WP-04-to-WP-01C.md` у репозиторії немає (запити зведено в картки) — статус оновлює оркестратор.
- Нових запитів від WP-01C немає.

## Fixes after gate 2

Gate 2 — pass (`testing-pr2.md`, коміти тестувальника `a29c323`, `81d9af7` не переписувались).
Знахідки закрито комітом `fix(wp-01c): close gate-2 findings ...`.

| ID | Рішення | Де | Тест |
|---|---|---|---|
| M-1 (medium) `set` у `core` → недетермінований `state_hash` | **Заборона**, не сортування: масив у `JsonValue` — `Annotated[list[JsonValue], Strict()]`; `set`/`frozenset`/`tuple` → помилка валідації. Сортування безпечніше лише на вигляд: воно мовчки переставляло б елементи, порядок яких викликач міг вважати значущим, і маскувало б помилку parser-а (той самий принцип, що в `UtcDatetime` — відхиляти, не нормалізувати). JSON-шлях (projector, `model_validate_json`) не змінюється — JSON-масив завжди `list`. Snapshot-и не змінились (`Strict()` не впливає на JSON Schema). Зачіпає і PR1-блоки (`CurrentDocumentBase`, `DomainChangedEvent.payload`) — лише Python-callers з non-list, тобто саме помилковий шлях | `_base.py` `JsonValue` | `test_gate2_fixes.py::test_state_hash_identical_across_hash_seeds_and_set_always_rejected` (4 процеси, `PYTHONHASHSEED` 0/1/2/12345), `test_non_list_sequences_rejected_*` |
| M-2 (medium) лише структурні межі | `BoundedJsonObject` додатково: рядок-значення ≤ 65 536 символів, ключ ≤ 256, ціле в `[-2^63, 2^63-1]` (BSON int64), canonical UTF-8 bytes блоку ≤ 1 MiB. Узгодження з 256 KiB: межа блоку свідомо більша — великий `domain.changed` payload іде через `payload_artifact` (`EventTooLargeError`), а три блоки ≤ 3 MiB лишають запас під 16 MiB Mongo. Схеми не змінились | `_base.py` | `test_string_length_limit`, `test_key_length_limit`, `test_int64_range`, `test_block_canonical_size_limit`, `test_block_limit_is_above_event_inline_limit` |
| L-1 (low) одиночні сурогати | Закрито: `canonical_json_bytes` кидає `CanonicalEncodingError` (не сирий `UnicodeEncodeError`); `BoundedJsonObject` рахує canonical bytes на валідації, тож сурогат у блоках відхиляється моделлю. Тест тестувальника `test_lone_surrogate_in_python_input_never_produces_canonical_bytes` посилено до нової гарантії (новий коміт, історія не переписана) | `canonical.py`, `_base.py` | `test_lone_surrogate_is_canonical_encoding_error`, оновлений тест тестувальника |
| L-2 (low) `source_locale_raw` без межі | `max_length=64` у `NewsVersionCreatedEvent` (snapshot `events/news_version_created.v1.json` оновлено; модель ще не злита, версія лишається `1.0`). `SourceTime.source_locale_raw` (PR1, злитий) не чіпав — посилення злитого контракту = major за `docs/contracts.md` §3 | `news.py` | `test_source_locale_raw_is_bounded` |
| Запит WP-04 PR1: `language_unsupported` | Уже є з першого коміту PR2 (`9d9f1de`, snapshot `common/news_translation.v1.json` рядок 81; тест `test_quality_flags_contain_required_minimum`). Minor bump не потрібен: `1.0` ще не злитий і не мав споживачів, тож «попередньої minor-версії» без цього значення не існує; bump до `1.1` вимагав би fixture `1.0` без значення, якого ніколи не було | `enums.py` | наявний |

`docs/contracts.md` §7 і §11.1 доповнено (заборона non-list масивів, нові межі, сурогати).

### Команди після виправлень

```text
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
282 files already formatted
$ uv run mypy src
Success: no issues found in 81 source files
$ uv run collector contracts export --check
schemas up to date: schemas
exit=0
$ uv run pre-commit run --all-files
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
```

```text
$ uv run pytest -m "not live" -q -rs
(хвіст; -p no:cacheprovider, тому назви 2 падінь у виводі не збереглися — tail -8 їх обрізав)
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:310: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49: перевірка діє лише там, де стек обіцяний (COLLECTOR_E2E_REQUIRED=1 — крок job `docker` після `up -d --wait`)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:62: перевірка діє лише там, де стек обіцяний (COLLECTOR_E2E_REQUIRED=1 — крок job `docker` після `up -d --wait`)
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
2 failed, 1374 passed, 23 skipped, 8 warnings in 4986.64s (1:23:06)

# Повторний прогін тих самих тестів двома частинами (разом = повний набір "not live"):
$ uv run pytest -m "not live and not integration and not e2e" -q
1081 passed, 1 skipped, 317 deselected, 8 warnings in 320.29s (0:05:20)
$ uv run pytest -m "(integration or e2e) and not live" -q -rfE
295 passed, 22 skipped, 1082 deselected, 2 warnings in 1049.20s (0:17:29)
```

**2 падіння в повному прогоні — не атрибутовані.** Прогін тривав 1 год 23 хв при 4 паралельних
повних прогонах інших worktree (wp-00-5, wp-01b-1, wp-02-1, wp-01d-1c scaling) і їхніх
контейнерах. Назви падінь не збереглися (без cache provider, вивід обрізано). Повторний прогін
усього набору двома частинами — 0 падінь (1376 passed). Найімовірніше — відомі timing-флейки
(PR1 `test_project_groups_scales_linearly_on_disjoint_merges`, що падав у першому прогоні, і/або
scaling-тести WP-01D, `docs/plan/reports/WP-01D/flaky-scaling-tests.md`), але це **не
доведено**; окремо позначаю як неперевірене. Нові тести gate-2 (`test_gate2_fixes.py`, 13) і вся
група contracts зелені в обох прогонах.
