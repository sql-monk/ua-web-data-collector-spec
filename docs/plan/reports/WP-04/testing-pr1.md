# WP-04 PR1 — звіт незалежного тестування (`wp/04-1-segmenter-tm`)

| Поле | Значення |
|---|---|
| Worktree / branch | `.worktrees/wp-04-1` / `wp/04-1-segmenter-tm` (diff `git diff main...HEAD`, база `626b7e4`) |
| Перевірений HEAD реалізації | `cdacaed` (docs) поверх `4a61109`, `877df0f`, `2ff0e7e`, `19dfb23`, `8d1ce09` |
| Коміт тестувальника | `0e06048` `test(wp-04): adversarial segmenter/preservation/TM/plan tests for PR1` |
| Контракт | `docs/plan/cards/WP-04.md` «PR1», «Спільні вимоги»; ТЗ §5.4, §10 кроки 11–12, §12.1, §16.1 п.1; REVIEW R-03—R-05, R-08, R-09, R-20; U-2 |
| Середовище | Windows 11, CPython 3.13.9 (uv venv), хост навантажений паралельними агентами; Docker — не потрібен для PR1 |
| Порядок | власний прогін і тести → мутації → лише потім `implementation-pr1.md` |
| Розмір PR (≈1019 логічних рядків) | не оцінювався — рішення оркестратора (PR приймається одним) |

## Команди та дослівний вивід

```text
$ uv sync --frozen
Checked 67 packages in 33ms
EXIT=0

$ uv run ruff check .
All checks passed!
EXIT=0

$ uv run ruff format --check .
290 files already formatted
EXIT=0

$ uv run mypy src
Success: no issues found in 87 source files
EXIT=0
```

`ruff`/`format`/`mypy` виконано до додавання тестів тестувальника; після — повторно на нових
файлах (`ruff check`, `ruff format --check`, `mypy tests/unit/translation/test_*_adversarial.py`
→ `Success: no issues found in 3 source files`) і через pre-commit нижче.

```text
$ uv run pytest -rs tests/unit/translation      # після коміту 0e06048
tests\unit\translation\test_language_plan.py ........................... [  1%]
tests\unit\translation\test_memory_glossary.py .....................     [  3%]
tests\unit\translation\test_no_silent_skip.py .......................... [  5%]
tests\unit\translation\test_pipeline.py ..............                   [  8%]
tests\unit\translation\test_preservation.py ............................ [ 10%]
tests\unit\translation\test_preservation_adversarial.py ................ [ 11%]
tests\unit\translation\test_pure_modules.py ......                       [ 13%]
tests\unit\translation\test_segmenter.py ...........................     [ 15%]
tests\unit\translation\test_segmenter_adversarial.py ................... [ 16%]
tests\unit\translation\test_tm_plan_adversarial.py ..................... [ 95%]
====================== 1506 passed, 7 xfailed in 17.18s =======================
EXIT=0
```

`skipped = 0` у файлах WP-04. 7 `xfailed` — усі `strict=True` і відповідають знахідкам T-1…T-5
(дозволено anti-skip сканером; перетворяться на XPASS→FAIL, щойно дефект виправлено).

```text
$ uv run pytest -m "not live" -q                 # повний прогін на HEAD cdacaed (до тестів тестувальника)
...
SKIPPED [6] tests\e2e\test_gui_runtime_contract.py:167: gui не відповідає на http://127.0.0.1:80 — ...
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:191, :207, :217, :225, :237, :263, :278, :296, :302, :310 (по 1), :245 ([4]) — те саме
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49, :62 — COLLECTOR_E2E_REQUIRED не задано
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
FAILED tests/integration/scaling/test_worker_runtime.py::test_killed_replica_lease_is_recovered_and_finished_by_another_instance
FAILED tests/integration/scaling/test_worker_runtime.py::test_drain_finishes_active_task_and_takes_no_new_jobs
FAILED tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising
3 failed, 1238 passed, 23 skipped, 8 warnings in 5774.29s (1:36:14)
EXIT=1

# ізольований повтор трьох scaling-тестів
$ uv run pytest -q tests/integration/scaling/test_worker_runtime.py::<ті самі 3 тести>
FAILED tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising
1 failed, 2 passed in 213.09s (0:03:33)

$ uv run pytest -q tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising
E       AssertionError: heartbeat ... (повідомлення в cp1251)
E       assert 0 >= 1
E        +  where 0 = <tests.integration.scaling.test_worker_runtime.HangingSessions object at 0x000002D2E6484440>.hangs
1 failed in 53.04s

# усі не-integration/не-e2e тести разом із тестами тестувальника (HEAD 0e06048)
$ uv run pytest -m "not live and not integration and not e2e" -q -rs
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
2279 passed, 1 skipped, 317 deselected, 7 xfailed, 8 warnings in 680.75s (0:11:20)
EXIT=0
```

