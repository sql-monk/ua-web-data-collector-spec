# WP-01C PR2 — spec review (gate 4)

Branch `wp/01c-2-payload-news-contracts` @ `0dfa5e3`, diff `git diff main...HEAD` (40 файлів, +5631/−20).
Вхід: картка `docs/plan/cards/WP-01C.md` (загальні частини + розділ «PR2»), ТЗ §5.1, §5.4, §5.5, §9.2,
§9.3, §9.4, §9.6, §12.1, §16.3, §17.2 (WP-01C), §18, Додаток C; `REVIEW.md` R-04, R-18, R-20, R-30,
R-36, R-42, R-43; ADR-0003/0004; звіти `implementation-pr2.md`, `testing-pr2.md`, `code-review-pr2.md`;
картки споживачів `WP-01B.md` PR2, `WP-01A.md` PR3b, `WP-04.md` PR1/PR2.

## Вердикт

**accept** (з умовами до merge: SR-5, CI). `missing` — 0; `partial` — 6 позицій (див. §10). Знахідок
critical/high немає. Medium SR-1 і SR-3 не блокують: SR-1 закривається додаванням поля
(для ще не злитого `1.0` — без bump), SR-3 — документацією. Їх варто закрити до merge, поки `1.0`
не злитий і не має споживачів.

## Власна верифікація (read-only, без Docker)

```text
$ uv run collector contracts export --check
schemas up to date: schemas
exit=0
$ uv run mypy src
Success: no issues found in 81 source files
$ uv run ruff check .
All checks passed!
$ uv run pytest tests/unit/contracts tests/contract/contracts -q -p no:cacheprovider
657 passed in 63.39s (0:01:03)
$ find schemas -name "*.json" | wc -l
41
```

Повний прогін `-m "not live"` я не повторював. Беру звіти: у тестувальника 1190 passed / 23 skipped
(`testing-pr2.md:32`). Після виправлень у реалізатора було 2 неатрибутовані падіння, а повторний
прогін двома частинами дав 1081 + 295 passed, 0 failed (`implementation-pr2.md:245-251`). CI ще не
запускався.

## 1. Acceptance

### 1.1 §17.2, рядок WP-01C

| Вимога | Доказ | Статус |
|---|---|---|
| «…artifact, projection command/ack, … domain event … schemas; compatibility fixtures green» — у PR2 розширено на payload, records і news | 9 нових snapshot-ів (32 → 41); fixtures `tests/fixtures/contracts/documents/{normalized_projection_payload,entity_projection_version,observation_record,seller_contact_observation,review_question_record,news_translation,news_version_created}.v1.0.json`; `tests/contract/contracts/test_compatibility.py:31::test_every_versioned_document_has_a_fixture`; `test_pr2_tester_contract.py` (compat minor/major, 7 моделей × 7 версій); мій прогін — 657 passed | evidenced |

### 1.2 Картка PR2: вимоги 1–6

