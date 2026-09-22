# WP-01C — звіт реалізації (`wp/01c-contracts`)

| Поле | Значення |
|---|---|
| WP | WP-01C «Shared data contracts» |
| Branch / worktree | `wp/01c-contracts` / `.worktrees/wp-01c` (від `main` `ea8d75e`, після merge WP-00 PR1) |
| Картка | `docs/plan/cards/WP-01C.md` |
| Розділи ТЗ | §5.1, §5.4, §5.5, §7.3, §9.2, §9.3, §9.4, §9.6, §9.8, §9.9, §10 п.5–10 (+ §12.2, §16.1, §18) |
| REVIEW.md | R-18, R-20, R-27, R-30, R-36, R-37, R-38, R-41, R-42, R-43, R-45, R-46, R-49 |
| Середовище | Windows 11, uv 0.12.13, CPython 3.13.9, pydantic 2.13.5, phonenumbers 9.0.39 |
| Commits | `dc60f45` identity/temporal/enums/values · `7a2696f` artifacts/projection/events/current · `3e65442` resolution/release · `6aca83c` schema export/CLI/snapshots/docs · `6945057` звіт · `98add4b` fix dependency/schema_version (див. останній розділ) |
| Обсяг | ~2 460 рядків у `src/collector/contracts/**` (12 модулів), ~2 370 рядків тестів/fixtures, 32 JSON Schema snapshots |

Продуктивний код > 800 рядків, тому — один branch, чотири логічні коміти; кожен проміжний коміт
самодостатній (`__init__.py` експортує лише наявні модулі; тести contracts зелені на кожному:
71 → 102 → 114 → 208 тестів).

## Що зроблено

### Вимога 1 — ідентичність (`identity.py`, `source_registry.py`)

- `EntityId = Annotated[UUID, AfterValidator(version == 7)]`; `Uuid7Generator` (RFC 9562: 48-bit
  ms, 12-bit лічильник у `rand_a`, 62 random) з lock — строго монотонний у межах процесу навіть при
  переповненні лічильника і кроці годинника назад; `new_entity_id()`, `entity_id_timestamp()`.
  Python 3.13 не має `uuid.uuid7` (перевірено: `hasattr(uuid, "uuid7") == False`), тому власний
  генератор замість `uuid-utils` (нативна залежність не додається).
- `SourceIdentity(source_id, source_item_id)`: синтаксис `<kind>_<cc>_<slug>` + наявність у
  `docs/research/source-registry.yaml` (loader read-only, `lru_cache`, env `COLLECTOR_SOURCE_REGISTRY`
  або пошук угору від пакета/cwd; `yaml` імпортується lazy). Тест: 70 ID, без дублікатів, kind-префікс
  збігається з `kind`.
- `identity_hash_v1(canonical_url, stable_attributes) -> "v1:<sha256>"` — алгоритм у
  `docs/contracts.md` §4.3; golden `tests/fixtures/contracts/identity_golden.json` містить точний
  `canonical_json`, SHA-256 якого перевірено вручну (`printf ... | sha256sum`).
- `fetch_idempotency_key`, `planned_at_bucket`, `translation_idempotency_key`, `raw_object_key` —
  golden у тому ж файлі. `NormalizedUrl(original, normalized)` відхиляє tracking-параметри в
  `normalized`, `original` зберігається.

### Вимога 2 — осі стану (`enums.py`) — R-18

П'ять `StrEnum` з точними значеннями §5.5 (`@unique`), `STATE_AXES`, `map_research_access_state()`
(`free→full`, `body_unavailable→metadata_only`, `retryable→FetchOutcome.retryable`, решта однойменні;
невідома позначка → `ValueError`). Заборона інших enum стану — `docs/contracts.md` §1, §5.

### Вимога 3 — часова модель (`temporal.py`) — R-43, R-49

`UtcDatetime` відхиляє naive і offset ≠ 0 (не нормалізує). `SourceTime`, `SystemTime`
(`ingested_at ≥ fetched_at`), `EntityTime` (блок `time` §9.2; validator відхиляє
`source_event_at`/`source_updated_at == fetched_at`), `EffectiveTime` + `derive_effective_time`
(basis `source_event → source_updated → observed`, `fetched_at` не використовується),
`BitemporalInterval.contains(as_of_valid_time, as_known_at)`, `build_intervals(versions)` — обидві осі
незалежно; тести late arrival / backdated correction / relisting / порядок входу.

