# Живе дослідження новинних джерел: DE, FR, GB, US, BE, AT

## Паспорт перевірки

- Дата перевірки: **2026-09-22 (Europe/Kyiv)**.
- Обсяг: усі 18 новинних джерел, указаних у `TECHNICAL_SPECIFICATION.md` для Німеччини, Франції, Великої Британії, США, Бельгії та Австрії.
- Метод: анонімні HTTP GET із редиректами, стабільним desktop User-Agent і без cookies/облікового запису; окремо запитано homepage, `robots.txt`, sitemap/index і дочірній sitemap де він був доступний, RSS/Atom-кандидат, рубрику та матеріал. Актуальні sample URL бралися з видимих посилань RSS/sitemap/homepage; для повністю блокованого AP News використано пошукову видачу лише як спосіб знайти URL, а не як доказ доступності чи полів сторінки.
- `operationally unverified` означає, що endpoint фактично запитано, але CDN/challenge/consent/paywall не дозволив перевірити цільовий HTML або повний текст. Це не твердження, що сайт недоступний з усіх мереж.
- Оцінка: `coverage /25 + structured access /25 + anonymous accessibility /20 + data richness /15 + stability and observability /15 = total /100`.

Позначення в таблиці: `H` homepage, `R` robots, `S` sitemap, `F` feed, `C` category/list, `A` article; `200→URL` — фактичний редирект; `200 challenge` або `200 consent` — HTTP успішний, але не корисний article HTML.

## Порівняльна матриця

| Країна | Джерело | H/R/S/F/C/A під час перевірки | Рейтинг |
|---|---|---|---:|
| DE | Tagesschau | `200/200/404/200/200/200` | 86 |
| DE | Deutsche Welle | `200→/de/themen/s-9077/200/200/200/200/200` | 94 |
| DE | ZEIT | `403/200/403/200/403/403` | 52 |
| FR | France 24 | `403/200/403/200/403/403` | 56 |
| FR | RFI | `403/200/403/200/403/403` | 56 |
| FR | Le Monde | `200/200/200/200/200 challenge/200 challenge або 402` | 76 |
| GB | BBC News | `200/200/200/200/200/200` | 98 |
| GB | The Guardian | `200/200/200/200/200/200` | 97 |
| GB | Sky News | `403/200/200/200/403/403` | 63 |
| US | NPR | `200/200/200/200/200/200` | 100 |
| US | AP News | `403/200/403/403/403/403` | 34 |
| US | The New York Times | `403/200/200/200/403/403` | 68 |
| BE | VRT NWS | `200/200/200/200/200/200→canonical` | 100 |
| BE | RTBF Info | `200→/info/200/200/200/200→/dossier/monde/200` | 98 |
| BE | The Brussels Times | `200/200/200/200 HTML/200/200` | 88 |
| AT | ORF News | `200/200/404/200/200/200` | 76 |
| AT | Der Standard | `200 consent/200/200/200/200 consent/200 consent` | 71 |
| AT | Die Presse | `200/200/200/200/200/200` | 91 |

## Спільний контракт адаптера

Кожен адаптер має повертати незмінні `source_id`, `source_item_id`, `canonical_url`, requested/final URL, redirect chain, HTTP status/content type/fetched time/ETag/Last-Modified, країну й мову; `headline`, `subheadline`, `summary`, рубрику, теги; `published_at`, `modified_at`; авторів та їхні профілі; оригінальні `body_html`, `body_text` і порядок контентних блоків; зображення/відео/аудіо з URL, caption, credit, dimensions/duration; related links; ознаки liveblog та записи liveblog; ознаки доступності повного тексту. Оригінал і український переклад зберігати окремими версіями.

Порядок каналів для всіх джерел: feed/news sitemap для швидкого discovery, archive sitemap для backfill, HTML статті для повного тексту. Якщо стаття без реєстрації повертає challenge, consent-only shell або paywall без body, зберігати лише доступні feed/sitemap метадані зі статусом `body_unavailable`; не вигадувати body і не вважати цей запис повністю зібраним.

