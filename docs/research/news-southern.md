# Живе дослідження новинних джерел: RO, SI, HR, IT, ES

Дата перевірки: **2026-09-22**. Усі перевірки виконані анонімно, без облікового запису. HTTP-перевірки робилися з браузерним `User-Agent`, переходом за редиректами та тайм-аутом 20–30 секунд; окремо перевірено відображення через браузерний індексатор, коли CDN блокував командний клієнт.

Цей документ описує фактично перевірений стан. Статус `операційно неперевірено` означає, що конкретний канал не вдалося відтворити з поточного середовища; це не твердження про постійну недоступність сайту.

## Спільний контракт адаптера

Для першої версії адаптер приймає лише матеріали, повний текст яких доступний без реєстрації. Сторінка з анонімно доступним заголовком або вступом, але закритим основним текстом, залишається discovery-записом зі статусом `content_access=metadata_only` і не надходить до перекладу.

Рекомендований стек для всіх джерел:

- `Scrapy` + `httpx` для планування, HTTP, retry/backoff, ETag/Last-Modified і метрик;
- `feedparser` для RSS/Atom, `lxml` для sitemap та HTML;
- JSON-LD `NewsArticle` як перше джерело метаданих, OpenGraph як резерв, CSS/XPath як джерело текстових блоків;
- `trafilatura` лише як контрольний резервний екстрактор, а не як єдиний parser;
- Playwright лише для джерел, де серверна відповідь не містить потрібного публічного тексту; поточні успішно перевірені сторінки здебільшого server-rendered;
- сирий response зберігати перед нормалізацією: `fetched_url`, `final_url`, `status`, redirect chain, headers, body bytes, content hash, fetch time;
- source-specific fixture-тести на наведених нижче category/article URL та окремий canary на feed/sitemap.

Мінімальна нормалізована схема новини:

```text
source_id, source_item_id, country_code, language,
discovered_via, discovery_url, requested_url, final_url, canonical_url,
title_original, dek_original, body_original_html, body_original_text,
author_names[], section, tags[], published_at, modified_at,
images[{url, alt, caption, credit, width, height}],
videos[{url, embed_url, thumbnail_url, duration}], related_urls[],
is_accessible_for_free, content_access, fetched_at, http_status,
etag, last_modified, content_hash, parser_version
```

Додатково зберігати український переклад у версійованій дочірній сутності, не замінюючи оригінал: `translation_language=uk`, перекладені title/dek/body, модель, версія prompt, дата, статус QA та посилання на `content_hash` оригіналу.

## Зведений технічний рейтинг

Рейтинг не оцінює редакційну якість. Формула: coverage 25, structured access 25, anonymous accessibility 20, data richness 15, stability/observability 15.

| Країна | Джерело | Coverage | Structured | Anonymous | Richness | Stability | Разом |
|---|---|---:|---:|---:|---:|---:|---:|
| RO | HotNews | 24 | 25 | 20 | 13 | 13 | **95** |
| RO | Digi24 | 23 | 25 | 20 | 13 | 13 | **94** |
| RO | Agerpres | 22 | 13 | 12 | 14 | 6 | **67** |
| SI | RTV Slovenija | 23 | 22 | 20 | 14 | 12 | **91** |
| SI | STA | 20 | 20 | 6 | 8 | 5 | **59** |
| SI | 24UR | 23 | 22 | 20 | 14 | 12 | **91** |
| HR | HRT Vijesti | 23 | 21 | 20 | 14 | 13 | **91** |
| HR | Index.hr | 23 | 22 | 12 | 14 | 9 | **80** |
| HR | Jutarnji | 23 | 23 | 18 | 13 | 10 | **87** |
| IT | RaiNews | 23 | 23 | 14 | 14 | 13 | **87** |
| IT | ANSA | 23 | 24 | 18 | 14 | 13 | **92** |
| IT | la Repubblica | 23 | 24 | 9 | 13 | 8 | **77** |
| ES | RTVE Noticias | 24 | 23 | 20 | 14 | 13 | **94** |
| ES | El País | 23 | 24 | 15 | 14 | 10 | **86** |
| ES | La Vanguardia | 23 | 22 | 18 | 14 | 11 | **88** |

## Румунія (`RO`, `ro`)

### HotNews — 95/100

Перевірені URL і відповіді:

- homepage: `https://hotnews.ro/` — 200, без редиректу;
- robots: `https://hotnews.ro/robots.txt` — 200; у файлі оголошено `sitemap-news.xml` та `sitemap.xml`;
- sitemap index: `https://hotnews.ro/sitemap.xml` — 200, 8 242 дочірні добові sitemap, від 2026-09-21 до 2000-01-01;
- news sitemap: `https://hotnews.ro/sitemap-news.xml` — 200;
- RSS: `https://hotnews.ro/feed` — 200 `application/rss+xml`; старий домен `rss.hotnews.ro` не резолвився;
- category: `https://hotnews.ro/politic` — 200 і редирект на `https://hotnews.ro/c/actualitate/politic`;
- article: `https://hotnews.ro/de-ce-crede-basescu-ca-relatiile-cu-ue-nu-vor-fi-afectate-daca-siegfried-muresan-ar-face-o-mica-intelegere-cu-aur-2355044` — 200.

**URL і backfill.** Матеріал має шаблон `/{slug}-{numeric_id}`; категорії — `/c/{group}/{section}`. Добові sitemap є надійним повним backfill: `/sitemap.xml?yyyy=YYYY&mm=MM&dd=DD`. RSS і news sitemap використовувати для низької затримки, а добові sitemap — для звірки повноти. Пагінація category не потрібна для backfill.

**Структура і поля.** Article HTML містить canonical, OpenGraph і один JSON-LD `NewsArticle`: headline, description, author, publisher, section, image, language, published/modified. Це WordPress: `https://hotnews.ro/wp-json/wp/v2/posts/{numeric_id}` повернув 200 та поля `id`, `date`, `modified`, `slug`, `link`, `title`, `content.rendered`, `excerpt`, `author`, `featured_media`, `categories`, `tags`, `autori`, `sponsor`, `acf`, `parsely`; `wp-json/wp/v2/categories?per_page=100` також повернув 200. REST є рекомендованим parser source, HTML — джерелом canonical і контролю видимого тексту.

**Доступ і реалізація.** Текст є у server response, JS не потрібен. Слово `premium` у рекламній конфігурації не є ознакою paywall для перевіреного матеріалу; рішення про доступність приймати за наявністю повного body, а не keyword. Discovery: RSS + news sitemap; backfill: добові sitemap; fetch: REST + HTML; parser: REST fields з JSON-LD/OG reconciliation. Оцінка пояснюється практично повним архівом і стабільним REST, мінус невеликий бал за кілька паралельних представлень одного поля.

### Digi24 — 94/100

Перевірені URL і відповіді:

- homepage `https://www.digi24.ro/` — 200;
- robots `https://www.digi24.ro/robots.txt` — 200;
- RSS `https://www.digi24.ro/rss` — 200; також `rss_files/google_news.xml` — 200;
- sitemap index `https://www.digi24.ro/sitemaps/sitemap-index.xml` — 200, 172 місячні sitemap від `sitemap-articles-2026-09.xml` до `sitemap-articles-2000-10.xml`;
- section sitemap `https://www.digi24.ro/sitemaps/sitemap-section.xml` — 200;
- category `https://www.digi24.ro/stiri/actualitate` — 200;
- article `https://www.digi24.ro/stiri/externe/sua/prabusire-in-sondaje-pentru-donald-trump-popularitatea-presedintelui-american-a-atins-cel-mai-scazut-nivel-din-cariera-sa-politica-3958197` — 200.

**URL і backfill.** Article: `/stiri/{section}/{optional-subsection}/{slug}-{id}`; category: `/stiri/{section}`. RSS дає recent stream, Google News XML — коротке новинне вікно, місячні sitemap — детермінований історичний backfill. Не покладатися на невидиму пагінацію category.

**Структура і поля.** На article є canonical, два JSON-LD блоки та OG; `NewsArticle` містить headline, description, image, author, publisher, `datePublished`, `dateModified`. HTML додає section/subsection, body paragraphs, фото/підписи, video embeds і related links. RSS містить title, link, description, publication time та media. Parser: JSON-LD для метаданих, source-specific CSS для body/media.

**Доступ і реалізація.** Homepage, category, RSS, sitemap та перевірена стаття доступні без реєстрації й без JS. Discovery: RSS; completeness: Google News + current monthly sitemap; backfill: monthly sitemap. Високий рейтинг — машинні канали та довгий архів; невелике зниження за відсутність одного документованого content API.