| # | Вимога картки | Доказ | Статус |
|---|---|---|---|
| 1 | `NormalizedProjectionPayload`: `schema_version`, `entity_kind`, `entity_uuid`, `source`, `identity_hash`, `core`/`attributes`/`latest_state`, `time`, observation-поля | `src/collector/contracts/payload.py:57-99`; `test_payload.py:32,43`; `test_pr2_tester_contract.py:245::test_wp01b_payload_input_fields` | evidenced. Відхилення прийнятні: `source: SourceRef` (бо §9.2 вимагає `canonical_url`); `time` розбито на `source_time` + `system_time`; окремого `observed_at` в observation немає, береться `system_time.observed_at`. Усе задокументовано в `docs/contracts.md` §11.1 |
| 1 | Перевірка `entity_uuid` проти ref окремою функцією | `payload.py:106-130` (`check_payload_matches_artifact`, додатково перевіряє schema_version і domain); `test_payload.py:55,60,66`; мутація M1 (`testing-pr2.md:102`) | evidenced |
| 1 | `core`/`attributes` bounded, без unbounded arrays (§9.2) | `_base.py:66-75` + `BoundedJsonObject` (depth 8 / items 256 / keys 512 / string 64 Ki / key 256 / int64 / блок 1 MiB); `test_payload.py:123,135`; `test_gate2_fixes.py:93-125`; мутація M2 | evidenced |
| 1 | Вибрати й задокументувати snapshot mongo/events | `schemas/events/normalized_projection_payload.v1.json`; `docs/contracts.md` §11.1; `test_pr2_snapshots.py:36` | evidenced |
| 2 | `EntityProjectionVersion` з усіма полями картки | `records.py:63-115`; `schemas/mongo/entity_projection_version.v1.json`; `test_records.py:41-112` | evidenced (див. SR-3 про семантику `state_changed`) |
| 3 | `ObservationRecord`, `SellerContactObservation`, `ReviewQuestionRecord` (мінімальні) + snapshots у `schemas/mongo/` | `records.py:118-202`; `schemas/mongo/{observation_record,seller_contact_observation,review_question_record}.v1.json`; `test_records.py:122-218`; `test_pr2_tester_contract.py:273,281` (index-поля §9.2) | evidenced (мінімальність узгоджена з карткою; про розрив із §9.2 див. SR-4) |
| 4 | `TranslationStatus` — рівно 4 значення, закритий, єдиний | `enums.py` (`TranslationStatus`); `test_news.py:48`; `test_pr2_tester_adversarial.py:248`; мутація M5 | evidenced |
| 4 | `TranslationQualityFlag` — мінімум 3 прапорці + `language_unsupported` для WP-04 | `enums.py` (4 значення); `test_news.py:63`; `test_pr2_tester_adversarial.py:267` | evidenced |
| 4 | `NewsTranslation`: усі поля; `body_*` nullable або artifact ref; ключ ідемпотентності | `news.py:92-159`; `test_news.py:72-167`; `schemas/common/news_translation.v1.json` | evidenced. `cost: Money` замість `cost_minor` + валюта — та сама семантика §5.1. Про retry plan див. SR-1, про `target_language` — SR-2 |
| 4 | U-2: мови поза 16 — звичайні значення, окремого прапорця немає | `news.py:29-31` (`LanguageCode` — pattern, не enum); `test_news.py:162::test_any_well_formed_source_language_is_valid_u2` | evidenced (див. §6) |
| 5 | `NewsVersionCreatedEvent` + canonical bytes через `encode_event` + snapshot | `news.py:48-89`; `events.py` (`PublishableEvent`, `media_type` як ClassVar); `schemas/events/news_version_created.v1.json`; `test_news.py:215,228`; handwritten golden і pinned sha (`testing-pr2.md:73`) | evidenced. Додаткові поля `event_id`/`event_type`/`version_number` потрібні для `outbox_events` |
| 6 | TM key у контракт не додається | `grep` у `src/collector/contracts/**` — функції TM key немає; `docs/contracts.md` §11.3 | evidenced |

### 1.3 Картка PR2: тести й acceptance

| Пункт | Доказ | Статус |
|---|---|---|
| Команди PR1 зелені | власна верифікація вище; `implementation-pr2.md:214-236` | evidenced (локально; CI — умова merge) |
| Нові snapshots, drift = fail | `export --check` exit 0; `test_schema_snapshots.py`; мутація M3 | evidenced |
| Compatibility fixtures v1.0 для кожної нової моделі | див. 1.1 | evidenced |
| `NewsTranslation` з `body=None` валідний, з naive datetime — ні | `test_news.py:79,91` | evidenced |
| Payload з чужим `entity_uuid` відхиляється | `test_payload.py:55` | evidenced |
| `encode_event(NewsVersionCreatedEvent)` byte-equivalent після round-trip | `test_news.py:215`; `test_pr2_tester_contract.py` (golden, hash seeds) | evidenced |
| «Жодного I/O» покриває нові модулі | `tests/contract/contracts/test_no_io_imports.py:80` | evidenced |
| `docs/contracts.md`: нові моделі й правило розширення | `docs/contracts.md:261-338` (§11.2 «Правило розширення доменними WP») | evidenced |
| Dependency-запити WP-01B→WP-01C і WP-04→WP-01C п.1 закриті | Окремих deps-файлів немає, запити зведено в картки (`implementation-pr2.md:188-195`); покриття споживачів — `test_pr2_tester_contract.py:245-301`; §6 цього звіту | evidenced (оновити статус у ledger має оркестратор) |

### 1.4 Дотичні пункти §16.3 (рівень контракту)