## Німеччина

### Tagesschau — 86/100 (`23 + 18 + 20 + 14 + 11`)

- Перевірені URL: homepage `https://www.tagesschau.de/`; robots `https://www.tagesschau.de/robots.txt`; RSS `https://www.tagesschau.de/index~rss2.xml`; рубрика `https://www.tagesschau.de/inland`; матеріал `https://www.tagesschau.de/inland/innenpolitik/landtagswahlen-mv-berlin-cdu-folgen-100.html`. `https://www.tagesschau.de/sitemap.xml`, `/sitemap-news.xml` і `/sitemaps/sitemap-index.xml` повернули 404.
- Discovery/backfill: RSS 2.0 дає `title`, `link`, `description`, `pubDate`, `dc:date`, `guid`, `content:encoded`. URL статті: `/{section}/{subsection}/{slug}-{numeric-suffix}.html`. Рубрика є server-rendered list; надійний історичний sitemap/pagination не підтверджено, тому глибокий backfill — `operationally unverified`.
- HTML/дані: article 200, близько 658 KB, без реєстрації; JSON-LD, OpenGraph, headline, author, published/modified time та `<article>`/body присутні. Доступні також lead, рубрика, image/video metadata, caption/credit і related links. JS для базового тексту не потрібен.
- Реалізація: `RSSPoller -> canonicalize queryless URL -> HTTPFetcher -> JsonLd/OpenGraph + site selectors`; RSS використати як швидкий cursor за `guid/pubDate`, рубрики — для reconciliation. Оцінка знижена через відсутність перевіреного sitemap/backfill.

### Deutsche Welle — 94/100 (`24 + 23 + 20 + 14 + 13`)

- Перевірені URL: `https://www.dw.com/de/` → `https://www.dw.com/de/themen/s-9077`; robots `https://www.dw.com/robots.txt`; sitemap index `https://www.dw.com/sitemap.xml`; RSS/RDF `https://rss.dw.com/rdf/rss-de-all`; рубрика `https://www.dw.com/de/deutschland/s-12321`; матеріал `https://www.dw.com/de/landtagswahlen-schwere-zeiten-für-kanzler-friedrich-merz/a-79362260`.
- Discovery/backfill: sitemap index містить мовні children, зокрема `/de/news-sitemap.xml`, `/de/article-sitemap.xml`, video/person/topic/navigation/gallery maps. RSS/RDF дає title/link/description/`dc:date`. Стаття має патерн `/de/{slug}/a-{id}`, рубрика — `/de/{name}/s-{id}`. Для backfill стрімити `/de/article-sitemap.xml`; news map — для свіжих URL.
- HTML/дані: article 200, близько 213 KB; JSON-LD і OG містять headline, description, datePublished/dateModified, author, image, publisher, canonical. Body є в server HTML; JS не обов'язковий. Рядки `subscription/paywall` знайдені у загальній розмітці, але sample body був анонімно доступний — це не підтверджений paywall для sample.
- Реалізація: sitemap index + RDF discovery, звичайний HTTP fetch, JSON-LD як первинний metadata parser, DOM selectors для body/media. Високий рейтинг завдяки повному sitemap і доступному HTML.

### ZEIT — 52/100 (`12 + 14 + 8 + 9 + 9`)