### Agerpres — 67/100

Перевірені URL і відповіді:

- `robots.txt` — 200, `Crawl-delay: 1`, пошукові маршрути закриті;
- homepage/category/article у браузерному індексаторі — 200: `https://agerpres.ro/`, `https://agerpres.ro/national`, `https://agerpres.ro/politic-extern/2026/09/22/video-sefa-diplomatiei-europene-indeamna-la-mentinerea-politicii-de-sanctiuni-impotriva-rusiei--1595677`;
- ті самі HTML URL у `curl`/PowerShell отримували Cloudflare 403;
- `/sitemap.xml` не є sitemap: редиректить на third-party `api.allorigins.win/...createfeed.bazqux.com...` і завершився 408;
- `/rss` та `/feed` редиректили на той самий неофіційний third-party feed; їх не використовувати як довірений канал;
- пошук `https://agerpres.ro/index.php/search` анонімно відкрився через браузерний індексатор, але закритий у robots і отримав 403 у HTTP-клієнті.

**URL і backfill.** Article: `/{section}/YYYY/MM/DD/{slug}--{id}`; list: `/national`, `/international`, `/english`, `/magyar`, `/statements`. На category перевірена нумерована пагінація (посилання 1, 2, 3, 4); точний query-параметр треба захопити браузерною мережею під час розробки, бо CLI був заблокований. Повного офіційного sitemap/RSS не підтверджено, тому історичний backfill операційно неперевірений і має йти через дозволені category/date сторінки з checkpoint по `(published_at,id)`.

**Структура і поля.** Анонімна article page показала title, section, exact timestamp, повний body, image/video embed, редактора/автора в підписі, view count і related category items. На homepage/list є title, excerpt, section і час. Є Romanian, English та Hungarian sections. У командному response structured data перевірити не вдалося через 403; schema/OG для адаптера — операційно неперевірено.

**Доступ і реалізація.** Реєстрація для прочитаного матеріалу не потрібна, але Cloudflare робить звичайний HTTP fetch нестабільним. До позитивного bounded canary у звичайному анонімному браузері route має стан `circuit_open`, а джерело — `blocked_anonymous` або `metadata_only` за іншими перевіреними routes. Low-rate Playwright дозволяється лише якщо canary стабільно отримує саме публічну сторінку без CAPTCHA/challenge/login; session tricks, fingerprint spoofing і сторонній feed не використовувати. Бали знижено саме за відсутність підтвердженого first-party discovery та повторювані 403.

## Словенія (`SI`, `sl`)

### RTV Slovenija — 91/100

Перевірені URL і відповіді:

- homepage `https://www.rtvslo.si/` — 200;
- robots — 200; для загального agent дозволені сторінки, але пошук/account закриті, окремі AI user-agents явно блокуються;
- `/rss` — 200 і редирект на сторінку списку RSS `https://www.rtvslo.si/rtv/seznam-rss-kanalov/523415`;
- загальний feed `https://www.rtvslo.si/feeds/00.xml` — 200 і редирект на `https://img.rtvslo.si/feeds/00.xml`;
- section feed `https://www.rtvslo.si/slovenija/rss` — 200 і редирект на `https://img.rtvslo.si/_up/export/rss/all/1.xml`;
- `/sitemap.xml` і `/sitemap_index.xml` — 410;
- category `https://www.rtvslo.si/slovenija` — 200;
- article `https://www.rtvslo.si/slovenija/zupancic-skok-je-tektonski-premik-pri-boju-proti-korupciji/794440` — 200.

**URL і backfill.** Article: `/{section}/{optional-topic}/{slug}/{id}`; category: `/{section}`. RSS є головним discovery. Sitemap відсутній; category page не показала статичного `rel=next`. Для backfill потрібен окремий adapter до фактичного load-more endpoint, який треба зафіксувати через browser network trace; до цього старіший backfill — операційно неперевірений. Не використовувати site search, бо він закритий у robots.

**Структура і поля.** Article HTML має canonical, OG і JSON-LD `NewsArticle`: headline, image, author abbreviation, publisher, published/modified. Server HTML містить body, lead, section/topic, photo/video blocks, captions, related items. Feed містить title, URL, description, time та media.