Три провали повного прогону — це `tests/integration/scaling/test_worker_runtime.py` (WP-01D).
Це таймінгові тести, а хост був навантажений: паралельно працювали ≈10 testcontainers інших
агентів. Два з трьох пройшли в ізольованому повторі.
`test_self_fencing_fires_when_the_database_hangs_without_raising` падає й ізольовано (двічі).
Це відомий оркестратору self-fencing scaling flake, і він **не пов'язаний з PR1**:
`git diff --name-only main...HEAD` поза `translation/**`, `pyproject.toml` і `uv.lock` містить
лише `docs/plan/reports/WP-04/implementation-pr1.md`; `src/collector/workers/**` і
`tests/integration/**` не змінено. Позначено окремо, до знахідок WP-04 не входить. Раджу
перевірити цей тест на `main` на ненавантаженому хості: у повному прогоні реалізатора
(1241 passed, 23 skipped) він проходив. Skip-и повного прогону — лише e2e/GUI і
Windows-loopback; у файлах WP-04 їх 0.

```text
$ uv run pre-commit run --all-files              # з доданими тестами (staged)
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
EXIT=0
```

### Anti-skip і відсутній `lingua`

```text
# тимчасовий tests/unit/translation/test_zz_deliberate_skip.py: lingua = pytest.importorskip("lingua")
$ uv run pytest -q tests/unit/translation/test_no_silent_skip.py
E       AssertionError: assert ['test_zz_del...importorskip'] == []
E         Left contains one more item: 'test_zz_deliberate_skip.py:3: importorskip'
FAILED tests/unit/translation/test_no_silent_skip.py::test_no_skip_or_non_strict_xfail[unit/translation/test_zz_deliberate_skip.py]
1 failed, 54 passed in 0.77s

# тимчасовий test_zz_empty_param.py: @pytest.mark.parametrize("x", EMPTY), EMPTY = []
FAILED tests/unit/translation/test_no_silent_skip.py::test_every_parametrize_has_non_empty_values[test_zz_empty_param.py:6]
1 failed, 55 passed in 0.79s

# симуляція «lingua не встановлено»: PYTHONPATH з пакетом lingua, що кидає ImportError
$ uv run pytest -q tests/unit/translation
ImportError while loading conftest '...\tests\unit\translation\conftest.py'.
src\collector\translation\detection.py:19: in <module>
    from lingua import IsoCode639_1, LanguageDetectorBuilder
E   ImportError: simulated: lingua not installed
EXIT=4
```

Тимчасові файли видалено. Відсутній `lingua` дає помилку збору (exit 4), а не skip — на Linux
тести не можуть тихо пропуститися. Нові тести не містять `skip`/`skipif`/`importorskip`,
нестрогих `xfail` і порожніх `parametrize` (скан `test_no_silent_skip.py` охоплює їх — 55
перевірок замість 35). `lingua` 2.2.0 у `uv.lock` має wheel-и `manylinux_2_17_x86_64/aarch64` і
`musllinux_1_2`, тож Linux-CI отримає той самий детектор. Сам Linux-прогін тестувальником не
виконувався (Docker не використовувався за завданням) — доказ лише CI job `python`.

## Acceptance-пункт → тест → результат