- Перевірені URL: `https://www.zeit.de/index`; robots `https://www.zeit.de/robots.txt`; оголошений index `https://www.zeit.de/gsitemaps/index.xml`; feed `https://newsfeed.zeit.de/index`; рубрика `https://www.zeit.de/politik/index`; sample `https://www.zeit.de/politik/deutschland/2026-09/vergesellschaftung-wohnungen-berlin-verbot-csu`.
- Результат: robots і feed — 200; homepage, sitemap, рубрика й стаття — 403 у цьому середовищі. Feed реально містить title/link/description/enclosure/category/`dc:creator`/pubDate/guid/`content:encoded`. URL: `/{section}/{subsection}/{YYYY-MM}/{slug}` або `/news/YYYY-MM/DD/{slug}`.
- Дані/доступ: feed дозволяє стабільно отримувати заголовок, анотацію/content payload, автора, категорії, час, image enclosure і canonical URL без реєстрації. Структуру article JSON-LD/OG та повноту body через прямий fetch не перевірено; paywall-клас матеріалу також треба визначати по кожній статті, не по feed.
- Реалізація: у v1 — feed-only discovery/ingestion плюс bounded browser smoke для перевірки публічного body. Не вмикати масовий HTML fetch, доки анонімний worker не пройде canary. Backfill через оголошений sitemap — `operationally unverified`. Рейтинг відображає корисний feed, але нестабільний anonymous HTML.

## Франція

### France 24 — 56/100 (`15 + 14 + 8 + 10 + 9`)

- Перевірені URL: `https://www.france24.com/fr/`; robots `https://www.france24.com/robots.txt`; sitemap `https://www.france24.com/sitemaps/fr/index.xml` і `/sitemaps/fr/news.xml`; RSS `https://www.france24.com/fr/rss`; рубрика `https://www.france24.com/fr/france/`; sample `https://www.france24.com/fr/europe/20260921-premiere-fois-torpille-americaine-tiree-depuis-sous-marin-britannique-sans-equipage-alliance-aukus-australie-excalibur-etats-unis-royaume-uni`.
- Результат: robots і RSS — 200; homepage, обидва sitemap, рубрика та стаття — 403. Robots оголошує language indexes і news maps для `fr/en/es/ar`. RSS має title, description, link, category, media thumbnail/enclosure, guid, pubDate, `dc:creator`.
- URL/дані: `/{lang}/{section}/{YYYYMMDD}-{slug}`, також `/{lang}/émissions/{show}/{YYYYMMDD}-{slug}` і video. У v1 підтверджені публічні поля обмежені RSS: headline, summary, author, time, category, media URL, canonical. JSON-LD/OG/body — `operationally unverified` через CDN 403.
- Реалізація: RSS cursor за guid/pubDate; dedupe по canonical; окремий browser worker canary для HTML, без припущення, що sitemap доступний з production egress. Якщо 403 — `body_unavailable`, не retry-loop.

### RFI — 56/100 (`15 + 14 + 8 + 10 + 9`)

- Перевірені URL: `https://www.rfi.fr/fr/`; robots `https://www.rfi.fr/robots.txt`; sitemap `https://www.rfi.fr/sitemaps/fr/index.xml` і `/sitemaps/fr/news.xml`; RSS `https://www.rfi.fr/fr/rss`; рубрика `https://www.rfi.fr/fr/france/`; sample `https://www.rfi.fr/fr/europe/20260921-élections-en-allemagne-comment-la-débâcle-de-la-cdu-aux-régionales-fragilise-friedrich-merz-à-l-international` (URL encoded під час запиту).
- Результат: robots/RSS 200; HTML та sitemaps 403. Robots перелічує language indexes/news maps для великого набору мов, включно з `fr` і `uk`. RSS поля збігаються з France 24: headline, description, category, `dc:creator`, pubDate, guid, thumbnail/enclosure.
- URL/дані: `/{lang}/{section}/{YYYYMMDD}-{slug}`; для радіоматеріалів можливі episode/show paths. Article JSON-LD/OG, повний transcript/body і pagination не підтверджено.
- Реалізація: RSS-first; media type визначати з URL/category/enclosure, не трактувати всі items як текстові статті. Sitemap/browser canary — окремо; 403 переводить адаптер у metadata-only. Рейтинг обмежений невалідацією HTML body.

