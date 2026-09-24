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