| Acceptance / вимога картки PR1 | Тести (реалізатор + **тестувальник**) | Результат |
|---|---|---|
| Segmenter: identity-reassembly для кожного fixture; `a>strong`, `li`+`br`, `blockquote>p`, таблиця | `test_segmenter.py::test_identity_reassembly_restores_equivalent_dom[*]`, `test_nested_inline_markup_stays_inside_one_segment`, `test_lists_and_table_cells_are_separate_segments`; **`test_identity_reassembly_is_byte_exact[*]` (35 кейсів × 2 режими, байт-у-байт)**, **`test_property_identity_roundtrip_is_byte_exact[0..299]`** | pass |
| Теги не рвуться / не губляться при перекладі (вкладені, незакриті, entities, RTL, emoji/ZWJ, `&nbsp;`, коментарі) | **`test_property_markup_outside_segments_survives_translation[0..299]`**, **`test_property_masked_translation_keeps_every_tag_and_attribute[0..299]`** (mask → переклад → validate → unmask → reassemble: послідовність тегів з атрибутами й коментарів ідентична), **`test_property_sentence_split_preserves_bytes`**, **`test_parser_normalizations_keep_dom_equivalent[*]`** | pass; CDATA — **xfail T-1** |
| `code/pre/script/style` не сегменти; атрибути не перекладаються без конфігу; `href` незмінний | `test_code_pre_script_style_and_translate_no_are_not_segments`, `test_href_is_unchanged_after_translation`, `test_attributes_are_segments_only_when_configured`; **`test_protected_content_never_reaches_segment_text[*]`** (8), **`test_attribute_values_are_never_segment_text_unless_configured`**, **`test_attribute_segment_escapes_hostile_translation`**, **`test_noncharacter_slot_markers_in_input_cannot_forge_attribute_slots`** | pass; незакритий inline `<code>` — **xfail T-2** |
| Hostile HTML: незакриті теги, `<!ENTITY>`, глибока вкладеність | `test_hostile_html_does_not_crash_or_resolve_entities`, `test_very_deep_nesting_does_not_exhaust_stack`; **`BYTE_EXACT_CASES`: `unclosed_inline`, `implied_li/td`, `stray_end_tags`, `unknown_entity`, `deep_inline`** | pass |
| Нормалізація для хешу (NFC, пробіли, керівні), оригінал незмінний | `test_normalization_for_hash_does_not_touch_original`, `test_whitespace_and_nfc_variants_give_same_key`; **`test_equivalent_forms_share_segment_hash[*]` (6)**, **`test_distinct_text_does_not_collide_after_normalization[*]` (13)** | pass; ZWJ-колізія — **xfail T-5** |
| TM key — рівно 5 компонентів, зміна кожного → інший ключ, golden | `test_changing_any_component_changes_key[*]`, `test_key_payload_has_exactly_five_components`, `test_golden_key_is_stable_across_processes`; **`test_actual_key_payload_has_exactly_five_components`** (spy на фактичний payload, а не реконструкцію), **`test_every_component_alone_changes_key[*]`**, **`test_swapping_values_between_components_changes_key`**, **`test_provider_model_boundary_is_not_ambiguous`**, **`test_all_keys_are_distinct_across_a_grid`** | pass |
| `uk` → `not_required`, провайдер не викликано, текст не записано; короткі/числові сегменти | `test_uk_article_is_not_required_without_provider_or_tm`, `test_uk_article_without_foreign_segments_is_not_required`; **`test_uk_article_with_short_and_numeric_segments_is_not_required`** (TM теж не чіпається), **`test_short_foreign_segment_in_uk_article_inherits_uk`** | pass |
| Змішані мови по сегментах (R-08); U-2 `ru`/`ca` перекладаються | `test_mixed_uk_page_*`, `test_de_article_with_uk_quote_does_not_send_uk_segment`, `test_extra_languages_are_translated_u2[*]`; **`test_mixed_language_article_is_split_per_segment_language`** (de+fr+nl+ru+ca+uk+en в одній статті: по одному батчу на мову, `uk` лишився), **`test_lang_attribute_overrides_classifier_per_segment`**, **`test_classifier_detects_every_supported_language[*]` (19)**, **`test_every_supported_language_article_gets_the_right_action[*]` (19)** | pass |
| Детермінованість детектора між запусками | **`test_detector_is_deterministic_across_processes`** (2 subprocess з різним `PYTHONHASHSEED`), **`test_plan_decisions_are_identical_between_runs`** | pass (мова — точно; confidence — до 1e-12, див. T-6) |
| Body nullable (R-20), `None` ≠ `""` | `test_head_only_access_*`, `test_full_access_without_body_plans_no_body`, `test_metadata_only_translates_head_and_never_creates_body`; **`test_body_none_stays_none_and_empty_body_is_not_invented`**, **`test_head_only_access_never_returns_body_even_if_provided[*]`** | pass (title `""` → `None`, див. T-7) |
| Preservation 100%: числа в локальних форматах, дати, `%`, валюти, URL з query/fragment, e-mail, glossary | `test_correct_translation_passes_and_protected_values_are_restored[*]`, `test_each_mutation_is_caught[*]`; **`test_correct_translation_preserves_values_byte_for_byte[*]` (17, з перестановкою placeholder-ів)**, **`test_translator_corrupting_mask_is_detected[*]` (18 способів зіпсувати маску)**, **`test_closing_tag_before_opening_tag_is_detected`**, **`test_placeholder_in_unmasked_number_text_changed_is_detected`** | pass; мінус — **xfail T-3**, `)` у URL — **xfail T-4** |
| Glossary: зміна терміна → нова версія → новий ключ; version = SHA-256 вмісту | `test_changing_one_term_changes_version_and_tm_key`, `test_default_glossary_loads_and_version_is_content_hash`; **`test_glossary_version_is_stable_to_mapping_order_comments_and_formatting`**, **`test_glossary_version_depends_on_entry_list_order_fact`**, **`test_any_semantic_glossary_change_changes_version[*]` (5)** | pass (порядок списку — див. T-8) |
| Q-009, ідемпотентність, провал preservation блокує version | `test_q009_changed_paragraph_is_the_only_segment_sent`, `test_repeat_of_same_version_is_full_tm_hit`, `test_preservation_failure_blocks_version_but_keeps_valid_segments_in_tm`, `test_truncated_provider_response_is_not_assembled` | pass |
| Pure-модулі без I/O-імпортів | `test_pure_modules.py[*]` | pass |
| Anti-skip | `test_no_silent_skip.py` + ручна перевірка вище | pass |
| mypy strict, `pyproject`/`uv.lock` лише власні залежності | `mypy src` зелений; diff `pyproject.toml` — один рядок `lingua-language-detector>=2.1` з коментарем-власником; `uv.lock` — лише цей пакет | pass |

