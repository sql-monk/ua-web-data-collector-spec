# ADR-0007: Глобальна унікальність важливіша за помісячне партиціювання для трьох таблиць PR2; `parse_key` — ідентичність parse-кроку

| Поле | Значення |
|---|---|
| Date | 2026-09-23 |
| Owner | WP-01A |
| Status | accepted |

## Context

ТЗ §9.1 вимагає: «Великі fetch/event tables партиціонуються щомісяця за `fetched_at/created_at`».
«Спільні вимоги» картки WP-01A уточнюють список: `fetches`, `raw_objects`, `change_events`,
`outbox_events`, `audit_log`. `audit_log` і `fetches` партиційовано в PR1/PR2 без відхилень.

Для трьох інших таблиць PR2 буквальне виконання вимоги зіткнулося з обмеженням PostgreSQL:
unique-констрейнт або unique-індекс на партиційованій таблиці зобов'язаний включати весь
partition key (documented limitation, не налаштування). Кожна з трьох таблиць має інваріант,
який є не операційною зручністю, а вимогою коректності самого протоколу WP-01A:

- `raw_objects.sha256` — глобальна дедуплікація сирих байтів (§9.3 п.4): той самий контент,
  отриманий різними fetch, зберігається рівно один раз;
- `outbox_events.event_id` — ідемпотентність доставки (§7.3, R-30): consumer розрізняє
  доставки за `event_id`, дублікат з іншим партиційним значенням часу зламав би контракт
  «consumer ідемпотентний за `event_id`»;
- `change_events.event_id` — той самий інваріант для доменних подій змін.

Якби `created_at`/`fetched_at` увійшов у ці unique-ключі (обов'язкова умова PostgreSQL для
partition-local unique), дедуплікація стала б помісячною: той самий `sha256` або `event_id` у
двох різних місяцях більше не зловився б як дублікат. Це тихо ламає саме той інваріант, заради
якого unique існує, і виявляється лише на межі місяця — гірше за відсутність партиціювання.
Рішення прийняте до відкриття PR (spec-review PR2, `docs/plan/reports/WP-01A/spec-review-pr2.md`
§5.1, знахідка SR-3, D-1) і зафіксоване тут разом із правкою ТЗ §9.1.

Окреме, але спорідненого походження питання (spec-review PR2 §5.2, D-2): чим є ідемпотентність
`record_parse_result` — «той самий normalized artifact» чи «той самий parse-крок»? Перший
варіант («той самий artifact → той самий task») суперечить R-36 (A→B→A має дати нову версію:
другий parse того самого fetch, що повернув попередній стан A, — це нове, третє спостереження,
а не те саме, що перше) і §16.3 («повторний parse/projection тієї самої raw відповіді без
дублікатів» — про той самий fetch, а не той самий вміст).

## Decision

### D-1: `raw_objects`, `change_events`, `outbox_events` лишаються непартиціонованими; `fetches` і `audit_log` — партиціоновані, як і раніше

Партиціюється те, у чого головний інваріант — часовий діапазон (`fetches`, `audit_log`: запити
й retention природно йдуть по календарних місяцях, глобальної унікальності поза партицією
немає). Не партиціюється те, у чого головний інваріант — глобальна унікальність за нечасовим
ключем (`raw_objects.sha256`, `outbox_events.event_id`, `change_events.event_id`): для цих
трьох таблиць коректність дедуплікації переважає операційну зручність партиціювання.

