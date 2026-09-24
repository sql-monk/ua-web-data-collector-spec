# WP-04 PR1 — звіт реалізації (`wp/04-1-segmenter-tm`)

| Поле | Значення |
|---|---|
| WP / під-PR | WP-04 / PR1 «segmenter, мова, TM, glossary, preservation» |
| Branch / worktree | `wp/04-1-segmenter-tm` / `.worktrees/wp-04-1` |
| Картка | `docs/plan/cards/WP-04.md` — «PR1», «Спільні вимоги», «Передумови», «Рішення оркестратора» |
| Розділи ТЗ | §5.4, §5.5, §8, §9.3 п.7, §10 кроки 11–12, §12.1, §12.2, §12.3, §18; FR-016, FR-017; REVIEW.md R-03—R-05, R-08, R-09, R-20; рішення користувача U-2 |
| Середовище | Windows 11, uv 0.12.13, CPython 3.13 (venv), pytest 9.1.1, Docker 29.8 (для чужих integration-тестів) |
| Commits | `4a61109` segmenter + мовні константи + `lingua`; `877df0f` preservation/glossary/TM key; `2ff0e7e` детекція мови, planner, виконання плану над TM; `19dfb23` unit-тести, fixtures, anti-skip; далі — `test(wp-04)`/`docs(wp-04)` з виправленням fixture і цим звітом |

## Що зроблено

Усе — чиста логіка в `src/collector/translation/**` без I/O (крім читання packaged
glossary-файлу), без провайдера перекладу і без змін у чужих owned files.

### Публічні API

| Модуль | API | Вимога картки |
|---|---|---|
| `collector.translation.segmenter` | `segment_html(html, *, max_segment_chars=None, translate_attributes=()) -> SegmentedDocument`; `reassemble(doc, translations) -> str`; `Segment` (`index`, `kind`, `block`, `lang`, `tokens`, `.html`, `.text`); `matched_tag_pairs` | 1 |
| `collector.translation.normalize` | `normalize_text(text)` — NFC, керівні символи → пробіл, format-символи прибрано, згортання пробілів | 2 |
| `collector.translation.languages` | `CORE_SOURCE_LANGUAGES` (16), `EXTRA_SOURCE_LANGUAGES` (`ru`, `ca`), `TARGET_LANGUAGE`, `EXTRA_SOURCE_LANGUAGES_ENV`, `parse_extra_source_languages(raw)`, `supported_source_languages(extra)`, `normalize_language_tag(tag)` | 3, U-2/О-5 |
| `collector.translation.detection` | `LanguageClassifier` (Protocol), `LinguaClassifier`, `lingua_classifier(languages)` (кеш), `LanguageDecision`, `detect_article_language(...)`, `detect_segment_language(...)` | 3 |
| `collector.translation.planner` | `ArticleText` (локальний тип, TODO WP-01C PR2), `plan_article_translation(article, *, classifier, ...) -> TranslationPlan` (`.not_required`, `.to_translate`, `.quality_flags`), `QualityFlag` (локальний Literal, TODO WP-01C PR2) | 4, 8 |
| `collector.translation.memory` | `normalized_segment_hash(masked_text)`, `translation_memory_key(*, source_language, target_language, normalized_segment_hash, provider, model_version, glossary_version)`, `MemoryEntry`, `TranslationMemoryStore` (Protocol: `get_many`, `put_many`), `InMemoryTranslationMemory` | 5, О-2 |
| `collector.translation.glossary` | `load_glossary(path=None)`, `parse_glossary(data)`, `Glossary` (`version`, `entries_for(lang)`), `GlossaryEntry`; дані — `glossary/default.yaml` | 6 |
| `collector.translation.preservation` | `mask_segment(segment, glossary_entries) -> MaskedSegment` (`.text`, `.unmask()`), `validate_preservation(masked, translated) -> PreservationResult` (`.ok`, `.issues`, `.html`), `normalize_number` | 7 |
| `collector.translation.pipeline` | `execute_plan(plan, *, glossary, memory, translator, provider) -> TranslationOutcome`; `SegmentTranslator` (мінімальний async callable, TODO PR2 → `TranslationProvider`), `ProviderIdentity` | 9, ідемпотентність |

### Ключові рішення