## Рівень §16.1 → тести

| Рівень (картка: «Рівні тестів §16.1») | Тести в PR1 |
|---|---|
| 1 Unit — segmentation/reassembly | `test_segmenter.py`, `test_segmenter_adversarial.py` (≈1190 кейсів, 300 згенерованих документів × 4 властивості) |
| 1 Unit — language detection | `test_language_plan.py`, `test_tm_plan_adversarial.py` (19 мов, змішані статті, детермінованість) |
| 1 Unit — TM key | `test_memory_glossary.py`, `test_tm_plan_adversarial.py` (spy payload, чутливість, нормалізація/колізії) |
| 1 Unit — preservation | `test_preservation.py`, `test_preservation_adversarial.py` |
| 1 Unit — budget | не в PR1 (PR2) |
| 7 Translation QA | не в PR1 (PR3: golden corpus) |
| 3 Integration | не в PR1 (PR2: PostgreSQL під `collector_translation`) |

## Додані тести (коміт `0e06048`)

- `tests/unit/translation/test_segmenter_adversarial.py` — байт-у-байт identity на 35 ручних
  edge-кейсах (вкладені/незакриті теги, implied end, entities з/без `;`, `&nbsp;`, NBSP/NNBSP,
  `script`/`style`/`pre`/`code`/`kbd`, атрибути з `>` і текстом, коментарі з тегами, PI,
  DOCTYPE, RTL + bidi-керівні, emoji/ZWJ, CRLF, `textarea`, `svg/math`); захищений вміст
  не потрапляє в текст сегмента; escape hostile-перекладу атрибута; DOM-еквівалентні
  нормалізації stdlib-парсера; детермінований генератор well-formed HTML (`random.Random(seed)`,
  300 seed-ів; `hypothesis` у залежностях відсутній) з 4 властивостями; strict xfail T-1, T-2.
