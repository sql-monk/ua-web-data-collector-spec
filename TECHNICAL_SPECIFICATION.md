# Технічне завдання: дослідницька платформа збору каталогів, автобазарів і міжнародних новин

## 0. Паспорт документа

| Поле | Значення |
|---|---|
| Статус | Готово до декомпозиції та реалізації після підтвердження відкритих бізнес-рішень |
| Версія | 1.0 |
| Дата | 2026-09-22 |
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
6. Використовуємо лише публічно доступні сторінки/API/RSS/sitemap. Авторизація, приватні кабінети й paywall не входять до системи.
7. MVP призначений для внутрішнього дослідження; зовнішня публікація даних і UI не входять до MVP.
8. Інфраструктура MVP працює через Docker Compose на одному Linux-хості; компоненти лишаються горизонтально масштабованими.
9. Стартовий масштаб: до 5 млн активних сутностей, 30 млн спостережень на місяць, 3 млн новин/рік і до 5 ТБ сирих та очищених даних на рік.

Питання, що не блокують проєктування, але мають бути закриті до production:

- Які конкретні дослідницькі задачі будуть першими: ціни, асортимент, продавці, автомобілі, медіамоніторинг, події, тональність або тематичні тренди?
- Чи треба завантажувати бінарні файли фото/відео, чи достатньо їхніх URL, підписів і технічних метаданих?
- Які категорії товарів мають найвищий пріоритет для першого пілота?
- Яка допустима затримка оновлення і бюджет інфраструктури?
- Чи є API-ключі AUTO.RIA, OLX, Prom.ua, Rozetka, Google Cloud Translation або інших сервісів?
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
- вимірює свіжість, повноту, дублікати й помилки по кожному джерелу;
- дає змогу додавати нові адаптери без змін ядра.

### 2.2. У межах MVP

- Новини: повний оригінальний текст, заголовок, анонс, автори, рубрики, теги, час, canonical URL, мова, географія, медіа-метадані, очищений HTML, український переклад і provenance перекладу.
- Автобазари: повна публічна картка оголошення, опис, марка/модель/комплектація, рік, VIN, пробіг, технічні поля, географія, ціна, продавець, ім’я, телефони/e-mail, профіль, медіа URL, статус та історія змін.
- Каталоги: повна публічна картка товару, бренд, артикул/MPN/GTIN, категорія, характеристики, продавець, контакти, ціна, валюта, наявність, доставка, рейтинг, відгуки, запитання/відповіді, медіа URL та історія змін.
- Внутрішній API читання, експорт Parquet/JSONL, CLI керування, метрики і журнал запусків.

### 2.3. Поза межами MVP

- Купівля товарів, розміщення оголошень, повідомлення продавцям або інші write-операції на зовнішніх сайтах.
- Обхід CAPTCHA, fingerprinting-захисту, платного доступу чи геоблокування.
- Збір приватних кабінетів, чатів або полів, яких немає у публічному представленні.
- Розпізнавання облич і номерних знаків із зображень; текстові значення, уже опубліковані сайтом, зберігаються.
- Публічна пошукова система або UI для кінцевих користувачів.

### 2.4. Критерії успіху пілота

| Показник | Ціль |
|---|---|
| Успішні заплановані HTTP/API-запити | не менше 98% за 24 години без урахування контрольованих 304 |
| Новини: p95 затримки появи оригіналу | до 10 хв для RSS/API-джерел |
| Новини: p95 затримки українського перекладу | до 20 хв після отримання оригіналу |
| Автобазари: p95 затримки | до 60 хв у межах квоти API |
| Каталоги: p95 віку останнього спостереження | до 24 год для пріоритетних категорій |
| Валідність нормалізованих записів | не менше 99.5% за JSON Schema/Pydantic |
| Дублікати за ключем джерела | 0; семантичні дублікати між джерелами не більше 2% після матчингу |
| Відтворюваність | кожен запис має `source_id`, `fetch_id`, час, URL і hash сирого об’єкта |
| Відновлення після збою | повторний запуск не створює дублікатів і не втрачає підтверджені записи |

## 3. Операційні правила збору

1. Для кожного джерела обов’язковий `source manifest` із каналом доступу, URL-шаблонами, розкладом, ставкою запитів, cursor і політикою зберігання.
2. Пріоритет каналів: офіційний API → RSS/Atom → sitemap + HTML → headless browser, коли дані формуються JavaScript.
3. `robots.txt` отримується і версіонується як діагностичний артефакт, щоб пояснювати блокування та зміни структури.
4. 401/403/429, CAPTCHA або різке падіння yield автоматично зупиняють джерело й створюють технічний інцидент; нескінченні повтори заборонені.
5. User-Agent має бути стабільним, щоб поведінку crawler можна було відрізнити в логах і відтворити.
6. За замовчуванням: concurrency 1 на origin, не більше 0.2 запиту/с; ліміт підвищується тільки після вимірювання 429, latency й навантаження.
7. Повний текст новин і всі доступні публічні поля зберігаються. Оригінальні bytes незмінні; очищений текст і переклад є окремими похідними артефактами.
8. У MVP зберігаються URL, підписи, розміри й хеші медіа. Завантаження оригінальних фото/відео вмикається окремим параметром через значний обсяг.
9. Контактні дані зберігаються як versioned observations, оскільки продавець може змінити ім’я або телефон.
10. Denylist доменів і URL дає змогу терміново зупинити збір без перевипуску коду.

