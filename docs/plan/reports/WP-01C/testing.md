# WP-01C — звіт тестування (`wp/01c-contracts`)

| Поле | Значення |
|---|---|
| WP | WP-01C «Shared data contracts» |
| Branch / worktree | `wp/01c-contracts` / `.worktrees/wp-01c`, HEAD реалізації `07193ad`, тести тестувальника `1c6c5da` |
| Картка | `docs/plan/cards/WP-01C.md` (вимоги 1–10, acceptance) |
| Рівні §16.1 за карткою | 2 Contract, 9 Temporal, 1 Unit |
| Середовище | Windows 11, uv, CPython 3.13, pydantic 2.13.5, phonenumbers 9.0.39 |
| Тестувальник | wp-tester (незалежно; `implementation.md` прочитано лише після власного прогону) |
| **Вердикт** | **pass** — усі команди зелені, кожен acceptance-пункт має тест, що падає на зламаному коді; 1 знахідка medium (WP-01C-T-01) зафіксована strict-xfail тестом і має бути виправлена до merge (див. «Знахідки») |

## 1. Команди перевірки та дослівний вивід

Прогін на HEAD `07193ad` (до додавання тестів тестувальника):

```text
$ uv sync --frozen
Checked 46 packages in 27ms
exit=0

$ uv run ruff check .
All checks passed!
exit=0

$ uv run ruff format --check .
94 files already formatted
exit=0

$ uv run mypy src
Success: no issues found in 38 source files
exit=0

$ uv run pytest -m "not live"
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
================= 334 passed, 1 skipped, 6 warnings in 9.04s ==================
exit=0

$ uv run collector contracts export --check
schemas up to date: schemas
exit=0
```

Skip і 6 warnings — успадковані від WP-00 PR1 (`pytest-socket` на Windows), не стосуються WP-01C.
`mypy --strict` (`[tool.mypy] strict = true`) — `grep -rn "type: ignore" src/collector/contracts` порожній.

Повторний прогін після додавання тестів (HEAD `1c6c5da`):

```text
$ uv run ruff check .            → All checks passed!
$ uv run ruff format --check .   → 98 files already formatted
$ uv run mypy src                → Success: no issues found in 38 source files
$ uv run pytest -m "not live"
XFAIL tests/unit/contracts/test_release_adversarial.py::test_superseded_manifest_stays_immutable_like_published - WP-01C-T-01
XFAIL tests/unit/contracts/test_release_adversarial.py::test_transition_release_cannot_override_target_state_via_changes - WP-01C-T-02
449 passed, 1 skipped, 2 xfailed, 6 warnings in 12.02s
$ uv run collector contracts export --check → schemas up to date: schemas
```

Стабільність: `tests/unit/contracts tests/contract` виконано тричі поспіль — `322 passed, 2 xfailed` кожного разу; flaky не виявлено.

## 2. Acceptance-пункт → тест → результат

