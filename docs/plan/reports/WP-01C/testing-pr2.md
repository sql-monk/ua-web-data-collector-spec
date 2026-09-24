# WP-01C PR2 — testing report (`wp/01c-2-payload-news-contracts`)

Незалежний тестувальник. Worktree `.worktrees/wp-01c-2`, diff `git diff main...HEAD`
(коміти `9d9f1de`, `6072363`, `a7cc728`, `e20c163`). Рівні §16.1 для PR2: 2 Contract, 9 Temporal.
`implementation-pr2.md` прочитано лише після власного прогону. Продуктивний код не змінювався.
Тести тестувальника закомічено в `a29c323` (`test(wp-01c): ...`).

## Команди та дослівний вивід

```text
$ uv sync --frozen
Checked 66 packages in 8ms
$ uv run ruff check .
All checks passed!
$ uv run ruff format --check .
278 files already formatted
$ uv run mypy src
Success: no issues found in 81 source files
$ uv run collector contracts export --check
schemas up to date: schemas
EXIT 0
```

```text
$ uv run pytest -m "not live" -q        # до додавання тестів тестувальника; машина під навантаженням
...                                    # (паралельно 4-5 повних прогонів інших worktree)
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:191: gui ... `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
... (e2e GUI/runtime skip-и без піднятого compose)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49: ... (COLLECTOR_E2E_REQUIRED=1 ...)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:62: ... (COLLECTOR_E2E_REQUIRED=1 ...)
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback ... asyncio
1190 passed, 23 skipped, 8 warnings in 2802.91s (0:46:42)
[exited with code 0]
```

Skip-reason у консолі Windows cp1251 нечитабельні; усі 23 skip — `tests/e2e/**` (немає compose
стеку) і Windows-only `test_network_blocked.py:27`. Жодного skip у `tests/unit/contracts/**` і
`tests/contract/contracts/**`. Відомий таймінговий `test_project_groups_scales_linearly_on_disjoint_merges`
у цьому прогоні **пройшов** (окремий повтор не знадобився).

```text
$ uv run pytest tests/unit/contracts tests/contract/contracts -q -p no:cacheprovider -rs   # з тестами тестувальника
644 passed in 36.89s

$ uv run pytest tests/unit/contracts/test_pr2_tester_adversarial.py tests/contract/contracts/test_pr2_tester_contract.py -q -p no:cacheprovider -rs
173 passed in 24.21s

$ uv run pre-commit run --all-files     # після коміту тестів
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

## Acceptance → тест → результат

| Acceptance PR2 | Тест(и) | Результат |
|---|---|---|
| Команди перевірки PR1 зелені | вивід вище (ruff, format, mypy, pytest, `export --check`, pre-commit) | pass |
| Нові snapshots у `schemas/**`; drift = fail | `test_schema_snapshots.py::test_repository_snapshots_have_no_drift`, `collector contracts export --check`; mutation M3 ламає pinned snapshot bytes | pass |
| Compatibility fixtures v1.0 для кожної нової моделі | `test_compatibility.py::test_every_versioned_document_has_a_fixture`, `test_fixture_validates_against_current_model`; **нові:** `test_pr2_tester_contract.py::test_unknown_or_malformed_major_rejected` (7 моделей × 7 версій), `test_missing_schema_version_defaults_to_current_minor` | pass |
| minor/major: новий reader + старий документ; старий reader + нове поле; невідома major | **нові:** `test_new_minor_reader_accepts_old_minor_document`, `test_old_reader_rejects_new_minor_document_explicitly`, `test_old_minor_document_with_unknown_field_rejected_not_dropped` | pass |
| `NewsTranslation` з `body=None` валідний, naive datetime — ні | `test_news.py::test_translation_with_body_none_is_valid_r20`, `test_translation_rejects_naive_created_at`; **нові:** `test_non_utc_datetimes_rejected_everywhere`, `test_python_non_utc_tzinfo_rejected` | pass |
| `TranslationStatus` рівно 4 (golden), сумісність з ТЗ §5.4 (`not_required`) і §12 (`translation_failed`) | `test_news.py::test_translation_status_has_exactly_four_values_golden`; **нові:** `test_translation_status_exactly_four_closed_values`, `test_translation_statuses_named_in_spec_are_members`, `test_translation_status_is_the_only_translation_status_enum`, `test_quality_flags_cover_wp04_decisions` | pass |
| payload з чужим `entity_uuid` відхиляється функцією | `test_payload.py::test_entity_uuid_mismatch_with_ref_is_rejected`; **нові:** `test_substituted_entity_in_ref_is_rejected`, `..._domain_...`, `..._schema_version_...`, `test_ref_without_schema_version_is_rejected`, `test_substituted_state_hash_in_version_record_is_rejected`, `test_substituted_artifact_entity_in_records_is_rejected`, `test_substituted_translation_key_components_rejected` | pass (hash bytes artifact функція свідомо не перевіряє — задокументовано, це робить `collector.storage.get(key, expected_sha256)`) |
| `encode_event(NewsVersionCreatedEvent)` byte-equivalent після round-trip | `test_news.py::test_encode_news_event_byte_equivalent_after_round_trip`; **нові:** `test_news_event_bytes_match_handwritten_canonical_form` (bytes виписані вручну за ADR-0003), `test_news_fixture_event_bytes_match_pinned_snapshot` (sha256 `bc3c6c4a…859a`, 1309 bytes), `test_news_event_bytes_independent_of_key_order` (20 перестановок, dict і JSON з відступами), `test_news_event_bytes_stable_across_processes_and_hash_seeds` (4 процеси, `PYTHONHASHSEED` 0/1/4242/random), `test_news_event_nfd_and_nfc_text_encode_identically`, `test_utc_offset_zero_forms_are_equivalent_and_canonical_z` | pass |
| bounded `core`/`attributes`/`latest_state` (§9.2) | `test_payload.py::test_blocks_are_bounded`; **нові:** межа/межа+1 для глибини (об'єкти, масиви, порожні контейнери), ключів на вкладених рівнях, масивів у масивах, snapshot/observation records; NaN/±Infinity (Python і JSON-літерали), Decimal/datetime/UUID/bytes, не-UTF-8 bytes, lone surrogates | pass (див. знахідки M-1, M-2, L-1) |
| «жодного I/O» покриває нові модулі | `test_no_io_imports.py::test_io_probe_covers_pr2_modules` + walk_packages probe | pass |
| Temporal: nullable source time не підміняється system time; лише aware UTC | `test_payload.py::test_fetched_at_as_source_time_is_rejected_r43`; **нові:** `test_null_source_time_is_never_filled_with_system_time`, `test_source_time_equal_to_fetched_at_rejected_r43[source_event_at/source_updated_at]`, `test_non_utc_datetimes_rejected_everywhere` (8 моделей × naive/+02/-05), `test_review_source_times_stay_null_and_independent_of_observed_at` | pass |
| UUID `_id` у нових Mongo-записах | `test_pr2_snapshots.py::test_mongo_records_require_uuid_id_and_schema_version`; **нові:** `test_record_id_is_uuid_and_dumped_as_mongo_id`, `test_record_rejects_non_uuid_id` (ObjectId hex, текст, порожній, int, None), `test_record_requires_id` | pass |
| Споживачі: WP-01B PR2 (input/output, index-поля §9.2), WP-01A PR3b, WP-04 PR2 | **нові:** `test_wp01b_payload_input_fields`, `test_wp01b_index_fields_are_required_in_mongo_snapshots`, `test_wp01b_review_published_at_index_field_exists`, `test_wp04_input_fields_in_news_event`, `test_wp04_wp01a_translation_fields`, `test_translation_idempotency_key_same_for_head_and_body_jobs` | pass |
| `docs/contracts.md` описує нові моделі й правило розширення | ручна перевірка: §11, §11.2, §11.3 (TM key не контракт) | pass |

## Рівні §16.1 → тести

| Рівень | Тести |
|---|---|
| 2 Contract | `tests/contract/contracts/test_pr2_snapshots.py`, `test_compatibility.py`, `test_schema_snapshots.py`, `test_no_io_imports.py`; **нові** `tests/contract/contracts/test_pr2_tester_contract.py` (golden bytes, snapshot sha, compat minor/major, споживачі); `tests/unit/contracts/test_{payload,records,news}.py`; **нові** `tests/unit/contracts/test_pr2_tester_adversarial.py` (bounded, enum, ref-підміна, `_id`) |
| 9 Temporal | `test_payload.py::test_fetched_at_as_source_time_is_rejected_r43`, `test_naive_system_time_is_rejected`, `test_records.py::test_snapshot_time_rejects_fetched_at_as_source_time`; **нові** temporal-блок `test_pr2_tester_adversarial.py` (null source time, R-43 для обох полів, non-UTC у 8 моделях, еквівалентність `Z`/`+00:00`/`timezone.utc`) |

## Додані тести

- `tests/unit/contracts/test_pr2_tester_adversarial.py` — 81 тест (з параметризацією).
- `tests/contract/contracts/test_pr2_tester_contract.py` — 92 тести (з параметризацією).

Жоден не skip-ається (`-rs` без рядків SKIPPED для цих файлів); параметризації непорожні.

## Mutation-перевірка

Кожна мутація — тимчасова правка `src/**` через `sed`, прогін двох нових файлів, відкат
`git checkout -- <file>`; після всіх мутацій `git status` показував лише нові тест-файли.

| # | Мутація | Результат |
|---|---|---|
| M1 | `payload.py`: `if False and payload.entity_uuid != artifact.entity_uuid:` | `FAILED test_substituted_entity_in_ref_is_rejected` — 1 failed, 172 passed |
| M2 | `_base.py`: `if depth > MAX_JSON_DEPTH + 1:` (off-by-one) | 7 failed: `test_depth_limit_exact_boundary_for_objects[core/attributes/latest_state]`, `test_depth_limit_counts_list_levels_too[×3]`, `test_empty_containers_at_depth_boundary` |
| M3 | `canonical.py`: `sort_keys=False` | red: `test_news_event_bytes_match_handwritten_canonical_form`, `test_news_fixture_event_bytes_match_pinned_snapshot`, `test_news_event_bytes_stable_across_processes_and_hash_seeds`, + тести, що спираються на state_hash fixture-ів |
| M4 | `payload.py`: `pass  # R-43` замість `self.entity_time()` | 2 failed: `test_source_time_equal_to_fetched_at_rejected_r43[source_event_at]`, `[source_updated_at]` |
| M5 | `enums.py`: додано `FAILED = "failed"` у `TranslationStatus` | `FAILED test_translation_status_exactly_four_closed_values` (`'failed'` зайве) |

## Звірка з `implementation-pr2.md`

- Команди: заявлене підтверджено (ruff/format/mypy/`export --check`/pre-commit зелені; повний
  прогін у мене 1190 passed / 23 skipped без падінь; у реалізатора 1 таймінгове падіння, яке тут
  не відтворилось).
- Рішення 1–10 реалізатора відповідають коду; `schemas/common/` покрито ADR-0004 (четверта група).
- «Жодного skip у нових тестах» — підтверджено.
- Реалізатор сам вказує ризик «межі BoundedJsonObject без вимірів» — тестування показало, що межі
  лише структурні (див. M-2).

## Знахідки

| ID | Severity | file:line | Опис |
|---|---|---|---|
| M-1 | medium | `src/collector/contracts/_base.py:55` (`list[JsonValue]` у lax-режимі) | Python `set`/`tuple` у `core`/`attributes`/`latest_state` мовчки приводяться до `list`. Для `set` порядок залежить від `PYTHONHASHSEED`: `core={'tags': {'alpha','beta','gamma','delta'}}` дав 4 різні `state_hash` у 4 процесах (`v1:403f77a6…`, `v1:19c39806…`, `v1:142bb558…`, `v1:6280be69…`). Parser, що будує payload з Python-об'єктів, отримає недетерміновані artifact bytes і хибні `state_changed`/observations/`domain.changed`. Шлях projector-а (JSON bytes → `model_validate_json`) детермінований (`test_json_origin_payload_state_hash_is_deterministic`). Рекомендація: strict для контейнерів (`Strict()` на `list`) — dependency до WP-01C до старту адаптерів. |
| M-2 | medium | `src/collector/contracts/_base.py:69-96` | `BoundedJsonObject` обмежує лише структуру (depth/keys/items): рядок 20 MiB у `core` приймається (canonical bytes 20 972 527), що більше за ліміт документа Mongo 16 MiB; цілі поза int64 (`2**70`) приймаються й серіалізуються, хоча BSON їх не зберігає (PyMongo `OverflowError`). Відмова станеться в projector-і після валідації, а не на контракті. Рекомендація: межа довжини рядка/загального розміру і діапазон int64. |
| L-1 | low | `src/collector/contracts/canonical.py:125` | Lone surrogate у Python-input (`"x\ud800"`) проходить валідацію `NormalizedProjectionPayload`/`NewsVersionCreatedEvent`, а `canonical_json_bytes`/`encode_event` падають `UnicodeEncodeError` (підклас `ValueError`, не `CanonicalEncodingError`). JSON-шлях відхиляє escape `\ud800` (`test_lone_surrogate_json_escape_rejected_by_projector_path`). Тест фіксує мінімальну гарантію — bytes не утворюються. |
| L-2 | low | `src/collector/contracts/news.py:69` | `source_locale_raw` без `max_length`; значення 300 000 символів дає `EventTooLargeError` з порадою «перенесіть payload у payload_artifact», але в `NewsVersionCreatedEvent` немає artifact-fallback — outbox-подія не може бути сформована. Рекомендація: `max_length` (напр. 64) для locale. |
| I-1 | info | `src/collector/contracts/canonical.py:102` | Ключі, що збігаються після NFC (`"é"` NFC/NFD), приймаються моделлю, але canonical-кодування відхиляє їх (`CanonicalEncodingError`) — поведінка PR1, узгоджена з ADR-0003, проте валідація й кодування розходяться. |
| I-2 | info | `docs/decisions/0003-canonical-event-serialization.md` | ADR-0003 описує `encode_event` лише для `domain.changed`; PR2 додав `news.version_created` з власним media type (формат bytes той самий, підтверджено handwritten golden). Реалізатор це вказав — оновити на етапі docs. |

Flaky-тестів у цьому прогоні не виявлено.

## Вердикт

**pass** — усі acceptance-пункти PR2 підтверджені тестами (наявними й доданими), команди зелені,
мутації M1–M5 ловляться. Знахідки M-1/M-2 (medium) не блокують PR2 (шлях projector-а через JSON
детермінований; відмова по розміру/int64 станеться як permanent у projector-і), але мають бути
закриті dependency-запитом до WP-01C до того, як адаптери WP-05/06/08/10 почнуть писати payload-и.