- **Segmenter.** Документ = послідовність незмінних шматків вихідного HTML і слотів
  сегментів, тож identity-reassembly відтворює дерево (у тестах — порівняння нормалізованого
  DOM, не рядків), а переклад змінює лише вміст слотів. Сегмент — inline-вміст найближчого
  блоку (`p`, `li`, `h1–h6`, `blockquote>p`, `figcaption`, `td/th`, `dt/dd`, …); inline-теги
  (`a`, `em`, `strong`, `b`, `i`, `br`, `span`, `sup/sub`, …) — токени всередині сегмента;
  `code/kbd/samp/var` і `translate="no"` усередині тексту — один захищений токен; `pre`,
  `script`, `style`, `svg`, `math`, `template`, `textarea`, `noscript`, `head` — не сегменти.
  Непарні inline-теги на краях сегмента виносяться за його межі (не «рвуться» перекладом).
  Довгий блок ділиться на речення лише при `max_segment_chars` і лише поза inline-тегами.
  `alt`/`title` — окремі сегменти `kind="attribute"` лише за `translate_attributes`
  (слот-маркер U+FDD0…U+FDD1 у сирому тезі; ці noncharacters у вході замінюються на U+FFFD).
  Неявні закриття (`p`, `li`, `td/th`, `dt/dd`, `tr`) — за HTML5; стек з лічильником відкритих
  тегів і межею пошуку 256 — лінійний час на hostile-вкладеності (100 000 рівнів ≈ 1,8 с).
- **Мова (§12.2, R-08, U-2).** Стаття: metadata → `lang` єдиного кореневого елемента →
  classifier. Сегмент: власний `lang` предка (не кореневий) → короткий (< 30 символів) або без
  літер успадковує мову статті → classifier з порогом 0,6; нижче порогу — мова статті +
  `uncertain=True` + кандидат і confidence (прапорець `low_language_confidence`), нічого не
  відкидається. Classifier обмежено `uk` + 16 + extra (`ru`, `ca`). Мова з metadata/`lang` поза
  набором — дія `unsupported` + `language_unsupported`, сегмент не надсилається (О-5).
- **`not_required`.** `original_language == uk` і всі сегменти `keep` → `execute_plan` не
  звертається ні до TM, ні до перекладача й не повертає текст.
- **R-20/FR-016.** title/lead — завжди, якщо є; body — лише для `full`/`partial` і лише
  наявний. `unknown` — як head-only (FR-016 дозволяє body тільки для `full/partial`).
- **Маскування.** Placeholder `<x id="N"/>` (приймається й `<x id="N"></x>`); маскуються
  inline-теги, захищені токени, URL, e-mail, терміни glossary, дати, числа з `%`/`‰`/валютами
  (`€ $ £ zł Ft Kč lei kn` + ISO-коди) у форматах `1.234,5` / `1 234,5` / `1,234.5`.
  Валідатор: кожен placeholder рівно один раз, парні теги не переставлені/не перехрещені,
  теги — мультимножина, числа — мультимножина після нормалізації формату, URL і e-mail —
  побайтово. Незалежні placeholder-и можуть змінювати порядок (перестановка слів у перекладі
  легітимна) — «переставлений placeholder» ловиться для пар тегів.
- **TM key (FR-017, О-2).** SHA-256 canonical JSON рівно п'яти ключів
  (`source_language`, `target_language`, `normalized_segment_hash`,
  `provider: {name, model_version}`, `glossary_version`) через `canonical_json_bytes`/
  `sha256_hex` з `collector.contracts`. Хеш рахується від masked-тексту: сегменти, що
  відрізняються лише числами/URL/href, мають спільний TM-запис, а підстановка йде зі свого
  сегмента. Golden-значення — `tests/fixtures/translation/tm_key_golden.json`.
- **Q-009 / ідемпотентність.** До перекладача йдуть лише masked-сегменти без TM hit (дублікати
  в статті — один раз); кожен переклад (і з TM) валідовується; валідні нові пишуться в TM навіть
  коли інші впали; версія збирається лише з повного валідного набору.
- **Glossary.** `glossary/` — пакет із `default.yaml` (schema 1, `target_language: uk`, пари
  `<lang>` / `"*"`, записи `target` або `keep: true`); `version` = `canonical_sha256` вмісту;
  формат валідується з `ValueError` (дублікати, обидва/жодного з `target`/`keep`, мова).

### Залежності