| Пункт §16.3 | Що дає PR2 | Статус |
|---|---|---|
| «повторний parse або projection … не створює дублікати» | `projection_task_id` у всіх records (основа unique index §9.2); `ObservationRecord` задокументовано як idempotent за task (`records.py:119-123`) | partial — сам доказ дає WP-01B PR2 (integration) |
| «доставка … `3, 1, 2` залишає current на версії 3» | CAS-інваріант `applied_to_current` в `EntityProjectionVersion` (`records.py:109-114`); `test_records.py:73` | partial — доказ у WP-01B PR2 |
| «late-arriving/backdated fixtures зберігають source та system time окремо» | payload відхиляє `source_*_at == fetched_at` (`payload.py:88-95`); `test_payload.py:94`; `test_records.py:112`; temporal-блок тестувальника | partial — інтервали вже є з PR1; E2E — WP-11A/14 |
| «lineage від експортованого рядка до raw artifact…» | `Lineage` обовʼязковий у всіх 4 records; `lineage.projection_task_id == projection_task_id` (`records.py:42-45`) | partial — API lookup: WP-11A |
| «…`content_access`… оригіналом і українським перекладом» | `content_access` в event; `NewsTranslation` | partial — доказ дають WP-06x/WP-14 |
| «30 перекладів… human QA» | — | not applicable (WP-04/06x; контракт не впливає) |

## 2. DoD §18

| # | Пункт | Доказ | Статус |
|---|---|---|---|
| 1 | Один WP, без сторонніх змін | `git diff main...HEAD --stat`: лише `src/collector/contracts/**`, `schemas/**` (+`README.md`), `tests/{unit,contract,fixtures}/contracts/**`, `docs/contracts.md`, `docs/plan/reports/WP-01C/*-pr2.md` — усе це owned files картки PR2 і ADR-0004 (`schemas/common/`) | evidenced |
| 2 | Formatter, lint, types, тести | власна верифікація; `testing-pr2.md:10-60`; `implementation-pr2.md:214-251` | evidenced (локально; CI не запускався — умова merge) |
| 3 | Зміна схеми має migration і compatibility evidence | Compatibility: fixtures + `test_compatibility.py`. Migration: нові моделі без даних; Mongo validators — перша міграція WP-01B PR2 (`WP-01B.md:113-118`), PG-таблиці — WP-01A PR3b. Зміни в злитих PR1-моделях (`Strict` на масивах, `CanonicalEncodingError`) не міняють JSON Schema (drift зелений) і зачіпають лише хибний Python-шлях | evidenced |
| 4 | Зміна timestamp-контракту має temporal evidence | `test_payload.py:94,101`; `test_records.py:112,196`; `test_pr2_tester_adversarial.py` (non-UTC у 8 моделях); мутація M4 | evidenced |
| 5 | Новий адаптер: manifest/fixtures/… | — | not applicable (адаптерів немає) |
| 6 | Документація, метрики, runbook | `docs/contracts.md` §7, §11; `schemas/README.md`. ADR-0003 застарів (SR-6, етап docs). Метрик/runbook контракти не мають | partial |
| 7 | Secret scan; контакти не у fixtures з приватних джерел | pre-commit `Detect hardcoded secrets … Passed` (`implementation-pr2.md:234`); контакти у fixture синтетичні (`044 000-00-00`, `Seller@Example.com` — `seller_contact_observation.v1.0.json`, `factories.py:255-256`) | evidenced |
| 8 | Findings рецензентів мають статус `fixed` / `accepted owner/date` / `n/a` | gate 2: M-1, M-2, L-1, L-2 — `fixed`, перевірено в коді (`_base.py`, `canonical.py:125-129`, `news.py:69`) і тестах `test_gate2_fixes.py`. **Без статусу:** gate 2 I-1, I-2; gate 3 — усі 4 low (`code-review-pr2.md:10-13`); ризики в `implementation-pr2.md:167-180` без owner/дати | partial → SR-5 |
| 9 | Merge лише після CI і review; зафіксовано SHA | ще не злито | not applicable на gate 4 (умова merge) |

## 3. Додаток C