### Вимога 4 — гроші та нормалізація (`values.py`)

`Money(amount_minor: int, currency: ^[A-Z]{3}$)` — float у `amount_minor` відхиляється явним
before-validator. `ContactValue.from_raw()`: телефон → E.164 (`phonenumbers`, region UA), e-mail →
lowercase + IDNA (`idna`, UTS-46); `raw` завжди зберігається; невалідне → `normalized=None`.
`MeasuredValue(value: Decimal, unit, raw_value, raw_unit)`.

### Вимога 5 — artifacts (`artifacts.py`) — R-27, R-38, R-41

`ArtifactRef(uri, sha256, size_bytes, media_type, schema_version)`, `RawArtifactRef` (+ fetch_id,
fetched_at, requested/final URL, HTTP status, content-type/encoding, etag, last-modified raw),
`NormalizedArtifactRef` (+ `entity_uuid`, `domain`, `parser_version`, `fetch_id`, `raw_sha256`,
`raw_uri`, `produced_at`). `UploadClaim` + `can_commit(claim, now, generation)` =
`status is leased and claim_generation == generation and lease_expires_at > now`.

### Вимога 6 — projection / receipt / ack / event (`projection.py`, `events.py`, `canonical.py`) — R-30, R-36, R-37, R-42

- `ProjectionCommand` (внутрішня команда; `artifact.entity_uuid == entity_uuid`),
  `AppliedProjectionReceipt` з усіма полями картки; validator: event descriptor (inline bytes або
  `event_artifact`, рівно одне, + `event_id`) присутній **тоді й лише тоді**, коли
  `should_emit_domain_changed`; `event_sha256 == sha256(event_bytes)`; inline ≤ 256 KiB;
  `applied_to_current ⇒ result_version == projection_version`; `state_changed ⇒ previous_hash ≠ result_hash`.
- `ProjectionAcknowledgement.from_receipt()` без повторної серіалізації.
- `DomainChangedEvent` (`payload` xor `payload_artifact`) + `encode_event()` → `EncodedEvent(bytes,
  media_type, sha256)`; `EventTooLargeError` при > 256 KiB. Canonical serialization: sorted keys,
  без пробілів, NFC, UTC `...ffffffZ`, Decimal без експоненти, base64 bytes, NaN/inf/naive відхиляються.
  Тести: byte-equivalence повторної серіалізації і після round-trip, незалежність від порядку ключів
  payload, незалежність від локалі (`setlocale` uk/de, якщо доступні).
- `should_emit_domain_changed(receipt) = applied_to_current and state_changed` — параметризований тест
  усіх чотирьох комбінацій.

### Вимога 7 — current document (`current.py`)

`CurrentDocumentBase` дослівно за YAML §9.2 (`_id` alias + `populate_by_name`, `entity_kind`,
`source: SourceRef`, `identity_hash`, `projection_version`, `state_hash`, `core`, `attributes`,
`latest_state`, `lineage: Lineage`, `time: EntityTime`, `first_seen_at`, `last_seen_at`) +
`EntityKind`. `compute_state_hash_v1` над `core+attributes+latest_state` — тест на незалежність від
порядку полів і unicode-форми; документ зі stale `state_hash` відхиляється. Відхилення від літери
§9.2: `schema_version` — рядок `"1.0"` (картка вимагає `major.minor`), а не int `1`; major збігається.

### Вимога 8 — resolution (`resolution.py`) — R-45

`ResolutionDecision` з усіма полями §9.8; `project_groups(decisions) -> ResolutionSnapshot(groups,
blocked_pairs, applied/skipped/superseded_decision_ids)`: детермінований порядок replay,
superseded-рішення пропускаються, `manual_block` блокує наступні auto `merge` до superseding decision,
`manual_link` блоку не підлягає, `unmerge` виводить members. Тест «merge → manual_block → unmerge →
replay» + supersede блоку + злиття цілих груп + unmerge одного member.

### Вимога 9 — release (`release.py`) — R-46