| Acceptance (картка) | Тест | Результат |
|---|---|---|
| Усі команди зелені; `mypy --strict` без ignore | розділ 1 | pass |
| Snapshot JSON Schema для кожної публічної моделі; `--check` без drift | `test_schema_snapshots.py::test_repository_snapshots_have_no_drift`, `test_snapshot_exists_and_is_self_consistent[32]`; **+** `test_adversarial_export.py::test_every_public_model_has_exactly_one_snapshot_on_disk` (32 файли = `EXPORTED_CONTRACTS`), `test_single_snapshot_tamper_turns_check_red[5 мутацій]` (підміна одного snapshot → `check_schemas` = 1 drift, CLI exit 1), `test_whitespace_only_change_is_still_drift` | pass |
| identity hash golden | `test_identity.py::test_identity_hash_golden`, `test_fetch_and_translation_keys_golden`; **+** `test_adversarial.py::test_state_hash_golden_for_empty_blocks` (точні bytes payload `{"attributes":{},"core":{},"latest_state":{},"v":1}`) | pass |
| Усі 70 `source_id` валідні, без дублікатів | `test_identity.py::test_registry_has_70_unique_valid_ids`, `test_source_identity_accepts_registered_ids[70]`, `test_registry_rejects_duplicates`; **+** `test_adversarial.py::test_source_identity_rejects_unknown_case_and_padding[5]`, `test_source_identity_rejects_blank_item_id[3]`, `test_source_identity_item_id_is_stripped_and_case_preserved`; `test_adversarial_export.py::test_registry_is_read_once_on_first_validation_only` (файл читається 1 раз на 51 валідацію) | pass |
| П'ять enum §5.5 з точними значеннями + mapping research | `test_enums.py::test_state_axes_have_exact_values[5]`, `test_map_research_access_state[9]`; **+** `test_adversarial.py::test_state_axes_value_sets_equal_spec_verbatim` (рівність множин, перетин осей лише `unknown`), `test_models_reject_research_synonyms_but_mapping_accepts[7]` (`"free"`, `"body_unavailable"`, `"FULL"` відхиляються `TypeAdapter`/конструктором, mapping приймає), `test_mapping_does_not_accept_fetch_outcome_labels_except_retryable` | pass |
| naive datetime відхиляється | `test_temporal.py::test_system_time_rejects_naive_and_non_utc[4]`, `test_source_time_rejects_naive_but_allows_null`; **+** `test_adversarial.py::test_non_utc_aware_datetime_is_rejected_not_normalized` (`+03:00` → **відхилення**, не нормалізація — узгоджено з карткою; для `SystemTime`, `SourceTime`, `BitemporalInterval`, JSON-вхід) | pass |
| `fetched_at` не потрапляє у `source_event_at` | `test_temporal.py::test_entity_time_rejects_source_event_equal_to_fetched_at`, `test_derive_effective_time_never_uses_fetched_at`, `test_current.py::test_current_document_time_block_rejects_fetched_at_as_source_event`; **+** `test_adversarial.py::test_entity_time_rejects_source_updated_equal_to_fetched_at`, `test_derive_effective_time_with_only_observed_at` | pass |
| Bitemporal: late arrival / backdated / relisting | `test_temporal.py::test_intervals_late_arrival_*`, `test_intervals_backdated_*`, `test_intervals_relisting_*`; **+** `test_adversarial.py::test_build_intervals_single_version_both_upper_bounds_open`, `test_build_intervals_duplicate_effective_at_shares_valid_axis_but_not_known`, `test_build_intervals_unordered_input_equals_sorted_and_output_sorted_by_version`, `test_build_intervals_duplicate_ingested_at_shares_known_axis` | pass |
| `encode_event` byte-equivalent і ліміт 256 KiB | `test_events.py::test_encode_event_deterministic_and_byte_equivalent_after_round_trip`, `test_encode_event_limit_256_kib_and_artifact_alternative`; **+** `test_adversarial.py::test_encode_event_nested_reorder_gives_same_bytes_and_sha`, `test_encode_event_list_order_is_significant`, `test_encode_event_datetime_with_and_without_microseconds`, `test_encode_event_decimal_vs_float_are_distinct_and_round_trip_stable`, `test_encode_event_unicode_nfd_is_normalized_to_nfc`, `test_encode_event_exactly_256_kib_ok_and_plus_one_rejected` (рівно 262 144 B — ok; +1 B — `EventTooLargeError(size=262145)`; ліміт у bytes, не символах) | pass |
| `state_hash` незалежний від порядку полів | `test_current.py::test_state_hash_independent_of_field_order_and_unicode_form`; **+** `test_adversarial.py::test_state_hash_nested_dict_reorder_same_but_list_order_significant`, `test_state_hash_unicode_nfd_equals_nfc_for_values_and_keys` (закриває вакуумний NFD-кейс, див. T-03), `test_state_hash_none_is_not_the_same_as_missing_key` | pass |
| `should_emit_domain_changed` | `test_projection.py::test_should_emit_domain_changed_rule[4]`, `test_receipt_event_descriptor_present_iff_applied_and_changed` (mutation M1) | pass |
| Resolution replay | `test_resolution.py::test_merge_manual_block_unmerge_replay` (+3); **+** `test_adversarial.py::test_unmerge_without_prior_merge_is_noop_but_applied`, `test_two_manual_blocks_on_same_pair_are_idempotent`, `test_supersedes_unknown_decision_is_tolerated_and_reported`, `test_replay_order_is_effective_at_not_list_order_nor_recorded_at`, `test_merge_unmerge_merge_replay_ends_in_new_group`, `test_merge_across_partially_blocked_group_is_skipped_whole` | pass |
| Release state machine, `published` immutable | `test_release.py::test_state_machine_transitions_table`, `test_published_is_immutable`; **+** `test_release_adversarial.py::test_every_forbidden_transition_raises[29]` (усі 29 недозволених пар через `transition_release`), `test_transition_table_has_29_forbidden_and_7_allowed_pairs`, `test_published_manifest_rejects_every_field_mutation[13]`, `test_published_manifest_frozen_and_validate_json_round_trip_identical`, `test_direct_published_manifest_needs_all_evidence` | pass (published); **T-01/T-02 xfail** для superseded / `state` через `**changes` |
| Compatibility fixtures | `test_compatibility.py::test_fixture_validates_against_current_model[7]`, `test_model_rejects_other_major[6]`, `test_repository_snapshot_is_compatible_with_current_model[32]`, `test_removing_field_from_real_model_snapshot_is_detected` | pass |
| Жодного I/O у `collector.contracts` (імпорт не тягне httpx/sqlalchemy/pymongo) | `test_no_io_imports.py` (2); **+** `test_adversarial_export.py::test_import_contracts_reads_no_data_files_and_no_lazy_deps` (subprocess `python -I` зі spy на `open`/`Path.open`: імпорт усіх модулів пакета не читає жодного файлу даних — лише `*.dist-info/entry_points.txt` від `importlib.metadata`; `yaml`/`phonenumbers`/`idna` не завантажуються) | pass |
| `docs/contracts.md` описує процедуру змін і ownership | розділи 1 (ownership, dependency-запит), 3.1/3.2/3.3 (minor/major/drift), 4.3 (алгоритм identity_hash_v1), 6 (canonical) — перевірено читанням; збігається з кодом | pass |
| Вимога 4: `Money` без float, E.164 (default UA), e-mail lowercase + IDNA, raw збережено | `test_values.py` (7); **+** `test_adversarial.py::test_phone_normalization_default_ua_and_invalid_keeps_raw[7]`, `test_email_normalization_idna_lowercase_and_invalid[7]` (`Іван@Пошта.УКР` → `іван@xn--80a1acn3a.xn--j1amh`), `test_contact_value_direct_construction_validates_normalized_shape`, `test_money_rejects_float_python_and_json[3]`, `test_money_negative_allowed_currency_lowercase_rejected` | pass |
| Вимога 5: `can_commit` | `test_artifacts.py::test_can_commit_requires_live_lease_same_generation_and_leased_status`; **+** `test_adversarial.py::test_can_commit_expired_lease_boundary_stale_and_equal_generation` (`lease_expires_at == now` → False; generation 4/6 при claim 5 → False; статуси committed/released/expired → False) | pass |