- `tests/unit/translation/test_preservation_adversarial.py` — 17 кейсів коректного перекладу
  (de/fr/en/pl/hu/cs/CH формати, NBSP/NNBSP, дати трьох видів, `%`/`‰`, URL із query і
  fragment, `www.`, e-mail з `+`, glossary target/keep/`*`, href) з перестановкою placeholder-ів;
  18 способів зіпсувати маску (дроп/дубль/невідомий id, лапки, пробіл, регістр, HTML-escape,
  перенумерація, «переклад» placeholder-а, зайві число/URL/e-mail/тег/`</a>`, порожній
  переклад); strict xfail T-3, T-4; факт про перестановку чисел.
- `tests/unit/translation/test_tm_plan_adversarial.py` — фактичний payload TM key (spy),
  чутливість до кожного компонента й меж provider/model, сітка унікальності; еквівалентність і
  неколізійність нормалізації; glossary version (порядок ключів, коментарі, формат; порядок
  списку; семантичні зміни); R-20 `None` vs `""`; head-only доступ; `uk` з короткими/числовими
  сегментами; змішана стаття з 7 мов; `lang` проти classifier-а; детектор на 19 мовах і між
  процесами; strict xfail T-2 (pipeline), T-5.

## Mutation-перевірка

Кожна мутація — тимчасова правка продуктивного файлу, прогін, `git checkout -- <file>`;
`git status` після всіх мутацій показував лише нові тестові файли.

| # | Мутація | Прогін | Результат |
|---|---|---|---|
| M1 | `segmenter.py:419` — `self._parts.append(tail)` → `pass` (губиться хвостовий пробіл сегмента) | `test_segmenter_adversarial.py` | `334 failed, 853 passed, 2 xfailed` — червоний |
| M2 | `preservation.py` — `issues.append("url_mismatch")` → `pass` | `test_preservation_adversarial.py` | `FAILED ...test_translator_corrupting_mask_is_detected[extra_url]` — `1 failed, 39 passed` — червоний. Для порівняння, тести реалізатора на тій самій мутації: `2 failed, 48 passed` |
| M3 | `memory.py` — `"glossary_version"` прибрано з payload TM key | `test_tm_plan_adversarial.py` | `FAILED ...test_actual_key_payload_has_exactly_five_components`, `...test_every_component_alone_changes_key[glossary_version]`, `...test_all_keys_are_distinct_across_a_grid` — `3 failed` — червоний |
| M4 | `normalize.py` — `unicodedata.normalize("NFC", text)` → `text` | `test_tm_plan_adversarial.py` | `FAILED ...test_equivalent_forms_share_segment_hash[...]` для пар NFC/NFD `Café`, `Țară`, `ö` — `3 failed, 43 passed` — червоний |

## Знахідки