**Доступ і реалізація.** Homepage/category/article/feed анонімні, article text server-rendered, Playwright не потрібен для поточного матеріалу. Discovery: per-section RSS; fetch: HTTP HTML; parser: JSON-LD + CSS. Coverage високе, але немає sitemap/підтвердженого архівного endpoint, тому зняті бали за backfill та observability.

### STA — 59/100

Перевірені URL і відповіді:

- homepage через `curl` — 200, через PowerShell — Cloudflare 403; браузерний індексатор повернув internal error;
- robots — 200; для `User-agent: *` оголошено `Sitemap: https://www.sta.si/websitemap`, закриті account/query/image-transform paths; багато named bots заблоковані;
- sitemap index `https://www.sta.si/websitemap` — 200, 424 entries;
- current sitemap `https://www.sta.si/websitemap-current` — 200, містить актуальні article URL;
- `/rss` — 403, `/feed` — 404;
- list page — homepage, а також sitemap current;
- sample article `https://www.sta.si/3601345/v-novem-mestu-zacetek-nove-kosarkarske-sezone-lige-otp-banka` — Cloudflare 403 у HTTP-клієнті; анонімний повний article у браузері не підтверджено.

**URL і backfill.** Article: `/{numeric_id}/{slug}`. Sitemap має current і місячні/історичні частини; він є придатним discovery/backfill навіть коли HTML блокується. Пагінацію homepage не використовувати.

**Структура і поля.** Із sitemap надійно доступні canonical URL та modification timestamps. Повний набір article fields, JSON-LD/OG, видимий body і межа subscriber-only content для sample page — операційно неперевірені. STA має subscription/account навігацію, тому сам факт URL у sitemap не означає анонімний повний текст.

**Доступ і реалізація.** У першій версії STA використовувати лише як discovery adapter. Fetcher повинен класифікувати 403 і `content_access=metadata_only`; не запускати переклад без підтвердженого повного body. Окремий browser canary може перевіряти анонімну доступність, але не входити автоматично в production. Низька оцінка спричинена непідтвердженим full-text access, а не відсутністю покриття sitemap.

### 24UR — 91/100

Перевірені URL і відповіді:

- homepage `https://www.24ur.com/` — 200;
- robots — 200, оголошено `https://www.24ur.com/sitemaps/sites/1`;
- sitemap endpoint у поточному CLI-виклику повернув 403, тому його вміст операційно неперевірений;
- RSS `https://www.24ur.com/rss` — 200 `application/xml`;
- category `https://www.24ur.com/novice/` — 200;
- article `https://www.24ur.com/novice/slovenija/poljsko-ministrstvo-o-nesreci-v-ljubljani-usluzbenec-ni-padel-skozi-okno.html` — 200.

**URL і backfill.** Article: `/{vertical}/{section}/{slug}.html`; category: `/{vertical}/`. RSS — primary discovery. Category не містила статичного next link; sitemap треба повторно перевірити з production egress. До підтвердження sitemap гарантований backfill обмежений retention RSS і category/load-more, endpoint якого має бути знайдений через network trace.

**Структура і поля.** Article HTML містить кілька JSON-LD блоків; `NewsArticle.articleBody` був присутній, разом із автором, headline, description, dates, section, images. OG доступний. Server HTML містить body, photo/video, captions, related content і author URL. RSS дає recent items.

**Доступ і реалізація.** Перевірений article повністю доступний без реєстрації та JS. Рядок `premium` у sample стосувався назви рекламного матеріалу, тому keyword-based paywall detector заборонений. Discovery: RSS, completeness: sitemap після canary, fetch: HTTP, parser: JSON-LD articleBody + CSS blocks. Високий рейтинг з невеликим штрафом за 403 sitemap і непідтверджений глибокий backfill.

## Хорватія (`HR`, `hr`)

### HRT Vijesti — 91/100

Перевірені URL і відповіді:

- homepage `https://vijesti.hrt.hr/` — 200;
- robots — 200, оголошено `https://vijesti.hrt.hr/sitemaps/sitemap-index.xml`;
- sitemap index — 200, 14 children: current news та archive shards;
- current news sitemap `https://vijesti.hrt.hr/sitemaps/articles-news.xml` — 200;
- `/rss` і `/rss.xml` — 404;
- category `https://vijesti.hrt.hr/svijet` — 200;
- article `https://vijesti.hrt.hr/svijet/letovi-prizemljeni-na-americkoj-istocnoj-obali-zbog-tehnickog-kvara-12918856` — 200.

