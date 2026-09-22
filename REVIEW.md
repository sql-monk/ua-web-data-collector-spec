# Критичне рев’ю технічного завдання

Дата рев’ю: 2026-09-22

Об’єкт: `TECHNICAL_SPECIFICATION.md`

Результат: усі критичні й суттєві зауваження виправлено у версії 1.3.

## Чекліст і виправлення

| № | Рівень | Знахідка | Статус | Виправлення |
|---|---|---|---|---|
| R-01 | Критичний | Початковий scope виключав телефони, імена, VIN, повні тексти й контакти, хоча дослідницький dataset має містити всі публічні поля. | Fixed | Розширено §1, §2, §5, FR-018; додано versioned contacts, seller profiles, VIN, reviews/questions і raw provenance. |
| R-02 | Критичний | Початковий перелік новин охоплював лише Україну. | Fixed | У §4.1 додано 19 країн, 58 джерел із рейтингом, ISO-коди й мови. Чехословаччину розділено на Чехію та Словаччину; Англію нормалізовано до GB. |
| R-03 | Критичний | Не було pipeline повного тексту та перекладу українською. | Fixed | Додано extraction, translation queue, Google Cloud Translation adapter, local fallback, translation memory, glossary, immutable versions і translation lineage. |
| R-04 | Високий | Переклад міг перезаписати оригінал або стати невідтворюваним після зміни моделі. | Fixed | Оригінал, cleaned text і кожна версія перекладу зберігаються окремо; ключ містить article version, provider/model і glossary version. |
| R-05 | Високий | Не було вимірюваної якості перекладу. | Fixed | Додано multilingual golden corpus, preservation tests, 30 human-reviewed samples на мову та rubric із порогом 1.8/2 і нульовою толерантністю до зміни фактів/чисел. |
| R-06 | Високий | Work package «News adapters» був завеликим і не давав незалежним агентам працювати без конфліктів. | Fixed | Виділено SDK і translation core; країни розбиті на WP-06A—G, а vehicle/catalog adapters — на незалежні WP-08A—D і WP-10A—H. |
| R-07 | Високий | Масштаб 2 млн сутностей/2 ТБ не відповідав 19 країнам і повним текстам. | Fixed | Стартову оцінку піднято до 5 млн активних сутностей, 30 млн observations/місяць, 3 млн новин/рік і 5 ТБ/рік; додано cold tier і translation budget. |
| R-08 | Середній | Не було явної поведінки для україномовних і багатомовних статей. | Fixed | Для `uk` встановлюється `not_required`; змішані сторінки перекладаються по сегментах із визначенням мови. |
| R-09 | Середній | Не було контролю витрат і starvation під час історичного перекладу. | Fixed | Додано character budget, translation memory, пріоритет title/lead/body і заборону backfill витісняти свіжі новини. |
| R-10 | Середній | Повнота адаптера оцінювалася лише parse rate, що не виявляє мовчазно пропущені типи сторінок/поля. | Fixed | Додано FR-019, coverage report, field coverage, yield baseline і raw-to-field trace. |
| R-11 | Середній | Зберігання контактів могло неконтрольовано дублюватися в logs/metrics. | Fixed | Контакти зберігаються в доменних observations/raw, але не в metric labels і технічних logs; доступ до dataset контролює RBAC. |
| R-12 | Середній | Перший варіант містив зайві нетехнічні gate-и, які не належать до внутрішнього дослідницького scope. | Fixed | Розділ замінено на суто операційні правила; зайві approval-поля та блокери вилучено з ТЗ і source manifest. |
| R-13 | Критичний | Поділ P0/P1/P2 суперечив вимозі єдиного списку джерел. | Fixed | Класи вилучено; кожне джерело має числовий рейтинг із п'яти складових, датою та доказами. |
| R-14 | Критичний | Реєстраційні API й API keys помилково входили в v1. | Fixed | V1 став anonymous-only; AUTO.RIA переведено на sitemap/category/HTML, а manifest забороняє login, private cookie й API key. |
| R-15 | Високий | Список сайтів не був достатнім контрактом для незалежних агентів. | Fixed | Додано чотири live-дослідження: 70 джерел загалом, sample URLs, redirects, robots, RSS/sitemap, pagination/backfill, schema/поля, блокування і adapter strategy. |
| R-16 | Високий | 403 від HTTP-клієнта міг помилково означати, що публічного джерела немає. | Fixed | Для OLX, Rozetka і Comfy окремо фіксуються HTTP та anonymous-browser результати; CAPTCHA не обходиться, а стан стає `blocked_anonymous`. |
| R-17 | Середній | Work packages описували лише один новинний адаптер на країну. | Fixed | WP-06A—G тепер охоплюють кожне джерело відповідних країн; acceptance вимагає явного стану кожного джерела. |
| R-18 | Високий | `source`, `route`, lifecycle і content-access стани змішувалися в різних документах. | Fixed | У §5.5 введено чотири незалежні закриті enum, окремий `fetch_outcome` і mapping старих research-позначень. |
| R-19 | Високий | Browser fallback після HTTP 403 можна було прочитати як дозвіл обходити challenge. | Fixed | Дозволено лише звичайний anonymous JS-rendering; CAPTCHA/challenge/login/paywall зупиняють route без spoofing або private cookies. |
| R-20 | Високий | FR-016 вимагав full-body переклад навіть для `metadata_only`. | Fixed | Body став nullable; перекладаються всі фактично доступні поля, відсутній текст не генерується. |
| R-21 | Середній | HTTPX-рядок досі пропонував реєстраційний AUTO.RIA API. | Fixed | HTTPX обмежено live-перевіреними anonymous API; AUTO.RIA v1 явно використовує sitemap/category/HTML. |
| R-22 | Середній | 70 джерел не мали канонічних `source_id`. | Fixed | Додано `docs/research/source-registry.yaml` з 70 унікальними ID, names, domains, country/kind, ratings і research-файлами. |
| R-23 | Середній | RST phone reveal був записаний як підтверджене поле, хоча live reveal перевірено лише для OLX. | Fixed | RST reveal позначено `operationally_unverified`; acceptance вимагає окремий доказ для кожного сайту. |
| R-24 | Високий | Одна PostgreSQL-модель змушувала вкладати різнорідні каталожні й автомобільні attributes у relational/JSONB структуру. | Fixed | Запроваджено bounded-context polyglot persistence: PostgreSQL для control/news/lineage, MongoDB для catalog/vehicle current+history, S3 для raw/normalized artifacts; додано projection outbox, monotonic versions/CAS, applied receipts, acknowledgements, reconciler, exact-version export, validators/indexes і failure tests. |
| R-25 | Високий | Out-of-order tasks могли перезаписати новіший Mongo current document. | Fixed | Додано per-entity monotonic `projection_version`, conditional update/CAS, unique entity-version і тест доставки `3,1,2`. |
| R-26 | Високий | Одна назва receipt змішувала Mongo atomic proof і PostgreSQL acknowledgement. | Fixed | Розділено `applied_projection_receipts` у Mongo та `projection_acknowledgements` у PostgreSQL; Mongo receipt атомарний із observation/current write. |
| R-27 | Високий | Повний normalized payload у PostgreSQL JSONB утворював другий source of truth. | Fixed | Payload перенесено в immutable content-addressed S3 artifact; PostgreSQL містить лише URI/hash/schema/version/task/lineage. |
| R-28 | Високий | Незалежним WP бракувало точних storage/event контрактів. | Fixed | Додано ключі, стани, versions, leases, timestamps, BSON shapes, обов'язкові indexes та окремий WP-01C shared contracts. |
| R-29 | Високий | Cross-store API/export не гарантували snapshot consistency. | Fixed | API читає exact confirmed version або повертає inconsistency; export фіксує immutable watermark/manifest і exact Mongo versions. |
| R-30 | Середній | Projection command і publishable domain event були змішані. | Fixed | Введено окремі `projection.command` та `domain.changed`; останній атомарний із acknowledgement та власним publish outbox. |
| R-31 | Середній | Mongo bulk policy суперечила per-task transaction boundary. | Fixed | Batch дозволено лише для dispatch; кожна task/entity має окрему Mongo transaction. |
| R-32 | Середній | Для history та operational queue бракувало compound indexes. | Fixed | Додано indexes entity/time, parent/time, offer/seller/contact та PostgreSQL status/not-before/priority/publish lookup. |
| R-33 | Середній | Не було S3↔PostgreSQL crash/reconciliation protocol. | Fixed | Додано content-addressed PUT, HEAD/checksum verification перед DB commit, grace-period orphan sweeper і fault-injection test. |
| R-34 | Середній | Mongo consistency та transaction retry policy були неповні. | Fixed | Зафіксовано primary/majority/snapshot concerns, retry для transient/unknown commit і межу single-member replica set. |
| R-35 | Середній | Work packages конфліктували за shared schemas/migrations. | Fixed | WP-01C володіє shared contracts, WP-01A — SQL migrations, WP-01B — Mongo validators/indexes; інші WP працюють через owned APIs. |
| R-36 | Високий | Exact-version export суперечив правилу створення observation лише при зміні/heartbeat. | Fixed | Додано обов'язковий `entity_projection_versions` для кожної task; business observations лишилися change/heartbeat records; event потребує і apply, і state change. |
| R-37 | Середній | Після crash до PostgreSQL ack не гарантувалося byte-equivalent відновлення change event. | Fixed | Mongo receipt атомарно зберігає previous/result hash і canonical event descriptor/delta з hash; reconciler відтворює той самий event. |
| R-38 | Середній | Orphan sweeper міг змагатися з producer між HEAD та DB commit. | Fixed | Додано PostgreSQL upload claim із lease/token; stale producer повторює claim і HEAD/reupload, sweeper не чіпає live claim. |
| R-39 | Середній | Entity kinds та replay indexes були неповні для offers/sellers/reviews/questions. | Fixed | Розширено kinds, введено exact projection records та natural content-version unique index для reviews/questions. |
| R-40 | Середній | Mongo одночасно називався source of truth і materialized projection. | Fixed | Ролі уточнено: S3 raw — canonical evidence, normalized artifact — reproducible input, PostgreSQL — canonical control/index/news, Mongo — authoritative serving projection. |
| R-41 | Середній | Upload claim не мав формального fencing token. | Fixed | Додано unique object key, монотонну `claim_generation`, атомарне збільшення та commit predicate з generation і чинним lease. |
| R-42 | Середній | Byte-equivalent event replay не мав canonical serialization contract. | Fixed | Receipt зберігає готові UTF-8 event bytes, media type і SHA-256; reconciler копіює bytes без reserialization, великі payloads мають immutable artifact ref. |
| R-43 | Високий | Історія не розділяла час події у джерелі та час отримання/запису системою. | Fixed | Додано temporal contract із source/effective та system/knowledge axes, precision/timezone/inference metadata і late-arrival tests. |
| R-44 | Високий | Mandatory projection version на кожен fetch міг необмежено роздувати MongoDB. | Fixed | Додано retention pins, 90-day hot window для unchanged versions, verified Parquet compaction, rollback window та archive locator. |
| R-45 | Високий | Cross-source merge не мав формального unmerge/replay contract. | Fixed | Source records лишаються immutable; рішення versioned, мають evidence/model/actor/supersedes, manual block і відтворюваний resolution snapshot. |
| R-46 | Високий | Export manifest не був повним контрактом відтворюваного dataset release. | Fixed | Додано immutable release lifecycle, watermarks, registry/schema/code/config/model versions, exclusions, part counts/hashes і reproducibility test. |
| R-47 | Середній | Масштаб був оцінкою без регулярної capacity/cost моделі та scaling trigger. | Fixed | Додано щомісячний actual/30/90/365 forecast для network/S3/Mongo/PostgreSQL/WAL/backups/translation/compute і 30% headroom gate. |
| R-48 | Середній | Не було стандартного способу досліджувати releases без прямого доступу до operational БД. | Fixed | Додано pinned read-only DuckDB research kit поверх перевірених partitioned Parquet releases; новий analytics datastore потребує benchmark/ADR. |
| R-49 | Високий | Назва bitemporal не мала формального valid/known interval query contract. | Fixed | Release тепер містить `[valid_from, valid_to)` та `[known_from, known_to)`, basis/inference metadata і окремі `as_of_valid_time`/`as_known_at` запити. |
| R-50 | Високий | Compaction могла створити вікно між видаленням hot version і доступністю archive locator. | Fixed | Archive part і hashes перевіряються, locators атомарно публікуються в PostgreSQL до Mongo delete; API весь час бачить hot або archive version. |