`ReleaseManifest` з усіма полями §9.9 (`entity_versions` xor `partition_index_sha256`; excluded/degraded
потребують `reason`; published/superseded потребують `published_at`, `parts`, `quality_report`,
`reconciliation_result`), `ReleasePart`, `ReleaseWatermark`, `EntityVersionRef`, `SourceInclusion`,
`ComponentVersions`, `RELEASE_TRANSITIONS`, `can_transition`, `transition_release`,
`validate_manifest_update` (published: змінюються лише `state`/`superseding_release_id`). Тест —
повна таблиця переходів 6×6 і immutable published.

### Вимога 10 — схеми, версіонування, сумісність (`_base.py`, `schema_export.py`, CLI)

- `ContractModel` (frozen, `extra="forbid"`, base64 bytes) з `contract_version` класу;
  `VersionedDocument` з полем `schema_version` (default = `contract_version`, перевіряється в
  `__pydantic_init_subclass__`; документ іншого major або новішого minor відхиляється).
- `EXPORTED_CONTRACTS` — 32 моделі у групах `common` (18), `events` (4), `mongo` (2), `releases` (8);
  `uv run collector contracts export [--check] [--output]` → `schemas/<group>/<name>.v<major>.json`
  (sorted keys, sorted `required`, `$id`, `x-contract-version`). `--check` → exit 1 з переліком
  `drift/missing/stale`; CRLF checkout толерується.
- Compatibility: fixtures `tests/fixtures/contracts/documents/<name>.v1.0.json` для всіх 7
  `VersionedDocument`; тест валідації + round-trip; тест відхилення іншого major/новішого minor;
  `check_compatibility(old, new)` (видалене/перейменоване поле, нове required, зміна типу, видалене
  enum-значення або `$defs`) на синтетичних схемах і на реальному snapshot (видалення `identity_hash`
  ловиться); repository snapshot vs модель — сумісні.
- `docs/contracts.md`: ownership, dependency-процедура, minor/major процедура, drift, алгоритм
  identity_hash_v1, canonical serialization, state_hash, temporal/projection/resolution/release правила.
  `schemas/README.md`: як генерувати, структура груп.
- CLI: `contracts_app` додано в `src/collector/cli.py` як `hidden=True` (див. Dependency-запити).

## Команди та вивід

Windows 11, `PYTHONUTF8=1`, worktree після `git commit 6aca83c`:

```text
$ uv sync --frozen
Checked 46 packages in 3ms

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
93 files already formatted

$ uv run mypy src
Success: no issues found in 38 source files

$ uv run pytest -m "not live"
============================= test session starts =============================
platform win32 -- Python 3.13.9, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\repos\webscraper\.worktrees\wp-01c
configfile: pyproject.toml
plugins: anyio-4.15.1, asyncio-1.4.0, socket-0.8.1, respx-0.23.1
collected 336 items
tests\contract\contracts\test_compatibility.py ......................... [  7%]
tests\contract\contracts\test_no_io_imports.py ..                        [ 16%]
tests\contract\contracts\test_schema_snapshots.py ...................... [ 22%]
tests\unit\contracts\test_artifacts.py .....                             [ 28%]
tests\unit\contracts\test_cli_contracts.py ....                          [ 29%]
tests\unit\contracts\test_current.py ......                              [ 31%]
tests\unit\contracts\test_enums.py .................                     [ 36%]
tests\unit\contracts\test_events.py ..........                           [ 39%]
tests\unit\contracts\test_identity.py .....................              [ 45%]
tests\unit\contracts\test_projection.py ..........                       [ 48%]
tests\unit\contracts\test_release.py .......                             [ 50%]
tests\unit\contracts\test_resolution.py .....                            [ 52%]
tests\unit\contracts\test_temporal.py .................                  [ 57%]
tests\unit\contracts\test_values.py ................                     [ 61%]
tests\unit\test_cli.py ...........................                       [ 69%]
tests\unit\test_cli_adversarial.py ..................................... [ 80%]
tests\unit\test_foundation_config.py ...........................         [ 90%]
tests\unit\test_logging.py ....................                          [ 96%]
tests\unit\test_network_block_adversarial.py ......                      [ 98%]
tests\unit\test_network_blocked.py .s...                                 [100%]
=========================== short test summary info ===========================
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
================= 335 passed, 1 skipped, 6 warnings in 5.76s ==================

$ uv run collector contracts export --check
schemas up to date: schemas
exit=0
```

