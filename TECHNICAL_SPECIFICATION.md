# Технічне завдання: дослідницька платформа збору каталогів, автобазарів і міжнародних новин

## 0. Паспорт документа

| Поле | Значення |
|---|---|
| Статус | Готово до декомпозиції та реалізації |
| Версія | 1.5 |
| Дата | 2026-09-24 |
| Мова | Українська |
| Робоча назва системи | UA Web Data Collector |
| Призначення | Регулярно отримувати, зберігати, перекладати та нормалізувати публічні дані для внутрішніх досліджень |
| Географія | Каталоги й авторинок — Україна; новини — 19 країн Європи та Північної Америки |
| Одержувачі | Команда розробки, незалежні агенти-виконавці, DevOps, аналітики даних |

Цей документ є контрактом реалізації. Якщо вимога змінюється, агент спочатку змінює ТЗ або додає ADR (Architecture Decision Record), а потім код. Перелік джерел перевірено 2026-09-22; доступність RSS, sitemap і структуру сторінок треба автоматично перевіряти надалі.

## 1. Вихідні припущення та питання до замовника

До отримання інших відповідей діють такі припущення:

1. Каталоги та авторинок збираємо по всій Україні, усі категорії й типи транспортних засобів.
2. Новини збираємо для України, Німеччини, Франції, Великої Британії, США, Литви, Латвії, Естонії, Польщі, Угорщини, Румунії, Чехії, Словаччини, Словенії, Хорватії, Італії, Іспанії, Бельгії та Австрії.
3. «Чехословаччина» у вихідній постановці трактується як дві сучасні країни: Чехія (`CZ`) і Словаччина (`SK`). «Англія» трактується як Велика Британія (`GB`), але регіон England зберігається окремим тегом, якщо його дає джерело.
4. Збираємо всі поля, які публічно показані на сторінці або повернуті публічним каналом: повний текст, імена, телефони, e-mail, профілі продавців, VIN, характеристики, коментарі, відгуки, URL і метадані медіа.
5. Для кожної новини зберігаємо незмінений оригінал і машинний переклад українською. Переклад не замінює оригінал і може бути перегенерований іншою моделлю.
6. У v1 використовуємо лише сторінки, RSS/Atom, sitemap і source API, які працюють без реєстрації та входу. Авторизація на джерелах, source API keys, приватні кабінети й paywall не входять до v1; credentials внутрішньої інфраструктури та провайдера перекладу належать іншому контуру.
7. MVP призначений для внутрішнього дослідження; зовнішня публікація даних і UI не входять до MVP.
8. Інфраструктура MVP працює через Docker Compose на одному Linux-хості: PostgreSQL для control plane/news, MongoDB replica set для каталогів/авто та S3-compatible artifact store; компоненти лишаються горизонтально масштабованими.
9. Стартовий масштаб: до 5 млн активних сутностей, 30 млн спостережень на місяць, 3 млн новин/рік і до 5 ТБ сирих та очищених даних на рік.
10. Усі application-компоненти, включно з GUI, API, workers, scheduler, exporter і telemetry, постачаються OCI images; Docker Engine/host storage та зовнішній OIDC/translation provider є інфраструктурними залежностями, а не контейнерами проєкту.

Питання, що не блокують проєктування, але мають бути закриті до production:

- Які конкретні дослідницькі задачі будуть першими: ціни, асортимент, продавці, автомобілі, медіамоніторинг, події, тональність або тематичні тренди?
- Чи треба завантажувати бінарні файли фото/відео, чи достатньо їхніх URL, підписів і технічних метаданих?
- Яка допустима затримка оновлення і бюджет інфраструктури?
- Який строк зберігання сирих документів і історії цін?
- Який місячний бюджет машинного перекладу і який відсоток текстів треба перекладати негайно?

Відповіді фіксуються в `docs/decisions/` окремими ADR.

## 2. Мета, межі та критерії успіху

### 2.1. Мета

Створити керовану платформу, яка:

- знаходить нові й змінені сторінки/записи;
- завантажує їх налаштованим каналом із контрольованою частотою;
- зберігає доказовий сирий оригінал і метадані отримання;
- перетворює різні формати у стабільні доменні контракти та перекладає новини українською;
- веде історію змін, не плутаючи відсутність відповіді з видаленням оголошення;
- розрізняє час події у джерелі, час спостереження, отримання та запису;
- вимірює свіжість, повноту, дублікати й помилки по кожному джерелу;
- створює відтворювані dataset releases для досліджень;
- дає змогу додавати нові адаптери без змін ядра.

### 2.2. У межах MVP

- Новини: повний оригінальний текст, заголовок, анонс, автори, рубрики, теги, час, canonical URL, мова, географія, медіа-метадані, очищений HTML, український переклад і provenance перекладу.
- Автобазари: повна публічна картка оголошення, опис, марка/модель/комплектація, рік, VIN, пробіг, технічні поля, географія, ціна, продавець, ім’я, телефони/e-mail, профіль, медіа URL, статус та історія змін.
- Каталоги: повна публічна картка товару, бренд, артикул/MPN/GTIN, категорія, характеристики, продавець, контакти, ціна, валюта, наявність, доставка, рейтинг, відгуки, запитання/відповіді, медіа URL та історія змін.
- Внутрішній API читання, український operator GUI, керовані worker pools, експорт Parquet/JSONL, DuckDB research kit, CLI керування, метрики і журнал запусків.

### 2.3. Поза межами MVP

- Купівля товарів, розміщення оголошень, повідомлення продавцям або інші write-операції на зовнішніх сайтах.
- Обхід CAPTCHA, fingerprinting-захисту, платного доступу чи геоблокування.
- Збір приватних кабінетів, чатів або полів, яких немає у публічному представленні.
- Розпізнавання облич і номерних знаків із зображень; текстові значення, уже опубліковані сайтом, зберігаються.
- Публічна пошукова система або UI для кінцевих користувачів.

### 2.4. Критерії успіху пілота

| Показник | Ціль |
|---|---|
| Успішні заплановані fetch-запити | не менше 98% за 24 години без урахування контрольованих 304 |
| Новини: p95 затримки появи оригіналу | до 10 хв для RSS/Atom і публічних API |
| Новини: p95 затримки українського перекладу | до 20 хв після отримання оригіналу |
| Автобазари: p95 затримки | до 60 хв для інкрементального discovery |
| Каталоги: p95 віку останнього спостереження | до 24 год для запланованого сегмента обходу |
| Валідність нормалізованих записів | не менше 99.5% за JSON Schema/Pydantic |
| Дублікати за ключем джерела | 0; семантичні дублікати між джерелами не більше 2% після матчингу |
| Відтворюваність | кожен запис має `source_id`, `fetch_id`, час, URL і hash сирого об’єкта |
| Відновлення після збою | повторний запуск не створює дублікатів і не втрачає підтверджені записи |
| Часова коректність | source/observation/fetch/ingest timestamps не підміняють один одного; невідомий source time лишається nullable |
| Відтворюваність dataset release | повторна збірка за manifest дає ті самі part hashes або пояснений versioned diff |

## 3. Операційні правила збору

1. Для кожного джерела обов’язковий `source manifest` із каналом доступу, URL-шаблонами, розкладом, ставкою запитів, cursor і політикою зберігання.
2. Порядок вилучення: анонімний офіційний API або RSS/Atom → sitemap + HTML → headless browser, коли те саме публічне представлення формується JavaScript.
3. `robots.txt` отримується і версіонується як діагностичний артефакт, щоб пояснювати блокування та зміни структури.
4. 401/403/429, CAPTCHA або різке падіння yield відкривають circuit breaker конкретного каналу/route і створюють технічний інцидент; інші незалежно перевірені routes того самого джерела можуть лишатися `healthy`, а отримані через них items мати `content_access=metadata_only`. Нескінченні повтори заборонені.
5. User-Agent має бути стабільним, щоб поведінку crawler можна було відрізнити в логах і відтворити.
6. За замовчуванням: concurrency 1 на origin, не більше 0.2 запиту/с; ліміт підвищується тільки після вимірювання 429, latency й навантаження.
7. Повний текст новин і всі доступні публічні поля зберігаються. Оригінальні bytes незмінні; очищений текст і переклад є окремими похідними артефактами.
8. У MVP зберігаються URL, підписи, розміри й хеші медіа. Завантаження оригінальних фото/відео вмикається окремим параметром через значний обсяг.
9. Контактні дані зберігаються як versioned observations, оскільки продавець може змінити ім’я або телефон.
10. Denylist доменів і URL дає змогу терміново зупинити збір без перевипуску коду.
11. Browser fallback дозволений після HTTP 403 лише як перевірка звичайного анонімного JS-rendered представлення, яке людина бачить без додаткових дій. Якщо браузер показує CAPTCHA, challenge, login або paywall замість даних, route зупиняється; fingerprint spoofing, CAPTCHA solving і private-session cookies заборонені.

## 4. Джерела та їхній технічний рейтинг

Усі записи нижче — просто джерела: немає базових, додаткових або пріоритетних класів. Рейтинг `0–100` не визначає редакційну цінність чи чергу реалізації. Це сума coverage `0–25`, structured access `0–25`, anonymous accessibility `0–20`, data richness `0–15` і stability/observability `0–15`. Рейтинг версіонується разом із датою та доказами; недоступне без реєстрації джерело не активується у v1.

Канонічні `source_id`, display name, домени, country/kind, рейтинг і файл доказів зафіксовані в [source-registry.yaml](docs/research/source-registry.yaml). Адаптери не вигадують альтернативних ID.

### 4.1. Новини

| Країна | ISO / мови | Джерела з рейтингом |
|---|---|---|
| Україна | `UA` / `uk` | Суспільне 96; Українська правда 85; LIGA.net 94; Укрінформ 94 |
| Німеччина | `DE` / `de` | Tagesschau 86; Deutsche Welle 94; ZEIT 52 |
| Франція | `FR` / `fr` | France 24 56; RFI 56; Le Monde 76 |
| Велика Британія | `GB` / `en` | BBC News 98; The Guardian 97; Sky News 63 |
| США | `US` / `en` | NPR 100; AP News 34; The New York Times 68 |
| Литва | `LT` / `lt` | LRT 97; 15min 92; Delfi LT 89 |
| Латвія | `LV` / `lv` | LSM 98; Delfi LV 90; TVNET 89 |
| Естонія | `EE` / `et` | ERR 99; Postimees 86; Delfi EE 86 |
| Польща | `PL` / `pl` | Polskie Radio 66; PAP 32; TVN24 82 |
| Угорщина | `HU` / `hu` | Telex 97; HVG 89; 444 91 |
| Румунія | `RO` / `ro` | HotNews 95; Digi24 94; Agerpres 67 |
| Чехія | `CZ` / `cs` | iROZHLAS 69; ČT24 99; Seznam Zprávy 94 |
| Словаччина | `SK` / `sk` | STVR Správy 99; Aktuality.sk 96; SME 31 |
| Словенія | `SI` / `sl` | RTV Slovenija 91; STA 59; 24UR 91 |
| Хорватія | `HR` / `hr` | HRT Vijesti 91; Index.hr 80; Jutarnji 87 |
| Італія | `IT` / `it` | RaiNews 87; ANSA 92; la Repubblica 77 |
| Іспанія | `ES` / `es` | RTVE Noticias 94; El País 86; La Vanguardia 88 |
| Бельгія | `BE` / `nl`, `fr`, `de` | VRT NWS 100; RTBF Info 98; The Brussels Times 88 |
| Австрія | `AT` / `de` | ORF News 76; Der Standard 71; Die Presse 91 |

Детальні live-паспорти з URL, статусами, redirects, RSS/sitemap, URL-схемами, pagination/backfill, JSON-LD/OG, полями, блокуваннями та стратегією адаптера: [UA, Baltics, PL, HU, CZ, SK](docs/research/news-central-baltic.md), [DE, FR, GB, US, BE, AT](docs/research/news-western.md), [RO, SI, HR, IT, ES](docs/research/news-southern.md). Сторонній RSS-агрегатор не є першоджерелом: зберігати canonical URL, назву редакції та оригінальну мову. Для багатомовної Бельгії країну й мову визначати окремо.

### 4.2. Автобазари