## 3. Рівень §16.1 → тести

| Рівень | Тести |
|---|---|
| 1 Unit (identity hash, money, E.164/e-mail) | `tests/unit/contracts/test_identity.py`, `test_values.py`, `test_artifacts.py`, `test_current.py`, `test_enums.py`, `test_events.py`, `test_projection.py`, `test_resolution.py`, `test_release.py`, `test_cli_contracts.py`; **+** `test_adversarial.py` (розділи 1, 2, 4, 5, 6, 7, 8), `test_release_adversarial.py` |
| 2 Contract (schema snapshots + compatibility) | `tests/contract/contracts/test_schema_snapshots.py`, `test_compatibility.py`, `test_no_io_imports.py`; **+** `test_adversarial_export.py` (9) |
| 9 Temporal (nullable/precision/timezone, late arrival, backdated, relisting, обидві осі) | `tests/unit/contracts/test_temporal.py` (17); **+** `test_adversarial.py` розділ 3 (7) |
| 10 Resolution (на рівні контракту) | `test_resolution.py` (5); **+** `test_adversarial.py` розділ 8 (6) |

## 4. Додані тести (коміт `1c6c5da`, 117 тестів; Edit лише в `tests/**`)

| Файл | Що покриває |
|---|---|
| `tests/unit/contracts/test_adversarial.py` (69) | enum §5.5 (множини, синоніми, mapping), temporal (`+03:00`, all-None derive, `build_intervals` single/dup/unordered), `encode_event` (nested reorder, мікросекунди, Decimal/float, NFD→NFC, 256 KiB рівно/+1), `state_hash` (nested reorder, порядок списку, `None` vs відсутній, NFD, golden), `project_groups` (unmerge без merge, подвійний block, dangling supersedes, порядок `effective_at`/`recorded_at`, replay), `SourceIdentity` (невідомий/регістр/порожній), `ContactValue`/`Money`, `can_commit` |
| `tests/unit/contracts/test_release_adversarial.py` (39) | усі 29 заборонених переходів, 13 мутацій published manifest (і через `transition_release(..., SUPERSEDED, **changes)`), frozen, JSON round-trip, direct-published evidence; `xfail(strict=True)` для T-01, T-02 |
| `tests/contract/contracts/test_adversarial_export.py` (9) | підміна одного snapshot (5 видів мутацій) → `--check` exit 1; whitespace-only зміна = drift; 32 файли на диску = реєстр; чистота імпорту (жодного файлу даних); реєстр читається один раз |
| `tests/fixtures/contracts/release_factories.py` | фабрики manifest для release-тестів (pytest `--import-mode=importlib` не дозволяє імпортувати сусідній `test_release.py`) |