**URL і backfill.** Article: `/{section}/{slug}-{id}`; category: `/{section}`. Sitemap index є primary discovery і backfill; current shard опитувати часто, archive shards — одноразово та для repair. Пагінація category не потрібна для повноти.

**Структура і поля.** Next.js сторінка server-rendered. Є canonical, OG і JSON-LD `NewsArticle` з headline, description, image, dates, keywords, publisher; HTML/Next data містять body, section, video/image objects та related links. Author у sample JSON-LD був відсутній — parser має дозволяти null.

**Доступ і реалізація.** Реєстрація і JS для тексту не потрібні. Discovery/backfill: sitemap; fetch: HTTP; parser: JSON-LD + DOM/embedded Next data. Стабільна структура і чіткий sitemap дають високий бал; знижено за відсутність RSS і автора в sample metadata.

### Index.hr — 80/100

Перевірені URL і відповіді:

- прямі `curl`/PowerShell запити до homepage, robots, RSS, category та article отримували Cloudflare 403;
- браузерний індексатор анонімно відкрив homepage/category/article з 200-подібним повним документом;
- `https://www.index.hr/robots.txt` через браузер: `User-Agent: *`, порожній `Disallow`;
- RSS directory `https://www.index.hr/rss/info` відкрився анонімно і перелічив feeds `rss`, `rss/vijesti`, `rss/vijesti-hrvatska`, `rss/vijesti-svijet`, `rss/vijesti-eu` та інші;
- category `https://www.index.hr/vijesti` — анонімний повний list;
- article `https://www.index.hr/vijesti/clanak/horvatincic-vozi-zagrebom-slikali-smo-ga/2837127.aspx` — анонімно показав повний текст.

**URL і backfill.** Article: `/{vertical}/clanak/{slug}/{id}.aspx`; category: `/{vertical}`. RSS — найкращий discovery. Офіційний sitemap не виявлено. Category має `Najnovije`/`Prikaži još vijesti`, але exact load-more endpoint через CLI не перевірено; історичний backfill до network-trace цього endpoint є операційно неперевіреним.

**Структура і поля.** У browser article видимі title, author/desk, published/updated time, body with subheads, photo URL/credit, tags, related items та comments count. JSON-LD/OG у сирому article response не перевірено через 403. RSS directory показує гранулярні feeds за вертикалями і підрубриками.

**Доступ і реалізація.** Контент читається без реєстрації, однак звичайний fetcher блокувався. Рекомендовано RSS discovery + browser-backed fetch pool із circuit breaker; parser по DOM, а JSON-LD вважати optional до production canary. Не плутати пропозицію підписки «без реклами» з paywall. Оцінку знижено за Cloudflare і непідтверджений backfill.

### Jutarnji — 87/100

Перевірені URL і відповіді:

- homepage `https://www.jutarnji.hr/` — 200;
- robots — 200, оголошено gzip sitemap index `https://www.jutarnji.hr/sitemapindex-jutarnji.xml.gz`;
- gzip index — 200 `application/octet-stream`, після decompression 82 monthly children, включно з `sitemap-jutarnji-2026-09.xml.gz`;
- RSS `https://www.jutarnji.hr/feed` — 200;
- category `https://www.jutarnji.hr/vijesti` — 200;
- article `https://www.jutarnji.hr/vijesti/svijet/sad-vraca-baze-na-grenlandu-i-siri-prisutnost-na-istoku-15748653` — 200.

**URL і backfill.** Article: `/{vertical}/{section}/{slug}-{id}`; category: `/{vertical}`. RSS — live discovery; monthly gzip sitemap — complete backfill/checkpoint. Gzip треба декомпресувати як файл, бо сервер віддає не `Content-Encoding: gzip`, а готовий `.gz` payload.

**Структура і поля.** Article має canonical, OG і JSON-LD `NewsArticle`: headline, description, `articleBody`, section, image, dates, keywords, author, publisher. Sample мав `structData-isPremium` порожній і повний короткий wire-style body; HTML також містить photo/credit та related links.

**Доступ і реалізація.** Сайт має free/paid infrastructure, тому на кожному item перевіряти `structData-isPremium`, CSS `itemFullText--premium`, видимий body length і paywall marker. Закриті матеріали залишати metadata-only; sample був анонімно доступний. JS не потрібен для sample. Високі structured/backfill бали, але знижена anonymous/stability оцінка через змішаний режим доступу.