| Джерело | Рейтинг | Канал v1 без реєстрації | Що збираємо |
|---|---:|---|---|
| [AUTO.RIA](https://auto.ria.com/uk/legkovie/) | 91 | sitemap, категорії, HTML/JSON-LD | усі типи авто, повна картка, продавець і анонімно доступні контакти |
| [OLX Авто](https://www.olx.ua/uk/transport/legkovye-avtomobili/) | 85 | sitemap/category, browser detail fallback | повна картка, продавець, контакти після анонімного reveal, media URL |
| [RST.ua](https://rst.ua/ukr/) | 59 | category/detail legacy HTML | оголошення, продавець, контакти, ціни й характеристики |
| [Automoto.ua](https://automoto.ua/uk/car) | 91 | sitemap/gzip, HTML/JSON-LD | агреговані оголошення, контакти, ціна й upstream provenance |

### 4.3. Каталоги і ціни

| Джерело | Рейтинг | Канал v1 без реєстрації | Ключова модель |
|---|---:|---|---|
| [Prom.ua](https://prom.ua/robots.txt) | 95 | product/model sitemap, HTML/JSON-LD | product + seller offer |
| [Rozetka](https://rozetka.com.ua/robots.txt) | 91 | category tree, HTML/JSON-LD, browser fallback | product + multi-seller offers |
| [Епіцентр](https://epicentrk.ua/robots.txt) | 94 | розділені product/category sitemaps, HTML | own/marketplace offers |
| [Allo](https://allo.ua/robots.txt) | 88 | XML/gzip sitemap, HTML/state | product + own/marketplace offer |
| [Hotline](https://hotline.ua/robots.txt) | 96 | XML/gzip sitemap, HTML/JSON-LD | normalized model + shop offers |
| [Comfy](https://comfy.ua/ua/smartfon/) | 90 | sitemap, browser/JSON-LD | product + retailer offer |
| [Foxtrot](https://www.foxtrot.com.ua/) | 86 | sitemap + categories, HTML/state | product + retailer offer |
| [MOYO](https://www.moyo.ua/) | 80 | human maps + categories, HTML/state | product + retailer offer |

Повний польовий паспорт цих 12 джерел із прикладами URL, схемами сторінок і переліками полів: [українські каталоги й авторинки](docs/research/ua-marketplaces.md).

Перед реалізацією адаптера агент зберігає датований snapshot RSS/sitemap/robots і 3–10 representative pages. Це технічна база для regression tests і пояснення змін сайту.

## 5. Дані, які збираємо

### 5.1. Спільні поля сутності

- `id` — UUIDv7 внутрішньої сутності;
- `source_id`, `source_item_id` — джерело та стабільний ID на джерелі;
- `canonical_url`, `source_url`;
- `title`, `description_excerpt`, `full_text`, `language`, `country_code`;
- `source_event_at`, `source_updated_at` — час події/оновлення, заявлений джерелом; nullable і ніколи не підміняється crawler time;
- `observed_at` — логічний час конкретного source snapshot; `fetched_at` — завершення HTTP fetch; `ingested_at` — commit normalized record; `first_seen_at`, `last_seen_at` — межі спостережень;
- `source_timezone_raw`, `source_time_precision`, `source_time_inferred` для збереження вихідної часової семантики;
- `status`: `active`, `inactive`, `deleted`, `unknown`;
- `content_hash`, `identity_hash`, `fetch_id`, `parser_version`;
- `raw_object_uri`, `schema_version`;
- `attributes` object для всіх публічних полів, які ще не стандартизовані: BSON у Mongo catalog/vehicle documents; JSONB у PostgreSQL дозволений лише для bounded news/control extensions, не для копії catalog/vehicle payload;
- `contacts` як окрема versioned collection: тип, нормалізоване й вихідне значення, ім’я/роль, `first_seen_at`, `last_seen_at`;
- `media_assets`: URL, тип, caption, width/height/duration, source hash і optional downloaded object URI.

Грошові значення зберігаються як `amount_minor BIGINT` + `currency CHAR(3)`, ніколи як float. Усі нормалізовані timestamps — UTC `timestamptz`; вихідний timezone/offset і точність зберігаються окремо. Для досліджень порядок визначається `source_event_at` або `observed_at`, а не `ingested_at`.

### 5.2. Product і OfferObservation

`Product`: brand, model, category path, GTIN/EAN/UPC, MPN, normalized/raw attributes, descriptions, documents, image/video URLs.

`Offer`: seller/store ID, seller name/profile/contacts, source product ID, SKU, URL, condition, delivery/payment/region, rating.

`OfferObservation`: observed_at, price, old price, availability, stock text, promotion label, seller/contact snapshot. Відгуки, запитання й відповіді мають власні стабільні source IDs і версії. Історія append-only; поточний стан — materialized view або окрема проєкція.

### 5.3. VehicleListing і VehicleObservation

`VehicleListing`: make/model/generation/trim IDs і вихідні назви, year, body, fuel, transmission, drive, engine volume, power, mileage, VIN, registration plate якщо опублікований текстом, description, equipment/options, damage/customs/inspection flags, location, seller type/name/profile, contacts, media URLs і URL оголошення.

`VehicleObservation`: observed_at, price, currency, mileage, listing status, promoted flag, view counters, seller/contact snapshot і зміни опису.

### 5.4. NewsArticle

`NewsArticle`: source article ID, canonical URL, country, original language, `content_access`, title, lead, nullable full original text/cleaned HTML, author/byline, section/tags, publication/update timestamps, related media URLs, links, source attribution і content hash.

`NewsTranslation`: article ID, target language `uk`, translated title/lead/body, provider, model/version, glossary version, source content hash, created_at, status, quality flags і cost/character count. Зміна оригіналу створює нову версію перекладу; старий переклад не перезаписується.

Якщо original language уже `uk`, `NewsTranslation.status = not_required`, а read API повертає оригінальні поля як українське представлення без повторного зберігання тексту. Якщо одна сторінка містить кілька мов, перекладати сегменти, для яких language detector не повернув `uk`.

### 5.5. Розділені осі стану

Незалежні агенти не створюють власних взаємозамінних enum. Використовуються чотири окремі осі:

- `source_state`: `enabled | paused | disabled | blocked_anonymous`; це стан планування всього джерела;
- `route_state`: `healthy | degraded | circuit_open | unsupported`; це стан конкретного RSS/sitemap/category/detail/browser route;
- `entity_lifecycle`: `active | inactive | deleted | unknown`; це життєвий цикл товару, оголошення або статті;
- `content_access`: `full | partial | metadata_only | blocked | challenge | premium | gone | unknown`; це фактична повнота одного fetched item.

`fetch_outcome = success | retryable | permanent_failure` є результатом спроби, а не станом доступу. Мапінг старих позначень у research: `free -> full`; `body_unavailable -> metadata_only`; `retryable` переноситься у `fetch_outcome`; `blocked/challenge/premium/gone` лишаються однойменними. `metadata_only` запис має nullable `body_original_*`, але зберігає доступні title/lead/metadata й ніколи не вважається повнотекстовим.

## 6. Функціональні вимоги

| ID | Вимога |
|---|---|
| FR-001 | Реєстр джерел керує статусом, рейтингом і його складовими, доказом анонімного доступу, каналом, розкладом, лімітами, URL-шаблонами і політикою зберігання без зміни коду ядра. |
| FR-002 | Планувальник створює ідемпотентні crawl runs і не запускає два несумісні повні обходи одного джерела одночасно. |
| FR-003 | Discovery читає API pagination, RSS/Atom і sitemap index/urlset, включно з gzip. |
| FR-004 | Fetcher підтримує conditional GET (`ETag`, `Last-Modified`), redirects, gzip/brotli, timeout, retry budget і per-origin rate limit. |
| FR-005 | Кожна отримана відповідь має immutable raw artifact, checksum, HTTP-метадані та посилання з БД. |
| FR-006 | Parser є чистою функцією `raw artifact + parser version -> normalized records + validation report`. |
| FR-007 | Upsert за `(source_id, source_item_id)` не втрачає історію спостережень і не дублює повторно оброблену відповідь. |
| FR-008 | Статус `inactive/deleted` встановлюється лише за позитивним сигналом API або після трьох успішних повних обходів без сутності; помилки обходу не рахуються. |
| FR-009 | Оператор може pause/resume/disable джерело, запустити backfill у межах дат/ID, переглянути останні помилки й повторити dead-letter task. |
| FR-010 | Експорт фільтрує за типом, джерелом і часовим інтервалом та формує versioned Parquet/JSONL із manifest і checksum. |
| FR-011 | Schema version і parser version присутні в кожному normalized record та експорті. |
| FR-012 | Система створює change events для нової сутності, зміни ціни/наявності/статусу та істотної зміни контенту. |
| FR-013 | Secrets надходять лише з environment/secret store і ніколи не потрапляють у logs, raw artifacts або fixtures. |
| FR-014 | URL normalization видаляє tracking-параметри, але зберігає вихідний URL і не об’єднує різні варіанти товару без доказу. |
| FR-015 | Кожен адаптер має offline fixture tests і контрольований live smoke test, вимкнений у звичайному CI. |
| FR-016 | Для кожної неукраїномовної новини система перекладає українською всі фактично доступні поля, зберігаючи оригінал: title/lead для `metadata_only`, а body лише для `full/partial` із наявним текстом. Відсутній body не генерується. |
| FR-017 | Translation memory не відправляє повторно незмінні сегменти; ключ містить source language, target language, normalized segment hash, provider/model і glossary version. |
| FR-018 | Публічні контакти, імена, профілі, VIN та інші доступні поля мають зберігатися разом із provenance і часовою версією. |
| FR-019 | Кожне джерело має coverage report: відомі типи сторінок, поля, pagination/backfill межі, кількість виявлених і пропущених записів. |
| FR-020 | Повна domain-модель каталогів і авто зберігається в MongoDB як bounded current documents та окремі append-only observations; PostgreSQL містить artifact pointer/hash, index, task і lineage, але не payload-копію. |
| FR-021 | Parser ніколи не робить синхронний запис у дві БД; PostgreSQL projection outbox і Mongo unique idempotency key забезпечують at-least-once delivery без дублікатів. |
| FR-022 | Reconciler виявляє task без Mongo applied receipt/PostgreSQL acknowledgement, acknowledgement без index і version drift; повторна проєкція з raw/normalized artifact відновлює узгодженість. |
| FR-023 | Read/export API приховує межу двох БД, але не виконує необмежені runtime joins; масові cross-domain вибірки формуються як versioned Parquet/JSONL. |
| FR-024 | Кожен доменний запис виконує bitemporal contract §9.6: source/effective time не підміняється fetch/ingest time, а всі припущення про час явно маркуються. |
| FR-025 | History compaction не видаляє версії, закріплені export/release/backup/incident/research references; dry-run, manifest і restore test обов'язкові. |
| FR-026 | Cross-source merge є versioned і оборотним: decision з evidence/score/rule/actor може бути superseded або undone без втрати source records та observations. |
| FR-027 | Dataset release має immutable manifest із registry/schema/parser/matcher/translation versions, watermark, exclusions, row/part counts і checksums. |
| FR-028 | Capacity plan щомісяця перераховує fetch/artifact/DB/index/WAL/backup/translation volumes, unit cost і headroom; перевищення threshold створює scaling decision. |
| FR-029 | V1 аналітика використовує DuckDB поверх immutable Parquet releases; новий production analytics datastore додається лише після benchmark і ADR. |
| FR-030 | Усі application services мають versioned OCI images, health/readiness checks, resource limits, non-root runtime і Docker Compose definition; state зберігається лише у named volumes/external stores. |
| FR-031 | Discovery, fetch, browser, parse, projector, translation, export і maintenance workers є stateless role-based pools, які масштабуються незалежно без зміни image. |
| FR-032 | Кількість container replicas та concurrency per replica мають окремі desired/current значення; replica scale-down використовує role-wide drain barrier, бо Compose/Swarm можуть самі обрати container для видалення. |
| FR-033 | Per-origin rate/concurrency budget є глобальним для всіх discovery/fetch/browser replicas; збільшення worker count не збільшує дозволену частоту запитів до джерела. |
| FR-034 | Operator GUI керує sources/routes, runs/jobs/dead letters, worker pools, matching decisions, translations, releases, retention/compaction і capacity, використовуючи лише versioned API. |
| FR-035 | GUI не має Docker socket або DB credentials. У Compose mode container replicas змінюються CLI; у Swarm mode окремий allowlisted stack controller застосовує audited desired replica count. |
| FR-036 | Усі mutating GUI/API actions використовують optimistic concurrency, idempotency key, RBAC, audit record і явне підтвердження для destructive/expensive operations. |
| FR-037 | GUI показує live progress через SSE, але після reconnect завжди відновлює стан із API snapshot/cursor; UI notification не є джерелом істини. |

## 7. Архітектура

```text
Source Registry ──> Scheduler ──> Discovery ──> Fetch Queue ──> HTTP/API Fetcher
       │                 │                              └──────> Browser Fetcher (exception)
       │                 │                                         │
       └── Route Guard ──┴─────────────────────────────────────────┤
                                                                  v
                                                     S3/MinIO Artifact Store
                                                                  │
                                                                  v
                                                   Extractor + Parser + Validator
                                                                  │
                                                                  v
                                         PostgreSQL Artifact Index/Projection Outbox
                                                   │                        │
                         ┌─────────────────────────┘                        └──────────────┐
                         v                                                                 v
           PostgreSQL Control + News                                      MongoDB Catalog + Vehicle
          jobs, cursors, lineage, news,                                  current documents, offers,
          translations, match graph, audit                              observations, sellers, contacts
                         │                                                                 │
                         └──────────────────> Read/Export API <────────────────────────────┘
                                                   │
                            Versioned Parquet/JSONL Releases
                                      │                 │
                             DuckDB Research       Other Consumers

PostgreSQL News ──> Translation Queue/Worker + QA ──> PostgreSQL Translation Versions
Failed terminal jobs ──> PostgreSQL Dead Letter

All stages ──> OpenTelemetry metrics/traces/logs ──> Prometheus + Grafana + Loki

Operator Browser ──> GUI/Nginx ──> FastAPI Operator API ──> PostgreSQL Control
                                      │          │
                                      │          └── SSE status/events
                                      v
                              Worker Pool Desired State
                                      │
                              Stack Controller
                         Compose CLI (manual) / Swarm Services
```

### 7.1. Межі компонентів

- **Registry/Route Guard:** єдина точка URL allow/deny rules і швидкості. Адаптер не може напряму обійти її.
- **Scheduler:** створює jobs; не містить CSS/XPath selectors.
- **Discovery:** повертає кандидатні URL/IDs і cursor; не парсить доменну картку.
- **Fetcher:** отримує bytes; не знає доменної схеми.
- **Artifact Store:** immutable, content-addressed, шифрований; містить raw та versioned normalized artifacts, тому повторний parse/projection не потребує мережі.
- **Extractor/Parser:** адаптер джерела + версія; виділяє main content, structured data, contacts та доменні поля; не робить зовнішніх HTTP-запитів.
- **Translation:** сегментує очищений оригінал, використовує translation memory і перекладає в `uk`; ніколи не змінює original artifact.
- **Normalizer/Matcher:** приводить одиниці, довідники й ідентичності, не змінює raw.
- **PostgreSQL Core:** operational state, jobs, cursors, fetch/parse metadata, lineage, news, translations, cross-source identity/matching, projection tasks і outbox.
- **MongoDB Domain Store:** поточні source documents і append-only observations каталогів/авто, sellers, contacts, reviews/questions; не керує scheduler або source cursors.
- **Mongo Projector:** бере task/artifact pointer із PostgreSQL, читає валідований normalized artifact із S3/MinIO, ідемпотентно проєктує його в MongoDB та повертає applied receipt.
- **Exporter:** read-only відносно PostgreSQL і MongoDB; об'єднує результати лише через стабільні internal UUID/source identity, а не через неявні cross-database joins.
- **Research Kit:** read-only DuckDB queries/views поверх перевірених Parquet releases; не читає operational БД напряму й не змінює дані.
- **Operator GUI:** український web client для спостереження й керування; не має прямого доступу до PostgreSQL, MongoDB, S3 або Docker Engine.
- **Worker Pool Controller:** звіряє desired/current pool state, керує drain і, лише у Swarm mode, змінює replicas через окремий allowlisted deployment adapter.

### 7.2. Черга MVP

Використати PostgreSQL job table з `FOR UPDATE SKIP LOCKED`, lease timeout, `attempt`, `not_before`, унікальним idempotency key і dead-letter status. Це скорочує кількість сервісів і гарантує транзакційний outbox.

Перехід на RabbitMQ/Redpanda допускається лише після виміряної межі: понад 100 jobs/s стабільно, черга понад 1 млн pending jobs або потреба в незалежному масштабуванні багатьох типів споживачів. Перехід оформлюється ADR і не змінює job payload contract.

### 7.3. Межа PostgreSQL / MongoDB

| Дані/функція | Авторитетне сховище/роль | Причина |
|---|---|---|
| Sources, policies, routes, cursors, jobs, retries, fetch/parse metadata | PostgreSQL, canonical control state | транзакційні переходи станів, leases, унікальні ключі, аудит |
| Raw HTML/XML/JSON | S3/MinIO, canonical evidence | immutable bytes, checksum, compression і lifecycle |
| Normalized projection payloads | S3/MinIO, immutable reproducible projection input | не дублює domain payload у PostgreSQL; version/hash пов'язують його з raw і parser |
| NewsArticle, versions, translations, segments | PostgreSQL, canonical news store | чіткі зв'язки original/version/translation, повнотекстові й часові запити |
| Global entity index, aliases, match candidates, export manifests | PostgreSQL, canonical cross-domain index | зв'язки між джерелами й посилання на Mongo document IDs |
| Каталожні source items, offers, reviews/questions, observations | MongoDB, authoritative serving projection | різнорідні вкладені attributes і різні схеми категорій; клієнти не читають payload із PostgreSQL |
| Vehicle listings, seller/contact snapshots, observations | MongoDB, authoritative serving projection | поліморфні комплектації/стани й повна source-specific картка |
| Screenshots, великі media/export artifacts | S3/MinIO, artifact store | content-addressing і lifecycle |

PostgreSQL і MongoDB не мають спільної транзакції та не використовують синхронний dual-write з parser. Потік запису:

1. Fetcher зберігає raw artifact у S3/MinIO; parser створює окремий immutable normalized projection artifact у тому самому store.
2. Одна PostgreSQL-транзакція записує `parse_attempt`, pointer/hash/schema version normalized artifact, монотонний для `entity_uuid` `projection_version`, `projection_task` і `projection.command` outbox event.
3. Mongo Projector обробляє одну task/entity в одній MongoDB-транзакції: завжди вставляє `entity_projection_version`, умовно оновлює current document лише якщо вхідний `projection_version` більший, при зміні state hash/heartbeat додає business observation і атомарно вставляє `applied_projection_receipt`. Ключі task та `(entity_uuid, projection_version)` унікальні.
4. Після Mongo commit projector в одній PostgreSQL-транзакції записує `projection_acknowledgement`, монотонно оновлює confirmed version в `entity_index` і завершує job. Лише receipt з `applied_to_current=true AND state_changed=true` створює deterministic `domain.changed` event разом із його publish outbox row. Crash між кроками 3–4 спричиняє безпечний replay, а не нову observation.
5. Reconciler порівнює незавершені PostgreSQL tasks із Mongo `applied_projection_receipts`/документами; cursor не вважається повністю опрацьованим, доки всі його projection tasks не acknowledged або quarantined.

`projection_version` видається PostgreSQL атомарно під row/advisory lock для конкретного `entity_uuid`. Доставка може бути не по порядку: старіша task зберігає exact version record і receipt, але compare-and-set не дозволяє їй перезаписати новіший current state. Seller/contact, якщо це окремий current document, має власний `entity_uuid` і монотонну version; не можна ділити version counter між неатомарними агрегатами.

`projection.command` є внутрішньою командою projector і не публікується зовнішнім споживачам. `domain.changed` виникає тільки після Mongo commit і лише для `applied_to_current=true AND state_changed=true`; має `event_id`, `aggregate_id`, `aggregate_version`, `event_type`, `payload_schema_version` і публікується щонайменше один раз з окремого PostgreSQL outbox. Projector один раз формує ready-to-publish UTF-8 event bytes зі стабільним `event_id` і записує bytes (`BSON Binary`), media type та SHA-256 у Mongo receipt у тій самій транзакції. Reconciler копіює ці bytes у PostgreSQL `bytea` без повторної серіалізації, тому після crash відтворює byte-equivalent event; consumer дедуплікує за `event_id`. Максимум inline event — 256 KiB, більший payload зберігається як immutable artifact із URI/hash у receipt.

MongoDB є авторитетним serving store catalog/vehicle, але відновлюваною проєкцією canonical raw evidence та versioned normalized input. Заборонено робити MongoDB canonical джерелом scheduler state або PostgreSQL джерелом повної картки каталогу/авто. Read API робить bounded two-step lookup через `entity_index`; масові аналітичні об'єднання виконуються в versioned Parquet export, а не runtime cross-database join.

### 7.4. Резервування, відновлення та перебудова MongoDB

- PostgreSQL має point-in-time recovery; S3/MinIO — versioning/immutability; MongoDB — регулярний snapshot із зафіксованим operation time та перевіреним restore.
- Pointers/hashes normalized artifacts і projection outbox зберігаються щонайменше до успішної перевірки двох наступних MongoDB backups. Сам payload не дублюється в PostgreSQL JSONB. Raw/normalized artifacts і lineage мають чинну retention policy незалежно від MongoDB.
- MongoDB domain state вважається відновлюваною materialized projection: після втрати collection її можна детерміновано перебудувати з PostgreSQL artifact pointers/tasks та immutable raw/normalized artifacts без повторного звернення до сайтів.
- Для повного disaster recovery спочатку відновлюються PostgreSQL і artifact store, потім Mongo snapshot, після чого projector replay-ить усі tasks після snapshot watermark, а reconciler підтверджує нульовий drift.
- Restore drill виконується щоквартально в ізольованому середовищі; результат містить watermark, кількість replayed tasks, hash/count звірку, фактичні RPO/RTO та підписаний acceptance report.

### 7.5. Docker deployment

Один pinned application image `collector` запускає API, scheduler, controller, CLI та worker roles через різні commands; browser worker має окремий image із pinned Playwright browser. GUI збирається multi-stage image і віддається non-root Nginx. PostgreSQL, MongoDB, MinIO й telemetry використовують pinned vendor image digests.

Compose profiles:

| Profile | Services |
|---|---|
| `core` | `postgres`, `mongo`, `minio`, one-shot `migrate-postgres`, `ensure-mongo`, `api`, singleton `scheduler` |
| `workers` | `discovery-worker`, `fetch-worker`, `parse-worker`, `projector-worker`, `translation-worker`, `export-worker`, `maintenance-worker` |
| `browser` | resource-limited `browser-worker`, за замовчуванням 0 або 1 replica |
| `gui` | `gui` static server/reverse proxy |
| `observability` | OpenTelemetry Collector, Prometheus, Grafana, Loki |
| `tools` | one-shot admin, release verifier і DuckDB research container |

Вимоги до Compose/containers:

- worker services не задають `container_name`, host ports або local persistent state, тому їх можна масштабувати; `worker_instance_id` генерується на boot, а Docker hostname зберігається лише як metadata;
- назовні публікується тільки GUI ingress (`80/443` або configurable host binding); API доступний same-origin через reverse proxy, а DB/object/telemetry ports за замовчуванням не bind-яться на public host interface;
- named volumes дозволені тільки stateful services; application images read-only, non-root, із writable tmpfs для тимчасових файлів;
- окремі мережі `ingress`, `backend`, `source-egress`, `provider-egress`, `telemetry`; discovery/fetch/browser мають source egress, translation — provider egress, API — лише OIDC egress, GUI бачить тільки API;
- secrets передаються Docker secrets/files, а не bake-time ARG, image layer або committed `.env`;
- healthcheck перевіряє process і критичну dependency; readiness лишається false до migrations/validators; scheduler/controller мають singleton advisory lease;
- `stop_grace_period` довший за максимальний bounded task shutdown; SIGTERM запускає drain, SIGKILL є fault case з lease recovery;
- images мають immutable tag + digest, OCI labels з Git SHA/schema version, SBOM і vulnerability scan;
- stateful services не масштабуються worker controls; Mongo/PostgreSQL topology змінюється окремим runbook/ADR.

У single-host Compose кількість replicas змінюється підтримуваною Docker командою, наприклад:

```bash
docker compose --profile core --profile workers --profile gui up -d --wait
docker compose up -d --no-recreate --scale fetch-worker=4 --scale parse-worker=2
docker compose scale --no-deps translation-worker=3 browser-worker=1
```

У Compose mode GUI одразу застосовує `desired_concurrency` до живих workers, а для зміни container replicas зберігає desired count, показує audited CLI command і чекає heartbeats після ручного виконання; Docker socket у web/API не монтується. Для автоматичного apply replicas із GUI використовується Docker Swarm replicated services: controller працює лише на manager node, може змінювати replicas тільки сервісів із label `collector.scalable=true`, перевіряє min/max/resource limits і не має дозволу змінювати images, mounts, networks, secrets або stateful services.

### 7.6. Worker pools і масштабування

| Role | Черга/робота | Default replicas × concurrency | Особливі обмеження |
|---|---|---:|---|
| `discovery` | RSS/sitemap/API pagination → fetch jobs | 1 × 4 | singleton per source/run через lease |
| `fetch` | звичайні HTTP GET → raw artifact | 2 × 8 | global origin limiter має верховенство |
| `browser` | anonymous JS rendering | 0 × 1 | окремий image; CPU/RAM budget; max 1 per origin |
| `parse` | raw → normalized artifact/news version | 2 × CPU count | без network egress |
| `projector` | projection task → Mongo + ack | 1 × 8 | одна Mongo transaction на task/entity |
| `translation` | segments → Ukrainian version | 1 × 4 | character/cost budget і fresh-news priority |
| `export` | dataset release parts/manifests | 1 × 2 | immutable output; memory/disk scratch limit |
| `maintenance` | reconcile, compaction, sweeps, capacity | 1 × 1 | mutually exclusive named leases |

`worker_pools` зберігає `role`, `desired_replicas`, `desired_concurrency`, `min/max_replicas`, resource profile, `mode = manual | autoscale`, revision і updated actor/reason. `worker_instances` містить boot UUID, role, deployment/container metadata, version, status (`starting | ready | draining | stopped | stale`), slots, heartbeat і active leases. `scale_commands` має idempotency key, expected pool revision, requested values, status/result і audit link.

Scale command states: `requested | draining | awaiting_manual_apply | applying | applied | failed | superseded`. Desired-state update і command insert є однією PostgreSQL-транзакцією. У Compose mode command переходить у `awaiting_manual_apply` і містить exact CLI; у Swarm mode controller переводить його в `applying`. `applied` дозволений лише коли heartbeat-derived current replicas/concurrency відповідають desired revision.

Масштабування не змінює семантику черги:

- claim виконується через `FOR UPDATE SKIP LOCKED`; кожна task має lease owner/expiry і heartbeat;
- глобальний PostgreSQL token bucket за normalized origin атомарно видає leased request permit всім discovery/fetch/browser replicas; rate tokens і concurrency permits обліковуються окремо, release і expiry ідемпотентні, а per-container semaphore лише додатково обмежує локальну concurrency;
- scale-up починає claim лише після readiness. Перед зменшенням container replicas controller ставить role-wide drain barrier: усі instances role припиняють claim, завершують/повертають leases, orchestrator зменшує replicas, а survivors відновлюють claim після підтвердження new revision. Це не покладається на те, який container Compose/Swarm вирішить видалити;
- зміна `desired_concurrency` застосовується без restart на межі task: нові slots відкриваються одразу, зайві закриваються після завершення активних tasks;
- repeated scale command безпечний за idempotency key; optimistic pool revision відхиляє stale GUI action;
- autoscale у v1 вимкнений за замовчуванням. Після load test він використовує queue oldest age + pending/running ratio, три послідовні measurement windows, 5-minute cooldown, min/max і окремий browser/translation budget; manual override має пріоритет.

### 7.7. Operator GUI

GUI за замовчуванням українською, desktop-first і придатний для планшета. Основні екрани:

1. **Огляд:** health компонентів, freshness/SLO, queue backlog, worker pools, storage/capacity, translation cost і активні інциденти.
2. **Джерела:** рейтинг і його докази, source/route states, розклад, rate budget, останні runs; pause/resume/disable і bounded backfill.
3. **Jobs і помилки:** фільтри за source/type/status, lease/retries, dead letters, raw/parse/projection lineage, idempotent replay.
4. **Workers:** desired/current replicas та concurrency, instances/versions/heartbeats/active leases; scale, drain/reapply desired state й autoscale policy в межах min/max.
5. **Дані:** news original + український translation, catalog/vehicle current/history, seller/contacts, raw lineage та exact-version view.
6. **Matching:** candidates/evidence/score, merge, manual block, unmerge і preview впливу на наступний release.
7. **Releases/exports:** build progress, inclusions/exclusions, quality, parts/checksums, download/verify і DuckDB command.
8. **Retention/capacity:** pins, compaction dry-run/result, hot/cold bytes, actual/forecast/cost і scaling recommendations.
9. **Аудит:** хто, коли, що змінив, request/idempotency ID, before/after revision і результат.

Frontend: React + TypeScript + Vite, route-level code splitting, generated OpenAPI client і query cache. Таблиці використовують server-side cursor pagination/filter/sort; контакти не кешуються в browser storage. Live counters/progress надходять через SSE з event cursor, heartbeat і reconnect; після gap UI робить snapshot refresh. Mutations мають disabled/pending/success/error states і не вважаються виконаними до server acknowledgement.

Auth реалізує FastAPI BFF через OIDC Authorization Code + PKCE, secure `HttpOnly/SameSite` session cookie та CSRF protection. Ролі з §13 визначають видимість і дії; UI не замінює server authorization. Scale-down, replay batch, unmerge, compaction apply, source disable і release publish показують impact preview; destructive/expensive дії вимагають typed confirmation, reason і свіжу resource revision.

| Можливість | `viewer` | `researcher` | `operator` | `admin` |
|---|:---:|:---:|:---:|:---:|
| Health, queues, workers, audit, redacted data | ✓ | ✓ | ✓ | ✓ |
| Full research records/contacts, create bounded export, temporary research pin | — | ✓ | ✓ | ✓ |
| Pause/resume, bounded backfill, replay, pool scale/drain у чинних min/max | — | — | ✓ | ✓ |
| Accept/reject match candidate, build/validate release, compaction dry-run | — | — | ✓ | ✓ |
| Change pool min/max/autoscale, source disable, manual block/unmerge, publish/supersede release | — | — | — | ✓ |
| Compaction apply/rollback, indefinite pin, configuration/role mapping | — | — | — | ✓ |

OIDC group → application role mapping versioned і audited. Backend перевіряє роль на кожному endpoint та SSE subscription; frontend gating є лише UX. Stateful database topology, image/mount/network/secret mutation і видалення artifact bucket не доступні через GUI жодній ролі.

## 8. Технології та їх призначення

| Технологія | Для чого | Обґрунтування/обмеження |
|---|---|---|
| Python 3.13 | усі worker/API компоненти | зріла scraping/data екосистема; версію фіксувати через `.python-version` |
| `uv` + `pyproject.toml` + lockfile | залежності й відтворювані збірки | один lockfile; бот оновлень створює окремі PR |
| HTTPX | основний async HTTP-клієнт fetch core (SSRF-guard, DNS pinning на кожному redirect hop, потоковий обрив body, ліміти), а також анонімні публічні JSON API і тестовані HTTP clients | selectors тільки в adapters; typed client для JSON API створюється лише для live-перевіреного anonymous endpoint; реєстраційний AUTO.RIA API у v1 не використовується. Scrapy не використовується — див. ADR-0008 (конфлікт з WorkerRuntime/PostgreSQL origin limiter, §7.6, R-53) |
| feedparser | RSS/Atom | зберігати feed entry ID і raw XML |
| Trafilatura + selectolax/lxml | виділення повного тексту й очищення HTML | site-specific selectors мають пріоритет; generic extractor є fallback |
| lingua-language-detector або fastText lid.176 | визначення мови | результат з confidence; source-declared language не ігнорувати мовчки |
| Playwright Python, pinned | звичайне анонімне JS-rendering як виняток | після bounded canary без challenge можна ввімкнути low-rate production route; browser binary pinned; CAPTCHA/challenge/login, fingerprint spoofing і private cookies не обходити |
| Pydantic v2 + JSON Schema | versioned контракти і валідація | schema snapshots у репозиторії |
| PostgreSQL 18 | control plane, job queue, news/translations, lineage, matching, outbox | транзакційний source of truth; JSONB лише для bounded extension fields |
| SQLAlchemy 2 + Alembic | PostgreSQL persistence і міграції | міграції forward-only; downgrade лише де безпечно |
| MongoDB 8.0 replica set | каталоги, offers, авто, sellers/contacts та їхні observations | гнучкі BSON documents; replica set потрібен для транзакцій/change streams; exact image digest pin |
| PyMongo Async API | MongoDB projector і domain repository | без ODM-магії; Pydantic contract → явний BSON mapping; retryable writes, primary reads, `readConcern=majority` і `writeConcern=majority`; транзакції — `snapshot` |
| S3-compatible storage (MinIO local, managed S3 prod) | raw і normalized artifacts, screenshots за потреби, exports | content-addressed keys; lifecycle: hot → compressed archive → delete згідно з політикою |
| DuckDB, pinned | локальні й CI-відтворювані дослідження Parquet releases | [напряму читає Parquet і підтримує filter/projection pushdown](https://duckdb.org/docs/stable/data/parquet/overview); read-only views/macros; не є operational DB та не читає mutable current state |
| FastAPI | operator/read API, OIDC BFF, SSE, health/readiness | єдиний write/control boundary для GUI; OpenAPI є frontend contract |
| React + TypeScript + Vite | український operator GUI | typed components/client, route chunks; React документує [TypeScript integration](https://react.dev/learn/typescript) і Vite-based setup |
| TanStack Query + React Router | server state, cursor lists, mutations і routes | cache keys містять API/schema version; contacts не persist-яться у browser storage |
| Nginx non-root image | static GUI assets і same-origin reverse proxy `/api` | immutable assets, CSP/security headers; auth/session логіка лишається у FastAPI BFF |
| Google Cloud Translation Advanced | основний переклад усіх перелічених мов в українську | офіційно підтримує `de`, `fr`, `en`, `lt`, `lv`, `et`, `pl`, `hu`, `ro`, `cs`, `sk`, `sl`, `hr`, `it`, `es`, `nl` і `uk`; batch для backfill, online для нових статей |
| NLLB-200 distilled або Marian/OPUS-MT | локальний fallback і cost experiment | запускати тільки після benchmark на затвердженому multilingual наборі; не змішувати результати без `provider/model` |
| Redis-compatible cache (optional) | translation memory hot cache; high-throughput limiter optimization після ADR | v1 global rate permit canonical у PostgreSQL; Redis не є source of truth |
| OpenTelemetry + Prometheus + Grafana + Loki | метрики, traces, logs, alerting | `source_id` у labels лише при контрольованій cardinality; URL не label |
| pytest + pytest-asyncio + respx | unit/contract/integration tests | мережа заборонена у звичайних tests |
| Vitest + Testing Library + Playwright Test | GUI unit/component/E2E | E2E запускається проти Docker stack; scale/destructive flows використовують test deployment adapter |
| Ruff + mypy strict | lint, format, type checks | однакові локально й у CI |
| Docker Engine + Compose | local, CI і single-host MVP | [Compose підтримує `--scale SERVICE=NUM`](https://docs.docker.com/reference/cli/docker/compose/up/); profiles, healthchecks, resource limits, без `container_name` у workers |
| Docker Swarm mode | production replicated workers і GUI-controlled scaling | [replicated services мають desired replica count](https://docs.docker.com/engine/swarm/how-swarm-mode-works/services/); controller обмежений allowlist/min-max і audit |
| GitHub Actions | CI, dependency/security scan, image build | secrets тільки GitHub Environments/Actions Secrets |

Версії бібліотек фіксуються lockfile. Оновлення Playwright завжди супроводжується перевстановленням відповідного browser binary, що прямо вимагає його [документація](https://playwright.dev/python/docs/browsers). Версію PostgreSQL перевіряти за офіційною [політикою підтримки](https://www.postgresql.org/support/versioning/). MongoDB 8.0 має [офіційний lifecycle](https://www.mongodb.com/legal/support-policy/lifecycles) до 2029-10-31; deployment використовує replica set, бо [standalone не підтримує multi-document transactions](https://www.mongodb.com/docs/manual/core/transactions-production-consideration/). Для collections обов'язкова [$jsonSchema validation](https://www.mongodb.com/docs/manual/core/schema-validation/), а unbounded arrays заборонені через [ліміт BSON document 16 MiB](https://www.mongodb.com/docs/manual/reference/limits/). Реалізація використовує офіційний [`AsyncMongoClient`](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/mongoclient/) і повторює всю транзакцію для `TransientTransactionError`; при `UnknownTransactionCommitResult` повторюється commit/перевірка receipt з тим самим idempotency key. Single-member replica set у local MVP надає transaction semantics, але не високу доступність.

## 9. Контракти даних

### 9.1. Мінімальні таблиці PostgreSQL

| Таблиця/група | Мінімальний контракт |
|---|---|
| `sources`, `source_policy_versions`, `source_routes`, `source_cursors` | UUID PK; canonical `source_id`; version/status; cursor payload; `created_at/updated_at`; optimistic version |
| `crawl_runs`, `crawl_jobs` | UUID PK/FK; `job_type`, `status` (`pending/leased/succeeded/retry/quarantined`), priority, idempotency key, `attempt/max_attempts`, `not_before`, `lease_owner/lease_expires_at`, timestamps/error code |
| `origin_rate_buckets`, `origin_rate_permits` | bucket: PK normalized origin, token/refill/concurrency budget, available tokens, `blocked_until`, revision/time; permit: UUID, origin, owner instance/job, acquired/lease expiry/released time; atomic acquire/idempotent return/expiry recovery |
| `worker_pools`, `worker_instances`, `scale_commands` | pool role, desired/current replicas+concurrency, min/max/mode/revision; instance boot ID/status/heartbeat/version/leases; idempotent requested/applied scale + actor/reason/result |
| `fetches`, `raw_objects`, `parse_attempts` | UUID PK/FK; requested/final URL, HTTP metadata, raw `sha256/uri/size`, parser/schema version, result/error, timestamps |
| `artifact_upload_claims`, `normalized_artifacts` | claim: unique object key, owner, status, lease expiry, monotonic int64 `claim_generation`; artifact: UUID PK, `entity_uuid`, `sha256/uri/size`, media type, domain, schema version, raw/fetch/parser lineage; без domain payload JSONB |
| `projection_tasks` | UUID `task_id`; FK artifact/entity; monotonic `projection_version`; target collection/schema; status/priority/attempt/not-before/lease; unique `(entity_uuid, projection_version)` і `parse_key` — ідентичність parse-кроку `(fetch_id, raw_sha256, parser_version, entity_uuid, target_collection)`, не вміст artifact (`docs/decisions/0007-event-tables-global-unique-over-partitioning.md`, D-2) |
| `projection_acknowledgements` | PK/FK `task_id`; entity/version; Mongo receipt ID/cluster time; `applied_to_current`, `state_changed`; acknowledged timestamp; result/event hashes |
| `entity_index` | PK `entity_uuid`; domain/source identity; Mongo collection/document ID; `confirmed_projection_version`; unique source identity; timestamps |
| `change_events`, `outbox_events` | UUID PK; event/aggregate/version/type/schema; payload або artifact pointer; `available_at`, `published_at`, attempts/error; unique event ID |
| `news_articles`, `news_article_versions`, `news_translations`, `translation_segments` | UUID PK/FK; source identity; immutable article version; original/cleaned/translated artifact refs; language/provider/model/glossary versions; timestamps |
| `entity_aliases`, `match_candidates`, `entity_resolution_decisions` | UUID PK/FK; candidate/decision version, action, member IDs, canonical group, score/evidence, rule/model version, actor, reason, effective/system timestamps, supersedes ID |
| `dataset_releases`, `release_parts`, `retention_pins`, `compaction_runs`, `version_archive_index` | immutable release/part manifests, watermarks, schema/code/config hashes, row counts/checksums; pin owner/reason/expiry; compaction state; exact entity/version → Parquet part/row-group locator + hash |
| `capacity_snapshots`, `exports`, `quality_results`, `dead_letters`, `audit_log` | measured/forecast volume and unit cost or manifest/status; actor/reason; timestamps |

Великі fetch-таблиці (`fetches`, `audit_log`) партиціонуються щомісяця за `fetched_at/created_at`. `raw_objects`, `change_events`, `outbox_events` лишаються непартиціонованими — їхній головний інваріант (глобальна унікальність за `sha256`/`event_id`) несумісний з partition-local unique PostgreSQL; обґрунтування, умови (retention `outbox_events`, тригер перегляду для `change_events`) і розглянуті альтернативи — `docs/decisions/0007-event-tables-global-unique-over-partitioning.md`. Обов'язкові operational indexes: `crawl_jobs(status, not_before, priority, job_id)`, `projection_tasks(status, not_before, priority, task_id)`, `outbox_events(published_at, available_at, event_id)` і `entity_index(domain, confirmed_projection_version, entity_uuid)`. `entity_index` не дублює domain document. Видалення raw object або domain document не повинно руйнувати lineage record.

### 9.2. Мінімальні collections MongoDB

- `catalog_items_current`, `catalog_offers_current`;
- `entity_projection_versions` для exact-version read/export;
- `catalog_offer_observations`, `product_reviews`, `product_questions`;
- `vehicle_listings_current`, `vehicle_observations`;
- `sellers_current`, `contact_observations`;
- `applied_projection_receipts` для idempotency/reconciliation.

| Тип документа | Обов'язкові поля |
|---|---|
| будь-який `*_current` | UUID `_id/entity_uuid`; `schema_version`; source identity; `projection_version`; `state_hash`; bounded core/attributes/latest state; lineage; first/last seen |
| `entity_projection_versions` | entity UUID; кожна `projection_version` і task; state hash; `state_changed`; previous current version/hash; bounded snapshot або immutable normalized artifact ref; lineage |
| offer/listing observation | UUID `_id`; parent/entity UUID; `projection_version`; `projection_task_id`; `observed_at`; state hash; observation reason (`changed/heartbeat`); snapshot або artifact ref; lineage |
| seller/contact observation | seller/entity UUID; source identity; typed contact values + original values; `observed_at`; projection version/task; lineage |
| review/question | UUID; parent catalog item UUID; source identity/item ID; `content_version`; author/name when public; rating/text/status; published/updated/observed timestamps; projection task; lineage |
| `applied_projection_receipts` | PK `projection_task_id`; entity UUID/version; target collection/document ID; `applied_to_current`; `state_changed`; previous/result version+hash; ready event bytes/media type/SHA-256 або immutable artifact ref; committed/cluster time |

Current document зберігає bounded snapshot, який зазвичай читається разом: source identity, normalized core, source-specific `attributes`, category/equipment, короткий media preview, latest state і lineage pointer. Це відповідає MongoDB-підходу «дані, які читаються разом, зберігати разом»; offers, observations, reviews, questions, контакти та повні media lists не вбудовуються як unbounded arrays, а мають окремі collections із reference IDs згідно з рекомендаціями щодо [embedding versus references](https://www.mongodb.com/docs/manual/data-modeling/concepts/embedding-vs-references/).

Мінімальний контракт current document:

```yaml
_id: UUID
schema_version: 1
entity_kind: catalog_item | catalog_offer | vehicle_listing | seller
source:
  source_id: string
  source_item_id: string
  canonical_url: string
identity_hash: string
projection_version: int64       # monotonic per entity_uuid
state_hash: string
core: object                    # versioned normalized fields
attributes: object              # source-specific polymorphic fields
latest_state: object            # price/status/mileage summary if applicable
lineage:
  fetch_id: UUID
  raw_sha256: string
  parser_version: string
  projection_task_id: UUID
time:
  source_event_at: datetime | null
  source_updated_at: datetime | null
  observed_at: datetime
  fetched_at: datetime
  ingested_at: datetime
  source_timezone_raw: string | null
  source_time_precision: second | minute | hour | day | month | year | unknown
  source_time_inferred: boolean
first_seen_at: datetime
last_seen_at: datetime
```

Обов'язкові indexes:

- unique `{source.source_id: 1, source.source_item_id: 1}` для current collections;
- unique `{entity_uuid: 1}` для current collections і unique `{entity_uuid: 1, projection_version: 1}` для `entity_projection_versions`;
- unique `{projection_task_id: 1}` для `entity_projection_versions`, observations і `applied_projection_receipts`;
- unique `{source.source_id: 1, source.source_item_id: 1, content_version: 1}` для reviews/questions; content version — source update version/timestamp або deterministic content hash;
- `{entity_uuid: 1, observed_at: -1}` для history; `{catalog_item_id: 1, last_seen_at: -1}` для offers; `{seller_id: 1, observed_at: -1}` для contacts; `{parent_item_id: 1, published_at: -1}` для reviews/questions;
- `{last_seen_at: -1}`, status/category/brand/model/location/seller indexes лише за підтвердженими query patterns;
- index budget і `$indexStats` review; wildcard index не вмикати без benchmark;
- стандартні collections у v1; MongoDB time-series або sharding — лише після load test та ADR.

Кожна collection має versioned `$jsonSchema`; validator спочатку працює `warn` на контрольованій міграції, потім `error`. Зміна document schema супроводжується backward-compatible reader, migration/reprojection plan і fixtures.

### 9.3. Ідентичність та ідемпотентність

1. Первинний природний ключ: `(source_id, source_item_id)`.
2. Якщо source ID відсутній, використовувати versioned `identity_hash` із canonical URL та стабільних атрибутів; алгоритм і поля документуються.
3. Fetch idempotency key: `source_id + normalized_url + planned_at_bucket + request_variant`.
4. Raw object key: `sha256(body)`; однакові bytes фізично не дублюються.
5. `entity_projection_version` додається для кожної task/version. Business observation додається лише якщо змінився значущий state hash або сплив heartbeat interval; `idempotency_key` однаковий при replay.
6. Cross-source matching ніколи не зливає записи без score і provenance; невпевнені збіги потрапляють у `match_candidates`.
7. Translation idempotency key: `article_version_id + target_language + provider + model_version + glossary_version`.
8. Телефон нормалізується в E.164, e-mail — lowercase/IDNA domain, але вихідний рядок завжди зберігається.

### 9.4. Сумісність контрактів

- Додавання optional field — minor schema version.
- Видалення/перейменування/зміна типу — major schema version і міграція споживачів.
- Кожен PR зі зміною схеми містить PostgreSQL migration або Mongo reprojection/migration plan, JSON Schema diff, fixture і compatibility test.
- Вихід адаптера не залежить від порядку полів чи локалі процесу.

### 9.5. Узгоджене читання та export snapshot

- `entity_index.confirmed_projection_version` змінюється тільки після підтвердженого Mongo receipt і ніколи не зменшується: `GREATEST(existing, receipt.projection_version)`. `domain.changed` створюється лише для `applied_to_current=true AND state_changed=true`.
- Online API читає PostgreSQL index, потім Mongo з filter `{entity_uuid, projection_version}`. Якщо current document новіший, API читає hot `entity_projection_versions`, а після compaction — `version_archive_index` і exact Parquet row/artifact ref. Лише якщо версії немає ні в hot, ні в archive, повертає `409 projection_inconsistent`, ставить reconcile task і не змішує версії.
- Export спочатку фіксує immutable manifest із PostgreSQL snapshot watermark та парами `(entity_uuid, confirmed_projection_version)`, а потім читає exact Mongo version records/snapshots. Manifest містить hash кожного part і schema versions; нові projections не змінюють уже створений export.
- Reconciler та consistency-critical reads використовують primary + majority concern. Eventual/stale secondary reads дозволені лише окремому exploratory endpoint із явним `consistency=stale_ok` і без export/quality рішень.

### 9.6. Часова модель

Система використовує дві незалежні часові осі:

- **source/effective time:** `source_event_at`, `source_updated_at` і source validity, якщо її явно дає джерело;
- **system/knowledge time:** `fetched_at`, `observed_at`, `ingested_at` і commit/version time системи.

Невідомий source time лишається `null`; заборонено заповнювати його `fetched_at`. Якщо аналітичний контракт потребує fallback, він повертає окремі `effective_at` і `effective_at_basis = source_event | source_updated | observed` та `source_time_inferred=true`. Зберігаються вихідний текст часу, timezone/offset, declared locale і precision (`second | minute | hour | day | month | year | unknown`).

У dataset release для кожної версії обчислюються дві напіввідкриті осі: `[valid_from, valid_to)` за `effective_at` і `[known_from, known_to)` за `ingested_at`; відкрита верхня межа є `null`. `valid_to` визначається наступною source-effective версією тієї сутності, `known_to` — наступною версією, яку система дізналася. Query contract підтримує окремі `as_of_valid_time` та `as_known_at`; late-arriving record вставляється у version history і не переписує system time. Новинне виправлення, relisting авто або backdated price зберігають обидві осі.

### 9.7. Retention, pinning і compaction

- Current documents, changed business observations, news versions, release manifests, lineage та audit зберігаються безстроково за замовчуванням; великі payloads переходять у cold object tier.
- `entity_projection_versions` із `state_changed=false` тримаються в MongoDB 90 днів, потім архівуються в partitioned Parquet за domain/source/month. Changed versions та heartbeat snapshots не видаляються з дослідницької історії; їх можна перемістити в cold Parquet, але не втратити.
- `retention_pins` захищають artifact/version за release, активним export, backup watermark, incident або явно названим research run. Pin має owner, reason, scope, created/expiry; безстроковий pin потребує owner review раз на рік.
- Compactor працює mark → dry-run manifest → immutable archive part → row/hash verify → PostgreSQL transaction, що вставляє archive locators і переводить run у `verified` → Mongo delete → 30-day rollback window → artifact sweep. API починає бачити locator до видалення hot record, тому немає вікна, де exact version відсутня. Він не видаляє current/confirmed version, останній successful snapshot сутності, pinned version або artifact із живим lineage reference.
- Mongo/API отримують archive locator для compacted version. Відтворення published release читає pinned hot record або archived Parquet з тим самим hash; silent omission заборонений.

### 9.8. Оборотне entity resolution

- Source records ніколи фізично не зливаються. Global entity/group є materialized projection послідовності `entity_resolution_decisions`.
- Decision contract: `decision_id`, `decision_version`, `action = merge | unmerge | reject | manual_link | manual_block`, member entity IDs, canonical group ID, evidence refs, feature values, score/calibration version, rule/model version, actor, reason, effective/system time і optional `supersedes_decision_id`.
- Auto-merge дозволений лише за domain threshold і precision gate; `manual_block` забороняє повторне auto-merge до явного superseding decision.
- Unmerge/split перебудовує aliases, group projection і наступні releases, але не змінює попередні published releases та не втрачає observations. Кожен export містить resolution snapshot/version.

### 9.9. Dataset release contract

Published dataset release є immutable і має стани `draft | building | validating | published | failed | superseded`. Manifest містить:

- `release_id`, human-readable tag, created/published time, owner і purpose;
- PostgreSQL snapshot/export watermark та список `(entity_uuid, projection_version)` або hash partition index;
- source registry/policy versions, included/excluded/degraded sources і причини;
- schema, parser, normalizer, matcher, resolution, translation provider/model/glossary versions;
- Git commit, container image digests, sanitized config hash і build command;
- part URIs, formats, partitions, row counts, min/max effective/system times, byte sizes і SHA-256;
- quality report, reconciliation result і link на previous/superseding release.

Для deterministic rebuild exporter фіксує column order/types, partition keys, stable row sort `(entity_uuid, projection_version)`, null/decimal/timestamp representation, compression codec/level, row-group size і writer library/version. Volatile build timestamps не потрапляють у part contents; SQL без явного `ORDER BY` не може використовувати фізичний file order як змістовний.

Опублікований release не перезаписується. Виправлення створює новий release; `uv run collector release verify --manifest <path>` перевіряє manifest/schema/part hashes до виконання DuckDB research SQL.

### 9.10. Operator API contract

Усі endpoints мають prefix `/api/v1`, генерують OpenAPI і повертають `application/problem+json` для помилок. List response: `items`, `next_cursor`, `snapshot_at`, `total_estimate` лише якщо дешево обчислюється. Mutation response: `command_id`, `status`, `resource_revision`, `audit_id`; `202 Accepted` використовується для довгих jobs. Mutations вимагають `Idempotency-Key`, а зміна versioned resource — `If-Match`/expected revision.

Мінімальні endpoint groups:

| Group | Read | Commands |
|---|---|---|
| System | `/system/overview`, `/health/components`, `/capacity` | acknowledge incident |
| Sources | `/sources`, `/sources/{id}`, `/sources/{id}/runs`, `/routes` | pause/resume/disable, update schedule/rate, backfill preview/start |
| Jobs | `/jobs`, `/jobs/{id}`, `/dead-letters` | replay preview/one/batch, quarantine/release |
| Worker pools | `/worker-pools`, `/worker-pools/{role}/instances`, `/scale-commands` | update desired replicas/concurrency, drain instance, set autoscale policy |
| Data | `/news`, `/catalog-items`, `/vehicle-listings`, `/entities/{id}/versions`, `/lineage/{id}` | export selection; domain records read-only |
| Translations | `/translation-jobs`, `/translation-quality`, `/translation-budget` | retry failed job, pause/resume backfill, update budget within role limits |
| Matching | `/match-candidates`, `/resolution-decisions` | merge/block/unmerge preview/apply |
| Releases | `/dataset-releases`, `/dataset-releases/{id}` | build, validate, publish, supersede, verify |
| Retention | `/retention-pins`, `/compaction-runs` | pin/unpin, compaction dry-run/apply/rollback |
| Audit/live | `/audit`, `/events` | `/events` є SSE GET із `after` cursor/`Last-Event-ID` |

Batch command завжди має окремий preview із resolved item count, filters snapshot і estimated cost/impact; apply посилається на `preview_id` та відхиляється після expiry або зміни revision. SSE event містить monotonic cursor, type, resource ID/revision і мінімальний summary без контактів чи повного payload. Відсутня/прострочена cursor position повертає `resync_required`, після чого GUI перечитує відповідний snapshot endpoint.

## 10. Алгоритм збору й оновлення

1. Scheduler завантажує enabled source manifest і чинну policy version.
2. Route Guard отримує діагностичний snapshot robots і застосовує URL patterns з manifest.
3. Discovery читає API/RSS/sitemap курсор і створює jobs із priority та idempotency key.
4. Worker бере lease, перевіряє policy ще раз і виконує conditional request.
5. Для 200/206 worker спочатку бере PostgreSQL upload claim із lease та монотонною `claim_generation`, пише bytes за content-addressed key у artifact store, виконує HEAD/checksum/size verification і лише з умовою `object_key + generation + lease_expires_at > now()` commit-ить посилання. Reacquire атомарно збільшує generation. Після паузи або втрати lease worker мусить повторно взяти claim і виконати HEAD; stale generation не може створити DB reference. Sweeper видаляє object лише після grace period, якщо немає DB reference або активного claim. Для 304 оновлюється freshness без нового raw object.
6. 429 поважає `Retry-After`; 5xx/network errors використовують exponential backoff із jitter; 401/403/CAPTCHA не ретраяться нескінченно, а ставлять channel/route incident. Джерело цілком вимикається лише коли не лишилося корисного анонімного каналу.
7. Parser читає immutable raw object, видає normalized records та validation report; catalog/vehicle payload серіалізується як versioned immutable artifact у S3/MinIO через той самий claimed/verified PUT protocol.
8. Для news PostgreSQL-транзакція upsert-ить article/version, lineage і outbox. Для catalog/vehicle вона записує artifact pointer/hash, атомарно видає per-entity `projection_version`, створює `projection_task` та `projection.command`, але не пише domain payload у PostgreSQL.
9. Mongo Projector обробляє одну task в одній транзакції: mandatory exact version record + conditional current update + optional business observation + `applied_projection_receipt`. Duplicate task повертає попередній результат; out-of-order task не знижує current version. Retry policy відповідає §8.
10. Після Mongo commit одна PostgreSQL-транзакція фіксує `projection_acknowledgement`, не зменшує confirmed entity version і, лише для `applied_to_current=true AND state_changed=true`, додає `domain.changed` та publish outbox; reconciler відновлює незавершені кроки з canonical event descriptor у receipt.
11. Для news article version створюється translation job. Текст сегментується по абзацах/реченнях без розриву HTML-структури, незмінні сегменти беруться з translation memory.
12. Translation worker перекладає в `uk`, відновлює структуру, валідовує числа, дати, URL, імена/терміни з glossary та записує immutable translation version.
13. Окремий publisher доставляє outbox events щонайменше один раз; споживачі зобов’язані бути ідемпотентними.
14. Завершення crawl run обчислює quality gates. Невдалий gate не позначає відсутні сутності видаленими.
15. Resolution worker створює candidates/decisions і перебудовує global entity groups; source records не змінюються, а unmerge є replay тієї самої decision history.
16. Release builder фіксує watermark і resolution snapshot, створює Parquet parts/manifest, перевіряє counts/hashes/quality та лише після цього атомарно переводить release у `published`.
17. Compactor архівує й видаляє hot history тільки за процедурою §9.7; capacity planner після кожного місячного зрізу оновлює forecast і scaling triggers.

Retry policy за замовчуванням: максимум 4 спроби для idempotent GET, backoff 5 с / 30 с / 2 хв / 10 хв із jitter; окремий денний retry budget на джерело. Timeout: connect 10 с, read 30 с, total 60 с; великі файли sitemap можуть мати окремий manifest override.

## 11. Вимоги до адаптерів джерел

Кожен адаптер містить:

- `manifest.yaml` із owner, status, country/languages, rating breakdown/date, anonymous-access evidence, base URLs, allow/deny patterns, channel, schedule, rate, retention і translation policy;
- discovery implementation із збережуваним cursor;
- parser(s) з явною `parser_version`;
- 3–10 raw fixtures без вилучення публічних полів: нормальний запис, contacts, missing optional fields, pagination, changed layout, 404/removed;
- golden normalized JSON і schema validation tests;
- selector health checks та minimum yield expectation;
- README із ручним smoke command, відомими лімітами і rollback/disable інструкцією.

Адаптер не має права:

- створювати власний HTTP client поза спільним fetch layer;
- змінювати глобальний rate limit;
- зберігати secrets; невідомі публічні поля дозволено зберігати в `attributes` із source path;
- виконувати browser evaluation для приватних або авторизованих internal API;
- вважати порожню відповідь доказом видалення.
- використовувати source endpoint, який вимагає реєстрацію, login, cookie приватної сесії або source API key у v1.

## 12. Якість, дедуплікація та повнота

### 12.1. Quality gates на crawl run

- parse success rate ≥ 99% для стабільного адаптера;
- required-field completeness ≥ 99.5%; для новин `title`, `canonical_url` і `source_event_at` або явний `source_time_missing_reason`; для offer/listing — ID, URL, price/status згідно з джерелом;
- item yield не падає більш ніж на 30% проти медіани 7 успішних порівнюваних запусків;
- cardinality категорій/валют/статусів не має неочікуваних нових значень;
- duplicate source keys = 0;
- source timestamps не більш ніж на 24 години в майбутньому й не старші заданого backfill window без flag; підозрілий час карантиниться, але не замінюється crawler time;
- усі записи мають `fetched_at/ingested_at`; nullable/source-time inference rate не відхиляється більш ніж на 10 percentage points від 7-run baseline без schema incident;
- для новин coverage translation = 100% завершених original article versions, крім записів зі статусом `translation_failed` і явним retry plan;
- placeholder/URL/числа в перекладі збережені на 100%, language detection українського результату ≥ 0.95 confidence для текстів понад 200 символів.

Провал gate переводить run у `quarantined`; дані лишаються збереженими, але не публікуються до огляду.

### 12.2. Нормалізація

- Unicode NFC, пробіли й керівні символи нормалізуються; вихідний текст лишається в raw.
- Мови визначаються з source metadata, `lang` і лише потім classifier; невпевненість зберігається.
- Одиниці переводяться до SI, але `raw_value/raw_unit` не втрачаються.
- Валюта не конвертується в primary record; FX-конверсія — окрема датована аналітична проєкція.
- Product matching: GTIN → brand+MPN → контрольований fuzzy candidate. Vehicle matching: source ID → точний VIN → нормалізовані make/model/year/mileage; невпевнені збіги не auto-merge.

### 12.3. Вибіркова перевірка

Для кожного релізу адаптера агент додає звіт не менш як по 30 випадкових записах: збіг з raw, валідність типів, ціна/валюта, час, URL, contacts і повнота полів. Для news adapter додатково перевіряються 30 перекладів носієм/редактором або за затвердженою rubric. Для малого джерела перевіряються всі записи, якщо їх менше 30.

Rubric перекладу, шкала 0–2 для кожного критерію: (1) збережено фактичний зміст і модальність; (2) правильно передано імена, посади, географію та терміни; (3) числа, валюти, дати, цитати й URL не спотворені; (4) українська граматично природна; (5) структура абзаців/списків збережена. Прохідний середній бал — не менше 1.8/2, при цьому критерії 1 і 3 повинні мати 2/2 у кожному зразку. Оцінка та reviewer ID зберігаються як `translation_quality_result`.

## 13. Технічна безпека

- Threat model: SSRF через sitemap/redirect, decompression bomb, oversized responses, malicious HTML/XML, SQL/log injection, secret leakage, dependency compromise і prompt-like текст усередині новин.
- Fetcher дозволяє лише `http/https`, перевіряє DNS/IP на кожному redirect і блокує loopback, link-local, private ranges та cloud metadata endpoints.
- Максимальний body за замовчуванням 20 МБ, sitemap gzip — 100 МБ після розпакування; перевищення карантиниться.
- XML parsing без external entities/DTD; HTML не виконується, крім ізольованого browser worker.
- Контейнери non-root, read-only root filesystem де можливо, окремі network policies й egress allowlist у production.
- Secrets скануються в pre-commit/CI; logs приховують Authorization/Cookie/API keys. Публічні контакти зберігаються у даних, але не дублюються в технічних logs і metric labels.
- Raw bucket шифрується; доступ розділений на writer/parser/auditor roles; object deletion журналюється.
- Облікові дані БД розділені за компонентами: scheduler/fetcher не має MongoDB credentials; parser пише лише artifact pointer/task/outbox у PostgreSQL; projector читає визначені projection rows у PostgreSQL і пише лише domain collections у MongoDB; API/exporter має read-only ролі в обох БД. Migration role не використовується runtime-процесами.
- Operator API: OIDC, RBAC (`viewer`, `researcher`, `operator`, `admin`), audit log усіх mutating actions. Сирі контакти доступні `researcher` і вище.
- GUI використовує same-origin BFF session, CSRF token і restrictive CSP; access/refresh tokens не зберігаються в `localStorage/sessionStorage`.
- GUI/API/worker images не отримують Docker socket. Swarm controller ізольований на manager node, читає лише committed `scale_commands` з audit link, перевіряє service label, pool revision, min/max і дозволений diff `replicas`; його credentials/network недоступні GUI.
- Batch replay, source disable, scale-to-zero, unmerge, compaction apply і release publish мають impact preview, typed confirmation, reason та audit before/after.
- Dependency та image scanning — щотижня і на кожен PR; critical CVE блокує release або має датоване risk acceptance.

## 14. Спостережуваність та експлуатація

### 14.1. Обов’язкові метрики

- `crawl_jobs_total{source,status}`, `crawl_job_duration_seconds`;
- `http_requests_total{source,status_class}`, `http_429_total`, `policy_blocks_total`;
- `items_discovered/parsed/accepted/quarantined`;
- `parser_failures_total{source,parser_version,error_code}`;
- `source_freshness_seconds`, `queue_oldest_age_seconds`, `dead_letters_total`;
- `raw_bytes_total`, `raw_dedup_ratio`, `artifact_orphans_total`, DB/storage utilization;
- `projection_tasks_total{domain,status}`, `projection_lag_seconds`, `projection_replays_total`, `cross_store_drift_total`;
- `worker_pool_replicas{role,state=desired|ready|draining|stale}`, `worker_slots{role,state}`, `worker_heartbeat_age_seconds`, `worker_active_leases`, `scale_commands_total{role,status}`;
- `origin_rate_permits_total{origin_group,result}`, `origin_inflight`, limiter wait/lock duration; origin labels мають bounded mapping, не raw hostname cardinality;
- Operator API request/error/latency, SSE active connections/reconnects/cursor gaps і GUI asset/version mismatch;
- `late_arrivals_total`, `source_time_null_ratio`, `source_time_inferred_total`, temporal-order violations;
- `resolution_decisions_total{action,actor_type}`, candidate precision sample, blocked remerge attempts;
- `compaction_candidates/archived/deleted/pinned`, archive verification failures, hot/cold bytes;
- `dataset_release_builds_total{status}`, release age/duration/rows/bytes/hash failures;
- capacity actual/forecast/headroom і monthly cost by storage/compute/translation class;
- `translation_jobs_total{source_language,status}`, `translation_characters_total`, `translation_cost`, `translation_latency_seconds`, `translation_memory_hit_ratio`;
- quality completeness/yield/duplicate metrics.

Не використовувати повні URL, exception messages або item IDs як metric labels.

### 14.2. Алерти

- SEV-1: витік secrets, неконтрольований request rate, підозрілий масовий export контактів, недоступність PostgreSQL/MongoDB/artifact store або підтверджена втрата projection.
- SEV-2: немає нових news понад 30 хв для активного джерела, projection lag понад 15 хв, cross-store drift, desired/ready worker mismatch понад 10 хв, усі replicas критичного role відсутні, release/archive hash failure, translation lag понад 20 хв, queue age понад SLO, parse success <95%, yield drop >50%, 429/403 spike.
- SEV-3: worker heartbeat stale, drain/scale command timeout, SSE cursor gaps, storage >70% або 90-day forecast порушує 30% headroom, source-time drift, окремий адаптер деградував, наближення анонімного rate budget.

Runbook має містити pause source, inspect raw/parse/projection error, scale/drain/recover worker pool, restore lease, replay from raw, reconcile PostgreSQL tasks із Mongo applied receipts та PostgreSQL acknowledgements, rotate key, sweep/expire artifact objects, rollback GUI/API/worker image і rollback parser/schema version.

## 15. Продуктивність і масштабування

- MVP worker process обробляє кілька jobs асинхронно, але per-origin limiter має верховенство над глобальною concurrency.
- Кожен role масштабується окремо; API, scheduler і stateful services не множаться командою scale worker pool.
- Replica count × concurrency є capacity ceiling, а не request-rate setting: origin token bucket і source policy завжди мають пріоритет.
- Worker не зберігає job/data на локальному filesystem; replacement replica підхоплює expired/released lease після readiness.
- Browser jobs ізольовані в окремій queue/pool з обмеженням CPU/RAM і concurrency 1 на pod/container.
- Translation jobs мають окрему queue, character budget і пріоритет: title/lead → body нової статті → backfill. Backfill не може витісняти свіжі новини.
- Sitemap streaming parser не завантажує весь документ у RAM.
- Projector може batch-читати/диспетчеризувати незалежні tasks, але кожна task/entity виконується в окремій MongoDB-транзакції; один unordered bulk не може змішувати atomic units. PostgreSQL batches мають обмежений transaction time.
- PostgreSQL API використовує keyset pagination; MongoDB — стабільний compound sort + `_id` cursor. `OFFSET/skip` не застосовувати на великих наборах.
- PostgreSQL partitions, Mongo indexes/collection sizes і retention перевіряються на dataset масштабу не менш як 2× річний прогноз.
- Raw HTML/XML/JSON, очищений оригінал і переклади зберігаються безстроково за замовчуванням; object storage має versioning, compression і tiering. Media binaries мають окремий retention через обсяг.

### 15.1. Capacity і cost model

Щомісячний `capacity_snapshot` зберігає actual, 30/90/365-day forecast, unit price/config source, confidence і owner для таких класів:

| Клас | Базова формула прогнозу | Trigger рішення |
|---|---|---|
| Fetch/network | planned requests × measured retry/change ratio × p50/p95 response bytes | rate/egress або daily budget >80% |
| S3 artifacts/releases | new compressed bytes + versioning overhead − verified lifecycle deletion | forecast залишає <30% headroom за 90 днів |
| Mongo data/indexes | current + projection versions + observations + measured index/replica factor | data+indexes >70% usable disk або working set не вміщується |
| PostgreSQL/WAL | rows/day × measured bytes + WAL + indexes + PITR retention | disk/WAL >70% або checkpoint/replication SLO порушено |
| Backups | full/incremental size × retention/replicas + restore scratch space | restore drill перевищив затверджений RTO/RPO |
| Translation | new/changed characters − translation-memory hits × provider unit price | forecast >80% monthly character/cost budget |
| Compute/browser | measured CPU/RAM seconds per 1k jobs × planned volume | p95 queue age/SLO порушено при <70% utilization reserve |

Модель використовує фактичні p50/p95 за останні 30 днів, окремо показує backfill і steady state та не змішує logical/compressed/replicated bytes. Зміна storage engine, sharding або нового analytics datastore потребує benchmark на 2× forecast, оцінки migration/restore cost і ADR. Для v1 дослідницькі запити виконуються DuckDB по partitioned Parquet; ClickHouse/OpenSearch не додаються без доведеного query/SLA gap.

## 16. Тестування та приймання

### 16.1. Рівні тестів

1. **Unit:** URL normalization, money/time/contact parsing, identity hash, retry decisions, language detection, translation segmentation/reassembly.
2. **Contract:** кожен fixture → очікуваний versioned JSON; schema compatibility.
3. **Integration:** PostgreSQL + MongoDB replica set + MinIO, job lease/recovery, outbox/projector/reconciliation, out-of-order/concurrent projection, transient/unknown Mongo commit result, S3 orphan sweep, SQL migrations і Mongo validators/indexes з нуля.
4. **End-to-end offline:** fixture discovery → raw/normalized artifacts → PostgreSQL pointer/task → Mongo projection/news SQL write → exact-version snapshot export, мережа заблокована.
5. **Live smoke:** максимум 3–10 configured URL, явний прапорець, стабільний User-Agent, без CI schedule.
6. **Load:** черга, PostgreSQL і MongoDB на 2× прогнозі, browser pool окремо.
7. **Translation QA:** golden multilingual corpus для всіх 16 вихідних мов, preservation тест чисел/URL/імен, glossary і regression score.
8. **Security:** SSRF redirect, zip bomb, XXE, hostile HTML, secret log checks.
9. **Temporal:** nullable/precision/timezone cases, late arrivals, backdated corrections, relisting і перевірка source/system axes.
10. **Resolution:** merge → manual block → unmerge → replay; source observations незмінні, старий release відтворюється, новий використовує новий snapshot.
11. **Compaction:** pin race, dry-run, archive row/hash verification, concurrent exact reads never see a gap, rollback window, restore exact version із Parquet.
12. **Release/analytics:** дві збірки з однаковим manifest дають ті самі hashes; DuckDB contract queries проходять без доступу до operational БД.
13. **Capacity:** synthetic 2× forecast, формули unit cost і thresholds перевіряються golden snapshot tests.
14. **Docker:** `docker compose config`, image build/SBOM, cold start/migrations/health, named-volume restart, profile isolation і pinned digest checks.
15. **Scaling:** replicas `1→4→1→0→2`, concurrent global rate-limit, expired permit recovery, concurrency hot-change, drain during active task, killed replica lease recovery, stale command/revision і controller allowlist tests.
16. **GUI:** React unit/component, generated-client contract, RBAC/CSRF, cursor pagination, SSE gap/reconnect, accessibility smoke і Playwright operator E2E.

### 16.2. Команди як контракт

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not live"
docker compose config --quiet
docker compose build --pull
docker compose --profile core --profile workers --profile gui up -d --wait
docker compose up -d --no-recreate --scale fetch-worker=4 --scale parse-worker=2
uv run alembic upgrade head
uv run collector db ensure-mongo --validators --indexes
uv run collector e2e --source fixtures --offline
uv run collector release build --watermark test --output .artifacts/release
uv run collector release verify --manifest .artifacts/release/manifest.json
duckdb ':memory:' -c "SELECT count(*) FROM read_parquet('.artifacts/release/**/*.parquet')"
cd web && npm ci && npm run lint && npm run test && npm run build && npm run test:e2e
```

Фінальні назви CLI можуть змінитися один раз у foundation PR; після цього README і CI мають виконувати саме ці команди.

### 16.3. Приймання релізу

- усі quality gates зелені на пілоті 7 діб;
- відновлення після kill worker та окремої недоступності PostgreSQL/MongoDB продемонстровано;
- повторний parse або projection тієї самої raw відповіді не створює дублікати;
- fault injection після Mongo commit, але до PostgreSQL acknowledgement, завершується idempotent replay; reconciler повертає drift до нуля;
- доставка projection versions у порядку `3, 1, 2` залишає current на версії 3; усі tasks мають рівно один receipt/acknowledgement й export читає підтверджену exact version;
- fault injection після S3 PUT, але до PostgreSQL commit, не створює DB reference на відсутній object; concurrent sweeper не видаляє object із живим claim, а producer зі stale claim повторює HEAD/reupload перед commit;
- ізольований restore PostgreSQL/raw/MongoDB за процедурою §7.4 відтворює domain state до зафіксованого watermark, а count/hash reconciliation не знаходить втрат або дублів;
- один source pause зупиняє нові запити не пізніше 60 секунд;
- відсутність credentials у source runtime не ламає інші джерела й не спричиняє спроб login;
- lineage від експортованого рядка до raw artifact відкривається через один API lookup, навіть якщо API внутрішньо читає entity index у PostgreSQL і document у MongoDB;
- late-arriving/backdated fixtures зберігають source та system time окремо й дають правильні `[valid_from, valid_to)` інтервали;
- merge/unmerge replay не змінює source records, відтворює попередній release і створює новий resolution snapshot;
- compaction dry-run, pin race, concurrent read, archive verification і rollback пройдено; published release після compaction має ті самі part hashes;
- capacity snapshot побудовано з фактичних pilot metrics; усі класи мають не менше 30% 90-day headroom або затверджений scaling ADR;
- DuckDB відкриває release після hash verification і виконує contract queries без credentials PostgreSQL/MongoDB;
- чистий Docker host підіймає core/workers/gui однією documented командою; migrations/validators завершуються до readiness, restart не втрачає named-volume data;
- scale `fetch 1→4→1` і `parse 1→4→1` змінює ready replicas без дублів, а сумарний origin request rate не перевищує source policy;
- scale-down під активним job завершує або повертає lease без втрати; kill replica відновлюється після lease expiry;
- Compose mode повертає audited scale command, Swarm test adapter застосовує лише replica diff allowlisted worker service; GUI/API не мають Docker socket;
- GUI E2E покриває pause/resume source, bounded backfill, dead-letter replay, worker scale/drain, matching block/unmerge, release publish і compaction dry-run з RBAC/audit evidence;
- кожне джерело з §4.1 має доказаний `source_state`; кожен route — `route_state`, а sample item — `content_access` із §5.5; для кожної з 19 країн щонайменше одне джерело з `source_state=enabled` віддає item із `content_access=full`, оригіналом і українським перекладом через API/SQL;
- 30 випадкових перекладів на кожну вихідну мову пройшли human QA за rubric, critical meaning errors = 0.

## 17. План реалізації незалежними агентами

### 17.1. Правила паралельної роботи

- Один work package — один owner, окрема branch/PR, чіткі вхідні/вихідні контракти.
- Агенти не редагують чужий адаптер або shared schema без узгодженого issue/ADR.
- Спочатку зливаються WP-00 і WP-01C, потім WP-01A/WP-01B/WP-01D та WP-02—WP-04; адаптери й GUI можуть паралельно працювати на versioned fixtures/OpenAPI після цього.
- WP-01C є єдиним owner shared IDs/event/artifact contracts; WP-01A — єдиним owner PostgreSQL migrations; WP-01B — єдиним owner Mongo validators/index migrations. Інші WP подають зміну shared schema як окрему dependency-задачу відповідному owner, а не редагують її паралельно.
- Кожен PR містить: зміни, тести, fixture provenance, ризики, як вимкнути/відкотити, що не перевірено live.
- Заборонено переносити тестові докази між джерелами: успішний Prom adapter не є доказом для Rozetka.
- Інтегратор не виправляє мовчки адаптер: повертає конкретний failed contract власнику або окремим PR із посиланням.

### 17.2. Work packages

| WP | Власність | Залежить від | Результат і критерій приймання |
|---|---|---|---|
| WP-00 | Docker/application foundation | — | repo layout, Python/web locks, multi-stage images, Compose profiles/networks/volumes/secrets, migrations, CI, SBOM; clean-host stack smoke green |
| WP-01C | Shared data contracts | WP-00 | canonical UUID/source identity, temporal axes, artifact, projection command/ack, resolution decision, domain event і dataset release schemas; compatibility fixtures green |
| WP-01A | PostgreSQL foundation | WP-01C | єдине ownership SQL migrations; control/news schemas, jobs, artifact pointers, projection tasks/acks, outboxes, entity index/lineage, release/pin/capacity tables; clean SQL integration green |
| WP-01B | MongoDB domain foundation | WP-01C, WP-01A | єдине ownership Mongo validators/index migrations; replica set, repositories, projector, receipts/reconciler, compaction/archive locator; crash/out-of-order/restore tests green |
| WP-01D | Worker pool control | WP-00, WP-01A | role commands, pool/instance/scale contracts, PostgreSQL origin limiter, heartbeat/drain, Compose command adapter і Swarm replica adapter; scale/rate/fault tests green |
| WP-02 | Fetch core | WP-01A | HTTP fetcher, robots snapshot, allowlist, limiter, retries, raw S3; SSRF/rate tests green |
| WP-03 | Discovery | WP-01A, WP-02 | API/RSS/sitemap streaming, cursors, idempotent jobs; gzip/pagination fixtures green |
| WP-04 | Translation core | WP-01A | segmenter, provider interface, Google adapter, translation memory, glossary, QA corpus; all language pairs green |
| WP-05 | News adapter SDK | WP-02–04 | RSS/sitemap/article extraction base, full-text contract, country/language config |
| WP-06A | News UA/DE/AT | WP-05 | окремий adapter на кожне джерело цих країн із §4.1 + translations |
| WP-06B | News FR/BE | WP-05 | окремий adapter на кожне джерело цих країн + `fr/nl/de -> uk` translations |
| WP-06C | News GB/US | WP-05 | окремий adapter на кожне джерело цих країн + `en -> uk` translations |
| WP-06D | News Baltics | WP-05 | усі LT/LV/EE джерела + `lt/lv/et -> uk` translations |
| WP-06E | News PL/HU/RO | WP-05 | усі джерела цих країн + translations |
| WP-06F | News CZ/SK/SI/HR | WP-05 | усі джерела цих країн + translations |
| WP-06G | News IT/ES | WP-05 | усі джерела цих країн + translations |
| WP-07 | Vehicle contracts & matching | WP-01A, WP-01B | vehicle/seller/contact contracts, reversible resolution decisions, manual block/unmerge, dictionaries і matching; golden fixtures; schema changes через WP-01A/B owners |
| WP-08A–D | Vehicle adapters | WP-03, WP-07 | один незалежний пакет на AUTO.RIA, OLX Авто, RST, Automoto; full public field coverage |
| WP-09 | Catalog contracts & matching | WP-01A, WP-01B | product/offer/review/question contracts, reversible resolution decisions, category mapping і matching; benchmark; schema changes через WP-01A/B owners |
| WP-10A–H | Catalog adapters | WP-03, WP-09 | один незалежний пакет на Prom, Rozetka, Epicentr, Allo, Hotline, Comfy, Foxtrot, MOYO |
| WP-11A | Operator API/releases | WP-01A, WP-01B, WP-04, WP-07, WP-09 | OpenAPI §9.10, bounded reads, preview/idempotent commands, pause/replay, immutable releases, temporal/resolution snapshots; reproducibility/RBAC tests |
| WP-11B | DuckDB research kit | WP-11A | pinned DuckDB, manifest verifier, read-only views/macros і representative price/vehicle/news SQL; offline contract queries green |
| WP-11C | Operator GUI | WP-01D, WP-11A | React/TypeScript Ukrainian GUI, generated client, cursor tables, SSE, worker scaling/drain і всі operator flows §7.7; RBAC/a11y/Playwright E2E green |
| WP-12 | Observability/lifecycle/runbooks | WP-01B, WP-01D, WP-02, WP-04, WP-11A | dashboards, worker/projection/drift/source/translation/release/capacity alerts, compactor, backup/restore/scale runbooks; injected-failure exercises |
| WP-13 | Security review | WP-02–12 | threat model validation, dependency/container scans, secret checks; findings triaged |
| WP-14 | Integration/release | усі required WP | 7-day pilot, 19-country coverage, traceability matrix, acceptance report; no unresolved critical/high findings |

### 17.3. Issue template для агента

Кожна задача повинна мати: scope/out-of-scope, файли у власності, input contract/version, output contract/version, fixtures, команди перевірки, acceptance criteria, залежності, source coverage і rollback/disable plan. Один adapter subpackage належить одному агенту; спільні SDK/схеми змінюються окремим PR.

## 18. Definition of Done

Задача завершена лише якщо:

- реалізація відповідає одному issue/WP і не містить сторонніх змін;
- formatter, lint, types, unit/contract/integration tests пройшли;
- зміна схеми має migration і compatibility evidence;
- зміна timestamp, matching або release contract має temporal/replay/reproducibility evidence;
- новий адаптер має manifest, fixtures, golden outputs, field coverage report, quality sample і bounded live smoke;
- документація, метрики й runbook оновлені;
- secret scan чистий; публічні контакти присутні тільки в Mongo domain collections та immutable domain artifacts, а не в fixtures з випадково приватних джерел або технічних logs;
- reviewers’ findings позначені `fixed`, `accepted with owner/date` або `not applicable` з аргументом;
- PR злитий тільки після CI та required review; commit SHA і release evidence зафіксовані.

## 19. Ризики та рішення

| Ризик | Імовірність/вплив | Запобігання | Тригер зупинки |
|---|---|---|---|
| Зміна DOM/API | висока/середній | raw fixtures, yield gates, versioned parser | parse <95% або yield -50% |
| Блокування/IP rate limit | висока/середній | API/RSS first, low rate, quota guard, source circuit breaker | 429 spike, 403/CAPTCHA або Retry-After |
| Помилкове «видалення» сутностей | середня/високий | 3 успішні full runs, quarantine | incomplete crawl/quality failure |
| Несанкціонований доступ до зібраних контактів | низька/високий | encryption, RBAC, audit log, закритий research API | аномальний export/access pattern |
| Вибух обсягу raw storage | висока/середній | compression, content addressing, lifecycle | >75% capacity або прогноз >бюджету |
| Невірний cross-source match | середня/середній | deterministic IDs first, candidate review | precision нижче 99% для auto-merge |
| Необоротний помилковий merge | середня/високий | source records immutable, versioned decisions, manual block, unmerge replay | source observation втрачено або split неможливий |
| Плутанина source/system time | висока/високий | окремі temporal axes, nullable source time, inference flag, late-arrival tests | crawler time записано як source time або negative interval |
| Неконтрольоване зростання projection history | висока/високий | hot/cold policy, pins, verified compaction, capacity forecast | <30% 90-day headroom або compaction verify failed |
| Невідтворюваний dataset release | середня/високий | immutable manifest, code/config/schema versions, part hashes, DuckDB verifier | повторна збірка має непояснений hash/count diff |
| Scale-out перевищує source rate | середня/високий | global PostgreSQL token bucket, source budget, multi-replica load test | aggregate origin rate вище policy або 429 spike |
| Scale-down втрачає/дублює jobs | середня/високий | drain state, bounded grace, leases, idempotency, kill tests | lost ack, duplicate domain change або stuck lease |
| Компрометація через Docker control | низька/критичний | GUI/API без socket; isolated Swarm controller, service allowlist, replica-only diff, audit | команда змінює image/mount/network/secret або stateful service |
| GUI показує застарілий успіх | середня/середній | server acknowledgement, optimistic revision, SSE cursor + snapshot recovery | дія показана completed без audit/server state |
| Розсинхронізація PostgreSQL і MongoDB | середня/високий | transactional outbox, monotonic projection version/CAS, Mongo applied receipt, PostgreSQL acknowledgement, reconciler, exact-version export | відсутній ack >15 хв або `cross_store_drift_total > 0` після reconcile |
| Надмірно великий Mongo document | середня/високий | bounded embedding, окремі observation/review/media collections, size metric | document >8 MiB або unbounded array detected |
| Надмірне використання браузера | середня/середній | browser by exception, budget metric | >10% fetches без ADR |
| Vendor API quota/ціна | середня/середній | cursor, cache, priority, quota alerts | залишок квоти <20% до reset |
| Висока вартість перекладу | висока/середній | translation memory, сегментний hash, character budget, batch | прогноз перевищує місячний бюджет |
| Помилка або втрата змісту в перекладі | середня/високий | original immutable, glossary, preservation tests, human QA sample | critical meaning error або змінене число/ім’я |
| Неповне виділення тексту новини | середня/високий | site selectors + generic fallback, paragraph count/yield gates | body порожній або різко коротший за baseline |

## 20. Відкриті питання і журнал рішень

| ID | Питання | Default до відповіді | Owner/дедлайн |
|---|---|---|---|
| Q-001 | Який перший дослідницький сценарій? | медіамоніторинг + історія цін | Product owner, до pilot |
| Q-002 | Завантажувати бінарні фото/відео чи лише URL/метадані? | URL/метадані; binary download off | Product owner, до WP-02 close |
| Q-003 | Як шардити повний каталог між worker pools? | стабільний hash категорії; збирати всі категорії | Engineering, до WP-03 close |
| Q-004 | Яку глибину історичного backfill робити? | максимально доступна в sitemap/API, але не старше 5 років | Product, до масового backfill |
| Q-005 | Retention raw/history? | raw/changed history безстроково з cold tier; unchanged projection records 90 днів hot, далі verified Parquet compaction | Data owner, до pilot |
| Q-006 | Інфраструктурний бюджет/SLO? | один хост MVP, SLO з §2.4 | Product/DevOps, до WP-00 close |
| Q-007 | Чи переходить login/API-key канал у майбутню версію? | ні; v1 завжди anonymous-only | Product, після v1 |
| Q-008 | Місячний бюджет Google Cloud Translation? | character budget конфігурується; backfill paused без ліміту | Product, до WP-04 live |
| Q-009 | Перекладати оновлену статтю повністю чи лише змінені сегменти? | лише змінені сегменти, потім збирати повну version | Product, до WP-04 close |
| Q-010 | Яка production topology MongoDB? | single-member replica set у локальному MVP; 3 data-bearing members у різних failure domains перед HA production | DevOps, до production |
| Q-011 | Яка cadence dataset releases? | щотижня та on-demand; published releases immutable | Research owner, до WP-11A close |
| Q-012 | Які auto-merge thresholds по доменах? | auto-merge лише deterministic IDs; fuzzy лишається candidate до benchmark precision ≥99% | Data owner, до WP-07/WP-09 close |
| Q-013 | Який production deployment mode? | Compose для local/single-host MVP; Swarm mode для automatic GUI-controlled replicas | DevOps, до WP-01D production enablement |
| Q-014 | Увімкнути worker autoscale? | ні; manual desired replicas/concurrency до 7-day load/pilot evidence | Product/DevOps, після pilot |

Рішення оформлювати у `docs/decisions/NNNN-title.md` з полями Context, Decision, Consequences, Date, Owner, Status.

## Додаток A. Мінімальна структура репозиторію

```text
.
├── README.md
├── TECHNICAL_SPECIFICATION.md
├── REVIEW.md
├── pyproject.toml
├── uv.lock
├── docker-compose.yml
├── src/collector/
│   ├── contracts/
│   ├── core/
│   ├── fetch/
│   ├── discovery/
│   ├── adapters/
│   │   ├── news/
│   │   ├── vehicles/
│   │   └── catalogs/
│   ├── normalization/
│   ├── translation/
│   ├── persistence/{postgres,mongo}/
│   ├── workers/
│   ├── orchestration/{compose,swarm}/
│   ├── api/
│   └── telemetry/
├── web/
│   ├── src/{api,components,features,routes}/
│   └── tests/{unit,e2e}/
├── schemas/{events,mongo,releases}/
├── migrations/{postgres,mongo}/
├── research/{sql,views}/
├── deploy/{compose,swarm}/
├── tests/{unit,contract,integration,e2e,fixtures}/
├── sources/<source_id>/manifest.yaml
├── docs/{adr,runbooks,decisions}/
├── dashboards/
└── .github/{workflows,ISSUE_TEMPLATE}/
```

## Додаток B. Приклад маніфесту джерела

```yaml
schema_version: 1
id: vehicle_ua_auto_ria
name: AUTO.RIA used vehicles
domain: vehicles
owner: data-acquisition
status: enabled
channel: sitemap_html
country_codes: [UA]
source_languages: [uk, ru]
base_urls:
  - https://auto.ria.com/
allowed_url_patterns:
  - '^https://auto\.ria\.com/uk/auto_[^/]+_[0-9]+\.html$'
denied_url_patterns: []
access:
  registration_required: false
  login_required: false
  anonymous_verified_at: '2026-09-22T00:00:00Z'
  anonymous_status: ok
rating:
  total: 91
  coverage: 24
  structured_access: 22
  anonymous_accessibility: 18
  data_richness: 15
  stability_observability: 12
  assessed_at: '2026-09-22'
schedule: '*/30 * * * *'
rate_limit:
  requests_per_second: 0.2
  concurrency: 1
  daily_request_budget: 10000
policy:
  robots_url: https://auto.ria.com/robots.txt
  open_route_circuit_on_status: [401, 403, 429]
  collect_all_public_fields: true
translation:
  enabled: false                 # vehicle records are normalized, not translated in MVP
storage:
  raw_hot_days: 90
  raw_archive_days: null         # keep indefinitely in cold tier
parser:
  name: auto_ria_vehicle
  version: 1.0.0
```

## Додаток C. Матриця трасування вимог

| Ціль | Вимоги | Перевірка |
|---|---|---|
| Керований збір | FR-001, FR-002, FR-004, §3 | rate/circuit-breaker tests, source manifest review |
| Доказовість і відтворення | FR-005, FR-006, FR-011, §9 | lineage integration, replay-from-raw E2E |
| Історія без втрат/дублів | FR-007, FR-008, FR-012 | idempotency, incomplete-crawl, change-event tests |
| Якість | §12, FR-015, FR-019 | contract fixtures, 30-record sample, coverage report, quality gates |
| Оригінал + переклад | FR-016, FR-017, §5.4 | multilingual golden corpus, translation lineage, human QA sample |
| Повні публічні поля | FR-018, §5 | raw-to-field trace, contacts/version tests |
| Технічна безпека | FR-013, §13 | SSRF/XXE/secret tests and scans |
| Експлуатація | FR-009, §14 | pause/replay drill, dashboards and alert exercise |
| Узгодженість двох БД | FR-020—FR-023, §7.3—§7.4, §9 | crash-window replay, reconciliation, backup/restore drill, bounded read/export tests |
| Часова коректність | FR-024, §9.6 | timezone/precision/null, late-arrival, relisting і bitemporal interval tests |
| Керована історія | FR-025, §9.7 | pin race, compaction dry-run, archive hash/row verify, rollback/restore |
| Оборотний matching | FR-026, §9.8 | merge/block/unmerge/replay, immutable source records, resolution snapshot |
| Відтворювані дослідження | FR-027, FR-029, §9.9 | manifest/part hashes, identical rebuild, offline DuckDB contract queries |
| Capacity і витрати | FR-028, §15.1 | monthly actual/forecast, golden formulas, 2× load і headroom gate |
| Docker і масштабування | FR-030—FR-033, §7.5—§7.6 | clean-host start, profiles, replica/drain/kill, global rate-limit і volume restart tests |
| Operator GUI | FR-034—FR-037, §7.7, §9.10 | OpenAPI contract, RBAC/CSRF, cursor/SSE recovery, preview/idempotency, accessibility і Playwright E2E |
| Незалежна реалізація | §17, §18 | WP acceptance, CI, contract/version ownership |