- **`lingua-language-detector>=2.1`** (lock: 2.2.0) — runtime, коментар-власник WP-04 у
  `pyproject.toml`. Обґрунтування: §8 називає lingua/fastText; fastText потребує окремого
  завантаження моделі (мережа/артефакт), lingua має моделі у wheel, працює offline,
  підтримує всі 16 + `uk` + `ru` + `ca`, дає confidence і має `py.typed`/`.pyi` (mypy strict
  без ignore). Заміри (Windows, 19 мов): побудова детектора + перша класифікація ≈ 0,04 с;
  working set процесу 16,5 → 26,6 МБ (і з `with_preloaded_language_models`). **Ризик:** wheel
  ≈ 170 МБ, у venv ≈ 291 МБ — збільшить образ воркера (див. «Ризики»).
- **HTML-парсер — stdlib `html.parser`, `lxml` не додано.** Причини: без нової залежності й
  бінарного wheel; не обробляє DTD/`<!ENTITY>` і не резолвить зовнішні сутності (невідомі
  `&name;` лишаються буквально); ітеративний (libxml2 має ліміт глибини 256 без `HUGE`);
  дає сирий текст стартових тегів — атрибути (`href`) відтворюються побайтово.
- `FORBIDDEN_FOUNDATION_DEPS = ("scrapy", "httpx", "psycopg")` не зачеплено; `httpx` не
  додавався (PR2 використає той, що додасть WP-02 PR1). `tests/unit/test_foundation_config.py`
  не змінювався і зелений.

### Тести (`tests/unit/translation/**`, 173 тести, мережа заблокована pytest-socket)

| Acceptance-пункт картки | Тест(и) |
|---|---|
| identity-reassembly DOM-еквівалентний для кожного fixture; `a>strong`, `li`+`br`, `blockquote>p`, таблиця; `code/pre/script` не сегменти; `href` незмінний; hostile HTML | `test_segmenter.py`: `test_identity_reassembly_restores_equivalent_dom[*]` (6 fixtures × 2 режими), `test_nested_inline_markup_stays_inside_one_segment`, `test_lists_and_table_cells_are_separate_segments`, `test_code_pre_script_style_and_translate_no_are_not_segments`, `test_href_is_unchanged_after_translation`, `test_hostile_html_does_not_crash_or_resolve_entities`, `test_very_deep_nesting_does_not_exhaust_stack`, `test_unbalanced_inline_tags_at_segment_edges_stay_outside_segment`, `test_long_block_is_split_on_sentences_only_above_limit`, `test_attributes_are_segments_only_when_configured` |
| нормалізація для хешу, оригінал незмінний | `test_normalization_for_hash_does_not_touch_original`, `test_whitespace_and_nfc_variants_give_same_key` |
| TM key: 6 параметризованих випадків, пробіли/NFC → той самий ключ, golden | `test_memory_glossary.py`: `test_changing_any_component_changes_key[*]` (6), `test_key_payload_has_exactly_five_components`, `test_golden_key_is_stable_across_processes` |
| `uk` → `not_required`, fake не викликано, текст не записано | `test_pipeline.py::test_uk_article_is_not_required_without_provider_or_tm`, `test_language_plan.py::test_uk_article_without_foreign_segments_is_not_required` |
| змішані мови: `uk`+`en`+`pl` → рівно 2 сегменти; `de` з `uk`-цитатою; «Так» успадковує | `test_mixed_uk_page_translates_exactly_foreign_segments`, `test_mixed_uk_page_sends_exactly_two_foreign_segments`, `test_de_article_with_uk_quote_does_not_send_uk_segment` |
| U-2: `ru`/`ca` перекладаються; мова поза набором → `language_unsupported` | `test_extra_languages_are_translated_u2[ru,ca]`, `test_classifier_detects_core_extra_and_uk[*]`, `test_language_outside_core_and_extra_is_unsupported`, `test_unsupported_language_segment_is_not_sent_and_blocks_version` |
| body nullable (R-20) | `test_head_only_access_plans_title_and_lead_without_body[*]` (6 станів), `test_full_access_without_body_plans_no_body[*]`, `test_metadata_only_translates_head_and_never_creates_body[*]`, `test_full_without_body_translates_head_only` |
| preservation 100%: кейси й окремий тест на кожну мутацію | `test_preservation.py`: `test_correct_translation_passes_and_protected_values_are_restored[*]` (12 кейсів), `test_each_mutation_is_caught[*]` (цифра, втрачений/змінений URL, переставлений placeholder `<a>`/`</a>`, втрачений `</a>`, зайва цифра, дубль, невідомий, інжектований тег), `test_crossed_tags_are_caught` |
| glossary: зміна терміна → нова версія → новий ключ; do-not-translate незмінний | `test_changing_one_term_changes_version_and_tm_key`, `test_glossary_target_and_do_not_translate_are_substituted`, `test_do_not_translate_glossary_term_survives_translation` |
| Q-009: змінено 1 з 5 абзаців → 1 сегмент, v2 з 5 перекладених | `test_q009_changed_paragraph_is_the_only_segment_sent` |
| ідемпотентність: 100% TM hit, 0 викликів | `test_repeat_of_same_version_is_full_tm_hit`, `test_other_model_version_does_not_reuse_tm` |
| провал preservation → відмова збирати version, не тихий пропуск | `test_preservation_failure_blocks_version_but_keeps_valid_segments_in_tm`, `test_truncated_provider_response_is_not_assembled` |
| жодного I/O у pure-модулях | `test_pure_modules.py` (subprocess-імпорт `segmenter`/`memory`/`glossary`/`preservation`/`planner`/`pipeline` без `sqlalchemy`, `httpx`, `asyncpg`, `pymongo`, `google`) |
| тест проти мовчазного skip | `test_no_silent_skip.py`: AST-скан усіх файлів `tests/unit/translation/**` і `tests/integration/translation/**`; кожен `parametrize` обчислюється і має бути непорожнім; самоперевірка сканера на `importorskip`/`skip`/`skipif`/нестрогому `xfail` |