Додатково: `uv run pre-commit run --all-files` — усі hooks `Passed` (eof/whitespace/yaml/toml/
large-files/merge-conflict/private-key, ruff check, ruff format, gitleaks, markdownlint-cli2).
Skip і 6 warnings — успадковані від WP-00 PR1 (Windows-відхилення мережевої політики, ADR-0001),
не стосуються WP-01C. Нових тестів WP-01C: 208 (156 unit + 52 contract); загалом 335 passed
(було 127 у PR1).

## Acceptance ↔ тести

| Acceptance | Тест |
|---|---|
| identity hash golden | `test_identity.py::test_identity_hash_golden`, `test_fetch_and_translation_keys_golden` |
| 70 `source_id` валідні, без дублікатів | `test_identity.py::test_registry_has_70_unique_valid_ids`, `test_registry_rejects_duplicates` |
| п'ять enum §5.5 точні + mapping research | `test_enums.py::test_state_axes_have_exact_values`, `test_exactly_five_state_axes`, `test_map_research_access_state` |
| naive datetime відхиляється | `test_temporal.py::test_system_time_rejects_naive_and_non_utc`, `test_source_time_rejects_naive_but_allows_null` |
| `fetched_at` не потрапляє у `source_event_at` | `test_temporal.py::test_entity_time_rejects_source_event_equal_to_fetched_at`, `test_derive_effective_time_never_uses_fetched_at`, `test_current.py::test_current_document_time_block_rejects_fetched_at_as_source_event` |
| bitemporal late arrival / backdated / relisting | `test_temporal.py::test_intervals_late_arrival_*`, `test_intervals_backdated_*`, `test_intervals_relisting_*` |
| `encode_event` byte-equivalent + ліміт 256 KiB | `test_events.py::test_encode_event_deterministic_and_byte_equivalent_after_round_trip`, `test_encode_event_limit_256_kib_and_artifact_alternative`, `test_projection.py::test_receipt_hash_size_and_version_invariants` |
| `should_emit_domain_changed` | `test_projection.py::test_should_emit_domain_changed_rule`, `test_receipt_event_descriptor_present_iff_applied_and_changed` |
| `state_hash` незалежний від порядку полів | `test_current.py::test_state_hash_independent_of_field_order_and_unicode_form` |
| resolution replay | `test_resolution.py::test_merge_manual_block_unmerge_replay` (+3) |
| release state machine, published immutable | `test_release.py::test_state_machine_transitions_table`, `test_published_is_immutable` |
| compatibility fixtures | `test_compatibility.py::test_fixture_validates_against_current_model[7]`, `test_model_rejects_other_major[7]`, `test_removing_or_renaming_field_is_breaking`, `test_removing_field_from_real_model_snapshot_is_detected` |
| snapshot для кожної публічної моделі, `--check` без drift | `test_schema_snapshots.py::test_repository_snapshots_have_no_drift`, `test_snapshot_exists_and_is_self_consistent[32]`, `test_cli_contracts.py` |
| без I/O у `collector.contracts` | `test_no_io_imports.py::test_import_contracts_does_not_load_io_libraries` (subprocess `python -I`), `test_contract_sources_have_no_io_or_network_imports` |
| `docs/contracts.md` процедура змін і ownership | розділи 1, 3 |

## Що не перевірено

- **Linux-паритет** — команди виконано лише на Windows (у CI буде повний `--disable-socket`; тести
  WP-01C сокетів не використовують, окрім subprocess `python -I` у `test_no_io_imports`, який
  pytest-socket не перехоплює за визначенням). Локаль-тест `test_canonical_independent_of_process_locale`
  на Windows пройшов для `Ukrainian_Ukraine.1251`/`German_Germany.1252`; на Linux відпрацює, якщо
  `uk_UA.UTF-8`/`de_DE.UTF-8` встановлені, інакше тихо пропускає локалі (не падає).