| Ціль | Що покриває PR2 | Доказ | Статус |
|---|---|---|---|
| Узгодженість двох БД (§7.3, §9) | Вхід projector-а (payload + перевірка ref), version record з CAS-інваріантами, observation records | `payload.py`, `records.py`; `test_payload.py`, `test_records.py` | evidenced (контракт); integration — WP-01B |
| Історія без втрат/дублів | version record на кожну task (R-36); observation лише `changed`/`heartbeat`; `projection_task_id` для unique | `records.py:63-150` | evidenced (контракт) |
| Часова коректність (§9.6) | R-43 у payload і snapshot; nullable source time у reviews; лише aware UTC | див. §2 п.4 | evidenced |
| Оригінал + переклад (§5.4) | `NewsVersionCreatedEvent`, `NewsTranslation`, `TranslationStatus`, `TranslationQualityFlag`, R-20, R-04 | `news.py`; `test_news.py` | partial — retry plan для `translation_failed` (§12.1), SR-1 |
| Повні публічні поля (§5) | typed + raw contacts; review/question мінімальні | `records.py:153-202` | partial — доменні поля review (§9.2 rating/text/status) і offer/vehicle — WP-07/WP-09 (SR-4) |
| Незалежна реалізація (§17, §18) | 41 snapshot, drift-check, compat fixtures, тести споживачів, правило розширення | `schema_export.py`; `docs/contracts.md` §11.2; `test_pr2_tester_contract.py:245-301` | evidenced |

## 4. Регресії REVIEW.md

| R | Що має бути в коді/тестах | Доказ | Статус |
|---|---|---|---|
| R-18 | Статус перекладу — окремий enum, не пʼята «вісь стану»; `content_access` береться з §5.5 | `test_news.py:57::test_translation_status_is_not_a_state_axis`; `news.py:22,72` (`ContentAccess`); `test_pr2_tester_adversarial.py:248` | evidenced |
| R-20 | Body nullable: переклад без body валідний; event без body для `metadata_only`, body заборонений для `metadata_only/blocked/challenge/gone` | `news.py:36-45,80-89,122-123`; `test_news.py:79,188,201`; `test_pr2_snapshots.py:45,57` | evidenced |
| R-30 | Нова публічна подія відокремлена і від `projection.command`, і від `domain.changed` | окремий клас, media type, `decode_*`; `test_news.py:228` | evidenced |
| R-42 | Canonical bytes + SHA-256 для `news.version_created`, byte-equivalent після round-trip | `events.py` (`encode_event` приймає `PublishableEvent`, media type з ClassVar); `test_news.py:215`; handwritten golden і pinned sha `bc3c6c4a…859a`; 4 hash seeds; мутація M3 | evidenced. Fallback на `event_artifact` для news-події немає — поля bounded (L-2 fixed), лише `ArtifactUri` без межі довжини (info, для ADR-0003) |
| R-43 | `fetched_at` не потрапляє в source time; source time nullable | `payload.py:88-95`; `test_payload.py:94`; `test_records.py:112`; `test_pr2_tester_adversarial.py` (null source time); мутація M4 | evidenced |
| R-36 (дод.) | Version record обовʼязковий на кожну task | `records.py:63-115`; `test_records.py:73` | evidenced |
| R-04 (дод.) | Ключ перекладу містить article version, provider/model і glossary; перевіряється | `news.py:130-141`; `test_news.py:96` | evidenced |

## 5. Q-питання §20

У картці Q-питань немає. Q-008/Q-009 (бюджет, сегментний переклад) контракт не зачіпає, ADR до них
пише WP-04. Q-005 (retention) залежить від семантики `EntityProjectionVersion.state_changed`
(§9.7): див. SR-3. Висновок: not applicable, лише з приміткою SR-3.

## 6. Покриття споживачів