Доказ, що anti-skip падає на навмисному `pytest.importorskip("lingua")` і на порожньому
`parametrize` (тимчасові файли, видалені після прогону):

```text
# tests/unit/translation/test_zz_deliberate.py: lingua = pytest.importorskip("lingua")
E       AssertionError: assert ['test_zz_del...importorskip'] == []
tests\unit\translation\test_no_silent_skip.py:85: AssertionError
FAILED tests/unit/translation/test_no_silent_skip.py::test_no_skip_or_non_strict_xfail[unit/translation/test_zz_deliberate.py]
1 failed, 34 passed in 0.43s

# tests/unit/translation/test_zz_empty.py: @pytest.mark.parametrize("x", EMPTY), EMPTY = []
FAILED tests/unit/translation/test_no_silent_skip.py::test_every_parametrize_has_non_empty_values[test_zz_empty.py:6]
1 failed, 35 passed in 0.60s
```

## Команди та вивід

Усі команди — у `C:\repos\webscraper\.worktrees\wp-04-1`, Windows 11.

```text
$ uv sync --frozen
Checked 67 packages in 36ms

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
289 files already formatted

$ uv run mypy src
Success: no issues found in 87 source files

$ uv run pytest -rs tests/unit/translation
tests\unit\translation\test_pure_modules.py ......                       [ 84%]
tests\unit\translation\test_segmenter.py ...........................     [100%]
============================= 173 passed in 5.16s =============================

$ uv run pytest -m "not live" -rs -q
... (SKIPPED — лише файли поза WP-04; перелік нижче)
1241 passed, 23 skipped, 10 warnings in 2705.16s (0:45:05)

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

Skipped у повному прогоні (23; жодного у `tests/unit/translation/**`, тож gate
«`skipped > 0` у файлах WP-04» не порушено):

```text
SKIPPED tests\e2e\test_gui_runtime_contract.py:191, :207, :217, :225, :237, :263, :278, :296,
        :302, :310 (по 1), :245 ([4]) — gui не запущено на http://127.0.0.1:80
SKIPPED tests\e2e\test_runtime_suite_is_enforced.py:49, :62 — COLLECTOR_E2E_REQUIRED не задано
SKIPPED tests\unit\test_network_blocked.py:27 — Windows: loopback потрібен asyncio
```

Команди картки `pytest -m integration tests/integration/translation` (PR2+),
`python -m collector.translation.qa` (PR3) і `docker compose config --quiet` (PR2) до PR1 не
застосовні: integration-тестів і QA-runner-а в PR1 немає, compose не змінювався.

Обсяг продуктивного коду (без порожніх рядків, коментарів і docstring-ів; підрахунок
AST-скриптом): `segmenter` 343, `preservation` 172, `pipeline` 143, `planner` 129,
`detection` 76, `glossary` 63, `memory` 46, `languages` 34, `normalize` 13 — **разом 1019**
(фізичних рядків ≈ 1400 разом із docstring-ами).

## Що не перевірено

- **Linux-паритет** (CI job `python`, повний `disable_socket`) — локально лише Windows, де
  pytest-socket дозволяє loopback. PR1 не має мережевого коду, `lingua` працює offline; доказ —
  лише вивід CI. Тест «`GoogleTranslationProvider` без respx падає швидко» — PR2 (провайдера в
  PR1 немає).
- **Пам'ять `lingua` у Linux-образі воркера** — міряно лише на Windows (working set). Розмір
  образу з `lingua` не міряно (Dockerfile — не owned file WP-04).
- **Якість детекції на реальних статтях** — лише синтетичні речення по 1–2 на мову; повне
  покриття 16 + extra мов — golden corpus PR3. Поріг 0,6 і поріг «короткого» сегмента
  (30 символів) — стартові значення, не калібровані на корпусі.
- **Поведінка Google з placeholder-ами `<x id="N"/>`** — not testable offline; формат перевірить
  PR2 на записаних формах відповіді та контрольований live-прогін (лише з дозволу користувача).

## Ризики

| Ризик | Статус / пом'якшення |
|---|---|
| **Обсяг PR1 ≈ 1019 логічних рядків > ~800** картки | open. Коміти згруповані так, що PR розбивається без переписування: `4a61109`+`877df0f` (segmenter, preservation, glossary, TM key ≈ 671) → PR1a; `2ff0e7e` (detection, planner, pipeline ≈ 348) → PR1b; тести розкладаються за модулями. Рішення — за gate/оркестратором |
| Wheel `lingua-language-detector` ≈ 170 МБ (≈ 291 МБ у venv) збільшить runtime-образ | open (ризик картки). Приріст пам'яті ≈ 10 МБ на 19 мов. Варіанти для оркестратора/WP-01D: окремий образ `translation-worker` або extra-група залежностей; WP-04 Dockerfile не змінює |
| Локальні типи замість контрактів WP-01C PR2: `ArticleText`, `QualityFlag` (Literal), `not_required` як властивість плану замість `TranslationStatus` | accepted до PR2. Перехід: `QualityFlag` → `collector.contracts.TranslationQualityFlag` (`preservation_failed`, `low_language_confidence`, `provider_truncated` збігаються з мінімумом WP-01C; `language_unsupported` — О-5); `ArticleText` будується з `NewsVersionCreatedEvent` + artifact-ів; статус версії (`not_required`/`translated`/`translation_failed`) виставляє handler PR2 з `TranslationOutcome` |
| `SegmentTranslator` — мінімальний callable, не `TranslationProvider` | accepted: PR2 п.1 вводить Protocol з класифікованими помилками; `execute_plan` лишається, змінюється тип параметра |
| TM-запис спільний для сегментів, що відрізняються лише числами/URL/термінами glossary (masked-хеш) | accepted — ключ від нормалізованого masked-тексту (FR-017); ризик — узгодження відмінків навколо підставленої форми glossary; покаже golden corpus PR3 і human QA WP-06x |
| Порядок незалежних placeholder-ів не перевіряється (лише пари тегів) | accepted: перестановка слів у перекладі легітимна; підміна двох чисел місцями не ловиться автоматично — критерій 3 rubric (human QA) |
| Число з одним роздільником і трьома цифрами після нього (`1.234`) трактується як групування | accepted: placeholder відновлюється побайтово, тож хибних провалів немає; стосується лише немаскованих чисел |

## Як вимкнути або відкотити

PR1 — бібліотечний код без викликачів у runtime (handler — PR2), без міграцій, compose і змін
чужих файлів: `git revert` комітів `4a61109..HEAD` безпечний; разом із ним прибирається
залежність `lingua-language-detector` з `pyproject.toml`/`uv.lock`. Вимикати нічого не
потрібно — до PR2 код ніде не викликається.

## Dependency-запити

Немає. `src/collector/contracts/**`, `tests/unit/test_foundation_config.py` та інші чужі owned
files не змінювались. Нагадування для PR2 (не запит): `TranslationQualityFlag` WP-01C має
містити `language_unsupported` (рішення О-5); якщо ні — WP-04 PR2 подасть
`docs/plan/deps/WP-04-to-WP-01C.md`.
