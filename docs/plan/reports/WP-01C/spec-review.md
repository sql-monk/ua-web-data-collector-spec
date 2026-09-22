# WP-01C — пострев'ю за ТЗ (`wp/01c-contracts`)

| Поле | Значення |
|---|---|
| WP | WP-01C «Shared data contracts» |
| Branch / worktree | `wp/01c-contracts` / `.worktrees/wp-01c` |
| Рев'юваний commit | `8f38eb4` (`git diff main...HEAD`, base `ea8d75e`; `main` = `ad2b464`, попереду лише на ledger-коміти); 90 файлів, +10 525 / −15 |
| Картка | `docs/plan/cards/WP-01C.md` — вимоги 1–10 (усі підпункти), «Acceptance», «Rollback/disable» |
| Розділи ТЗ | §17.2 (рядок WP-01C), §5.1, §5.5, §7.3, §9.2, §9.3, §9.4, §9.6, §9.8, §9.9, §10 п.5–10, §16.1, §16.3 (дотичні), §18, §20, Додаток A, Додаток C |
| REVIEW.md | R-18, R-20, R-30, R-36, R-37, R-42, R-43, R-45, R-46, R-49 |
| Вхідні звіти | `implementation.md` (з розділами «Виправлення dependency/schema_version», «Виправлення після gate 2», «Відповіді на код-рев'ю»), `testing.md` (`pass`, T-01…T-07), `code-review.md` (`approve`, 0 critical/high, 2 medium, 8 low) |
| Рев'юер | wp-spec-reviewer, read-only; записи — лише цей файл і `docs/acceptance/traceability.md` |

## Вердикт

**`accept`** — `missing`: 0; `partial`: 8 (усі — межі scope картки (integration/release/NewsArticle у WP-01A/01B/11A), етапи docs/merge поза етапом 4 або одна low-знахідка без блокування); знахідок critical/high: 0. Усі 10 знахідок код-рев'ю (2 medium, 8 low) і 7 спрощень мають статус `fixed` / `accepted (owner WP-01C, 2026-09-22)` / `not applicable (аргумент)`; кожен `fixed` підтверджено в коді (розділ 6). Знахідки тестування T-01/T-02/T-06 — `fixed` у `44c8aa3`, підтверджено в коді.

Задекларовані реалізатором відхилення (група `schemas/common/`, `schema_version` int для current document, hidden→visible `contracts`, правки чужих тестів) оцінено в розділі 7: жодне не потребує ADR або зміни ТЗ; одне (`schemas/common/`) потребує однорядкового доповнення картки (owned files) — знахідка low.

## Власна верифікація (Windows 11, `PYTHONUTF8=1`, HEAD `8f38eb4`)

```text
$ uv sync --frozen                     → Checked 46 packages
$ uv run ruff check .                  → All checks passed!
$ uv run ruff format --check .         → 101 files already formatted
$ uv run mypy src                      → Success: no issues found in 38 source files
$ uv run pytest -m "not live" -q       → 474 passed, 1 skipped, 6 warnings in 14.39s
                                         (skip: tests/unit/test_network_blocked.py:27 — успадкований WP-00 Windows-skip)
$ uv run collector contracts export --check → schemas up to date: schemas   exit=0
$ uv run pre-commit run gitleaks --all-files → Passed
$ grep -rn "type: ignore|noqa" src/collector/contracts/ → 0 рядків
```

Зонди (`uv run python -`, фактичний вивід):

- п'ять enum §5.5 — значення дослівно збігаються з ТЗ; `map_research_access_state`: `free→full`, `body_unavailable→metadata_only`, `retryable→FetchOutcome.retryable`, `gone→gone`;
- `SystemTime(observed_at=naive)` → `ValidationError "naive datetime заборонений"`; `+03:00` → `"datetime має бути в UTC, отримано offset 3:00:00"` (відхилення, не нормалізація);
- `EntityTime(source_event_at == fetched_at)` → `ValidationError "source_event_at дорівнює fetched_at: source time не підміняється crawler time"` (R-43);
- `derive_effective_time(SourceTime(), system)` → `basis=observed`, `source_time_inferred=True`, `effective_at = observed_at` (не `fetched_at`);
- `known_source_ids()` → 70;
- `encode_event` для двох подій з різним порядком ключів payload: bytes рівні, sha256 рівні; `encode(decode(bytes)) == bytes`; media type `application/vnd.ua-collector.domain-changed.v1+json`; payload 256 KiB + overhead → `EventTooLargeError(size=262627 > 262144)`;
- `compute_state_hash_v1` для переставлених вкладених ключів — рівний;
- `Money(amount_minor=1.5)` → `ValidationError`; `ContactValue.from_raw(PHONE, "050 123 45 67")` → `+380501234567`, raw збережено; `Іван@Пошта.УКР` → `іван@xn--80a1acn3a.xn--j1amh`;
- `RELEASE_TRANSITIONS`: `draft→{building,failed}`, `building→{validating,failed}`, `validating→{failed,published}`, `published→{superseded}`, `failed→∅`, `superseded→∅`;
- `identity_hash_v1` з ключами `Brand`/`brand` у різному порядку й регістрі — рівний;
- `grep -ri "threshold|0\.99|cadence|weekly" src/collector/contracts docs/contracts.md` → порожньо (Q-011/Q-012 не зашиті).

## 1. Acceptance criteria (§17.2 рядок WP-01C, картка, §16.3 дотичні)

### 1.1. §17.2: «canonical UUID/source identity, temporal axes, artifact, projection command/ack, resolution decision, domain event і dataset release schemas; compatibility fixtures green»

| Елемент §17.2 | Доказ | Статус |
|---|---|---|
| canonical UUID identity | `src/collector/contracts/identity.py:53-111` (`EntityId` = UUID v7 validator, `Uuid7Generator`, `new_entity_id`); `tests/unit/contracts/test_identity.py::test_new_entity_id_is_uuid7_and_monotonic_within_process`, `::test_uuid7_generator_monotonic_when_clock_frozen_or_goes_backwards`, `::test_entity_id_rejects_non_v7`; snapshot `schemas/common/source_identity.v1.json` | evidenced |
| source identity | `identity.py:126-132` (`SourceIdentity`, `source_id` через `require_known_source_id`); `source_registry.py:114-140` (read-only `lru_cache` loader); `test_identity.py::test_registry_has_70_unique_valid_ids`, `::test_source_identity_accepts_registered_ids[70]`, `::test_registry_rejects_duplicates`; зонд: 70 ID | evidenced |
| temporal axes | `temporal.py:42-150` (`SourceTime`, `SystemTime`, `EntityTime`, `EffectiveTime`, `derive_effective_time`), `:153-242` (`BitemporalInterval`, `build_intervals`); `test_temporal.py` (17); snapshots `schemas/common/{source_time,system_time,entity_time,effective_time,bitemporal_interval,version_times,version_interval}.v1.json` | evidenced |
| artifact | `artifacts.py:29-95` (`ArtifactRef`, `RawArtifactRef`, `NormalizedArtifactRef`, `UploadClaim`, `can_commit`); `test_artifacts.py` (6); snapshots `schemas/common/{artifact_ref,raw_artifact_ref,normalized_artifact_ref,upload_claim}.v1.json` | evidenced |
| projection command/ack | `projection.py:29-52` (`ProjectionCommand`), `:130-166` (`ProjectionAcknowledgement.from_receipt`), `:55-122` (`AppliedProjectionReceipt`); `test_projection.py` (7); snapshots `schemas/events/{projection_command,projection_acknowledgement}.v1.json`, `schemas/mongo/applied_projection_receipt.v1.json` | evidenced |
| resolution decision | `resolution.py:30-79` (`ResolutionDecision`, усі поля §9.8), `project_groups`; `test_resolution.py::test_merge_manual_block_unmerge_replay` (+4); snapshots `schemas/releases/{resolution_decision,resolution_snapshot}.v1.json` | evidenced |
| domain event | `events.py:40-107` (`DomainChangedEvent`, `EncodedEvent`, `encode_event`/`decode_event`, `EVENT_INLINE_LIMIT_BYTES = 262144`); `canonical.py`; `test_events.py` (10); snapshots `schemas/events/{domain_changed_event,encoded_event}.v1.json` | evidenced |
| dataset release schemas | `release.py:48-152` (`ReleaseManifest`, `ReleasePart`, `ReleaseWatermark`, `EntityVersionRef`, `SourceInclusion`, `ComponentVersions`), `:206-260` (state machine, immutability); `test_release.py::test_manifest_has_all_spec_9_9_fields`, `::test_state_machine_transitions_table`, `::test_published_is_immutable`; snapshots `schemas/releases/*.v1.json` (8) | evidenced |
| compatibility fixtures green | `tests/fixtures/contracts/documents/*.v1.0.json` (7); `tests/contract/contracts/test_compatibility.py::test_every_versioned_document_has_a_fixture`, `::test_fixture_validates_against_current_model[7]`, `::test_model_rejects_other_major[6]`, `::test_repository_snapshot_is_compatible_with_current_model[32]`, `::test_removing_field_from_real_model_snapshot_is_detected`; прогін 474 passed | evidenced |

### 1.2. Acceptance картки

| Пункт Acceptance | Доказ | Статус |
|---|---|---|
| Усі команди зелені; `mypy --strict` без ignore | розділ «Власна верифікація»; `pyproject.toml:66` `strict = true`; `grep "type: ignore\|noqa" src/collector/contracts/` → 0 | evidenced |
| Snapshot JSON Schema для кожної публічної моделі; `--check` без drift | `schema_export.py:71-107` (`EXPORTED_CONTRACTS`, 32 моделі); 32 файли `schemas/{common,events,mongo,releases}/`; `test_schema_snapshots.py::test_repository_snapshots_have_no_drift`, `::test_snapshot_exists_and_is_self_consistent[32]`; `test_adversarial_export.py::test_every_public_model_has_exactly_one_snapshot_on_disk`, `::test_single_snapshot_tamper_turns_check_red[5]`; `--check` exit 0 | evidenced |
| identity hash golden | `tests/fixtures/contracts/identity_golden.json`; `test_identity.py::test_identity_hash_golden`, `::test_fetch_and_translation_keys_golden` | evidenced |
| Усі 70 `source_id` валідні | `test_identity.py::test_registry_has_70_unique_valid_ids`, `::test_source_identity_accepts_registered_ids[70]`; зонд 70 | evidenced |
| П'ять enum §5.5 з точними значеннями + mapping research | `enums.py:12-88`, `:182-188` (`STATE_AXES`); `test_enums.py::test_state_axes_have_exact_values[5]`, `::test_exactly_five_state_axes`, `::test_map_research_access_state[9]`; `test_adversarial.py::test_state_axes_value_sets_equal_spec_verbatim`; зонд | evidenced |
| naive datetime відхиляється | `temporal.py:26-38` (`require_utc`); `test_temporal.py::test_system_time_rejects_naive_and_non_utc[4]`, `::test_source_time_rejects_naive_but_allows_null`; `test_adversarial.py::test_non_utc_aware_datetime_is_rejected_not_normalized`; mutation M2/M3 у `testing.md` §5; зонд | evidenced |
| `fetched_at` не потрапляє у `source_event_at` | `temporal.py:84-94` (`EntityTime._source_time_not_fetched_at`), `:127-150` (`derive_effective_time` без `fetched_at`); `test_temporal.py::test_entity_time_rejects_source_event_equal_to_fetched_at`, `::test_derive_effective_time_never_uses_fetched_at`; `test_current.py::test_current_document_time_block_rejects_fetched_at_as_source_event`; зонд | evidenced |
| bitemporal інтервали late arrival / backdated / relisting | `temporal.py:197-242`; `test_temporal.py:144` `::test_intervals_late_arrival_keeps_system_axis_of_earlier_versions`, `:156` `::test_intervals_backdated_correction_shares_valid_axis`, `:169` `::test_intervals_relisting_keeps_both_axes_separate` (обидві осі перевірені окремо); mutation M5 | evidenced |
| `encode_event` byte-equivalent і ліміт 256 KiB | `events.py:88-102`, `canonical.py:115-126`; `test_events.py::test_encode_event_deterministic_and_byte_equivalent_after_round_trip`, `::test_encode_event_limit_256_kib_and_artifact_alternative`; `test_adversarial.py::test_encode_event_exactly_256_kib_ok_and_plus_one_rejected`; зонд | evidenced |
| `state_hash` незалежний від порядку полів | `current.py:27-42`; `test_current.py::test_state_hash_independent_of_field_order_and_unicode_form`; `test_adversarial.py::test_state_hash_nested_dict_reorder_same_but_list_order_significant`, `::test_state_hash_unicode_nfd_equals_nfc_for_values_and_keys`; `test_code_review_fixes.py::test_state_hash_survives_json_round_trip`; зонд | evidenced |
| resolution replay | `resolution.py:150-211` (`project_groups`); `test_resolution.py:44` `::test_merge_manual_block_unmerge_replay` (merge → manual_block → unmerge → replay, той самий snapshot при reverse-порядку) (+4); `test_adversarial.py` розділ 8 (6) | evidenced |
| release state machine | `release.py:206-260`; `test_release.py::test_state_machine_transitions_table` (6×6), `::test_published_is_immutable`, `::test_superseded_is_immutable_except_missing_link`; `test_release_adversarial.py::test_every_forbidden_transition_raises[29]`, `::test_published_manifest_rejects_every_field_mutation[13]` | evidenced |
| compatibility fixtures | див. 1.1 останній рядок | evidenced |
| Жодного I/O у `src/collector/contracts/**` | `test_no_io_imports.py::test_import_contracts_does_not_load_io_libraries` (subprocess `python -I`, `httpx/sqlalchemy/pymongo`), `::test_contract_sources_have_no_io_or_network_imports`; `test_adversarial_export.py::test_import_contracts_reads_no_data_files_and_no_lazy_deps` | evidenced |
| `docs/contracts.md` описує процедуру змін і ownership | `docs/contracts.md` §1 (ownership, dependency-запит, заборона чужих enum), §3.1 (minor), §3.2 (major + migration/reprojection plan + fixture + compat test), §3.3 (drift) | evidenced |

### 1.3. Вимоги 1–10 картки (кожен підпункт)

| # | Підпункт | Доказ | Статус |
|---|---|---|---|
| 1 | `EntityId` = UUIDv7, генератор, тест монотонності | `identity.py:53-111`; `test_identity.py::test_new_entity_id_is_uuid7_and_monotonic_within_process`, `::test_uuid7_generator_thread_safe_unique` | evidenced |
| 1 | `SourceIdentity` + loader read-only кешований; 70 ID, без дублікатів | `identity.py:126-132`; `source_registry.py:114-140` (`lru_cache`); `test_identity.py::test_registry_has_70_unique_valid_ids`, `::test_registry_loader_is_cached_and_env_override`; `test_adversarial_export.py::test_registry_is_read_once_on_first_validation_only` | evidenced |
| 1 | `identity_hash_v1` versioned + документ + golden | `identity.py:169-202` (`v1:<sha256>`); `docs/contracts.md` §4.3; `identity_golden.json`; `test_identity.py::test_identity_hash_golden` | evidenced |
| 1 | Fetch idempotency key + golden; `NormalizedUrl` без tracking, original збережено | `identity.py:207-234`, `:135-151`; `test_identity.py::test_fetch_and_translation_keys_golden`, `::test_normalized_url_rejects_tracking_params_keeps_original`, `::test_planned_at_bucket_floors_to_bucket` | evidenced |
| 1 | Translation idempotency key | `identity.py:237-252`; `test_identity.py::test_fetch_and_translation_keys_golden` | evidenced |
| 1 | Raw object key `sha256(body)` | `identity.py:255-257`; golden у тому ж тесті | evidenced |
| 2 | П'ять `StrEnum` з точними значеннями; `map_research_access_state()`; заборона в `docs/contracts.md` | `enums.py:12-88`; `docs/contracts.md` §1, §5; `test_enums.py` (5); зонд | evidenced |
| 3 | `SourceTime` (6 полів, precision enum) | `temporal.py:42-50`; `enums.py:89-99` (`TimePrecision` = `second\|minute\|hour\|day\|month\|year\|unknown` дослівно); snapshot `entity_time.v1.json` `$defs/TimePrecision` | evidenced |
| 3 | `SystemTime` обов'язкові aware UTC; відхилення (не нормалізація) | `temporal.py:26-65`; `test_temporal.py::test_system_time_rejects_naive_and_non_utc`; `test_adversarial.py::test_non_utc_aware_datetime_is_rejected_not_normalized` | evidenced |
| 3 | `EffectiveTime` + `derive_effective_time()`; `fetched_at` не basis (R-43) | `temporal.py:111-150`; `enums.py:102-108` (`EffectiveAtBasis` = `source_event\|source_updated\|observed`); `test_temporal.py::test_derive_effective_time_never_uses_fetched_at`, `::test_effective_time_requires_inferred_flag_when_basis_not_source_event` | evidenced |
| 3 | `BitemporalInterval` + `build_intervals`; late arrival / backdated / relisting | `temporal.py:153-242`; `test_temporal.py:144-181` (три сценарії, обидві осі окремо), `::test_intervals_input_order_irrelevant_and_versions_unique` | evidenced |
| 4 | `Money(amount_minor: int, currency: str[3])` без float | `values.py:29-36` (`strict=True`, `^[A-Z]{3}$`); `test_values.py::test_money_rejects_float_and_decimal_string`; `test_code_review_fixes.py::test_money_strict_rejects_bool_str_float_decimal` | evidenced |
| 4 | `ContactValue`: E.164 (UA), e-mail lowercase + IDNA, raw збережено | `values.py:39-108`; `test_values.py::test_phone_to_e164_default_region_ua`, `::test_email_lowercase_idna`; `test_adversarial.py::test_phone_normalization_default_ua_and_invalid_keeps_raw[7]` | evidenced |
| 4 | `MeasuredValue(value, unit, raw_value, raw_unit)` | `values.py:111-117`; `test_values.py::test_measured_value_keeps_raw` | evidenced |
| 5 | `ArtifactRef`, `RawArtifactRef` (+HTTP), `NormalizedArtifactRef` (+entity/domain/parser/lineage) | `artifacts.py:29-71`; `test_artifacts.py::test_raw_artifact_ref_carries_http_metadata_raw`, `::test_normalized_artifact_ref_lineage_required` | evidenced |
| 5 | `UploadClaim` + `can_commit(claim, now, generation)` | `artifacts.py:74-95`; `test_artifacts.py::test_can_commit_requires_live_lease_same_generation_and_leased_status`, `::test_can_commit_rejects_naive_or_non_utc_now`; `test_adversarial.py::test_can_commit_expired_lease_boundary_stale_and_equal_generation` | evidenced |
| 6 | `ProjectionCommand` (усі поля картки; `schema_version` картки = `target_schema_version` + власний `schema_version`) | `projection.py:29-52`; `test_projection.py::test_projection_command_is_not_a_domain_event` | evidenced |
| 6 | `AppliedProjectionReceipt` (усі поля картки) | `projection.py:55-122`; `test_projection.py::test_receipt_hash_size_and_version_invariants`, `::test_receipt_with_event_artifact_instead_of_bytes`, `::test_receipt_json_round_trip_keeps_bytes` | evidenced |
| 6 | `ProjectionAcknowledgement` | `projection.py:130-166` (`from_receipt` без reserialization); fixture `projection_acknowledgement.v1.0.json` | evidenced |
| 6 | `DomainChangedEvent` + canonical serialization + `encode_event` + round-trip + ліміт 256 KiB | `events.py:40-107`; `canonical.py` (sorted keys, `separators=(",",":")`, NFC, `…ffffffZ`, Decimal без експоненти, base64); `test_events.py` (10), у т.ч. `::test_canonical_independent_of_process_locale` | evidenced |
| 6 | `should_emit_domain_changed = applied_to_current and state_changed` | `projection.py:125-127`; `test_projection.py::test_should_emit_domain_changed_rule[4]`, `::test_receipt_event_descriptor_present_iff_applied_and_changed`; mutation M1 | evidenced |
| 7 | `CurrentDocumentBase` за YAML §9.2 + `EntityKind`; `state_hash` versioned, детермінований | `current.py:60-109`; `enums.py:120-127`; звірка поле-за-полем — розділ 1.4; `test_current.py::test_current_document_matches_spec_9_2_shape` | evidenced |
| 8 | `ResolutionDecision` (усі поля) + `project_groups` + тест replay | `resolution.py:30-79`, `:150-211`; `test_resolution.py::test_merge_manual_block_unmerge_replay` | evidenced |
| 9 | `ReleaseManifest` (усі поля §9.9), `ReleasePart`, `ReleaseState` + переходи, `published` immutable | `release.py`; `test_release.py::test_manifest_has_all_spec_9_9_fields` (перелік полів у тесті = список §9.9), `::test_published_is_immutable` | evidenced |
| 10 | `schema_version` (`major.minor`); `collector contracts export`; CI drift-test | `_base.py:25-121`; `cli.py:115-143` (`contracts export [--check] [--output]`); `.github/workflows/ci.yml:56-57`; `test_schema_snapshots.py` | evidenced (виняток `CurrentDocumentBase` int — розділ 7) |
| 10 | Compatibility test (fixtures попередньої minor; видалення/перейменування без major падає) | `test_compatibility.py:31-71` (fixture v1.0 = поточна minor: попередньої ще немає; механізм примусовий через `test_every_versioned_document_has_a_fixture`), `:82-136` (`check_compatibility`: видалене/перейменоване поле, нове required, зміна типу, enum/`$defs`) | evidenced |
| 10 | `docs/contracts.md`: minor, major (+migration/reprojection plan, fixture, compat test), ownership, dependency-процедура | `docs/contracts.md` §1, §3.1, §3.2, §3.3 | evidenced |

### 1.4. §9.2 — мінімальний контракт current document, звірка поле-за-полем з YAML

Джерело: `current.py:60-89`, `temporal.py:68-82`, snapshot `schemas/mongo/current_document_base.v1.json` (перевірено зондом: `required`, `properties`, `$defs`).

| YAML §9.2 | Модель / snapshot | Статус |
|---|---|---|
| `_id: UUID` | `id: EntityId = Field(alias="_id")`; snapshot `required` містить `_id`; `entity_uuid` property | evidenced |
| `schema_version: 1` | `schema_version: int = Field(default=1, ge=1, strict=True)`; snapshot `{"type":"integer","default":1,"minimum":1}`; validator major == `contract_version` major; `test_current.py::test_current_document_matches_spec_9_2_shape` (відхиляє `2` і `"1.0"`) | evidenced |
| `entity_kind: catalog_item \| catalog_offer \| vehicle_listing \| seller` | `EntityKind`; snapshot enum `['catalog_item','catalog_offer','vehicle_listing','seller']` | evidenced |
| `source: {source_id, source_item_id, canonical_url}` | `SourceRef(SourceIdentity)` + `canonical_url`; snapshot `$defs/SourceRef` = 3 поля | evidenced |
| `identity_hash: string` | `IdentityHash` (`^v[1-9][0-9]*:[0-9a-f]{64}$`) | evidenced |
| `projection_version: int64` monotonic | `int = Field(ge=1)` (монотонність — persistence WP-01A/01B) | evidenced |
| `state_hash: string` | `StateHash` + validator перерахунку | evidenced |
| `core`, `attributes`, `latest_state: object` | `JsonObject` (strict JSON, CR-01); `core` required, інші `default_factory=dict` | evidenced |
| `lineage: {fetch_id, raw_sha256, parser_version, projection_task_id}` | `Lineage`; snapshot `$defs/Lineage` = 4 поля | evidenced |
| `time.source_event_at`, `time.source_updated_at: datetime \| null` | `EntityTime` `UtcDatetime \| None` | evidenced |
| `time.observed_at`, `time.fetched_at`, `time.ingested_at: datetime` | `EntityTime` required; snapshot `$defs/EntityTime.required = [fetched_at, ingested_at, observed_at]` | evidenced |
| `time.source_timezone_raw: string \| null` | `str \| None` | evidenced |
| `time.source_time_precision: second \| minute \| hour \| day \| month \| year \| unknown` | `TimePrecision`; snapshot enum дослівно | evidenced |
| `time.source_time_inferred: boolean` | `bool` | evidenced |
| `first_seen_at`, `last_seen_at: datetime` | `UtcDatetime`; validator `last_seen_at >= first_seen_at` | evidenced |

Зайвих полів немає (`extra="forbid"`); `EntityTime` — рівно 8 полів YAML.

### 1.5. §7.3 — shapes, ліміт, правило emit

| Вимога §7.3 | Доказ | Статус |
|---|---|---|
| `projection.command` — внутрішня, не публікується (R-30) | `projection.py:30` docstring; `test_projection.py::test_projection_command_is_not_a_domain_event` (`not isinstance(command, DomainChangedEvent)`, немає `event_id`) | evidenced |
| receipt: PK task, entity/version, target/document, `applied_to_current`, `state_changed`, previous/result version+hash, ready event bytes/media type/SHA-256 або artifact ref, committed/cluster time | `projection.py:66-85` — усі поля | evidenced |
| ack: task, entity/version, receipt id/cluster time, `applied_to_current`, `state_changed`, acknowledged time, result/event hashes | `projection.py:136-145` | evidenced |
| `domain.changed` має `event_id`, `aggregate_id`, `aggregate_version`, `event_type`, `payload_schema_version` | `events.py:50-54` | evidenced |
| Лише `applied_to_current AND state_changed` → event | `projection.py:125-127`; receipt validator `:89-102` (descriptor iff emit) | evidenced |
| Ready UTF-8 bytes + media type + SHA-256 у receipt, byte-equivalent replay без reserialization | `events.py:88-102`; `projection.py:103-112` (`sha256(event_bytes) == event_sha256`); `ProjectionAcknowledgement.from_receipt`; `test_projection.py::test_receipt_json_round_trip_keeps_bytes` | evidenced |
| Максимум inline 256 KiB, інакше artifact | `events.py:23` (`262144`), `:95-96`; `projection.py:104-106`; `test_adversarial.py::test_encode_event_exactly_256_kib_ok_and_plus_one_rejected` | evidenced |
| Out-of-order: старіша task не перезаписує новіший current | `projection.py:113-118`; `test_projection.py::test_receipt_out_of_order_task_does_not_lower_current` (контракт; compare-and-set — WP-01B) | evidenced (контрактний рівень) |

### 1.6. §9.3 п.1–8, §9.4, §9.6, §9.8, §9.9

| Пункт ТЗ | Доказ | Статус |
|---|---|---|
| §9.3 п.1 natural key `(source_id, source_item_id)` | `SourceIdentity` | evidenced |
| §9.3 п.2 versioned `identity_hash`, алгоритм і поля документовані | `identity_hash_v1`; `docs/contracts.md` §4.3 | evidenced |
| §9.3 п.3 fetch key | `fetch_idempotency_key` (4 складники) | evidenced |
| §9.3 п.4 raw key `sha256(body)` | `raw_object_key` | evidenced |
| §9.3 п.5 version record + observation лише при зміні/heartbeat; `idempotency_key` при replay | `ObservationReason` (`changed\|heartbeat`) `enums.py:151-156`; receipt `state_changed`; сама логіка projector — WP-01B | partial (контракт enum/receipt; поведінка — WP-01B) |
| §9.3 п.6 merge без score/provenance заборонений | `resolution.py:66-68` (`merge` без `score` → `ValidationError`), `evidence_refs`, `rule_or_model_version`, `actor` | evidenced |
| §9.3 п.7 translation key | `translation_idempotency_key` (5 складників) | evidenced |
| §9.3 п.8 E.164 / lowercase+IDNA, raw збережено | `values.py:71-108` | evidenced |
| §9.4 optional = minor; remove/rename/type = major; compatibility test; незалежність від порядку полів/локалі | `_base.py:86-121`; `docs/contracts.md` §3; `test_compatibility.py`; `test_events.py::test_canonical_independent_of_process_locale` | evidenced |
| §9.6 дві осі; `null` source time не заповнюється `fetched_at`; `effective_at_basis` enum; precision enum дослівно | розділ 1.3 вимога 3 | evidenced |
| §9.6 «зберігаються вихідний текст часу, timezone/offset, declared locale і precision» | `SourceTime`: `source_time_raw_text`, `source_timezone_raw`, `source_time_precision` — є; **declared locale — поля немає** | partial (знахідка 1, low) |
| §9.6 напіввідкриті `[valid_from, valid_to)`, `[known_from, known_to)`, `null` = відкрито; `as_of_valid_time`/`as_known_at` | `BitemporalInterval` (+`contains(as_of_valid_time, as_known_at)`), `build_intervals` | evidenced |
| §9.8 decision contract (усі поля) | `resolution.py:36-53`: `decision_id`, `decision_version`, `action` (5 значень дослівно), `member_entity_ids`, `canonical_group_id`, `evidence_refs`, `feature_values`, `score`, `calibration_version`, `rule_or_model_version`, `actor`, `reason`, `effective_at`, `recorded_at`, `supersedes_decision_id` | evidenced |
| §9.8 `manual_block` забороняє auto-merge до superseding decision; source records не зливаються | `project_groups` (`blocked` перевірка; `manual_link` не блокується); `test_resolution.py::test_superseding_block_allows_new_merge_but_history_is_kept` | evidenced |
| §9.8 кожен export містить resolution snapshot/version | `ReleaseWatermark.resolution_snapshot_id`, `ComponentVersions.resolution_version` | evidenced |
| §9.9 стани `draft\|building\|validating\|published\|failed\|superseded` | `ReleaseState` `enums.py:170-179` дослівно | evidenced |
| §9.9 manifest: усі 7 груп полів | `release.py:119-152`; `test_release.py::test_manifest_has_all_spec_9_9_fields` (25 полів); `ReleasePart` 10 полів (uri, format, partition, row_count, min/max effective/system, size_bytes, sha256) | evidenced |
| §9.9 published не перезаписується | `validate_manifest_update` (`_IMMUTABLE_STATES` = published+superseded) | evidenced |

### 1.7. §16.3 дотичні пункти (контрактний рівень)

| Пункт §16.3 | Доказ | Статус |
|---|---|---|
| late-arriving/backdated fixtures зберігають source та system time окремо і дають правильні інтервали | `test_temporal.py:144-181` | evidenced (контракт; fixtures у release — WP-11A) |
| merge/unmerge replay не змінює source records, відтворює попередній release, створює новий snapshot | `project_groups` чиста (source records не на вході); `ResolutionSnapshot`; `superseded` immutable | partial (відтворення release — WP-11A) |
| доставка `3, 1, 2` залишає current на 3; один receipt/ack на task | receipt validator out-of-order; `ProjectionAcknowledgement.from_receipt` 1:1 | partial (integration — WP-01B) |
| повторний parse/projection не створює дублікатів | ключі §9.3 п.3/4/7; `(entity_uuid, projection_version)` у receipt | partial (integration — WP-01A/01B) |

## 2. DoD §18 — дев'ять пунктів

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| 1 | Один WP, без сторонніх змін | diff: owned files + 4 файли поза ними (`src/collector/cli.py` — дозволено картці лише subcommand `contracts`: так; `src/collector/core/version.py`, `tests/unit/test_cli*.py`, `.github/workflows/ci.yml` — approved dependency change `docs/plan/deps/WP-01C-to-WP-00.md` стан `resolved`); `pyproject.toml` — лише залежності `idna`/`phonenumbers`/`pyyaml` (дозволено) | evidenced |
| 2 | formatter, lint, types, unit/contract tests | розділ «Власна верифікація» | evidenced |
| 3 | Зміна схеми має migration і compatibility evidence | нові контракти без персистенції — migration не застосовна (WP-01A/01B); compatibility: `test_compatibility.py`, fixtures, `check_compatibility` | evidenced (migration — not applicable: споживачів ще немає) |
| 4 | Зміна timestamp/matching/release contract має temporal/replay/reproducibility evidence | temporal: `test_temporal.py` (17) + adversarial (7); replay: `test_resolution.py` (5) + adversarial (6) + `test_code_review_fixes.py` (5); release: `test_release.py` (10) + `test_release_adversarial.py` (39); mutation M1–M6 | evidenced |
| 5 | Новий адаптер має manifest/fixtures/… | адаптерів немає | not applicable |
| 6 | Документація, метрики, runbook оновлені | `docs/contracts.md` (257 рядків), `schemas/README.md`, docstrings усіх моделей (потрапляють у `description` snapshot-ів); метрики/runbook — контракти без runtime; ADR-0003 — етап 5 за карткою («Docs (етап 5)») | partial (ADR-0003 — етап 5, заплановано карткою) |
| 7 | Secret scan чистий; публічні контакти не у fixtures з приватних джерел | `pre-commit run gitleaks --all-files` → Passed; fixtures контактів — синтетичні (`example.com`, `+380501234567`, `Пошта.укр`), реєстр джерел — публічні медіа | evidenced |
| 8 | Findings `fixed` / `accepted with owner/date` / `not applicable` | `implementation.md` «Відповіді на код-рев'ю»: CR-01…CR-09 fixed, CR-10 accepted (WP-01C, 2026-09-22), 2 спрощення accepted (WP-01C, 2026-09-22), 5 fixed, 1 n/a; T-01/T-02/T-06 fixed; T-03 закрито тестом тестувальника; T-04→CR-08 fixed; T-05 info (docstring); T-07 етап 5. Перевірка в коді — розділ 6 | evidenced |
| 9 | PR злитий після CI та required review; SHA зафіксовано | коміти `dc60f45..8f38eb4` зафіксовані у звіті; merge — після цього етапу | partial (merge — поза етапом 4) |

## 3. Додаток C — рядки, які покриває WP-01C

| Ціль | Вимоги | Що покрито | Доказ | Статус |
|---|---|---|---|---|
| Доказовість і відтворення | FR-005, FR-006, FR-011, §9 | Lineage contract (`Lineage`, `NormalizedArtifactRef` raw/fetch/parser lineage, `RawArtifactRef` HTTP metadata), `raw_object_key`, `UploadClaim`/`can_commit` fencing, identity/idempotency keys | `current.py:51-57`, `artifacts.py`, `identity.py`; `test_artifacts.py`, `test_identity.py` | evidenced (контракти; lineage integration/replay E2E — WP-01A/01B/14) |
| Часова коректність | FR-024, §9.6 | naive/offset відхилення, nullable source time, precision enum, `fetched_at` ≠ source time, `effective_at_basis`, bitemporal інтервали late-arrival/backdated/relisting | `temporal.py`; `test_temporal.py` (17), `test_adversarial.py` розділ 3 (7); mutation M2/M3/M5 | evidenced (declared locale — знахідка 1, low) |
| Оборотний matching | FR-026, §9.8 | `ResolutionDecision` (усі поля), `project_groups` replay merge→block→unmerge→replay, supersede-семантика, immutable source records (чиста функція), `ResolutionSnapshot` | `resolution.py`; `test_resolution.py`, `test_adversarial.py` розділ 8, `test_code_review_fixes.py:135-204` | evidenced (контракт; resolution worker/release replay — WP-07/09/11A) |
| Відтворювані дослідження | FR-027, FR-029, §9.9 | `ReleaseManifest` (усі поля §9.9), `ReleasePart` hashes/counts/times, state machine, `published`/`superseded` immutable, `partition_index_sha256`, `config_hash`, `image_digests`, `git_commit`, `build_command` | `release.py`; `test_release.py`, `test_release_adversarial.py` (39) | evidenced (контракт; identical rebuild/DuckDB — WP-11A/11B) |
| Узгодженість двох БД | FR-020—FR-023, §7.3—§7.4, §9 | `ProjectionCommand` ≠ `DomainChangedEvent`, receipt з event bytes/sha256, `ProjectionAcknowledgement.from_receipt` без reserialization, `should_emit_domain_changed`, out-of-order інваріант, `CurrentDocumentBase` §9.2 дослівно, `state_hash` v1 strict-JSON | `projection.py`, `events.py`, `canonical.py`, `current.py`; `test_projection.py`, `test_events.py`, `test_current.py` | evidenced (контракт; crash-window replay/reconciliation — WP-01B) |
| Незалежна реалізація | §17, §18 | Єдиний owner shared contracts; dependency-процедура; JSON Schema snapshots + drift-check у CI; `contracts` CLI через approved dependency | `docs/contracts.md` §1; `schema_export.py`; `.github/workflows/ci.yml:56-57`; `docs/plan/deps/WP-01C-to-WP-00.md` | evidenced |

## 4. Регресія REVIEW.md — де втілено в коді/тестах

| R | Суть | Втілення в коді | Тест | Статус |
|---|---|---|---|---|
| R-18 | Чотири незалежні закриті осі стану + `fetch_outcome` + mapping research | `enums.py:12-88` (5 `@unique StrEnum`), `:182-188` `STATE_AXES`; `docs/contracts.md` §5 заборона інших enum | `test_enums.py::test_state_axes_have_exact_values[5]`, `::test_exactly_five_state_axes`, `::test_map_research_access_state[9]`; `test_adversarial.py::test_models_reject_research_synonyms_but_mapping_accepts[7]` | evidenced |
| R-20 | Nullable body для `metadata_only` | `ContentAccess.METADATA_ONLY` `enums.py:48`; `JsonValue` допускає `None` (`_base.py:45-58`) — `core.body_original_text: null` валідний | `test_current.py:110` `::test_current_document_metadata_only_body_nullable` | partial (сам `NewsArticle`/`body_original_*` contract — поза scope картки: news article version tables → WP-01A, §5.4) |
| R-30 | command ≠ event | `projection.py:29-52` (`ProjectionCommand` без `event_id`, docstring «не публічна подія»); `events.py:40` окремий `DomainChangedEvent`; `docs/contracts.md` §9 | `test_projection.py::test_projection_command_is_not_a_domain_event` | evidenced |
| R-36 | Mandatory projection version; event потребує apply і state change | `projection.py:37` (`projection_version ge=1` required у command), `:68`, `:75` (`result_version`), `:113-118`; `should_emit_domain_changed` | `test_projection.py::test_should_emit_domain_changed_rule[4]`, `::test_receipt_out_of_order_task_does_not_lower_current`; mutation M1 | evidenced |
| R-37 | Receipt атомарно зберігає previous/result hash і event descriptor з hash | `projection.py:73-81` (`previous_version/hash`, `result_version/hash`, `event_id/bytes/media_type/sha256/artifact`), `:103-112` (sha256 == bytes); `ProjectionAcknowledgement.from_receipt` | `test_projection.py::test_receipt_hash_size_and_version_invariants`, `::test_receipt_json_round_trip_keeps_bytes` | evidenced |
| R-42 | Canonical serialization contract; bytes копіюються без reserialization; artifact ref для великих | `canonical.py` (sorted keys, compact, NFC, UTC `…ffffffZ`, Decimal, base64); `events.py:88-102` `encode_event`; `EVENT_INLINE_LIMIT_BYTES`; `payload_artifact`/`event_artifact` | `test_events.py::test_encode_event_deterministic_and_byte_equivalent_after_round_trip`, `::test_canonical_independent_of_process_locale`, `::test_encode_event_limit_256_kib_and_artifact_alternative`; `test_adversarial.py::test_encode_event_nested_reorder_gives_same_bytes_and_sha` | evidenced |
| R-43 | Source/effective vs system/knowledge осі; precision/timezone/inference; `fetched_at` не підміняє source time | `temporal.py:42-108` (`SourceTime`/`SystemTime`/`EntityTime` validator `:84-94`), `:127-150` (`derive_effective_time`) | `test_temporal.py::test_entity_time_rejects_source_event_equal_to_fetched_at`, `::test_derive_effective_time_never_uses_fetched_at`; `test_adversarial.py::test_entity_time_rejects_source_updated_equal_to_fetched_at` | evidenced |
| R-45 | Versioned decisions з evidence/model/actor/supersedes, manual block, відтворюваний snapshot | `resolution.py:30-79`, `:150-211` | `test_resolution.py::test_merge_manual_block_unmerge_replay`, `::test_superseding_block_allows_new_merge_but_history_is_kept`; `test_code_review_fixes.py::test_supersedes_chain_restores_block_after_double_cancel` | evidenced |
| R-46 | Immutable release lifecycle, watermarks, versions, exclusions, part counts/hashes | `release.py:48-152`, `:206-260`; `enums.py:170-179` | `test_release.py::test_manifest_has_all_spec_9_9_fields`, `::test_state_machine_transitions_table`, `::test_published_is_immutable`; `test_release_adversarial.py::test_published_manifest_rejects_every_field_mutation[13]` | evidenced (reproducibility test збірки — WP-11A) |
| R-49 | Формальний valid/known interval contract; `as_of_valid_time`/`as_known_at` | `temporal.py:153-179` (`BitemporalInterval.contains(as_of_valid_time=, as_known_at=)`), `:211-242` `build_intervals` | `test_temporal.py::test_intervals_backdated_correction_shares_valid_axis` (перевіряє `contains` за обома осями), `::test_intervals_late_arrival_keeps_system_axis_of_earlier_versions`, `::test_bitemporal_interval_validation` | evidenced |

## 5. Q-питання §20

Картка: Q-питань немає. Перевірено, що контракти не зашивають рішень:

| Q | Перевірка | Статус |
|---|---|---|
| Q-012 (auto-merge thresholds) | `grep -ri "threshold\|0\.99\|precision gate" src/collector/contracts docs/contracts.md` → порожньо; `ResolutionDecision` вимагає `score` для `merge` (§9.3 п.6), але не порівнює з порогом; `project_groups` не приймає auto-merge рішень — лише replay уже прийнятих. Default Q-012 («лише deterministic IDs») лишається рішенням WP-07/09 | evidenced (не зашито; safe default не порушено) |
| Q-011 (cadence releases) | `grep -ri "cadence\|weekly\|щотижн"` → порожньо; `ReleaseManifest` не має розкладу; `published` immutable відповідає default Q-011 | evidenced (не зашито) |

## 6. Звірка `fixed` код-рев'ю / тестування з кодом

| ID | Заявлено | Підтверджено в коді |
|---|---|---|
| CR-01 | strict-JSON `JsonValue`/`JsonObject` | `_base.py:36-58` (`Strict()`, `StrictFiniteFloat`, рекурсивний `type JsonValue`); `current.py:81-85`, `events.py:59`; `test_code_review_fixes.py:43-75` |
| CR-02 | зворотний індекс у `project_groups` | `resolution.py:164` (`groups: dict[UUID, set[UUID]]`), `:180-186` (`affected`); `test_code_review_fixes.py:135` (20 000 merge, поріг часу) |
| CR-03 | NFC-колізія ключів → помилка | `canonical.py:100-103`; `test_code_review_fixes.py:205` |
| CR-04 | casefold-колізія ключів у `identity_hash_v1` → `ValueError` | `identity.py:191-194`; `test_code_review_fixes.py:213` |
| CR-05 | окремі `*_artifact` поля замість union | `release.py:143-150`, validator `:139-147`; `test_code_review_fixes.py:221` |
| CR-06 | `isoformat` замість `strftime` | `canonical.py:53-54`; `test_code_review_fixes.py:250` |
| CR-07 | `re.fullmatch(E164_PATTERN)` | `values.py:68`; `test_code_review_fixes.py:255` |
| CR-08 / T-04 | `Money.amount_minor` strict | `values.py:32-35`; `test_code_review_fixes.py:261` |
| CR-09 | семантика `decision_version`/supersession документована та реалізована | `resolution.py:107-147` (`_effective_decisions`: остання версія, fixed point, цикл → `ValueError`); `docs/contracts.md` §10; `test_code_review_fixes.py:167-204` |
| CR-10 | accepted (owner WP-01C, 2026-09-22) | `temporal.py:221-223` docstring про межу |
| Спрощення (5 fixed) | `NonEmptyStr` у `_base.py:33`; `known_source_ids` `lru_cache` `source_registry.py:128`; `CONTRACTS_VERSION` у `_base.py:25` + `version.py:11` імпортує `_base`; `entity_id_timestamp` через `timedelta`; `_repo_roots` до `pyproject.toml` | так |
| Спрощення (2 accepted) | подвійна перевірка ліміту (`events.py:83-84`, `:95-96`) і дубль умови в `EntityTime` (`temporal.py:91-93`) — аргументи owner-а прийнятні, дата/owner вказані | так |
| T-01 | `superseded` immutable | `release.py:246-260` (`_IMMUTABLE_STATES`, гілка `allowed_link`); `test_release.py:251` — так |
| T-02 | `state`/`release_id` у `**changes` відхиляються | `release.py:222-226`, `{**changes, "state": target}`; `test_release.py:269` — так |
| T-06 | naive `now` у `can_commit` → `ValueError` | `artifacts.py:84-95`; `test_artifacts.py:89` — так |

## 7. Оцінка задекларованих відхилень

| Відхилення | Оцінка | Потрібно |
|---|---|---|
| Група `schemas/common/` поза `schemas/{events,mongo,releases}` картки та Додатка A | Додаток A — «мінімальна структура»; додаткова група не суперечить ТЗ. Value objects (Money, temporal, artifact refs) справді не належать до events/mongo/releases; acceptance вимагає snapshot для кожної публічної моделі. `schemas/README.md` описує групу. Owned files картки формально не включають `schemas/common/**` | Знахідка 2 (low): доповнити картку WP-01C (owned files → `schemas/{common,events,mongo,releases}/**`) — рішення оркестратора; ADR/зміна ТЗ не потрібні |
| `CurrentDocumentBase.schema_version: int` (після виправлення `98add4b`), тоді як вимога 10 картки — `major.minor` для кожної моделі | ТЗ §9.2 дослівно `schema_version: 1` (int); Mongo `$jsonSchema` validators (WP-01B) генеруються з цього snapshot, тож int — правильний вибір. Minor несе `contract_version` класу і `x-contract-version` snapshot-а; задокументовано `docs/contracts.md` §3; compat-тест адаптовано (`test_compatibility.py:50-53`) | Відповідає ТЗ; картка — узагальнення. Нічого не потрібно |
| hidden→visible `contracts` у `collector --help`; правки `tests/unit/test_cli.py`, `test_cli_adversarial.py`, `src/collector/core/version.py`, `ci.yml` | §16.2 — перелік CI-команд («README і CI мають виконувати саме ці команди»), не вичерпний список CLI; жодна команда §16.2 не змінена, drift-check доданий у CI додатково. Оформлено як `docs/plan/deps/WP-01C-to-WP-00.md` (стан `resolved` за рішенням оркестратора); тести WP-00 розширено явним allowlist `FOUNDATION_EXTENSIONS` з коментарем. `version.py` імпортує лише `collector.contracts._base` (без I/O, тест `test_no_io_imports`) | Відповідає ТЗ і §17.1 (dependency-задача до owner-а). Нічого не потрібно; пункт Dockerfile (`COPY docs/research/source-registry.yaml`) — відкритий для WP-00 PR2 (знахідка 4, info) |
| Правки чужих тестів: зняття `xfail` T-01/T-02 у `test_release_adversarial.py` (тестувальник); адаптація 2 тестів у `test_adversarial.py` під CR-01 | Зняття strict-xfail після фіксу — очікуваний крок (сам тестувальник це прописав); адаптація під CR-01 обумовлена обов'язковою medium-знахідкою, задекларована в `implementation.md`, стара поведінка (Decimal/datetime у payload) свідомо змінена на strict-JSON | Прийнятно; ADR не потрібен |

## 8. Знахідки пострев'ю

| # | Severity | Місце | Знахідка | Рекомендація |
|---|---|---|---|---|
| 1 | low | `src/collector/contracts/temporal.py:42-50` (`SourceTime`) | §9.6: «Зберігаються вихідний текст часу, timezone/offset, **declared locale** і precision». `source_time_raw_text`, `source_timezone_raw`, `source_time_precision` є; поля для declared locale немає. Картка (вимога 3) його не перелічує, тому acceptance не блокує, але ТЗ це вимагає для shared temporal contract | Додати `source_locale_raw: str \| None = None` у `SourceTime` (minor-сумісно, до появи споживачів — без підняття версії) або зафіксувати в картці/ТЗ, що locale несе `attributes` адаптера. Owner WP-01C |
| 2 | low | `docs/plan/cards/WP-01C.md` «Owned files»; `schemas/common/` (18 файлів) | Група `common` поза переліком owned files картки (`schemas/{events,mongo,releases}/**`) | Оркестратор: доповнити картку одним рядком; альтернатива реалізатора (перенести у `events/`) гірша семантично |
| 3 | info | `§5.1` ↔ контракти | Спільні поля §5.1 без shared value object: `media_assets` (URL, тип, caption, width/height/duration, source hash, downloaded URI), `content_hash`, `source_url`, `language`, `country_code`, `title/description_excerpt/full_text`. Вони належать domain `core` (WP-07/09/01A) і не вимагаються карткою | WP-07/WP-09 подають dependency-запит на `MediaAssetRef` value object до WP-01C, щоб уникнути паралельних моделей у двох доменах |
| 4 | info | `docs/plan/deps/WP-01C-to-WP-00.md` п.3; `source_registry.py:96-112` | У Docker image без `docs/research/source-registry.yaml` або `COLLECTOR_SOURCE_REGISTRY` будь-яка валідація `SourceIdentity` кидає `SourceRegistryError` (ризик 2 реалізатора) | WP-00 PR2: `COPY`/`ENV` у `Dockerfile`; WP-14 — smoke `collector` у контейнері з валідацією `SourceIdentity` |
| 5 | info | `docs/plan/cards/WP-01C.md` «Docs (етап 5)» | ADR-0003 «Canonical event serialization» не створено (свідомо; `docs/decisions/` не в owned files) | Етап 5 (docs): матеріал — `docs/contracts.md` §6 + docstring `canonical.py` |
| 6 | info | `README.md:69`, `docs/decisions/0001-foundation-stack.md:56` | Згадки `SCHEMA_VERSION_PLACEHOLDER` застаріли після `version.py` → `CONTRACTS_VERSION` | Етап 5 / owner WP-00 |
| 7 | info | `tests/contract/contracts/test_compatibility.py:33` | `# type: ignore[index]` у тесті; acceptance «`mypy --strict` без ignore» стосується `mypy src` — не порушено | За бажанням: `match = FIXTURE_NAME.match(...); assert match` |

Знахідок critical/high: 0. Жодна знахідка не потребує ADR або зміни ТЗ.

## 9. Підсумок статусів

| Блок | evidenced | partial | missing | not applicable |
|---|---|---|---|---|
| 1. Acceptance (§17.2, картка, §9.2/§7.3/§9.3–9.9, §16.3) | 90 | 5 (§9.3 п.5 поведінка projector — WP-01B; §9.6 declared locale — знахідка 1; §16.3 ×3 — integration/release у WP-01B/11A) | 0 | 0 |
| 2. DoD §18 | 6 | 2 (п.6 ADR-0003 — етап 5; п.9 merge — після етапу 4) | 0 | 1 (п.5 адаптери) |
| 3. Додаток C | 6 | 0 | 0 | 0 |
| 4. REVIEW.md | 9 | 1 (R-20 — `NewsArticle` поза scope картки, WP-01A) | 0 | 0 |
| 5. Q-питання | 2 | 0 | 0 | 0 |

`missing` = 0 для acceptance і DoD; critical/high без `fixed` = 0 → **`accept`**. Умови до merge: жодних блокуючих; знахідку 1 (declared locale) рекомендовано закрити minor-сумісним полем у цьому ж PR або першим follow-up до появи споживачів (WP-01A/01B), знахідку 2 — правкою картки.