| Споживач | Вимога споживача | Покриття контрактом | Статус |
|---|---|---|---|
| WP-01B PR2 п.1 | payload валідується контрактом; hash і розмір перевіряє викликач; `artifact.entity_uuid == command.entity_uuid` | `NormalizedProjectionPayload`, `check_payload_matches_artifact`, `PayloadArtifactMismatchError(ValueError)` → permanent; entity у command проти artifact перевіряє PR1 (`ProjectionCommand`) | evidenced |
| WP-01B PR2 п.2(б) | version record завжди, зі snapshot або artifact | `EntityProjectionVersion` | evidenced; **пастка SR-3**: для не-applied task картка WP-01B (п.3) вимагає `state_changed=false` у receipt, а контракт version record вимагає `state_changed = (state_hash ≠ previous_state_hash)` |
| WP-01B PR2 п.2(г) | observation `changed`/`heartbeat` | `ObservationRecord.reason: ObservationReason` | evidenced |
| WP-01B PR1 validators | `$jsonSchema` із snapshot-ів, `_id` UUID | усі 4 нові records мають `_id` required, `format: uuid`, `additionalProperties: false` (`test_pr2_snapshots.py:28`) | evidenced |
| WP-01A PR3b п.1, 4 | payload outbox `news.version_created`; `status` — `TranslationStatus`, включно з `not_required`/`translation_failed`; ON CONFLICT за `translation_idempotency_key` | `NewsVersionCreatedEvent` (поля outbox — `docs/contracts.md` §11.3); `NewsTranslation.translation_idempotency_key` перевіряється; `test_pr2_tester_contract.py:301::test_wp04_wp01a_translation_fields` | evidenced |
| WP-04 PR2 (вхід) | `article_version_id`, `original_language`, `source_locale_raw`, `content_access`, `content_hash`, refs title/lead/body, backfill | `news.py:62-78`; `test_pr2_tester_contract.py:285::test_wp04_input_fields_in_news_event`; `decode_news_version_created` | evidenced |
| WP-04 PR2 (вихід) | version: переклад title/lead/body або artifact ref, provider/model/glossary, source hash, status, flags, character count/cost | `NewsTranslation`; `test_translation_idempotency_key_same_for_head_and_body_jobs` | evidenced |
| WP-04 О-5/U-2 | `language_unsupported` + `translation_failed` **з retry plan** | прапорець і статус є; **окремого поля retry plan немає** — WP-04 кладе його в `error_code` job-и (`WP-04.md:162`), а §12.1 рахує coverage за записами перекладу | partial → SR-1 |
| WP-04 PR1 п.5 / О-2 | TM key — функція WP-04 на `canonical_json_bytes`/`sha256_hex` з контрактів | обидві експортуються (`__init__.py:265,290`); TM key у контракт не додано | evidenced |
| U-2 ↔ enum-и мов | мови поза 16 (`ru`, `ca`, будь-яка інша) не відхиляються контрактом | enum-а мов у контрактах немає, `LanguageCode` — лише формат BCP 47 (`news.py:29-31`); `CORE/EXTRA_SOURCE_LANGUAGES` живуть у WP-04. Суперечності немає: мову поза 16 і поза extra відсіює WP-04 (прапорець `language_unsupported`), а не контракт | evidenced |

## 7. Знахідки