### Le Monde — 76/100 (`22 + 24 + 8 + 10 + 12`)

- Перевірені URL: `https://www.lemonde.fr/`; robots `https://www.lemonde.fr/robots.txt`; `https://www.lemonde.fr/sitemap_news.xml`; index `https://www.lemonde.fr/sitemap_index.xml`; RSS `https://www.lemonde.fr/rss/une.xml`; рубрика `https://www.lemonde.fr/international/`; sample `https://www.lemonde.fr/international/article/2026/09/22/la-rencontre-entre-emmanuel-macron-et-donald-trump-sur-le-moyen-orient-et-l-ukraine-a-ete-tres-constructive-selon-le-president-francais_6779511_3210.html`.
- Discovery/backfill: news sitemap і RSS — 200. Index має date-bucket children `/sitemap/articles/YYYY-MM-DD.xml` і охоплює історичні дати; news map містить loc, publication date/title й image. RSS містить title/description/link/pubDate/guid/media content+description. URL: `/{section}/article/YYYY/MM/DD/{slug}_{article-id}_{rubric-id}.html`; є `/live/`.
- Доступ: homepage повернув великий HTML з JSON-LD/OG, але category/article повертали 200 `Client Challenge`, а повторний article probe — 402. Отже повний анонімний body не підтверджено; `200` challenge не рахувати успіхом. Частина матеріалів має передплату.
- Реалізація: sitemap + RSS для повного discovery і backfill, article fetch лише conditional canary. Metadata parser бере sitemap/RSS; body parser активується тільки коли title/schema/article DOM пройшли content guards. Сильна discovery-структура, слабка гарантія full text у v1.

## Велика Британія

### BBC News — 98/100 (`25 + 24 + 20 + 15 + 14`)

- Перевірені URL: `https://www.bbc.com/news`; robots `https://www.bbc.com/robots.txt`; news index `https://www.bbc.com/sitemaps/https-index-com-news.xml`; archive index `https://www.bbc.com/sitemaps/https-index-com-archive.xml`; RSS `https://feeds.bbci.co.uk/news/rss.xml`; rubric `https://www.bbc.com/news/world`; sample `https://www.bbc.co.uk/news/articles/cqp80p78mxx5o`.
- Discovery/backfill: news index 200 і містить numbered children `/sitemaps/https-sitemap-com-news-{N}.xml`; archive index має numbered historical children із lastmod. RSS містить title, description, link, guid, pubDate, thumbnail. URL article: `/news/articles/{stable-id}`; video: `/news/videos/{stable-id}`. `.com` list та `.co.uk` article hosts треба зводити до canonical, не вважати дублікатами.
- HTML/дані: homepage/category/article 200. Article близько 450 KB, JSON-LD+OG, headline, published/modified, author, body, images та media metadata; базовий текст server-rendered, JS не потрібен. Реєстрація для sample не потрібна.
- Реалізація: RSS/news sitemap для low-latency; archive index для backfill; HTTP + JSON-LD/DOM. Окремі parsers для text/video/live pages, content guards за schema type. Один бал стабільності втрачено через multi-host canonicalization і різні шаблони.

### The Guardian — 97/100 (`25 + 23 + 20 + 15 + 14`)

- Перевірені URL: `https://www.theguardian.com/international`; robots `https://www.theguardian.com/robots.txt`; news sitemap `https://www.theguardian.com/sitemaps/news.xml`; RSS `https://www.theguardian.com/world/rss`; рубрика `https://www.theguardian.com/world`; sample `https://www.theguardian.com/us-news/2026/sep/21/trump-and-mamdani-meeting-gracie-mansion`.
- Discovery/backfill: news sitemap 200 з loc, publication/title/image; RSS 200 з title/link/description/pubDate/`dc:date`, categories, guid, media, creator. URL: `/{section}/{YYYY}/{mon}/{DD}/{slug}`, live pages мають `/live/`. Перевірений `?page=N` патерн для рубрик не входив у цей smoke; застосовувати тільки після contract test. Історичний backfill без API — через sitemap/list enumeration, межі не підтверджено.
- HTML/дані: article 200, близько 328 KB, JSON-LD+OG із headline, description, published/modified, author, section/tags, image, publisher, `isAccessibleForFree`; body server-rendered. Реєстрація не потрібна. Subscription/support markup є, але sample text доступний.
- Реалізація: RSS+sitemap discovery; HTTP fetch; JSON-LD/OG metadata і DOM body/figure parser. Не використовувати Guardian Content API у v1 без ключа, бо вимога — без реєстрації.