| ID | Severity | file:line | Опис |
|---|---|---|---|
| T-2 | medium | `src/collector/translation/segmenter.py:269` | Незакритий inline `<code>`/`<kbd>`/`<samp>`/`<var>` або `translate="no"` переводить парсер у skip до кінця документа: усі наступні абзаци стають одним «захищеним» токеном першого сегмента. Приклад `<p>Befehl <code>ls</p><p>Dieser Absatz …</p>` → сегменти `['Befehl <code>ls</p><p>Dieser Absatz …</p>']`. У pipeline версія збирається як `complete=True` без quality flag, а текст другого абзацу лишається неперекладеним. Це мовчазний пропуск, який суперечить «не тихий пропуск» (картка PR1 п.7) і coverage 100% з §12.1. Браузер закриває `code` на `</p>`, тож текст видимий. Тести: `test_unclosed_inline_code_does_not_swallow_following_paragraphs`, `test_unclosed_code_does_not_yield_complete_version_with_untranslated_text` (strict xfail) |
| T-3 | medium | `src/collector/translation/preservation.py:42` | `_NUMBER` не містить знака, тому валідатор не бачить, що перекладач прибрав `−`/`-` перед числом (`−5 Grad` → `5`): інверсія значення проходить gate «числа збережені на 100%» (§12.1). Тест `test_dropped_minus_sign_is_detected[U+2212,hyphen]` (strict xfail) |
| T-1 | low | `src/collector/translation/segmenter.py:227` | `unknown_decl` відтворює марковану секцію як `<![{data}]>`, а stdlib передає `data` без закривального `]`: `<![CDATA[x < y]]>` → `<![CDATA[x < y]>`. Губиться байт і змінюється вміст bogus comment. У cleaned HTML трапляється рідко. Тест `test_cdata_section_is_byte_exact` (strict xfail) |
| T-4 | low | `src/collector/translation/preservation.py:44` | `_URL_TRAILING` відрізає закривальну `)` навіть коли дужка збалансована в URL (`…/wiki/Bus_(Verkehr)`). Перекладач, що губить `)`, ламає посилання непомітно. Тест `test_url_with_trailing_parenthesis_is_preserved` (strict xfail) |
| T-5 | low | `src/collector/translation/normalize.py:23` | Усі `Cf` прибираються (ZWJ, ZWNJ, RLM, LRE…): emoji ZWJ-послідовність 👨‍👩‍👧 і окремі 👨👩👧 дають один `normalized_segment_hash` / TM key. Для 16 основних мов ризик малий (ZWNJ значущий у фарсі/індійських письмах, яких немає в наборі). Тест `test_zwj_emoji_sequence_does_not_collide_with_separate_emoji` (strict xfail) |
| T-6 | low | `src/collector/translation/detection.py:59` | Confidence lingua недетермінована в останньому ulp навіть в одному процесі: `('fr', 0.997443962814769)` / `0.9974439628147691` / `0.9974439628147689` для того самого тексту. Мова стабільна. Поріг `0.6`/`0.95` (PR3) на межі теоретично може «мерехтіти»; у TM key confidence не входить. Тест порівнює з допуском 1e-12 |
| T-7 | low | `src/collector/translation/planner.py:124` | Для title/lead `""` і рядок із пробілів зливаються з `None`: outcome `title=None`. Body розрізняє `None` і `""` (R-20 виконано), title/lead — ні. Якщо контракт WP-01C PR2 розрізнятиме ці значення, це треба узгодити |
| T-8 | info | `src/collector/translation/glossary/__init__.py:72` | `glossary_version` стабільна до порядку ключів mapping, коментарів і YAML-форматування, але **чутлива до порядку записів у списку**: перестановка без зміни змісту дає нову версію і інвалідує TM пари. Звіт реалізатора каже лише «SHA-256 canonical вмісту» — формально правда, але цей нюанс не згаданий. Консервативно: хибних TM-хітів не дає |
| T-9 | info | `src/collector/translation/segmenter.py:192`, `:215-219` | DOM-еквівалентні, але не байт-у-байт нормалізації stdlib-парсера: `</P>`→`</p>`, `</p >`→`</p>`, `&amp`→`&amp;`, `&nbsp`→`&nbsp;`, `&#39`→`&#39;`, `--!>`→`-->`, `</ p>`→`<!-- p-->`. Картка вимагає порівняння DOM, а не рядків, тож вимога виконана. Крайній випадок: legacy-префікс без `;` на кшталт `&notit` стане `&notit;`, а це вже інший DOM |
| T-10 | info | `src/collector/translation/detection.py:105` | Сегменти без літер (`2024`, `12,5 %`) у не-`uk` статті отримують дію `translate` і йдуть провайдеру як `<x id="1"/>`. Це рахується в символи (budget PR2), хоч провайдеру нема чого перекладати. Дублікати згортаються TM-ключем |
| T-11 | info | `docs/plan/reports/WP-04/implementation-pr1.md` («Залежності», «Ризики») | Заява «приріст пам'яті ≈ 10 МБ на 19 мов» справедлива лише після класифікації одного тексту. Моделі lingua вантажаться ліниво по мовах: після класифікації текстів усіх 19 мов working set 18.9 → 64.4 МБ (+45 МБ), private 11.8 → 13.9 МБ. Тобто це переважно file-backed сторінки з `.pyd` |

Flaky-тестів у `tests/unit/translation/**` не виявлено: три прогони підпакета на фінальному
стані дали однаковий результат `1506 passed, 7 xfailed` (17.18 с, 204.39 с, 12.65 с — розкид часу
походить від навантаження хоста, а не від тестів).

## `lingua-language-detector`: факти для рев'ю (без рішення)