| ID | Severity | file:line | Опис | Рекомендація / owner |
|---|---|---|---|---|
| SR-1 | medium | `src/collector/contracts/news.py:92-159` | §12.1: coverage перекладу = 100 %, «крім записів зі статусом `translation_failed` **і явним retry plan**». WP-04 О-5 теж вимагає «`translation_failed` з retry plan». У `NewsTranslation` немає поля для retry plan або коду причини, тож gate §12.1 (WP-12/WP-11A) за записом перекладу не відрізнить failed-з-планом від failed-без-плану. | Поки `1.0` не злитий, додати `failure_code`/`retry_plan: str \| None` (bounded) і validator «обовʼязковий при `translation_failed`, заборонений інакше». Альтернатива — accepted з owner WP-04/WP-12 і датою + пізніший optional minor. Owner WP-01C |
| SR-2 | low | `news.py:111` | §5.4: «target language `uk`». Контракт приймає будь-який `LanguageCode`. Реалізатор сам зазначає, що звузити пізніше — це major; розширити `Literal["uk"]` пізніше — minor. | `target_language: Literal["uk"]` зараз або accepted з owner WP-04 і датою. Owner WP-01C |
| SR-3 | medium | `records.py:106-114`; `docs/contracts.md:299` | Для не-applied task (late arrival, 3-1-2) `EntityProjectionVersion.state_changed` = `state_hash ≠ previous_state_hash` (може бути `true`), а `AppliedProjectionReceipt.state_changed` для тієї ж task має бути `false` (`projection.py:119`, картка WP-01B PR2 п.3). Одна назва поля — дві семантики, і ця розбіжність ніде не названа. Якщо WP-01B скопіює значення з receipt у version record, validator відхилить запис і транзакцію буде перервано. Від семантики залежить також retention §9.7 («`state_changed=false` — 90 днів hot»). Сама семантика контракту безпечна: late-arriving версія з відмінним hash не компактиться. | Явно описати розбіжність у `docs/contracts.md` §11.2 і в картці WP-01B PR2 п.2(б)/п.3 (docs stage / оркестратор). Код не змінювати |
| SR-4 | low | `records.py:177-202` | §9.2, рядок review/question, вимагає `author/name when public; rating/text/status`. Картка свідомо обмежила PR2 мінімумом, а доменні поля WP-09 додасть як optional minor. Тоді `text/status` у validator ніколи не стануть обовʼязковими, як того вимагає §9.2; зробити їх required — major. | Оркестратору: зафіксувати в картці WP-09, що це або optional (з аргументом «when public» / ADR), або major `2.0` до перших даних WP-10. PR2 не змінювати |
| SR-5 | low (DoD п.8) | `code-review-pr2.md:10-13`; `testing-pr2.md:126-127`; `implementation-pr2.md:167-180` | Gate 3 low #1–#4 і gate 2 I-1/I-2 не мають статусу; ризики реалізатора — без owner/дати. | До merge позначити кожен як `fixed` / `accepted owner/date` / `n/a`. Мої пропозиції: #1 (bytes-ключі lax) — fixed або accepted WP-01C; #2 (`1` vs `1.0` у hash) — задокументувати в `docs/contracts.md` §7, owner WP-01C docs; #3 (мутабельні dict) — задокументувати правило «не мутувати», owner WP-01C/WP-01B; #4 (текст перекладу без межі, порядок прапорців) — accepted WP-04; I-2 → SR-6. Owner — реалізатор WP-01C |
| SR-6 | info (docs) | `docs/decisions/0003-canonical-event-serialization.md:11-25,72-74,96-105,130-131,133-154` | ADR-0003 застарів: (а) описує `encode_event`/`decode_event` лише для `DomainChangedEvent`, тоді як тепер `PublishableEvent` = `domain.changed` \| `news.version_created`, media type береться з ClassVar класу, є `decode_news_version_created`; (б) fallback 256 KiB → artifact є лише для `domain.changed`, а news-подія покладається на bounded поля; (в) не згадані strict-масиви (M-1: `set`/`tuple` відхиляє модель, хоча canonical-функція досі сортує set), межі `BoundedJsonObject` (блок 1 MiB > 256 KiB) і lone surrogate → `CanonicalEncodingError`. Формат bytes не змінився, тож потрібна поправка, а не новий ADR. | **Так, оновити на етапі docs** (owner WP-01C docs-writer), рядок «Amended 2026-09-24 (PR2)» |
| SR-7 | info | `implementation-pr2.md:245-261` | 2 неатрибутовані падіння в повному прогоні після виправлень. Повторний прогін частинами — 0 failed; тестувальник — 1190 passed. | Зелений CI job `python` — умова merge |

## 8. Відкрите питання: `AppliedProjectionReceipt` без `_id`

У PR1-контракті receipt не має `_id`, а snapshot `schemas/mongo/applied_projection_receipt.v1.json`
має `additionalProperties: false` без `_id`. Буквальний validator відхилив би кожен Mongo-документ.
**WP-01B PR1 уже розвʼязав це у своєму mapping:** `_id = projection_task_id` додається при записі і
відкидається при читанні (`.worktrees/wp-01b-1/src/collector/persistence/mongo/repositories.py:11,54,59`),
а validator отримує `_id: binData` окремим прапорцем (`validators.py:183,190-192`). Це відповідає
§9.2 «PK `projection_task_id`». Висновок: змінювати контракт для WP-01B не потрібно, dependency-запит
не потрібен. Два патерни (`_id` у контракті для нових records, `_id` у persistence для receipt)
варто описати в `docs/contracts.md` §9/§11.2 на етапі docs. Додати optional `_id` у receipt пізніше —
це minor, але лише якщо WP-01B попросить.

## 9. Пропозиції ADR / зміни ТЗ

- ТЗ змінювати не потрібно. SR-1 і SR-2 закриваються в коді WP-01C, SR-3 — документацією.
- SR-4 — рішення оркестратора для картки WP-09. Якщо review-поля лишаться optional, потрібна
  примітка до §9.2 або ADR («обовʼязкові, коли їх публікує джерело»).
- ADR-0003 — поправка на етапі docs (SR-6).

## 10. Підсумок статусів

`missing`: 0. `partial`: 6 позицій — DoD п.6 (ADR-0003), DoD п.8 (SR-5); Додаток C «Оригінал + переклад»
(SR-1) і «Повні публічні поля» (SR-4); споживач WP-04 О-5 (SR-1); §16.3 — пункти рівня контракту
(5 рядків, доказ дають WP-01B/11A/14; рахуються як одна позиція). Серед acceptance картки PR2 і §17.2 `partial`
немає.