### Sky News — 63/100 (`15 + 20 + 8 + 10 + 10`)

- Перевірені URL: `https://news.sky.com/`; robots `https://news.sky.com/robots.txt`; sitemap index `https://news.sky.com/sitemap.xml`; child `https://news.sky.com/sitemap/sitemap-news.xml`; RSS `https://feeds.skynews.com/feeds/rss/home.xml`; rubric `https://news.sky.com/world`; sample `https://news.sky.com/story/boris-johnson-resigns-live-updates-12593360`.
- Результат: robots, sitemap index і RSS — 200; homepage/category/article — 403. Index також перелічує page/video/image/weather sitemaps. RSS містить title/link/description/category/pubDate/guid/enclosure і Media RSS fields. URL: `/story/{slug}-{numeric-id}`; live URL може залишатися стабільним, а контент оновлюватися.
- Дані: metadata й media discovery перевірені; article JSON-LD/OG/body не перевірені з цього egress. Search-readable page існує, але це не доказ доступності production fetcher.
- Реалізація: RSS + news sitemap, повторний fetch live URLs за modified/content hash; HTML тільки через bounded browser canary. У разі 403 зберігати metadata-only без нескінченних retry.

## США

### NPR — 100/100 (`25 + 25 + 20 + 15 + 15`)

- Перевірені URL: `https://www.npr.org/`; robots `https://www.npr.org/robots.txt`; standard index `https://googlecrawl.npr.org/standard/sitemap_index.xml`; news map `https://googlecrawl.npr.org/news/sitemap_news.xml`; RSS `https://feeds.npr.org/1001/rss.xml`; rubric `https://www.npr.org/sections/world/`; sample `https://www.npr.org/2026/09/21/nx-s1-5973179/equinox-henge-sunrise-sunset`.
- Discovery/backfill: robots прямо оголошує standard/news/video/live-updates sitemap. Standard index розбитий на піврічні файли від 1990 року; news — fresh discovery. RSS має title/link/description/pubDate/guid/`content:encoded`/`dc:creator`. URL: `/{YYYY}/{MM}/{DD}/{id}/{slug}` або `g-s1`/`nx-s1` у path.
- HTML/дані: homepage/category/article 200; article близько 156 KB, JSON-LD+OG, headline, author, published/modified, body, images. Базовий article доступний анонімно; JS assets є, але body server-rendered.
- Реалізація: news feed/map для delta, standard sitemap index для deterministic backfill, HTTP + JSON-LD/DOM. Preserve audio/transcript/enclosure fields; parser type визначати за schema/DOM. Повний бал за перевірений архів, feed, доступний body й чіткі IDs.

### AP News — 34/100 (`8 + 8 + 4 + 6 + 8`)