## Італія (`IT`, `it`)

### RaiNews — 87/100

Перевірені URL і відповіді:

- homepage `https://www.rainews.it/` — 200;
- robots — 200; оголошені `sitemap.xml` і fast sitemap, окремі JSON/app/live paths закриті;
- RSS directory `https://www.rainews.it/rss` — 200 HTML; feed `https://www.rainews.it/rss/tutti` — 200 XML;
- sitemap index `https://www.rainews.it/sitemap.xml` — 200, 3 014 children для homepages/notiziari/media та інших shards;
- fast index `https://www.rainews.it/dl/rainews/sitemap/fast/sitemap.fast.xml` — 200;
- category `https://www.rainews.it/politica` — 200 і редирект на `https://www.rainews.it/archivio/politica`;
- article `https://www.rainews.it/articoli/2026/09/vandalizzate-a-roma-le-pietre-dinciampo-per-spizzichino-e-spagnoletto-f03e4c7b-8ed0-42e8-8acf-359199ed6a67.html` — 200.

**URL і backfill.** Article/video: `/{articoli|video}/YYYY/MM/{slug}-{uuid}.html`; category canonical: `/archivio/{section}`. RSS/fast sitemap — live discovery, sitemap tree — backfill. Category archive є резервом, але sitemap має бути primary.

**Структура і поля.** HTML має canonical, OG, JSON-LD/embedded article data з headline, description, source, body, image/video, dates, section/topic, UUID. В embedded data sample були `premium:true` і `login_required:true`, водночас article body фактично був у анонімній server response. Це provider-specific flags, а не достатня самостійна ознака закритого тексту.

**Доступ і реалізація.** У першій версії приймати лише item, для якого body повністю присутній у анонімному response і проходить completeness heuristic; не виконувати login і не обходити UI. JS для sample body не потрібен. Parser: embedded JSON/JSON-LD + DOM; discovery: RSS/fast sitemap; backfill: sitemap. Бали знижено через неоднозначну семантику access flags.

### ANSA — 92/100

Перевірені URL і відповіді:

- homepage `https://www.ansa.it/` — 200;
- robots — 200, sitemap index оголошено;
- RSS directory `https://www.ansa.it/sito/static/ansa_rss.html` — 200;
- general RSS `https://www.ansa.it/sito/ansait_rss.xml` — 200;
- sitemap index `https://www.ansa.it/sito/sitemaps/sito_sitemap_index.xml` — 200, 63 section shards;
- category `https://www.ansa.it/sito/notizie/politica/politica.shtml` — 200;
- article `https://www.ansa.it/sito/notizie/cultura/cinema/2026/09/21/dalle-ballerine-ai-diamanti-allasta-gli-oggetti-personali-di-brigitte-bardot_b28af765-ba10-4946-b968-6e1786c0dc09.html` — 200.

**URL і backfill.** Article: `/sito/notizie/{section}/{subsection}/YYYY/MM/DD/{slug}_{uuid}.html`; category ends with `.shtml`. RSS per section — discovery, section sitemap shards — backfill/completeness.

**Структура і поля.** Article має canonical, багатий OG та JSON-LD; доступні headline, lead/body, author/source, published/modified, section/subsection, keywords, image/gallery/video, caption/credit і related links. General RSS містить title/link/description/date/media.

**Доступ і реалізація.** Sample HTML і article content отримані без реєстрації. Є cookie/consent та subscription UI; fetcher не повинен натискати login, а має визначати наявність повного body у response. JS для extraction не потрібен. Рекомендовано RSS + sitemap, HTTP fetch, JSON-LD/DOM parser. Невеликий штраф — consent variants і розгалужена sitemap taxonomy.

### la Repubblica — 77/100

Перевірені URL і відповіді:

- homepage `https://www.repubblica.it/` — 200;
- robots — 200, оголошені news/general та vertical sitemap;
- RSS directory `https://www.repubblica.it/static/servizi/rss/index.html` — 200;
- homepage RSS `https://www.repubblica.it/rss/homepage/rss2.0.xml` — 200;
- news sitemap `https://www.repubblica.it/sitemap-n.xml` — 200;
- archive index `https://www.repubblica.it/sitemap.xml` — 200, 51 monthly gzip children від 2026-09 до 2022-07;
- category `https://www.repubblica.it/politica/` — 200;
- article `https://www.repubblica.it/politica/2026/09/22/news/scuola_pronto_decreto_tetto_alunni_stranieri_duemila_classi-425599250/` — 200, але JSON-LD позначив `isAccessibleForFree=false`.