- **Інтеграція з Mongo `$jsonSchema`** — snapshot-и мають `format: uuid/date-time`, `anyOf` з null,
  `additionalProperties: false`; чи всі конструкції підтримує Mongo validator (`$jsonSchema` не знає
  `format`, `$id`, `$schema`) — перевірить WP-01B при генерації validators (not testable offline тут).
- **Реальні `phonenumbers` кейси поза UA/PL** — покрито UA/PL номери з golden; повна матриця країн —
  у WP-07/09 адаптерах.
- **Продуктивність canonical serialization** на великих payload — не вимірювалась (ліміт 256 KiB;
  `test_encode_event_limit_256_kib` серіалізує ~256 KiB за < 50 мс).

## Ризики

1. **`hidden=True` для групи `contracts`** — команда працює, але не видна в `collector --help`, бо
   `tests/unit/test_cli_adversarial.py` (WP-00) вимагає точний набір §16.2. Зняти після dependency-запиту
   (нижче). Ризик: користувач не бачить команду в help; `docs/contracts.md` і `schemas/README.md` її
   документують.
2. **`SourceIdentity` залежить від файлу реєстру на диску** — у Docker image без `docs/research/`
   валідація кине `SourceRegistryError`. Потрібен `COPY` або env у `Dockerfile` (WP-00 PR2) — у
   dependency-запиті. Альтернатива на майбутнє: вбудувати реєстр як package data (рішення за
   оркестратором; реєстр read-only і належить research).
3. **Drift від docstring** — docstrings моделей потрапляють у `description` snapshot-ів, тому будь-яка
   редакція docstring вимагає `collector contracts export`. Це свідомо: description — частина
   контракту для споживачів схем; `check_compatibility` при цьому не вважає такі зміни breaking.
4. **R-43 validator на рівність `source_event_at == fetched_at`** — евристика: легітимний збіг до
   мікросекунди практично неможливий, але джерело з секундною точністю + `fetched_at`, обрізаний до
   секунд, теоретично може збігтися. Адаптери мають писати `fetched_at` з мікросекундами (з `time.time_ns()`).
5. **`schemas/common/`** — четверта група поза переліком `schemas/{events,mongo,releases}` картки:
   value objects (Money, temporal, artifact refs) не належать до жодної з трьох, а acceptance вимагає
   snapshot для кожної публічної моделі. Якщо gate вимагатиме суворо три групи — перемістити
   `common/*` у `events/` (вони вкладаються в події) одним перейменуванням у `EXPORTED_CONTRACTS`.
6. **UUIDv7 власний, а не `uuid-utils`** — менше залежностей, але при переході на Python 3.14 варто
   замінити на stdlib `uuid.uuid7()` (він теж монотонний у процесі); `EntityId` validator не зміниться.

## Як вимкнути або відкотити

Контракти не мають runtime-ефекту до появи споживачів (WP-01A/01B/01D/02): відкат — `git revert`
чотирьох комітів `dc60f45..6aca83c` (або revert PR). Єдиний спільний файл — `src/collector/cli.py`
(додано лише `contracts_app`; видалення трьох рядків `add_typer` + функції `contracts_export`
повертає PR1-стан). Залежності `idna`/`phonenumbers`/`pyyaml` у `[project.dependencies]` після
відкату можна лишити (pyyaml і так був у dev; idna — транзитивна через httpx).

## Dependency-запити

`docs/plan/deps/WP-01C-to-WP-00.md` (стан `open`), три пункти:

1. додати `"contracts"` до `SPEC_16_2_TOP_LEVEL` у `tests/unit/test_cli_adversarial.py`
   (і `TOP_LEVEL_COMMANDS` у `tests/unit/test_cli.py`), після чого WP-01C прибирає `hidden=True`;
   або лишити групу прихованою як dev/CI-команду;
2. `collector version`: замінити `SCHEMA_VERSION_PLACEHOLDER` на `collector.contracts.CONTRACTS_VERSION`
   (імпорт легкий — тест `test_no_io_imports`);
3. CI-крок `uv run collector contracts export --check` у `.github/workflows/ci.yml` і `COPY`/env для
   `docs/research/source-registry.yaml` у `Dockerfile` (PR2).