Зафіксовані контракти поточної поведінки (не вимоги картки, але їх зміна = свідоме рішення):
`Decimal` → JSON string, `float` → JSON number (`1.0` ≠ `1`); рядки/ключі → NFC (NFD-подія після
round-trip дорівнює NFC-варіанту); порядок списків значущий; `{"a": None}` ≠ `{}`; dangling
`supersedes_decision_id` не ламає replay, а потрапляє у `superseded_decision_ids`;
`build_intervals` при однаковому `ingested_at` ділить known-вісь (обидва `known_to=None`).

## 5. Mutation-перевірка (код тимчасово зламано, тест червоний, `git checkout -- <file>`)

| # | Мутація | Червоні тести | Відновлено |
|---|---|---|---|
| M1 | `projection.py::should_emit_domain_changed`: `and` → `or` | `test_projection.py::test_should_emit_domain_changed_rule[True-False-False]`, `[False-True-False]`, `test_receipt_event_descriptor_present_iff_applied_and_changed` — 3 failed, 67 passed | так |
| M2 | `temporal.py::require_utc`: naive datetime приймається (`return value`) | `test_temporal.py::test_system_time_rejects_naive_and_non_utc[bad0]`, `[2026-09-01T12:00:00]`, `test_source_time_rejects_naive_but_allows_null` — 3 failed, 80 passed | так |
| M3 | `temporal.py::require_utc`: offset ≠ 0 нормалізується `astimezone(UTC)` замість відхилення | `test_temporal.py::test_system_time_rejects_naive_and_non_utc[bad1]`, `[+03:00]`, `test_adversarial.py::test_non_utc_aware_datetime_is_rejected_not_normalized` — 3 failed, 74 passed | так |
| M4 | `release.py::validate_manifest_update`: guard `is not PUBLISHED` → `is not FAILED` (published більше не охороняється) | `test_release.py::test_published_is_immutable`, `test_happy_path…`, `test_release_adversarial.py::test_published_manifest_rejects_every_field_mutation[11 з 13]` — 14 failed, 38 passed | так |
| M5 | `temporal.py::_next_strictly_later`: `>` → `>=` | `test_temporal.py::test_intervals_backdated_correction_shares_valid_axis`, `test_adversarial.py::test_build_intervals_duplicate_effective_at_…`, `…duplicate_ingested_at_…` — 3 failed, 74 passed | так |
| M6 | `canonical.py::to_canonical_value`: прибрано NFC-нормалізацію рядків | `test_events.py::test_canonical_independent_of_key_order_and_unicode_form`, `test_adversarial.py::test_encode_event_unicode_nfd_is_normalized_to_nfc` — 2 failed, 74 passed. **`test_current.py::test_state_hash_independent_of_field_order_and_unicode_form` не впав** → T-03 | так |

`git status --short src` після серії — порожній.

## 6. Знахідки