- Перевірені URL: `https://apnews.com/`; robots `https://apnews.com/robots.txt`; оголошені `https://apnews.com/ap-sitemap.xml`, `/news-sitemap-content.xml`, `/hubs-sitemap-content.xml`; кандидат RSS `https://apnews.com/index.rss`; rubric `https://apnews.com/world-news`; актуальний sample `https://apnews.com/article/c4727651b9030d6e7f81c0457a33440b`.
- Результат: robots 200; решта endpoints — 403 з цього середовища. Robots підтверджує sitemap topology, але вміст карт не отримано. URL статті `/{section-optional}/article/{32-hex-id}`; точний slug не є потрібним для identity. RSS endpoint не підтверджено доступним і не слід закладати як контракт.
- Дані/доступ: актуальний article URL знайдено у пошуковій видачі, але прямий anonymous HTML/JSON-LD/body не пройшов. Усі поля, крім URL/видимих search metadata, — `operationally unverified`.
- Реалізація: джерело не включати до guaranteed full-text v1. Дозволити лише canary з production egress/browser; перехід до active — після 7-денного доказу sitemap+article 2xx і contract fixtures. При 403 circuit-breaker. Низький рейтинг є технічним, не редакційним.

### The New York Times — 68/100 (`18 + 23 + 5 + 10 + 12`)

- Перевірені URL: `https://www.nytimes.com/`; robots `https://www.nytimes.com/robots.txt`; news map `https://www.nytimes.com/sitemaps/new/news.xml.gz`; archive index `https://www.nytimes.com/sitemaps/new/sitemap.xml.gz`; RSS `https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml`; rubric `https://www.nytimes.com/section/world`; sample `https://www.nytimes.com/2026/09/21/business/ai-data-center-ipos.html`.
- Discovery/backfill: sitemaps і RSS 200. Archive index має monthly children `/sitemaps/new/sitemap-YYYY-MM.xml.gz`; news map містить loc/news/image metadata. RSS: title/link/description/pubDate/guid/creator/categories/media. URL: `/{YYYY}/{MM}/{DD}/{section}/{slug}.html`; Athletic має окремі patterns/maps.
- Доступ: homepage/category/article — 403 з цього egress; повний body анонімно не підтверджено, а частина матеріалів paywalled. Sitemap response фактично XML навіть із `.gz` URL, тому parser має визначати compression за bytes/header, а не suffix.
- Реалізація: metadata-only RSS+sitemap adapter у v1; article fetch лише conditional canary. Зберігати `body_unavailable/paywalled/blocked` окремо. Не використовувати NYT API без ключа/реєстрації.

## Бельгія

### VRT NWS — 100/100 (`25 + 25 + 20 + 15 + 15`)

- Перевірені URL: `https://www.vrt.be/vrtnws/nl/`; robots `https://www.vrt.be/robots.txt`; index `https://www.vrt.be/vrtnws/nl.sitemap-index.xml`; Atom `https://www.vrt.be/vrtnws/nl.rss.articles.xml`; rubric `https://www.vrt.be/vrtnws/nl/rubrieken/binnenland/`; short sample `https://vrtnws.be/p.6KVDK3aWv` → canonical VRT article.
- Discovery/backfill: language-specific indexes `nl/en/fr/de`, news map, tag/category/region maps, weekly `nl.sitemap.article.YYYY.WW.xml` і monthly story maps. Atom має title/id/updated/author/link/entry/published/summary; окремий `.rss.xml` існує на рівні article/liveblog. URL: `/vrtnws/{lang}/YYYY/MM/DD/{slug}/`, liveblog окремо.
- HTML/дані: homepage/category/article 200. Article близько 103 KB, JSON-LD+OG, published, author, body, images; anonymous and server-rendered enough for parsing, хоча page використовує JS/BFF для додаткових блоків.
- Реалізація: Atom/news sitemap delta, weekly sitemap backfill, resolve short URLs once and persist redirect; JSON-LD/DOM base parser, BFF only if a field is absent from HTML and endpoint remains anonymous. Separate article/liveblog/story contracts.

### RTBF Info — 98/100 (`25 + 24 + 20 + 15 + 14`)