Поза owned files WP-01C свідомо **не** створено ADR-0003 «Canonical event serialization»
(`docs/decisions/` не в owned files; матеріал для нього — `docs/contracts.md` §6 і docstring
`collector/contracts/canonical.py`) — це етап 5 (docs) за карткою.

## Виправлення dependency/schema_version (після рішення оркестратора)

Dependency-запит `docs/plan/deps/WP-01C-to-WP-00.md` схвалено як approved dependency change;
зміни застосовано у `wp/01c-contracts` (WP-00 PR1 злитий, owner-агент завершив), коміт
`98add4b fix(wp-01c): approved dependency changes and int schema_version for current document`:

1. **CLI**: `hidden=True` для групи `contracts` прибрано — `collector --help` показує `contracts`.
   `tests/unit/test_cli_adversarial.py`: `FOUNDATION_EXTENSIONS = frozenset({"contracts"})` з
   коментарем, що §16.2 — контракт CI-команд, а не вичерпний список CLI; перевірка
   `top_level == SPEC_16_2_TOP_LEVEL | FOUNDATION_EXTENSIONS`. `tests/unit/test_cli.py`:
   `FOUNDATION_EXTENSIONS = ("contracts",)` у перевірці `--help`.
2. **`collector version`**: `src/collector/core/version.py` імпортує
   `collector.contracts.CONTRACTS_VERSION` (`"1.0"`) замість `SCHEMA_VERSION_PLACEHOLDER`
   (константу видалено); тести `test_cli.py`/`test_cli_adversarial.py` порівнюють із
   `CONTRACTS_VERSION`. Вивід: `schema_version=1.0`.
3. **CI**: `.github/workflows/ci.yml` job `python` — крок
   `contracts JSON Schema snapshots без drift (WP-01C)`: `uv run collector contracts export --check`
   після pytest.
4. Стан запиту: `resolved (п.1–3 у wp/01c-contracts за рішенням оркестратора; п. Dockerfile → WP-00 PR2)`.
5. **`CurrentDocumentBase.schema_version` → `int`** (major) за YAML §9.2 (`schema_version: 1`):
   модель тепер наслідує `ContractModel` (не `VersionedDocument`), поле
   `schema_version: int = Field(default=1, ge=1, strict=True)`; validator вимагає рівності major з
   `contract_version` класу (`"1.0"` → 1). `CONTRACTS_VERSION` та snapshots лишаються `major.minor`
   (`x-contract-version: "1.0"`). Оновлено: `schemas/mongo/current_document_base.v1.json`
   (`type: integer`, `default: 1`, `minimum: 1`), fixture `documents/current_document_base.v1.0.json`
   (`"schema_version": 1`), `factories.py`, `test_current.py` (відхилення `2` і рядка `"1.0"`),
   `test_compatibility.py` (int major для current document; fixture обов'язковий і для нього),
   `docs/contracts.md` §2/§3. Попереднє відхилення від ТЗ знято.

Поза owned files лишаються згадки placeholder у `README.md` (рядок 69, «schema version placeholder»)
та `docs/decisions/0001-foundation-stack.md` (рядок 56) — owner WP-00; рекомендація для етапу docs:
замінити на «`schema_version` = `collector.contracts.CONTRACTS_VERSION`».

Повторний прогін після виправлень (Windows, `PYTHONUTF8=1`):

```text
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
94 files already formatted
$ uv run mypy src
Success: no issues found in 38 source files
$ uv run pytest -m "not live" -q
SKIPPED [1] tests/unit/test_network_blocked.py:27: Windows: loopback потрібен asyncio
334 passed, 1 skipped, 6 warnings in 16.35s
$ uv run collector contracts export --check
schemas up to date: schemas
$ uv run collector --help | grep contracts
  contracts   Shared data contracts: JSON Schema snapshots (§9.4); owner...
$ uv run collector version
package_version=0.1.0
git_sha=unknown
schema_version=1.0
$ uv run pre-commit run --all-files
(усі hooks Passed)
```

334 замість 335: `test_model_rejects_other_major` параметризований лише `VersionedDocument` (7 → 6);
відхилення іншого major для current document покриває
`test_current.py::test_current_document_matches_spec_9_2_shape`.