**URL і backfill.** Article: `/{section}/YYYY/MM/DD/news/{slug}-{id}/`; category `/{section}/`. RSS/news sitemap — live discovery; monthly gzip sitemap — backfill. Category pagination не потрібна для повноти.

**Структура і поля.** Article має canonical, OG та JSON-LD `NewsArticle`: headline, dates, author, publisher, image, section, `isAccessibleForFree`, articleBody. Для sample `articleBody` у JSON-LD був лише близько 400 символів, тобто metadata/teaser, не повний текст.

**Доступ і реалізація.** У першій версії фільтр жорсткий: `isAccessibleForFree=false` або teaser-only body => `metadata_only`, без перекладу body. Для free articles вимагати `isAccessibleForFree=true` і видимий завершений article body. Discovery і metadata можна збирати HTTP без JS; Playwright не використовувати для спроби відкрити paid text. Structured/backfill дуже сильні, anonymous full-text coverage — змішаний, що пояснює рейтинг.

## Іспанія (`ES`, `es`)

### RTVE Noticias — 94/100

Перевірені URL і відповіді:

- homepage `https://www.rtve.es/noticias/` — 200;
- `https://www.rtve.es/robots.txt` — 200 після одного редиректу на `/akamai/robots.txt`;
- robots оголошує багато sitemap, зокрема `https://www.rtve.es/sitemaps/sitemaps-news.xml` — 200;
- загальний `https://www.rtve.es/sitemaps/sitemaps.xml` у перевірці повернув 404, тому використовувати конкретний news sitemap;
- `https://www.rtve.es/api/noticias.rss` повернув 200, але лише 2 bytes — невалідний feed; `/noticias/rss/` — 404;
- category `https://www.rtve.es/noticias/espana/` — 200;
- article `https://www.rtve.es/noticias/20260921/espana-activa-mecanismos-preventivos-para-evitar-entradas-irregulares-a-ceuta-dia-23/17233304.shtml` — 200.

**URL і backfill.** Article: `/noticias/YYYYMMDD/{slug}/{id}.shtml`; category: `/noticias/{section}/`. News sitemap містить article URL і є primary discovery/backfill; він покриває актуальне вікно, тому для глибокої історії треба пройти дочірні/section sitemap з robots і зберігати checkpoints. Порожній RSS endpoint не використовувати.

**Структура і поля.** Article має canonical, багатий OG і два JSON-LD блоки. `NewsArticle` містить URL, headline, description, published/modified, image, author, video, publisher, section, language, `isAccessibleForFree`. HTML містить server-rendered body, image captions/credits, video/audio і related links.

**Доступ і реалізація.** Homepage, category, sitemap і article доступні без реєстрації; JS не потрібен для body. Discovery/backfill: news/section sitemap; fetch: HTTP; parser: JSON-LD + DOM. Оцінка висока, з невеликим штрафом за зламаний RSS і 404 загального index при робочих specific sitemap.

### El País — 86/100

Перевірені URL і відповіді:

- homepage `https://elpais.com/` — 200;
- robots — 200; багато named AI/bot user-agents заблоковані;
- RSS directory `https://elpais.com/info/rss/` — 200;
- homepage feed `https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada` — 200 і оголошений у homepage `<link rel=alternate>`;
- `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap_news.xml` у перевірці — 404;
- category `https://elpais.com/espana/` — 200;
- article `https://elpais.com/espana/2026-09-21/el-juez-peinado-abre-juicio-oral-a-begona-gomez-por-delitos-de-trafico-de-influencias-y-malversacion.html` — 200 та `isAccessibleForFree=true`.

**URL і backfill.** Article: `/{section}/YYYY-MM-DD/{slug}.html`; category: `/{section}/`. MRSS feeds — primary discovery, містять багато media/metadata. Офіційний sitemap у перевірених стандартних locations не знайдено; глибокий backfill через section archive/load-more потребує network trace і до цього є операційно неперевіреним.