## 4. Джерела і пріоритети підключення

Статуси: **P0** — перша хвиля; **P1** — друга хвиля після стабілізації P0; **P2** — розширення покриття. Канал визначається так: RSS/API для discovery, HTML для повного публічного вмісту, sitemap для backfill і контролю повноти.

### 4.1. Новини

| Країна | ISO / мови | P0 — базове джерело | P1 — додаткові джерела |
|---|---|---|---|
| Україна | `UA` / `uk` | [Суспільне](https://suspilne.media/) | [Українська правда](https://www.pravda.com.ua/rss-info/), [LIGA.net](https://www.liga.net/ua/rss-page), [Укрінформ](https://www.ukrinform.ua/) |
| Німеччина | `DE` / `de` | [Tagesschau](https://www.tagesschau.de/infoservices/rssfeeds) | [Deutsche Welle](https://www.dw.com/de/), [ZEIT](https://www.zeit.de/) |
| Франція | `FR` / `fr` | [France 24](https://www.france24.com/fr/) | [RFI](https://www.rfi.fr/fr/), [Le Monde](https://www.lemonde.fr/rss/) |
| Велика Британія | `GB` / `en` | [BBC News](https://www.bbc.com/news) | [The Guardian](https://www.theguardian.com/help/feeds), [Sky News](https://news.sky.com/) |
| США | `US` / `en` | [NPR](https://www.npr.org/) | [AP News](https://apnews.com/), [The New York Times](https://www.nytimes.com/) |
| Литва | `LT` / `lt` | [LRT](https://www.lrt.lt/) | [15min](https://www.15min.lt/), [Delfi LT](https://www.delfi.lt/) |
| Латвія | `LV` / `lv` | [LSM](https://www.lsm.lv/barotnes/replay.lsm.lv/lv) | [Delfi LV](https://www.delfi.lv/), [TVNET](https://www.tvnet.lv/) |
| Естонія | `EE` / `et` | [ERR](https://www.err.ee/eesti/rss) | [Postimees](https://www.postimees.ee/), [Delfi EE](https://www.delfi.ee/) |
| Польща | `PL` / `pl` | [Polskie Radio](https://www.polskieradio.pl/) | [PAP](https://www.pap.pl/), [TVN24](https://tvn24.pl/) |
| Угорщина | `HU` / `hu` | [Telex](https://telex.hu/) | [HVG](https://hvg.hu/), [444](https://444.hu/) |
| Румунія | `RO` / `ro` | [HotNews](https://hotnews.ro/ce-este-rss-1654765) | [Digi24](https://www.digi24.ro/), [Agerpres](https://agerpres.ro/) |
| Чехія | `CZ` / `cs` | [iROZHLAS](https://www.irozhlas.cz/rss) | [ČT24](https://ct24.ceskatelevize.cz/), [Seznam Zprávy](https://www.seznamzpravy.cz/) |
| Словаччина | `SK` / `sk` | [STVR Správy](https://spravy.stvr.sk/) | [Aktuality.sk](https://www.aktuality.sk/), [SME](https://www.sme.sk/) |
| Словенія | `SI` / `sl` | [RTV Slovenija](https://www.rtvslo.si/) | [STA](https://www.sta.si/), [24UR](https://www.24ur.com/) |
| Хорватія | `HR` / `hr` | [HRT Vijesti](https://vijesti.hrt.hr/) | [Index.hr](https://www.index.hr/rss/info), [Jutarnji](https://www.jutarnji.hr/) |
| Італія | `IT` / `it` | [RaiNews](https://www.rainews.it/rss) | [ANSA](https://www.ansa.it/sito/static/ansa_rss.html), [la Repubblica](https://www.repubblica.it/static/servizi/rss/index.html) |
| Іспанія | `ES` / `es` | [RTVE Noticias](https://www.rtve.es/noticias/) | [El País](https://elpais.com/info/rss/), [La Vanguardia](https://www.lavanguardia.com/rss) |
| Бельгія | `BE` / `nl`, `fr`, `de` | [VRT NWS](https://www.vrt.be/vrtnws/) | [RTBF Info](https://www.rtbf.be/archive/info), [The Brussels Times](https://www.brusselstimes.com/) |
| Австрія | `AT` / `de` | [ORF News](https://orf.at/) | [Der Standard](https://www.derstandard.at/), [Die Presse](https://www.diepresse.com/) |

P0 дає по одному національному джерелу з кожної країни, тобто 19 адаптерів. P1 додає різні редакційні перспективи. Сторонній RSS-агрегатор не є першоджерелом: зберігати canonical URL, назву редакції та оригінальну мову. Для multilingual Бельгії країну й мову визначати окремо.

### 4.2. Автобазари

| Пріоритет | Джерело | Канал | Що збираємо | Технічна примітка |
|---|---|---|---|---|
| P0 | [AUTO.RIA](https://developers.ria.com/docs/) | API + HTML detail | усі типи авто, довідники, повна публічна картка і контакти | API для discovery/ID, HTML для полів, яких немає в API; квоти рахуються окремо |
| P0 | [OLX Авто](https://www.olx.ua/uk/transport/legkovye-avtomobili/) | sitemap/category/detail HTML; API якщо доступний | повна публічна картка, продавець, контакти, фото URL | Playwright тільки для полів, які з’являються після JS; 403/429 зупиняє source |
| P1 | [RST.ua](https://rst.ua/) | sitemap/category/detail HTML | оголошення, контакти, ціна й характеристики | окремий adapter і власний identity key |
| P1 | [Automoto.ua](https://automoto.ua/) | sitemap/category/detail HTML | агреговані оголошення, контакти, ціна | визначати upstream source і не зливати дублікати без provenance |

### 4.3. Каталоги і ціни

| Пріоритет | Джерело | Канал | Що збираємо | Технічна примітка |
|---|---|---|---|---|
| P0 | [Prom.ua](https://prom.ua/robots.txt) | product sitemap + category/detail HTML | товари, продавці, контакти, відгуки, запитання, ціни, наявність | marketplace-wide discovery через sitemap; seller API не є заміною |
| P0 | [Rozetka](https://rozetka.com.ua/robots.txt) | sitemap/category/detail HTML | товари, продавці, характеристики, відгуки, запитання, ціни | окремо парсити product identity та offers різних продавців |
| P0 | [Epicentrk.ua](https://epicentrk.ua/robots.txt) | product/category sitemap + detail HTML | товари Epicentr і marketplace sellers, повні характеристики й ціни | sitemap розділяє власні та marketplace товари |
| P1 | [Allo](https://allo.ua/robots.txt) | `sitemap.xml`, `ua-sitemap.xml`, detail HTML | товари, характеристики, продавці, ціни, відгуки | JS fallback за виміряною потребою |
| P1 | [Hotline](https://hotline.ua/robots.txt) | sitemap/category/detail HTML | нормалізовані моделі товарів, магазини, історія пропозицій | корисний як cross-source product matcher |
| P1 | [Comfy](https://comfy.ua/) | sitemap/detail HTML | електроніка й побутова техніка | використовувати JSON-LD як перший selector |
| P2 | [Foxtrot](https://www.foxtrot.com.ua/) | sitemap/detail HTML | електроніка й побутова техніка | окремий source rate limit |
| P2 | [MOYO](https://www.moyo.ua/) | sitemap/detail HTML | електроніка й супутні товари | окремий source rate limit |

Перед реалізацією адаптера агент зберігає датований snapshot RSS/sitemap/robots і 3–10 representative pages. Це технічна база для regression tests і пояснення змін сайту.

## 5. Дані, які збираємо

### 5.1. Спільні поля сутності

- `id` — UUIDv7 внутрішньої сутності;
- `source_id`, `source_item_id` — джерело та стабільний ID на джерелі;
- `canonical_url`, `source_url`;
- `title`, `description_excerpt`, `full_text`, `language`, `country_code`;
- `published_at`, `updated_at_source`, `first_seen_at`, `last_seen_at` у UTC;
- `status`: `active`, `inactive`, `deleted`, `unknown`;
- `content_hash`, `identity_hash`, `fetch_id`, `parser_version`;
- `raw_object_uri`, `schema_version`;
- `attributes` JSONB для всіх публічних полів, які ще не стандартизовані;
- `contacts` як окрема versioned collection: тип, нормалізоване й вихідне значення, ім’я/роль, `first_seen_at`, `last_seen_at`;
- `media_assets`: URL, тип, caption, width/height/duration, source hash і optional downloaded object URI.

Грошові значення зберігаються як `amount_minor BIGINT` + `currency CHAR(3)`, ніколи як float. Час — `timestamptz`; вихідний timezone/offset зберігається окремо, якщо джерело його передає.

### 5.2. Product і OfferObservation

`Product`: brand, model, category path, GTIN/EAN/UPC, MPN, normalized/raw attributes, descriptions, documents, image/video URLs.

`Offer`: seller/store ID, seller name/profile/contacts, source product ID, SKU, URL, condition, delivery/payment/region, rating.

`OfferObservation`: observed_at, price, old price, availability, stock text, promotion label, seller/contact snapshot. Відгуки, запитання й відповіді мають власні стабільні source IDs і версії. Історія append-only; поточний стан — materialized view або окрема проєкція.

### 5.3. VehicleListing і VehicleObservation

`VehicleListing`: make/model/generation/trim IDs і вихідні назви, year, body, fuel, transmission, drive, engine volume, power, mileage, VIN, registration plate якщо опублікований текстом, description, equipment/options, damage/customs/inspection flags, location, seller type/name/profile, contacts, media URLs і URL оголошення.

`VehicleObservation`: observed_at, price, currency, mileage, listing status, promoted flag, view counters, seller/contact snapshot і зміни опису.

### 5.4. NewsArticle

`NewsArticle`: source article ID, canonical URL, country, original language, title, lead, full original text, cleaned HTML, author/byline, section/tags, publication/update timestamps, related media URLs, links, source attribution і content hash.

`NewsTranslation`: article ID, target language `uk`, translated title/lead/body, provider, model/version, glossary version, source content hash, created_at, status, quality flags і cost/character count. Зміна оригіналу створює нову версію перекладу; старий переклад не перезаписується.

Якщо original language уже `uk`, `NewsTranslation.status = not_required`, а read API повертає оригінальні поля як українське представлення без повторного зберігання тексту. Якщо одна сторінка містить кілька мов, перекладати сегменти, для яких language detector не повернув `uk`.

## 6. Функціональні вимоги

| ID | Вимога |
|---|---|
| FR-001 | Реєстр джерел керує статусом, каналом, розкладом, лімітами, URL-шаблонами і політикою зберігання без зміни коду ядра. |
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
| FR-016 | Для кожної неукраїномовної новини система створює український переклад заголовка, lead і повного тексту, зберігаючи оригінал. |
| FR-017 | Translation memory не відправляє повторно незмінні сегменти; ключ містить source language, target language, normalized segment hash, provider/model і glossary version. |
| FR-018 | Публічні контакти, імена, профілі, VIN та інші доступні поля мають зберігатися разом із provenance і часовою версією. |
| FR-019 | Кожне джерело має coverage report: відомі типи сторінок, поля, pagination/backfill межі, кількість виявлених і пропущених записів. |

## 7. Архітектура

```text
Source Registry ──> Scheduler ──> Discovery ──> Fetch Queue ──> HTTP/API Fetcher
       │                 │                              └──────> Browser Fetcher (exception)
       │                 │                                         │
       └── Route Guard ──┴─────────────────────────────────────────┤
                                                                  v
                                                        S3/MinIO Raw Store
                                                                  │
                                                                  v
                                                  Extractor + Parser + Validator
                                                                  │
                                       ┌──────────────────────────┼───────────────┐
                                       v                          v               v
                              PostgreSQL Core          Translation Queue   Dead Letter Queue
                                                                  │
                                                                  v
                                                     UK Translation + QA
                                       │
                              Change/Event Outbox
                                       │
                         Export API / Parquet / Consumers

All stages ──> OpenTelemetry metrics/traces/logs ──> Prometheus + Grafana + Loki
```

### 7.1. Межі компонентів

- **Registry/Route Guard:** єдина точка URL allow/deny rules і швидкості. Адаптер не може напряму обійти її.
- **Scheduler:** створює jobs; не містить CSS/XPath selectors.
- **Discovery:** повертає кандидатні URL/IDs і cursor; не парсить доменну картку.
- **Fetcher:** отримує bytes; не знає доменної схеми.
- **Raw Store:** immutable, content-addressed, шифрований; повторний parse не потребує мережі.
- **Extractor/Parser:** адаптер джерела + версія; виділяє main content, structured data, contacts та доменні поля; не робить зовнішніх HTTP-запитів.
- **Translation:** сегментує очищений оригінал, використовує translation memory і перекладає в `uk`; ніколи не змінює original artifact.
- **Normalizer/Matcher:** приводить одиниці, довідники й ідентичності, не змінює raw.
- **Core DB:** operational state, normalized entities, observations, lineage і outbox.
- **Exporter:** read-only відносно core tables.

### 7.2. Черга MVP

Використати PostgreSQL job table з `FOR UPDATE SKIP LOCKED`, lease timeout, `attempt`, `not_before`, унікальним idempotency key і dead-letter status. Це скорочує кількість сервісів і гарантує транзакційний outbox.

Перехід на RabbitMQ/Redpanda допускається лише після виміряної межі: понад 100 jobs/s стабільно, черга понад 1 млн pending jobs або потреба в незалежному масштабуванні багатьох типів споживачів. Перехід оформлюється ADR і не змінює job payload contract.

## 8. Технології та їх призначення

| Технологія | Для чого | Обґрунтування/обмеження |
|---|---|---|
| Python 3.13 | усі worker/API компоненти | зріла scraping/data екосистема; версію фіксувати через `.python-version` |
| `uv` + `pyproject.toml` + lockfile | залежності й відтворювані збірки | один lockfile; бот оновлень створює окремі PR |
| Scrapy 2.13.x | crawl lifecycle, downloader middleware, throttling, sitemap | основний HTTP crawler; selectors тільки в adapters |
| HTTPX | офіційні JSON API і тестовані клієнти | окремі typed clients для AUTO.RIA та інших API |
| feedparser | RSS/Atom | зберігати feed entry ID і raw XML |
| Trafilatura + selectolax/lxml | виділення повного тексту й очищення HTML | site-specific selectors мають пріоритет; generic extractor є fallback |
| lingua-language-detector або fastText lid.176 | визначення мови | результат з confidence; source-declared language не ігнорувати мовчки |
| Playwright Python, pinned | JS-rendering як виняток | окремий worker pool; browser binary має відповідати версії пакета; не застосовувати для обходу блокувань |
| Pydantic v2 + JSON Schema | versioned контракти і валідація | schema snapshots у репозиторії |
| PostgreSQL 18 | core data, job queue, history, outbox | підтримувана гілка до 2030; JSONB лише для extension fields |
| SQLAlchemy 2 + Alembic | persistence і міграції | міграції forward-only; downgrade лише де безпечно |
| S3-compatible storage (MinIO local, managed S3 prod) | raw HTML/XML/JSON, screenshots за потреби, exports | lifecycle: hot → compressed archive → delete згідно з політикою |
| FastAPI | operator/read API, health/readiness | не відкривати назовні без auth gateway |
| Google Cloud Translation Advanced | основний переклад усіх перелічених мов в українську | офіційно підтримує `de`, `fr`, `en`, `lt`, `lv`, `et`, `pl`, `hu`, `ro`, `cs`, `sk`, `sl`, `hr`, `it`, `es`, `nl` і `uk`; batch для backfill, online для нових статей |
| NLLB-200 distilled або Marian/OPUS-MT | локальний fallback і cost experiment | запускати тільки після benchmark на затвердженому multilingual наборі; не змішувати результати без `provider/model` |
| Redis-compatible cache (optional) | translation memory hot cache і distributed rate limits | source of truth лишається PostgreSQL; не потрібен на першому локальному запуску |
| OpenTelemetry + Prometheus + Grafana + Loki | метрики, traces, logs, alerting | `source_id` у labels лише при контрольованій cardinality; URL не label |
| pytest + pytest-asyncio + respx | unit/contract/integration tests | мережа заборонена у звичайних tests |
| Ruff + mypy strict | lint, format, type checks | однакові локально й у CI |
| Docker Compose | локальне середовище/MVP | non-root containers, healthchecks, resource limits |
| GitHub Actions | CI, dependency/security scan, image build | secrets тільки GitHub Environments/Actions Secrets |

Версії бібліотек фіксуються lockfile. Оновлення Playwright завжди супроводжується перевстановленням відповідного browser binary, що прямо вимагає його [документація](https://playwright.dev/python/docs/browsers). Версію PostgreSQL перевіряти за офіційною [політикою підтримки](https://www.postgresql.org/support/versioning/).

## 9. Контракти даних

### 9.1. Мінімальні таблиці

- `sources`, `source_policy_versions`, `source_cursors`;
- `crawl_runs`, `crawl_jobs`, `fetches`, `raw_objects`, `parse_attempts`;
- `products`, `offers`, `offer_observations`, `product_reviews`, `product_questions`;
- `vehicle_listings`, `vehicle_observations`, `sellers`, `contact_observations`;
- `news_articles`, `news_article_versions`, `news_translations`, `translation_segments`;
- `entity_aliases`, `match_candidates`;
- `change_events`, `outbox_events`, `exports`;
- `quality_results`, `dead_letters`, `audit_log`.

Великі observation/fetch tables партиціонуються щомісяця за `observed_at/fetched_at`. Foreign keys зберігаються там, де не блокують retention; видалення raw object не повинно руйнувати lineage record.

### 9.2. Ідентичність та ідемпотентність

1. Первинний природний ключ: `(source_id, source_item_id)`.
2. Якщо source ID відсутній, використовувати versioned `identity_hash` із canonical URL та стабільних атрибутів; алгоритм і поля документуються.
3. Fetch idempotency key: `source_id + normalized_url + planned_at_bucket + request_variant`.
4. Raw object key: `sha256(body)`; однакові bytes фізично не дублюються.
5. Observation додається лише якщо змінився значущий state hash або сплив heartbeat interval.
6. Cross-source matching ніколи не зливає записи без score і provenance; невпевнені збіги потрапляють у `match_candidates`.
7. Translation idempotency key: `article_version_id + target_language + provider + model_version + glossary_version`.
8. Телефон нормалізується в E.164, e-mail — lowercase/IDNA domain, але вихідний рядок завжди зберігається.

### 9.3. Сумісність контрактів

- Додавання optional field — minor schema version.
- Видалення/перейменування/зміна типу — major schema version і міграція споживачів.
- Кожен PR зі зміною схеми містить migration, JSON Schema diff, fixture і compatibility test.
- Вихід адаптера не залежить від порядку полів чи локалі процесу.

## 10. Алгоритм збору й оновлення

1. Scheduler завантажує enabled source manifest і чинну policy version.
2. Route Guard отримує діагностичний snapshot robots і застосовує URL patterns з manifest.
3. Discovery читає API/RSS/sitemap курсор і створює jobs із priority та idempotency key.
4. Worker бере lease, перевіряє policy ще раз і виконує conditional request.
5. Для 200/206 bytes пишуться в raw store; для 304 оновлюється freshness без нового raw object.
6. 429 поважає `Retry-After`; 5xx/network errors використовують exponential backoff із jitter; 401/403/CAPTCHA не ретраяться нескінченно, а ставлять source incident.
7. Parser читає immutable raw object, видає normalized records та validation report.
8. Транзакція upsert-ить entity, контакти й observation, додає change event, оновлює cursor та outbox.
9. Для news article version створюється translation job. Текст сегментується по абзацах/реченнях без розриву HTML-структури, незмінні сегменти беруться з translation memory.
10. Translation worker перекладає в `uk`, відновлює структуру, валідовує числа, дати, URL, імена/терміни з glossary та записує immutable translation version.
11. Окремий publisher доставляє outbox events щонайменше один раз; споживачі зобов’язані бути ідемпотентними.
12. Завершення crawl run обчислює quality gates. Невдалий gate не позначає відсутні сутності видаленими.

Retry policy за замовчуванням: максимум 4 спроби для idempotent GET, backoff 5 с / 30 с / 2 хв / 10 хв із jitter; окремий денний retry budget на джерело. Timeout: connect 10 с, read 30 с, total 60 с; великі файли sitemap можуть мати окремий manifest override.

## 11. Вимоги до адаптерів джерел

Кожен адаптер містить:

- `manifest.yaml` із owner, status, country/languages, base URLs, allow/deny patterns, channel, schedule, rate, retention і translation policy;
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

## 12. Якість, дедуплікація та повнота

### 12.1. Quality gates на crawl run

- parse success rate ≥ 99% для стабільного адаптера;
- required-field completeness ≥ 99.5%; для новин `title`, `canonical_url`, `published_at`; для offer/listing — ID, URL, price/status згідно з джерелом;
- item yield не падає більш ніж на 30% проти медіани 7 успішних порівнюваних запусків;
- cardinality категорій/валют/статусів не має неочікуваних нових значень;
- duplicate source keys = 0;
- часові значення не більш ніж на 24 години в майбутньому й не старші заданого backfill window без позначки.
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
- Operator API: OIDC, RBAC (`viewer`, `researcher`, `operator`, `admin`), audit log усіх mutating actions. Сирі контакти доступні `researcher` і вище.
- Dependency та image scanning — щотижня і на кожен PR; critical CVE блокує release або має датоване risk acceptance.

## 14. Спостережуваність та експлуатація

### 14.1. Обов’язкові метрики

- `crawl_jobs_total{source,status}`, `crawl_job_duration_seconds`;
- `http_requests_total{source,status_class}`, `http_429_total`, `policy_blocks_total`;
- `items_discovered/parsed/accepted/quarantined`;
- `parser_failures_total{source,parser_version,error_code}`;
- `source_freshness_seconds`, `queue_oldest_age_seconds`, `dead_letters_total`;
- `raw_bytes_total`, `raw_dedup_ratio`, DB/storage utilization;
- `translation_jobs_total{source_language,status}`, `translation_characters_total`, `translation_cost`, `translation_latency_seconds`, `translation_memory_hit_ratio`;
- quality completeness/yield/duplicate metrics.

Не використовувати повні URL, exception messages або item IDs як metric labels.

### 14.2. Алерти

- P1: витік secrets, неконтрольований request rate, підозрілий масовий export контактів, недоступність DB/raw store.
- P2: немає нових P0 news понад 30 хв, translation lag понад 20 хв, queue age понад SLO, parse success <95%, yield drop >50%, 429/403 spike.
- P3: storage >75%, окремий адаптер деградував, наближення API quota.

Runbook має містити pause source, inspect raw/parse error, restore lease, replay from raw, rotate key, expire/delete raw objects і rollback parser version.

## 15. Продуктивність і масштабування

- MVP worker process обробляє кілька jobs асинхронно, але per-origin limiter має верховенство над глобальною concurrency.
- Browser jobs ізольовані в окремій queue/pool з обмеженням CPU/RAM і concurrency 1 на pod/container.
- Translation jobs мають окрему queue, character budget і пріоритет: title/lead → body нової статті → backfill. Backfill не може витісняти свіжі новини.
- Sitemap streaming parser не завантажує весь документ у RAM.
- Bulk inserts observations виконуються пакетами 100–1000 із обмеженим transaction time.
- API читання використовує keyset pagination; `OFFSET` не застосовувати на великих таблицях.
- Партиції, indexes і retention перевіряються на dataset масштабу не менш як 2× річний прогноз.
- Raw HTML/XML/JSON, очищений оригінал і переклади зберігаються безстроково за замовчуванням; object storage має versioning, compression і tiering. Media binaries мають окремий retention через обсяг.

## 16. Тестування та приймання

### 16.1. Рівні тестів

1. **Unit:** URL normalization, money/time/contact parsing, identity hash, retry decisions, language detection, translation segmentation/reassembly.
2. **Contract:** кожен fixture → очікуваний versioned JSON; schema compatibility.
3. **Integration:** PostgreSQL + MinIO, job lease/recovery, transaction/outbox, migrations from empty DB.
4. **End-to-end offline:** fixture discovery → raw → parse → DB → export, мережа заблокована.
5. **Live smoke:** максимум 3–10 configured URL, явний прапорець, стабільний User-Agent, без CI schedule.
6. **Load:** черга і DB на 2× прогнозі, browser pool окремо.
7. **Translation QA:** golden multilingual corpus для всіх 16 вихідних мов, preservation тест чисел/URL/імен, glossary і regression score.
8. **Security:** SSRF redirect, zip bomb, XXE, hostile HTML, secret log checks.

### 16.2. Команди як контракт

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not live"
docker compose up -d --wait postgres minio
uv run alembic upgrade head
uv run collector e2e --source fixtures --offline
```

Фінальні назви CLI можуть змінитися один раз у foundation PR; після цього README і CI мають виконувати саме ці команди.

### 16.3. Приймання релізу

- усі quality gates зелені на пілоті 7 діб;
- відновлення після kill worker і недоступності DB продемонстровано;
- повторний parse тієї самої raw відповіді не створює дублікати;
- один source pause зупиняє нові запити не пізніше 60 секунд;
- видалення API key не ламає інші джерела;
- lineage від експортованого рядка до raw artifact відкривається за один API/SQL lookup;
- для кожної з 19 країн працює щонайменше одне P0-джерело, а оригінал і український переклад доступні через API/SQL;
- 30 випадкових перекладів на кожну вихідну мову пройшли human QA за rubric, critical meaning errors = 0.

## 17. План реалізації незалежними агентами

### 17.1. Правила паралельної роботи

- Один work package — один owner, окрема branch/PR, чіткі вхідні/вихідні контракти.
- Агенти не редагують чужий адаптер або shared schema без узгодженого issue/ADR.
- Спочатку зливаються WP-00—WP-04; адаптери можуть паралельно працювати на versioned fixtures/schema після цього.
- Кожен PR містить: зміни, тести, fixture provenance, ризики, як вимкнути/відкотити, що не перевірено live.
- Заборонено переносити тестові докази між джерелами: успішний Prom adapter не є доказом для Rozetka.
- Інтегратор не виправляє мовчки адаптер: повертає конкретний failed contract власнику або окремим PR із посиланням.

### 17.2. Work packages

| WP | Власність | Залежить від | Результат і критерій приймання |
|---|---|---|---|
| WP-00 | Foundation | — | repo layout, `pyproject`, lock, CI, Compose, coding/PR rules; порожній smoke проходить |
| WP-01 | Contracts & DB | WP-00 | Pydantic/JSON schemas, migrations, job lease, outbox, lineage; clean DB integration green |
| WP-02 | Fetch core | WP-01 | HTTP fetcher, robots snapshot, allowlist, limiter, retries, raw S3; SSRF/rate tests green |
| WP-03 | Discovery | WP-01, WP-02 | API/RSS/sitemap streaming, cursors, idempotent jobs; gzip/pagination fixtures green |
| WP-04 | Translation core | WP-01 | segmenter, provider interface, Google adapter, translation memory, glossary, QA corpus; all language pairs green |
| WP-05 | News adapter SDK | WP-02–04 | RSS/sitemap/article extraction base, full-text contract, country/language config |
| WP-06A | News UA/DE/AT | WP-05 | Суспільне, Tagesschau, ORF P0 adapters + translations |
| WP-06B | News FR/BE | WP-05 | France 24, VRT NWS P0 adapters + `fr/nl -> uk` translations |
| WP-06C | News GB/US | WP-05 | BBC, NPR P0 adapters + `en -> uk` translations |
| WP-06D | News Baltics | WP-05 | LRT, LSM, ERR P0 adapters + `lt/lv/et -> uk` translations |
| WP-06E | News PL/HU/RO | WP-05 | Polskie Radio, Telex, HotNews P0 adapters + translations |
| WP-06F | News CZ/SK/SI/HR | WP-05 | iROZHLAS, STVR, RTVSLO, HRT P0 adapters + translations |
| WP-06G | News IT/ES | WP-05 | RaiNews, RTVE P0 adapters + translations |
| WP-07 | Vehicle contracts & matching | WP-01 | vehicle/seller/contact schemas, dictionaries, source matching; golden fixtures |
| WP-08A–D | Vehicle adapters | WP-03, WP-07 | один незалежний пакет на AUTO.RIA, OLX Авто, RST, Automoto; full public field coverage |
| WP-09 | Catalog contracts & matching | WP-01 | product/offer/review/question/contact schemas, category mapping, matching benchmark |
| WP-10A–H | Catalog adapters | WP-03, WP-09 | один незалежний пакет на Prom, Rozetka, Epicentr, Allo, Hotline, Comfy, Foxtrot, MOYO |
| WP-11 | Operator API/export | WP-01, WP-04 | status/pause/replay/read API, original+translation export, Parquet/JSONL manifest; RBAC tests |
| WP-12 | Observability/runbooks | WP-02, WP-04, WP-11 | dashboards, source/translation alerts, SLO queries; injected-failure exercise |
| WP-13 | Security review | WP-02–12 | threat model validation, dependency/container scans, secret checks; findings triaged |
| WP-14 | Integration/release | усі required WP | 7-day pilot, 19-country coverage, traceability matrix, acceptance report; no unresolved critical/high findings |

### 17.3. Issue template для агента

Кожна задача повинна мати: scope/out-of-scope, файли у власності, input contract/version, output contract/version, fixtures, команди перевірки, acceptance criteria, залежності, source coverage і rollback/disable plan. Один adapter subpackage належить одному агенту; спільні SDK/схеми змінюються окремим PR.

## 18. Definition of Done

Задача завершена лише якщо:

- реалізація відповідає одному issue/WP і не містить сторонніх змін;
- formatter, lint, types, unit/contract/integration tests пройшли;
- зміна схеми має migration і compatibility evidence;
- новий адаптер має manifest, fixtures, golden outputs, field coverage report, quality sample і bounded live smoke;
- документація, метрики й runbook оновлені;
- secret scan чистий; публічні контакти присутні тільки в доменних таблицях/raw, а не в fixtures з випадково приватних джерел або технічних logs;
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
| Q-003 | Перші 3 категорії товарів? | смартфони, ноутбуки, шини | Product, до WP-06 |
| Q-004 | Яку глибину історичного backfill робити? | максимально доступна в sitemap/API, але не старше 5 років | Product, до масового backfill |
| Q-005 | Retention raw/history? | безстроково з cold tier | Data owner, до pilot |
| Q-006 | Інфраструктурний бюджет/SLO? | один хост MVP, SLO з §2.4 | Product/DevOps, до WP-00 close |
| Q-007 | Які API keys уже наявні? | відсутні; HTML/RSS first | Product, до відповідного adapter |
| Q-008 | Місячний бюджет Google Cloud Translation? | character budget конфігурується; backfill paused без ліміту | Product, до WP-04 live |
| Q-009 | Перекладати оновлену статтю повністю чи лише змінені сегменти? | лише змінені сегменти, потім збирати повну version | Product, до WP-04 close |

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
│   ├── persistence/
│   ├── api/
│   └── telemetry/
├── schemas/
├── migrations/
├── tests/{unit,contract,integration,e2e,fixtures}/
├── sources/<source_id>/manifest.yaml
├── docs/{adr,runbooks,decisions}/
├── dashboards/
└── .github/{workflows,ISSUE_TEMPLATE}/
```

## Додаток B. Приклад маніфесту джерела

```yaml
schema_version: 1
id: auto_ria_used
name: AUTO.RIA used vehicles
domain: vehicles
owner: data-acquisition
status: enabled
channel: official_api
country_codes: [UA]
source_languages: [uk, ru]
base_urls:
  - https://developers.ria.com/
allowed_url_patterns:
  - '^https://developers\.ria\.com/auto/'
denied_url_patterns: []
schedule: '*/30 * * * *'
rate_limit:
  requests_per_second: 0.2
  concurrency: 1
  daily_quota: null              # fill from actual API plan
policy:
  robots_url: null               # API channel; HTML adapter has its own manifest
  stop_on_status: [401, 403, 429]
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
| Незалежна реалізація | §17, §18 | WP acceptance, CI, contract/version ownership |