| ID | Severity | Місце | Опис |
|---|---|---|---|
| WP-01C-T-01 | **medium** | `src/collector/contracts/release.py:208` (`validate_manifest_update`) | Guard `if previous.state is not ReleaseState.PUBLISHED: return` — manifest у стані `superseded` (колишній published) приймає зміну будь-якого поля (`tag`, `parts`, `config_hash`, …). §9.9: «Published dataset release є immutable… Опублікований release не перезаписується»; superseded — термінальний стан published-release, на який посилаються попередні дослідження (§9.8: unmerge «не змінює попередні published releases»). Функція — єдиний контрактний guard для персистенції (WP-01A). Виправлення: `if previous.state not in {PUBLISHED, SUPERSEDED}: return` (для superseded — жодне поле не змінюється, або лише `superseding_release_id`). Тест: `test_release_adversarial.py::test_superseded_manifest_stays_immutable_like_published` (`xfail(strict=True)` — стане XPASS-fail після фіксу, маркер зняти). |
| WP-01C-T-02 | low | `src/collector/contracts/release.py:197` (`transition_release`) | `candidate = manifest.model_copy(update={"state": target, **changes})` — ключ `state` у `**changes` перекриває `target`: `transition_release(draft, FAILED, state=VALIDATING)` повертає manifest у `validating` в обхід `RELEASE_TRANSITIONS`. Виправлення: відхиляти `state`/`release_id` у `changes` (`ReleaseTransitionError`) або ставити `state` після `**changes`. Тест: `test_transition_release_cannot_override_target_state_via_changes` (`xfail(strict=True)`). |
| WP-01C-T-03 | low (якість тесту) | `tests/unit/contracts/test_current.py:65` | `test_state_hash_independent_of_field_order_and_unicode_form` використовує «Дриль», у якого NFD == NFC (жодного decomposable символу) — unicode-частина assert вакуумна; mutation M6 (прибрано NFC у canonical) її не зачепила. Закрито тестом тестувальника `test_state_hash_unicode_nfd_equals_nfc_for_values_and_keys` («Київ ї»); рекомендація — замінити «Дриль» на «Київ» у вихідному тесті. |
| WP-01C-T-04 | info | `src/collector/contracts/values.py:31` | `Money(amount_minor="100")` і `Money(amount_minor=True)` приймаються (pydantic lax `str`/`bool` → int); float відхиляється як вимагає §5.1. Якщо потрібна суворість — `strict=True` на полі (тоді JSON `"100"` теж відхилятиметься; це minor-сумісно для snapshot). |
| WP-01C-T-05 | info | `src/collector/contracts/release.py:63` (`SourceInclusion.source_id`) | Перевіряється лише синтаксис, не наявність у реєстрі (на відміну від `SourceIdentity`). Ймовірно свідомо (manifest несе `source_registry_version`, джерело могло існувати у старішому реєстрі) — варто зафіксувати в docstring. |
| WP-01C-T-06 | info | `src/collector/contracts/artifacts.py:84` (`can_commit`) | naive `now` → `TypeError` з `datetime`, не `ValueError`; помилка видима, але не контрактна. |
| WP-01C-T-07 | info | `docs/plan/cards/WP-01C.md` «Docs (етап 5)» | ADR-0003 «Canonical event serialization» не створено (свідомо, `docs/decisions/` не в owned files) — етап 5, не acceptance цього етапу. |

## 7. Звірка з `implementation.md`

Прочитано після власного прогону. Заявлене підтверджується: 334 passed / 1 skipped на `07193ad`
(збігається дослівно), `--check` чистий, `mypy` без ignore, 32 snapshots у 4 групах, `hidden=True`
знято, `collector version` → `schema_version=1.0`, CI-крок drift-check присутній. Таблиця
«Acceptance ↔ тести» відповідає реальним тестам. Ризик 4 (збіг `source_event_at == fetched_at`) і
ризик 2 (реєстр на диску в Docker) — коректно описані; додаткові знахідки тестувальника
(T-01…T-03) у звіті реалізації відсутні.

## 8. Вердикт

**pass.** Усі команди картки зелені; кожен acceptance-пункт покритий тестом, 6 mutation-перевірок
показали, що тести падають на зламаному коді. Одна знахідка medium (T-01, superseded manifest
мутабельний) і одна low (T-02) зафіксовані strict-xfail тестами у `1c6c5da` — рекомендовано
виправити в межах цього PR (owner WP-01C; після merge зміни лише через dependency-запит), після
чого зняти маркери `xfail`.