- Перевірені URL: `https://www.rtbf.be/info/` → `/info`; robots `https://www.rtbf.be/robots.txt`; index `https://www.rtbf.be/sitemap.xml`; child `https://www.rtbf.be/sitemaps/latest-news.xml`; RSS `https://rss.rtbf.be/article/rss/highlight_rtbf_info.xml`; rubric `https://www.rtbf.be/info/monde` → `/dossier/monde`; sample `https://www.rtbf.be/article/vagues-de-chaleur-quand-il-fait-trop-chaud-la-productivite-baisse-les-pertes-pour-2026-pourraient-s-elever-a-6-milliards-d-euros-11788427`.
- Discovery/backfill: sitemap index має menu/latest-news/dossiers та інші children; latest-news містить loc, lastmod, publication/language/date/title/image. RSS має title/link/description/pubDate/category/media/enclosure/author/guid. URL: `/article/{slug}-{numeric-id}`; thematic lists canonicalize to `/dossier/{slug}`.
- HTML/дані: homepage/rubric/article 200; article близько 855 KB, JSON-LD+OG, headline, published, author, body/media. JS є, але body присутній у response. У розмітці є subscription strings, проте sample доступний без входу.
- Реалізація: latest-news + RSS delta, sitemap children for reconciliation/backfill, HTTP + JSON-LD/DOM; нормалізувати `/info/*` redirects. Контентні guards потрібні через великий hydration payload.

### The Brussels Times — 88/100 (`24 + 20 + 20 + 13 + 11`)

- Перевірені URL: `https://www.brusselstimes.com/`; robots `https://www.brusselstimes.com/robots.txt`; index `https://www.brusselstimes.com/sitemap-index.xml`; news map `https://www.brusselstimes.com/google-news-sitemap.xml`; child `https://www.brusselstimes.com/post-sitemap.xml`; rubric `https://www.brusselstimes.com/belgium`; sample `https://www.brusselstimes.com/belgium/2327534/brussels-extends-city-centre-public-drinking-ban-by-four-years/`.
- Discovery/backfill: index 200 з `post-sitemap.xml`, `post-sitemap2.xml`, etc.; news map дає fresh loc; post maps містять loc/lastmod/changefreq/priority. URL: `/{section}/{numeric-id}/{slug}/`, інколи без section. `https://www.brusselstimes.com/feed` повернув **HTML homepage**, не RSS/Atom; не використовувати як feed.
- HTML/дані: homepage/category/article 200. Sample близько 267 KB; OG має title, description, author, URL, image; у HTML доступні body/byline/date/credit. JSON-LD Article не підтверджено. Subscription markup є; для кожного item треба перевіряти, чи body справді присутній без входу.
- Реалізація: Google News sitemap delta + numbered post sitemaps backfill; HTTP + OG/DOM parser. Feed capability позначити false. Rating знижено через fake-feed response й менш багату structured metadata.

## Австрія

### ORF News — 76/100 (`19 + 16 + 20 + 12 + 9`)

- Перевірені URL: `https://orf.at/`; robots `https://orf.at/robots.txt`; RSS `https://rss.orf.at/news.xml`; list `https://oesterreich.orf.at/`; sample `https://orf.at/stories/3442862/`. `https://orf.at/sitemap.xml` і `/stories/` повернули 404.
- Discovery/backfill: RSS 200 з title/link/`dc:date`/description/`dc:creator`; homepage — актуальний list. URL article стабільний `/stories/{numeric-id}/`. Перевіреного sitemap чи окремої archive pagination немає; `oesterreich.orf.at` є окремою загальноавстрійською list surface, а не повним архівом news.ORF.at.
- HTML/дані: article 200, близько 16 KB, JSON-LD+OG, headline/date/author та server-rendered текст; JS не потрібен. Image/lead/related links доступні. Немає реєстрації/paywall.
- Реалізація: RSS poller + homepage reconciliation; HTTP + JSON-LD/DOM. Backfill beyond feed window — `operationally unverified`; не сканувати numeric IDs. Нижчий рейтинг пояснюється відсутністю sitemap/архівного cursor, а не якістю article HTML.