- **`raw_objects`** — приймається без застережень: це не fetch/event-таблиця в термінах §9.1
  (сирий об'єкт-сховище за content hash, а не часовий журнал подій), і зростання обмежене тим
  самим `sha256`-дедупом, який і є причиною відхилення.
- **`outbox_events`** — приймається за умови: рядки не накопичуються без межі, бо є шлях
  видалення опублікованих. `purge_published(older_than)` — WP-01A PR3; виклик (maintenance-цикл
  або CLI-команда) — WP-12. До появи `purge_published` таблиця росте необмежено — це прийнятний
  тимчасовий стан (Q-005 safe default: жодного DELETE, доки видалення не реалізоване явно), а не
  постійна відсутність retention.
- **`change_events`** — приймається тимчасово, з явним ризиком необмеженого зростання (Q-005).
  Розглянута й відхилена на зараз альтернатива: місячні партиції `change_events` + окрема
  вузька непартиціонована таблиця `change_event_ids(event_id PK)` — глобальна унікальність живе
  в тонкій допоміжній таблиці (лише PK, без даних), а сам `change_events` партиціюється по
  `created_at` і дедуплікується вставкою в `change_event_ids` в тій самій транзакції
  (`INSERT ... ON CONFLICT DO NOTHING` там, потім умовний INSERT у `change_events`). Відхилено
  для PR2, бо додає другу таблицю й транзакційну звʼязку заради вигоди, яка поки не потрібна
  (обсяг `change_events` на MVP-масштабі малий: один рядок на `applied_to_current AND
  state_changed` ack). Тригер перегляду: обсяг `change_events` перевищує 2× річний прогноз §15,
  або перший реальний запит на partition pruning/retention для цієї таблиці — тоді застосувати
  альтернативу вище. Owner тригера й реалізації — WP-01A PR3 / WP-12.

Умови до злиття PR2 (усі виконані цим документом і супутньою правкою): (1) цей ADR;
(2) правка ТЗ §9.1 (рядок про партиціювання fetch/event-таблиць, з посиланням на ADR-0007);
(3) правка «Спільних вимог» картки WP-01A і формулювання тесту T-5 (partitioning).

### D-2: ідемпотентність `record_parse_result` — за ідентичністю parse-кроку (`parse_key`), не за вмістом artifact

`projection_tasks.parse_key` — детермінований hash від `(fetch_id, raw_sha256, parser_version,
entity_uuid, target_collection)`: повторний виклик `record_parse_result` із тими самими
вхідними даними parse-кроку повертає той самий task (`created=False`), а не створює дублікат.
Це відповідає:

- §16.3 «повторний parse/projection тієї самої raw відповіді без дублікатів» — «та сама raw
  відповідь» означає той самий `fetch_id`/`raw_sha256`, не той самий вміст normalized
  artifact;
- §9.3 п.5 і §7.3 крок 3 — ідемпотентність за ключем parse-кроку, не за результатом;
- R-36 — A→B→A (три послідовні fetch того самого URL, третій повторює стан першого) дає три
  різні parse-кроки (три різні `fetch_id`) і тому три різні `projection_version`, навіть якщо
  normalized-байти третього fetch побайтово збігаються з першим. Дедуплікація вмісту —
  окрема відповідальність `normalized_artifacts` (unique `object_key`, з дедупом за `sha256`
  усередині репозиторію), не `parse_key`.

`target_schema_version` навмисно не входить у `parse_key`: зміна цільової схеми MongoDB має
супроводжуватися зміною `parser_version` (parser і схема еволюціонують разом), інакше той
самий parse під нову схему виглядав би як повтор і не отримав би reprojection.

## Consequences

- Дедуплікація `raw_objects`/`outbox_events`/`change_events` лишається справжньою глобальною
  дедуплікацією, а не помісячною — головна причина рішення. Ціна — ці три таблиці ростуть без
  партиційного pruning; для `outbox_events` це компенсовано запланованим `purge_published`
  (owner WP-01A PR3, виклик WP-12), для `change_events` — тимчасово прийнятий ризик з тригером
  перегляду (owner WP-01A PR3 / WP-12), для `raw_objects` — власним `sha256`-дедупом.
- `change_events` без партиціювання й без retention до PR3/WP-12 — відкритий операційний ризик
  (Q-005). Якщо обсяг перевищить тригер (2× річний прогноз §15) раніше, ніж WP-01A PR3 чи
  WP-12 встигнуть це виконати, потрібен позаплановий forward-fix (місячні партиції +
  `change_event_ids`, альтернатива вище).
- `parse_key` покладається на дисципліну викликача (WP-02). Три умови зафіксовані окремим
  dependency-запитом `docs/plan/deps/WP-01A-to-WP-02.md`: (1) ключ normalized artifact-а —
  функція `(sha256, entity_uuid)`, інакше об'єкти без посилань лишаються в store назавжди
  (`code-review-pr2-r2.md`, N-1); (2) `fetch_id` — справжній `fetches.fetch_id` кожного HTTP
  запиту, не довільне значення; (3) зміна схеми цільової collection = зміна `parser_version`.
  Порушення будь-якої умови приховано ламає ідемпотентність або reprojection, а не падає
  одразу — це прийнятий компроміс на користь чіткого контракту, задокументований явно.
- `record_parse_result` додатково перевіряє узгодженість lineage (spec-review PR2, SR-2,
  закрито в цій сесії PR2): `attempt.fetch_id`, `raw_sha256`, `parser_version`, `domain` мають
  збігатися між `parse_attempt` і `artifact_ref`, інакше `InvalidValueError` до першого запису —
  це не скасовує залежність від WP-02 вище, а звужує клас помилок, які БД ловить сама.

## Related

- ТЗ: §9.1 (партиціювання fetch/event-таблиць — правка з посиланням на цей ADR; «artifact
  projection key»), §9.3 п.4–5, §7.3 кроки 2–3, §16.3, §15 (обсяг/прогноз), §20 (Q-005 safe
  default, формат ADR).
- Реалізація: `src/collector/persistence/postgres/models/outbox.py` (docstring з
  обґрунтуванням і розглянутими альтернативами), `partitions.py` (`PARTITIONED_TABLES`),
  `repositories/projection.py` (`record_parse_result`, `_require_consistent_lineage`,
  `parse_key`).
- Тести: `tests/unit/persistence/postgres/test_metadata.py`, `tests/integration/postgres/test_projection.py::test_repeated_record_for_same_artifact_returns_same_task`,
  `tests/integration/postgres/test_projection.py::test_attempt_and_artifact_lineage_must_describe_one_parse`,
  `tests/integration/postgres/test_fetch_partitions.py`.
- Звіти: `docs/plan/reports/WP-01A/spec-review-pr2.md` §5 (D-1, D-2), §9 (SR-2, SR-3), §6
  (Q-005); `docs/plan/reports/WP-01A/code-review-pr2-r2.md` (N-1); `docs/plan/reports/WP-01A/implementation-pr2.md` «Fixes after gate 4».
- Dependency: `docs/plan/deps/WP-01A-to-WP-02.md` (умови D-2 для викликача).