| Факт | Значення (виміряно / з `uv.lock`) |
|---|---|
| Версія / джерело | 2.2.0, PyPI, лише бінарні wheel-и **`cp313`** (sdist немає, abi3 немає) → перехід на CPython 3.14 блокується, доки не вийдуть wheel-и `cp314` |
| Розмір wheel | 169.98–172.50 МБ на платформу; Linux: `manylinux_2_17_x86_64` 170 332 278 Б, `manylinux_2_17_aarch64` 170 326 018 Б, `musllinux_1_2_x86_64` 170 585 841 Б |
| Розмір у venv | 291 МБ (`lingua/lingua.cp313-win_amd64.pyd` = 304 667 648 Б: один нативний модуль з моделями всіх ~75 мов; обмеження набору мов у коді **не** зменшує образ) |
| Пам'ять (Windows, working set) | імпорт: +≈4 МБ; build детектора на 19 мов: ≈0 (ліниве завантаження); перша класифікація: +10 МБ; після текстів усіх 19 мов: +45 МБ WS, private +2 МБ |
| Латентність | build ≈ 0.03 с; ≈ 1.5 мс на класифікацію речення (950 класифікацій за 1.42 с на навантаженому хості) |
| Точність на синтетичних реченнях | 19/19 мов (16 + `ru`, `ca` + `uk`) визначено правильно з confidence ≥ 0.6, включно з близькими парами cs/sk, hr/sl, lv/lt, es/ca, ro/it |
| Мережа | не потрібна (моделі в wheel), працює під `pytest-socket` |
| Детермінованість | мова стабільна; confidence — розкид в останньому ulp (T-6) |
| Linux у CI | wheel-и є для glibc і musl x86_64/aarch64; image-розмір `translation-worker` зросте на ≈170 МБ стиснуто / ≈290 МБ розпаковано (Dockerfile не owned WP-04; не міряно) |

## Звірка з `implementation-pr1.md`

- Команди (`ruff`, `format`, `mypy`, pre-commit) — підтверджено; 173 тести реалізатора зелені
  в моєму прогоні (тепер 1506 + 7 strict xfail разом із моїми).
- Acceptance-таблиця реалізатора відповідає фактичним тестам; anti-skip доказ відтворено.
- «Reassembly відтворює дерево» — підтверджено як DOM-еквівалентність і (на well-formed HTML)
  байт-у-байт; винятки — T-1 (CDATA), T-9.
- «Провал → не тихий пропуск» — не виконується для T-2.
- «Числа — мультимножина після нормалізації формату» — знак не враховано (T-3).
- «Приріст пам'яті ≈ 10 МБ на 19 мов» — занижено (T-11).
- «Порядок незалежних placeholder-ів не перевіряється» — реалізатор заявив це сам, я
  підтвердив тестом-фактом.
- Linux-паритет: так само, як у реалізатора, не підтверджено локально — лише CI.

## Вердикт

**pass.** Усі acceptance-пункти PR1 покриті, тести зелені, `skipped = 0` у файлах WP-04,
anti-skip працює, мутації ловляться. Дві medium-знахідки (T-2 — мовчазний неперекладений текст
після незакритого inline `<code>`; T-3 — втрата знака мінус не ловиться gate-ом preservation)
не блокують конкретних тестів картки, але суперечать принципам «не тихий пропуск» і
«числа 100%». Рекомендую виправити їх у fix-up цього PR або записати як обов'язковий пункт PR2
(strict-xfail тести вже на місці й почервоніють після виправлення — тоді прибрати `xfail`).

## Дотестування після fix-up `0c7a5d2`

- T-4: застарілий strict-xfail `test_url_with_trailing_parenthesis_is_preserved` замінено
  adversarial-тестами під нову поведінку (збалансована `)` входить у URL-placeholder):
  `test_balanced_parenthesis_url_is_masked_whole_and_restored[balanced,nested]`,
  `test_unbalanced_trailing_parenthesis_stays_text_and_passes`,
  `test_translator_losing_or_moving_url_parenthesis_is_detected[*]` (8 мутацій: дроп/дубль/зламані
  лапки/escape placeholder-а, URL перенабрано без `)`, `)` перенесено, частина в дужках загублена,
  зайвий перенабраний URL). Мутація `preservation.py` (прибрано виняток для збалансованої `)`)
  → `2 failed, 51 passed` — червоний; після `git checkout` зелено.
  `uv run pytest tests/unit/translation` → `1542 passed in 11.87s`, xfail/skip — 0.