**Структура і поля.** Sample має canonical, багатий OG і JSON-LD типів `NewsArticle`, `Article`, `ReportageNewsArticle`: headline, description, full `articleBody` (приблизно 7,5 тис. символів), author, keywords, section, dates, language, content location, images/media, publisher, `isAccessibleForFree=true`. MRSS дає title/link/description/date/media/category.

**Доступ і реалізація.** Сайт змішує free та subscriber items. Збирати тільки `isAccessibleForFree=true` і додатково перевіряти повноту body; false/відсутній body — metadata-only. Sample був повністю доступний без реєстрації і JS. Discovery: MRSS; fetch: HTTP; parser: JSON-LD. Бали знижено за змішаний access і непідтверджений повний backfill.

### La Vanguardia — 88/100

Перевірені URL і відповіді:

- homepage `https://www.lavanguardia.com/` — 200;
- robots — 200; стандартний `/sitemap.xml` — 404;
- RSS directory `https://www.lavanguardia.com/rss` — 200;
- homepage RSS `https://www.lavanguardia.com/rss/home.xml` — 200 і оголошений у homepage;
- `https://www.lavanguardia.com/sitemap-news.xml` — 200 після редиректу на `https://www.lavanguardia.com/sitemap-google-news.xml`;
- category `https://www.lavanguardia.com/politica` — 200, має `rel=next` на `/politica/2`;
- article `https://www.lavanguardia.com/politica/20260921/11640190/peinado-envia-juicio-begona-gomez-malversacion-trafico-influencias.html` — 200.

**URL і backfill.** Article: `/{section}/YYYYMMDD/{id}/{slug}.html`; category pagination: `/{section}/{page}`. RSS/Google News sitemap — live discovery; category pagination — backfill fallback. Повний historical sitemap не підтверджено, тому backfill слід обмежувати датою/максимальною сторінкою з checkpoint і перевіркою дублікатів.

**Структура і поля.** Article має OG і два JSON-LD блоки. `NewsArticle` містить headline/alternativeHeadline, description, full body (приблизно 2 тис. символів у sample), author, dates, section, keywords, images/video, publisher/source, language, `isAccessibleForFree=true`, citation. Canonical link у використаному regex не знайшовся, тому canonical брати з JSON-LD `mainEntityOfPage` або нормалізованого final URL і покрити fixture-тестом.

**Доступ і реалізація.** Sample доступний без реєстрації і JS. Оскільки сайт має subscription UI, приймати лише items з `isAccessibleForFree=true` та повним body. Discovery: RSS/news sitemap; backfill: category `/2`, `/3`, ...; fetch: HTTP; parser: JSON-LD. Високий бал за rich schema і статичну пагінацію, зниження за відсутність підтвердженого historical sitemap.

## Рекомендована послідовність незалежної реалізації

Кожен адаптер — окремий пакет робіт із власними fixtures, без спільного редагування source-specific selectors:

1. Реалізувати discovery та checkpoint для одного домену.
2. Реалізувати fetch і класифікацію `full`, `metadata_only`, `blocked`, `gone`, `retryable`.
3. Реалізувати parser і mapping у спільну схему; жодне поле не домислювати.
4. Додати fixture для homepage, robots, feed/sitemap, category і article з цього документа.
5. Додати canary-метрики: HTTP status, redirect target, items discovered, full-body ratio, schema-field presence, duplicates, parse failures, oldest/newest timestamp.
6. Провести replay на збережених responses та live smoke без реєстрації.

Паралельні групи без конфліктів: `RO-hotnews`, `RO-digi24`, `RO-agerpres`, `SI-rtvslo`, `SI-sta`, `SI-24ur`, `HR-hrt`, `HR-index`, `HR-jutarnji`, `IT-rainews`, `IT-ansa`, `IT-repubblica`, `ES-rtve`, `ES-elpais`, `ES-lavanguardia`. Спільний контракт і enum status фіксуються до старту source agents.

## Перевірені домени

`hotnews.ro`, `www.digi24.ro`, `agerpres.ro`, `www.rtvslo.si`, `img.rtvslo.si`, `www.sta.si`, `www.24ur.com`, `vijesti.hrt.hr`, `www.index.hr`, `www.jutarnji.hr`, `www.rainews.it`, `www.ansa.it`, `www.repubblica.it`, `www.rtve.es`, `elpais.com`, `feeds.elpais.com`, `www.lavanguardia.com`.