## Підсумкова перевірка узгодженості

- Scope ↔ поля: узгоджено — всі публічні поля мають доменні або extension-контракти.
- Країни ↔ мови ↔ translation provider: узгоджено — усі перелічені мови підтримані основним provider; українська не перекладається повторно.
- Архітектура ↔ функціональні вимоги: узгоджено — для кожного етапу є компонент, state і failure path.
- Джерела ↔ адаптери: узгоджено — кожне джерело має окремий owner/subpackage або доказаний стан `blocked_anonymous`.
- SLO ↔ метрики/алерти: узгоджено — окремо вимірюються crawl freshness і translation latency.
- Зберігання ↔ відтворюваність: узгоджено — raw, original, cleaned і translated artifacts immutable та пов’язані hash/version.
- Приймання ↔ тести: узгоджено — offline E2E, bounded live smoke, field coverage і multilingual QA мають числові пороги.
- Час ↔ історія: узгоджено — source/effective та system/knowledge axes окремі, late arrivals не переписують ingestion history.
- Matching ↔ releases: узгоджено — merge/unmerge versioned, а кожен release фіксує resolution snapshot.
- Retention ↔ дослідження: узгоджено — compaction не порушує pins, release hashes або відновлення exact version.
- Масштаб ↔ витрати: узгоджено — capacity snapshot має вимірювані формули, headroom gate і trigger для ADR.

## Залишкові відкриті рішення

Вони не є дефектами ТЗ і мають safe default у §20: перший дослідницький сценарій, media binaries, шардінг, backfill, retention, бюджет, cadence releases, Mongo topology та domain matching thresholds. Source API keys не є відкритим рішенням v1.