### Der Standard — 71/100 (`22 + 20 + 9 + 10 + 10`)

- Перевірені URL: `https://www.derstandard.at/`; robots `https://www.derstandard.at/robots.txt`; news map `https://www.derstandard.at/sitemaps/news.xml`; index `https://www.derstandard.at/sitemaps/sitemap.xml`; RSS `https://www.derstandard.at/rss`; rubric `https://www.derstandard.at/international`; sample `https://www.derstandard.at/story/3000000340741/trumps-sabotage-vorwurf-uno-252berpr252ft-rolltreppe-und-teleprompter`.
- Discovery/backfill: news map 200; index має monthly children `/sitemaps/sitemap-YYYY-MM.xml`. RSS 200 з title/link/description/item/guid/pubDate/content. URL: `/story/{numeric-id}/{slug}`.
- Доступ: homepage/category/article автоматично редиректять у `/consent/tcf/...`. Article shell 200, близько 60 KB, має OG і canonical, але JSON-LD/date/author/full body не були надійно підтверджені; consent/subscription markup домінує. Це `operationally unverified` для повного тексту без взаємодії.
- Реалізація: sitemap+RSS metadata adapter активний; article fetcher вважає `/consent/tcf/` неуспіхом body extraction і не слідує циклічно. Browser-based consent path можна оцінити окремим canary, але v1 contract не повинен від нього залежати.

### Die Presse — 91/100 (`24 + 23 + 17 + 14 + 13`)

- Перевірені URL: `https://www.diepresse.com/`; robots `https://www.diepresse.com/robots.txt`; news map `https://www.diepresse.com/news-sitemap`; archive index `https://www.diepresse.com/sitemap`; RSS `https://www.diepresse.com/rss/` і `/rss/ausland`; rubric `https://www.diepresse.com/ausland`; sample `https://www.diepresse.com/41284759/unterzeichnung-von-groenland-abkommen-am-dienstag`.
- Discovery/backfill: news sitemap, author sitemap й archive index 200; archive children `/sitemap/YYYY/MM`. RSS fields: title/link/description/pubDate/creator/guid. URL: `/{numeric-id}/{slug}`; identity брати з ID.
- HTML/дані: homepage/category/article 200. Sample близько 229 KB, JSON-LD+OG, headline, published, author, body, image/media; JS не потрібен для основи. Subscription/paywall markers є, тому anonymous accessibility треба визначати per article; sample URL віддав article body без login.
- Реалізація: RSS/news map delta, monthly sitemap backfill, HTTP + JSON-LD/DOM. Якщо schema каже restricted або body коротший за guard — metadata-only. Рейтинг нижче максимального через змішану доступність матеріалів.

## Режими реалізації за перевіреним доступом

Це один список джерел із технічним рейтингом, без поділу на «базові» та «додаткові» й без прихованої черги. Адаптери можуть реалізовуватися паралельно, але режим вмикання залежить від перевіреного доступу:

- `full_text`: NPR, VRT NWS, RTBF, BBC, Guardian, DW, Die Presse, Brussels Times, Tagesschau.
- `mixed`: ORF має слабкий backfill; Le Monde — body challenge; Der Standard — consent gate; NYT і Sky починають як metadata-first.
- `metadata_only` до позитивного canary: France 24, RFI, ZEIT, AP — лише фактично доступні feed/sitemap/metadata endpoints.

## Acceptance для незалежного агента адаптера

Для кожного джерела агент має передати manifest із усіма URL вище, recorded HTTP fixture для кожного endpoint, parser fixture для одного list і двох article types, URL identity/canonicalization tests, feed/sitemap cursor tests, `body_unavailable` fixture для challenge/paywall/403, field-coverage report і live smoke до 10 URL. Доказ одного домену не переноситься на інший. Джерело допускається до full-text mode лише коли sample body отримано без реєстрації й content guards відрізняють його від challenge/consent shell.
