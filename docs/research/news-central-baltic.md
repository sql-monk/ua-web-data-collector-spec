# Live-дослідження новинних джерел: UA, LT, LV, EE, PL, HU, CZ, SK

Дата перевірки: **2026-09-22** (Europe/Kyiv). Перевірено 25 джерел із поточного `TECHNICAL_SPECIFICATION.md` прямими анонімними HTTP-запитами з переходом за редиректами. Для кожного джерела відкрито homepage, `robots.txt`, заявлений або типовий sitemap, RSS/Atom-кандидат, одну list/category page та одну article page. `200 challenge` означає, що сервер повернув HTTP 200, але не матеріал. Статус стосується саме цього контрольного запуску й має повторно вимірюватися перед реалізацією.

## Єдина модель джерел і рейтингу

Усі записи нижче є просто джерелами з технічним рейтингом; поділу на «базові/додаткові» або P0/P1 тут немає.

Рейтинг `0–100` = coverage `0–25` + structured access `0–25` + anonymous accessibility `0–20` + data richness `0–15` + stability/observability `0–15`. У записах використано скорочення `C/S/A/R/O` у такому самому порядку. Coverage оцінює discovery та backfill; structured access — RSS/sitemap/JSON-LD; anonymous accessibility — фактичний доступ без входу; data richness — наповнення статті; stability/observability — сталі ID, timestamps, cursors і можливість контролю completeness.

### Контракт, спільний для всіх адаптерів

Зберігати immutable raw response та нормалізований `NewsArticleVersion`:

- `source_id`, `country_code`, `source_language`, `source_article_id`, `feed_guid`/`sitemap_lastmod` за наявності;
- requested URL, effective URL після redirect, canonical URL, URL discovery-сторінки, HTTP status, fetch time, ETag/Last-Modified, content hash;
- title, lead/description, original cleaned HTML, original plain text, author/byline, section, tags;
- `published_at`, `modified_at_source`, timezone/precision, first/last seen;
- усі публічні image/video/audio/embed URL, caption, credit, dimensions; related/source links;
- `access_state = free|partial|premium|blocked|challenge|unknown`, а не припущення, що кожен HTTP 200 містить повну статтю;
- окремий versioned `NewsTranslation`: `target_language=uk`, translated title/lead/body, provider/model/glossary versions, source content hash, QA flags.

Parser order: valid `NewsArticle`/`Article` JSON-LD → OpenGraph/article meta → embedded SSR state → source-specific HTML selectors. RSS використовується для low-latency discovery, sitemap — для completeness/backfill, category — як контроль yield; article HTML є джерелом повного тексту, якщо feed справді не містить `content:encoded`. Для sitemap index потрібен streaming XML parser, gzip і persisted child-sitemap cursor. JS/browser дозволяти лише коли raw HTML не містить потрібних публічних даних; не використовувати його як спосіб обходу блокування.

Під час root-перевірки зафіксовані канонічні редиректи: `pravda.com.ua → www.pravda.com.ua`, `liga.net → www.liga.net/ua`, `delfi.lt → www.delfi.lt`, `lsm.lv → www.lsm.lv`, `delfi.lv → www.delfi.lv`, `tvnet.lv → www.tvnet.lv`, `err.ee → www.err.ee`, `postimees.ee → www.postimees.ee`, `delfi.ee → www.delfi.ee`, `polskieradio.pl → www.polskieradio.pl`, `pap.pl → www.pap.pl`, `seznamzpravy.cz → www.seznamzpravy.cz`, `aktuality.sk → www.aktuality.sk`. Адаптер має зберігати requested та effective URL і дедуплікувати за canonical, не за довільним варіантом `www`.

## Україна (`UA`, `uk`)

### Суспільне — 96/100 (`C24/S24/A20/R14/O14`)

- Перевірені endpoints: homepage `https://suspilne.media/` 200; robots `https://suspilne.media/robots.txt` 200; sitemap index `https://suspilne.media/suspilne/sitemap/sitemap.xml` 200 (22 child maps) і news sitemap `.../sitemap-news.xml` 200; RSS `https://suspilne.media/rss/all.rss` 200, 200 items, без full-content element; list `https://suspilne.media/latest/` 200; article `https://suspilne.media/1409072-pat-telekanaliv-prezidentskogo-pulu-pripinili-znimati-trampa-jogo-cergovij-vistup-zalisivsa-bez-zvuku/` 200.
- URL/ID: national `/\d+-slug/`, regional `/{region}/\d+-slug/`; numeric prefix is source ID. List pagination confirmed as `/latest/?page=N`. Robots also exposes separate sport, culture and regional sitemap indexes.
- Data: SSR HTML contains `NewsArticle`, `Person`, `Organization`, `ImageObject`, breadcrumbs plus OG/article metadata (`published_time`, `modified_time`, author, section, tags). Extract title, lead, full body, authors, section/tags, timestamps, images/captions/credits and embedded media. Core parsing does not require JS.
- Adapter: RSS poll → news/regional sitemaps for reconciliation → HTTP article parser. Score is high because anonymous article, list, RSS and segmented sitemap all worked; one point is withheld from coverage/richness/observability because RSS has no full body and sitemap families require multiple cursors.

### Українська правда — 85/100 (`C20/S22/A16/R14/O13`)

- Endpoints: homepage `https://pravda.com.ua/` redirects to `https://www.pravda.com.ua/`, 200; robots 200 and declares `https://www.pravda.com.ua/sitemap/sitemap.xml`, but sitemap and `/news/` returned 403 in this run; RSS page `https://www.pravda.com.ua/rss-info/` 200; `https://www.pravda.com.ua/rss/` and `/rss/view_news/` 200 with 20 items and `content:encoded`; sampled article `https://www.pravda.com.ua/news/2026/08/14/8048692/` 200 without login.
- URL/ID: `/news/YYYY/MM/DD/{numeric_id}/`; articles/columns/projects are separate families. RSS supports all/news/main-news/publications. Historical list/backfill through HTML or sitemap is **operationally unverified** because both tested routes returned 403.
- Data: two JSON-LD blocks with `NewsArticle`, WebPage, author/image/publisher and breadcrumbs; OG includes canonical/title/description/image and `article:content_tier`. Parse RSS body plus article DOM, retaining tier/access markers and comparing hashes before accepting RSS as full equivalent.
- Adapter: RSS-first; fetch article for canonical/media/updates; do not schedule sitemap backfill until a bounded smoke confirms access. Score reduction is entirely from the observed 403 on category/sitemap and weaker completeness control, not from article richness.

### LIGA.net — 94/100 (`C23/S24/A19/R15/O13`)

- Endpoints: `https://liga.net/` redirects to `/ua`, 200; robots 200; `https://www.liga.net/sitemap-main/sitemap-index.xml` 200 (5 maps), news map 200; RSS catalogue `/ua/rss-page` 200 and `https://news.liga.net/ua/all/rss.xml` 200 with 20 summary items; list `https://www.liga.net/ua/news` 200; article `https://www.liga.net/ua/politics/articles/rosiia-nakopychuie-rakety-ukraina-hotuietsia-do-nayhirshoho-bloomberg` 200.
- URL/ID: localized `/{lang}/{section}/{type}/{slug}` plus product subdomains (`news`, `biz`, `tech`, `finance`, `life`). Pagination confirmed as `/ua/news/page/N`; sitemap includes main/news/authors/rubrics.
- Data: `NewsArticle`, author/publisher/image, published/modified, section/tags and rich OG. Capture visible article type, byline, lead, full body, related links and media. SSR HTML suffices; JS is only for interaction.
- Adapter: enumerate the official RSS catalogue, sitemap reconciliation, locale-aware article parser. Rating loses points only for multi-domain completeness and the need to keep locale/product provenance separate.

### Укрінформ — 94/100 (`C24/S22/A20/R14/O14`)

- Endpoints: homepage/robots 200; `https://www.ukrinform.ua/sitemap.xml` 200 with 143 child maps from current week through monthly archives, and `/sitemap/last.xml` 200; no general news RSS endpoint was discovered in homepage/robots during the check; list `https://www.ukrinform.ua/rubric-world` 200; article `https://www.ukrinform.ua/rubric-world/4166465-niderlandi-gotuutsa-do-novih-sankcij-ssa-proti-miznarodnogo-kriminalnogo-sudu-ap.html` 200.
- URL/ID: `/rubric-{section}/{numeric_id}-{slug}.html`; numeric ID is stable. Use `currentweek.xml`/`last.xml` for incremental discovery and monthly child maps for bounded backfill; category cursor was not exposed in ordinary anchors, so do not invent one.
- Data: three JSON-LD blocks (`NewsArticle`, WebPage, Person, ImageObject, breadcrumbs), OG/Twitter metadata, title/lead/body, author, publication/update, rubric/tags, image/caption/credit, embedded media. No JS needed for tested core fields.
- Adapter: sitemap-first with monthly cursor and HTML detail. Structured-access score is below maximum only because a general RSS feed was not confirmed.

## Литва (`LT`, `lt`)

### LRT — 97/100 (`C24/S24/A20/R15/O14`)

- Endpoints: homepage/robots 200; sitemap index `https://www.lrt.lt/servisai/sitemap/sitemap-index.xml` 200 with 19 gzip maps; RSS `https://www.lrt.lt/?rss` 200, 100 summary items; list `/naujienos/lietuvoje` 200; article `/naujienos/pasaulyje/6/3059493/rusijai-atakuojant-ukraina-pakelti-naikintuvai-lenkijoje` 200.
- URL/ID: `/naujienos/{section}/{section_id}/{article_id}/{slug}`; persist both numeric IDs. Gzip sitemap is preferred for archive; category pagination was not exposed in static anchors.
- Data: `NewsArticle`, Person, publisher/image and breadcrumbs plus article/OG metadata. Extract headline, lead/body, author, published time, section, images/captions/credits and audio/video where present. SSR is sufficient.
- Adapter: RSS → article, gzip sitemap backfill/reconciliation. Near-maximal score; the only uncertainty is category cursor semantics and how updates are represented in RSS.

### 15min — 92/100 (`C24/S24/A17/R14/O13`)

- Endpoints: homepage/robots 200; robots declares live index `.../articles_live_index.xml` and archive index `.../articles_index.xml` (80 archive maps); `https://www.15min.lt/rss` 200, 20 summary items; list `https://www.15min.lt/naujienos/aktualu/lietuva` 200; article `.../naujiena/aktualu/pasaulis/zelenskis-pakeliui-i-jav-susitiko-su-cza-direktoriumi-ratcliffe-u-57-2768644` 200.
- URL/ID: `/{product}/naujiena/{section}/{subsection}/{slug}-{section_id}-{article_id}`. Category uses a timestamp cursor such as `?offset=2026-09-18%2006:13:24`; persist exact server cursor, not page numbers.
- Data: three JSON-LD blocks (`NewsArticle`, Person, ImageObject, breadcrumbs/site search) and rich OG. Parse title, lead/body, author, dates, section, image/media and access label. Some pages advertise paid content, so classify each article; a 200 alone must not be treated as complete body.
- Adapter: RSS/live sitemap for new items, archive indexes for backfill, HTML parser with free/partial/premium detection. Anonymous-access points reduced for mixed access tiers.

### Delfi LT — 89/100 (`C24/S23/A16/R14/O12`)

- Endpoints: homepage/robots 200; robots redirects generic sitemap to `https://www.delfi.lt/news/sitemap/index.xml`, 200 with 306 children, and exposes `/news/sitemap/latest.xml`; no RSS endpoint was confirmed; list `/zinios-visiems/naujienos/` 200; article `.../baltijos-ir-lenkijos-vadovai-niujorke-aptare-sauguma-ir-parama-ukrainai-120307897` 200.
- URL/ID: multiple vertical prefixes ending in `-{numeric_id}`; keep full vertical path plus ID. Monthly sitemap index and latest map supply backfill/incremental discovery; category pagination/cursor was not visible in static links.
- Data: `NewsArticle`, WebPage/WebSite, publisher/image and OG; extract title, lead/body, author when present, dates, section, image/media and access state. Some content is subscription-labelled; verify body completeness per item. Core sample metadata was SSR.
- Adapter: latest sitemap → article, monthly sitemap reconciliation; HTML list only as yield monitor. Score reduced for no confirmed RSS, mixed access tiers and opaque category cursor.

## Латвія (`LV`, `lv`)

### LSM — 98/100 (`C25/S24/A20/R15/O14`)

- Endpoints: homepage/robots/sitemap 200; `https://www.lsm.lv/sitemap.xml` contains 162 weekly/language/master maps; RSS catalogue page `/barotnes/replay.lsm.lv/lv` 200 and category feed `https://www.lsm.lv/rss/?lang=lv&catid=14` 200 with 50 items; list `/zinas/latvija/` 200; article `/raksts/zinas/arzemes/21.09.2026-eiropas-izlukdienesti-bridina-par-krievijas-provokacijam.a664158/` 200.
- URL/ID: `/raksts/{vertical}/{section}/DD.MM.YYYY-{slug}.a{numeric_id}/`; ID after `.a` is stable. Sitemap is weekly and language-aware; category showed a `?p=1` style link, but sitemap is the safer backfill source.
- Data: `NewsArticle`, WebPage, Person/publisher/image and OG; capture lead/body, author/editor, dates, category/tags, media/captions/credits and related links. No JS needed for core text.
- Adapter: category RSS → detail; weekly sitemap completeness. Highest practical coverage; two points retained for cursor/change-behavior verification under sustained runs.

### Delfi LV — 90/100 (`C24/S23/A16/R15/O12`)

- Endpoints: homepage/robots 200; generic `/sitemap.xml` resolves to `https://www.delfi.lv/delfi/sitemap/index.xml`, while robots also names `https://www.delfi.lv/sitemap/index.xml`; latter returned 200 with 62 children; no RSS feed confirmed; list `/zinas` 200; article `/193/politics/120135351/valkas-novada-pasvaldibas-finansu-situacija-ir-kritiska-uzsver-ministrija` 200.
- URL/ID: `/{vertical_id}/{section}/{article_id}/{slug}`. Sitemap has numbered news indexes plus categories/tags/elections; persist nested-index cursor. Static list did not disclose pagination.
- Data: `NewsArticle`, WebPage, breadcrumbs, image/publisher; article meta includes published/modified, section and tags. Store numeric vertical/article IDs, full body, author, timestamps, media and access label. Mixed subscription markers require completeness checks.
- Adapter: sitemap-first, list yield monitor, detail HTML; no JS for tested core payload. Reduced score for unconfirmed RSS, mixed tiers and nested-index complexity.

### TVNET — 89/100 (`C23/S23/A16/R14/O13`)

- Endpoints: homepage/robots 200; generic `/sitemap.xml` 404 but robots-declared `https://www.tvnet.lv/sitemap` 200 with 46 news/section/video/gallery/author pages; RSS `/rss` 200 with 25 summary items; `/zinas` redirects to `/section/4221`, 200 with `?page=2`; article `/8549822/putinam-uzzimeja-tadu-rezultatu-kas-apliecina-sabiedribas-vairakuma-atbalstu-karam` 200.
- URL/ID: `/{numeric_id}/{slug}` and `/section/{numeric_id}`; page-number category pagination. `/sitemap/news` is a rolling article set; author maps are explicitly paged.
- Data: `NewsArticle`, WebPage/WebSite, Person, breadcrumbs and article/OG timestamps/tags. Extract full body, byline, section/tags, images/captions, videos and access state. Some pages carry subscription UI, so body completeness is per-item.
- Adapter: RSS → detail, sitemap/list reconciliation. Score reduced for rolling rather than clearly archival sitemap and mixed access tiers.

## Естонія (`EE`, `et`)

### ERR — 99/100 (`C25/S24/A20/R15/O15`)

- Endpoints: homepage/robots 200; `https://www.err.ee/sitemap` 200 with 11 news/video/general children; `https://www.err.ee/rss` and `/rss/eesti` 200 XML (the TЗ link `/eesti/rss` is an HTML page, not the feed); list `https://uudised.err.ee/l/eesti` redirects to `https://www.err.ee/l/eesti`, 200; article `/1610144527/saks-valimised-okupeeritud-aladel-olid-venemaa-abitu-poliitiline-sonum` 200.
- URL/ID: `/{numeric_id}/{slug}`, category `/l/{section}`. Separate numbered news/video sitemap shards provide backfill; numeric ID is stable.
- Data: `NewsArticle`, WebPage, Person/publisher/image, ItemList and breadcrumbs; article meta has published/modified/section/tags. Preserve text plus radio/video embeds and media metadata. Anonymous SSR worked.
- Adapter: real XML RSS endpoint → article; all sitemap shards for completeness. One structured point withheld because the linked RSS catalogue URL is easy to misclassify and must be resolved explicitly.

### Postimees — 86/100 (`C22/S23/A14/R14/O13`)

- Endpoints: homepage/robots 200; `/sitemap.xml` 404 but robots-declared `https://postimees.ee/sitemap` 200 with 46 maps; RSS `/rss` 200, 25 summary items; requested `/uudised` redirected to `/search`, 200, where pagination uses `?sections=...&page=N`; article `/8550250/el-ei-saavutanud-kokkulepet-vene-oligarhide-sanktsioonide-osas` 200.
- URL/ID: `/{numeric_id}/{slug}`; retain section filters from list URL. Sitemap has rolling news plus sections/videos/galleries/authors.
- Data: `NewsArticle`, WebPage/WebSite, Person, image and breadcrumbs with article/OG times/tags. Extract body and explicit subscription/preview state; many pages advertise paid access, so anonymous full-body coverage is not guaranteed.
- Adapter: RSS and rolling sitemap discovery, detail parser with paywall boundary detector. Rating reduction reflects mixed anonymous coverage and absence of a clearly complete historical article sitemap.

### Delfi EE — 86/100 (`C23/S22/A15/R14/O12`)

- Endpoints: homepage/robots 200; `https://www.delfi.ee/sitemap/index.xml` 200 with 62 child maps; no RSS/Atom confirmed; valid list `https://www.delfi.ee/kategooria/120000076/eesti` 200 (`/uudised` itself was 404 JSON); article `/artikkel/120612400/euroopa-liit-liigub-sanktsioonide-leevendamise-teed-vene-miljardarid-voivadki-paaseda` 200.
- URL/ID: `/artikkel/{numeric_id}/{slug}`, category `/kategooria/{numeric_id}/{slug}`. Numbered news maps plus sections/tags provide discovery/backfill; list cursor was not visible.
- Data: embedded `NewsArticle`/WebPage/site objects and OG provide title/lead/image/section; store author, timestamps, body, media and access state from detail DOM. Subscription-labelled content requires per-page completeness detection.
- Adapter: sitemap-first, category as yield monitor, HTML detail; no browser needed for sampled SSR metadata. Deductions: no confirmed feed, opaque list cursor, mixed access.

## Польща (`PL`, `pl`)

### Polskie Radio — 66/100 (`C15/S10/A20/R11/O10`)

- Endpoints: homepage 200; `robots.txt` 404; `/sitemap.xml` 200 but only 10 broad landing/podcast URLs, not an article archive; RSS catalogue page `/399/8214` 200 HTML but no machine feed URL was exposed to the static extractor; list `/5/1222` 200; requested article `/7/166/Artykul/3727321` redirects to `https://jedynka.polskieradio.pl/artykul/3727321`, 200.
- URL/ID: legacy `/{station}/{section}/Artykul/{id}` redirects to station subdomain `/artykul/{id}`. Category/archive pagination exists on numbered `/Strona/N` URLs (verified `/5/1222/Strona/55`).
- Data: sampled page had OG title/description/image and Next.js SSR state, but no `NewsArticle` JSON-LD. Extract ID, station/program, headline, lead/body, byline/date, audio/media from embedded state/DOM and retain redirect provenance.
- Adapter: category pagination plus detail SSR parser; treat RSS as **not yet confirmed**, not absent. Lower rating follows weak article sitemap/feed discoverability and multi-station redirects, despite anonymous article access.

### PAP — 32/100 (`C8/S3/A5/R9/O7`)

- Endpoints: homepage and `/aktualnosci` return HTTP 200 but only ~0.84 KB challenge shell; robots 200; `/sitemap.xml` redirects to homepage challenge; no RSS confirmed. Sample article `https://www.pap.pl/aktualnosci/astronomowie-5-sierpnia-czlon-rakiety-falcon-9-moze-sie-rozbic-na-ksiezycu` also returned the same shell. Browser/search index could read the category/article, but direct adapter behavior is **operationally unverified**.
- URL/ID: observed `/aktualnosci/{slug}` and category `/nauka`; indexed list pages show numbered pagination, but live cursor could not be validated through the challenge.
- Data: indexed page exposes title, lead/full text, publication/update, images/captions/credits; none of those fields were present in the direct 0.84 KB response, so they are not accepted as live-parser evidence.
- Adapter: disabled/circuit-open until an approved normal anonymous session can fetch repeatably; bounded browser smoke may determine whether public JS execution is sufficient. Do not parse the challenge as an empty article. Low rating is evidence-driven.

### TVN24 — 82/100 (`C20/S20/A16/R14/O12`)

- Endpoints: homepage/robots 200; `/sitemap.xml` 404 and robots declares no alternate sitemap; `https://tvn24.pl/najwazniejsze.xml` returns 200 XML but contained no RSS items in this check; list `/najnowsze` 200 with `/najnowsze/s-N` pagination; article `/swiat/michal-wawer-posel-konfederacji-kazda-partia-niemiecka-jest-prorosyjska-ale-grzegorz-braun-nie-jest-st9248341` 200.
- URL/ID: `/{section}/{slug}-st{numeric_id}` (also other content suffix families such as `-bi{id}`); archive pages `/section/s-N` and `/najnowsze/s-N`.
- Data: `NewsArticle`, WebPage, Organization/Person/ImageObject and VideoObject plus OG. Capture suffix type/ID, title, lead/body, author/time, section, image/video and explicit TVN24+ access state. The sampled HTML was rich SSR, but mixed Plus content must be rejected as incomplete when body is gated.
- Adapter: category pagination → detail, with feed used only after non-empty validation. Rating deductions reflect missing sitemap, empty tested feed and mixed access.

## Угорщина (`HU`, `hu`)

### Telex — 97/100 (`C24/S25/A19/R15/O14`)

- Endpoints: homepage/robots 200; `/sitemap.xml` 405; RSS `https://telex.hu/rss` 200 with 50 items and full-content elements; list `/rovat/belfold` 200; article `/kulfold/2026/09/22/orosz-parlamenti-valasztas-eredmeny-putyin-egyseges-oroszorszag-part` 200.
- URL/ID: `/{section}/YYYY/MM/DD/{slug}`; article path plus canonical/content hash is the identity because no numeric ID is present. Static category pagination was not exposed; use dated RSS/article URLs for cursor and archive only after separate discovery validation.
- Data: `NewsArticle`, WebPage, Person/publisher/image, breadcrumbs and article/OG published time, author, section/tags. RSS includes content; HTML supplies canonical/media/update/access verification. Anonymous SSR worked.
- Adapter: RSS-first with content hash and article verification. High score comes from full feed and rich schemas; coverage is one point lower because no sitemap/archive cursor was confirmed.

### HVG — 89/100 (`C22/S23/A17/R14/O13`)

- Endpoints: homepage/robots 200; `/sitemap.xml` 404; RSS `/rss` 200 with 60 summary items; list `/itthon` 200; article `/vilag/20260921_donald-trump-tamogatattsag-29-szazalek-kozvelemeny-kutatas-melypont` 200.
- URL/ID: `/{section}/YYYYMMDD_{slug}`. No static category cursor or sitemap was confirmed, so RSS plus dated URL provides incremental state but historical backfill remains weaker.
- Data: `NewsArticle`, Person/Organization, OG/article published/modified/section/tags. Extract headline/lead/body, author, times, image/media and access marker; `hvg360` is a distinct potentially gated family and must not be treated as complete anonymous text.
- Adapter: RSS → detail, category yield monitoring, date-bounded archive discovery only after fixture validation. Rating reflects excellent metadata but incomplete sitemap/backfill and mixed HVG360 access.

### 444 — 91/100 (`C21/S24/A19/R14/O13`)

- Endpoints: homepage/robots 200; news sitemap `https://444.hu/sitemap-news.xml` 200 with 74 recent URLs; RSS `/feed` 200 with 30 items and full-content elements; attempted `/aktualis`, `/friss-hirek`, `/hirek` were 404, so homepage is the confirmed rolling list; article `/2026/09/21/lazar-janos-tavat-es-napelemparkot-is-engedely-nelkul-epitett-a-batidai-birtokan` 200.
- URL/ID: `/YYYY/MM/DD/{slug}`; identity is canonical plus date/slug. News sitemap is recent-window only, not archival; no category page cursor was confirmed.
- Data: `NewsArticle`, Person/Organization, breadcrumbs and OG/article published metadata. RSS contains body; HTML adds canonical/media/byline/tags. Core article was anonymous SSR.
- Adapter: full-content feed → detail validation, recent sitemap completeness; historical backfill requires a separately researched archive route. Coverage points are lower solely for that gap.

## Чехія (`CZ`, `cs`)

### iROZHLAS — 69/100 (`C20/S17/A10/R12/O10`)

- Endpoints: homepage, robots, sitemap, list `/zpravy-domov` and sampled article all returned 403 to direct HTTP; RSS catalogue `/rss` and machine feed `https://www.irozhlas.cz/rss/irozhlas` returned 200, 20 summary items. Article sample: `/zpravy-svet/v-rusku-slysime-volani-po-valce-lide-jsou-na-ni-zavisli-diky-ni-vydelavaji-rika_2609220010_elev`.
- URL/ID: `/{section}/{subsection?}/{slug}_{YYMMDDHHMM}_{suffix}`. Feed offers section-specific endpoints such as `/rss/irozhlas/section/zpravy-domov`; feed timestamp/link can drive incremental discovery. HTML parsing/backfill is **operationally unverified** under current blocking.
- Data: RSS confirms title/link/GUID/date/summary. Full article schemas/body were not observable in this run and must not be claimed from memory.
- Adapter: RSS ingestion can be implemented independently; article fetcher remains circuit-open on 403 and stores the incident. Rating reflects usable structured discovery but unverified anonymous full text.

### ČT24 — 99/100 (`C25/S24/A20/R15/O15`)

- Endpoints: homepage/robots 200; robots declares separate article/news/section/tag/video/image sitemaps; article index `/sitemaps/sitemap_articles.xml` 200 with 38 shards and news sitemap 200; RSS `/rss` 200 with 30 items; list `/rubrika/domaci-5` 200 with `?page=N`; article `/clanek/svet/veci-spojene-s-hereckou-bardotovou-se-v-aukci-prodaly-za-temer-milion-eur-377825` 200.
- URL/ID: `/clanek/{section}/{slug}-{numeric_id}`, category `/rubrika/{slug}-{id}`. Full shard index is suitable for backfill; news sitemap and RSS cover freshness.
- Data: `Article`, Person/Organization/ImageObject/WebPage and OG; Next.js SSR contains article content. Extract title, lead/body, authors, published/updated, section/tags, image/video/audio and captions/credits.
- Adapter: RSS/news sitemap → detail; sharded article sitemap backfill; category as yield monitor. One structured point withheld pending long-run update semantics.

### Seznam Zprávy — 94/100 (`C24/S22/A20/R14/O14`)

- Endpoints: homepage/robots 200; generic `/sitemap.xml` 404 but robots declares article index `/sitemaps/sitemap_articles.xml` (20 shards), news/sections/tags sitemaps, all 200; RSS `/rss` 200 with 30 summary items; list `/sekce/domaci-13` 200; article `/clanek/zahranicni-staty-eu-se-shodly-na-prodlouzeni-sankci-proti-rusum-dve-jmena-zmizela-315852` 200.
- URL/ID: `/clanek/{section}-{slug}-{numeric_id}`, list `/sekce/{slug}-{id}`. Use article shards for historical backfill and news sitemap/RSS for freshness; static list did not expose a page cursor.
- Data: embedded schema object includes `NewsArticle`, Person/publisher/image, speakable and breadcrumbs; OG is present in rendered source even though no standalone `application/ld+json` tag was counted in one response. Extract title, perex/body, author/time, section/tags and media.
- Adapter: RSS/news sitemap → SSR detail; shard reconciliation. Slight deduction for schema placement/list cursor, not for anonymous access.

## Словаччина (`SK`, `sk`)

### STVR Správy — 99/100 (`C25/S25/A20/R15/O14`)

- Endpoints: homepage/robots 200; `/sitemap.xml` redirects to `/sitemap_index.xml`, 200 with 24 WordPress maps; RSS `/feed/` 200 with 100 items and full content; list `/kategoria/slovensko/` 200 with `/page/N/`; article `/2026/09/dochodky-by-na-buduci-rok-mali-narast-o-tri-a-pol-percenta-za-nizsou-valorizaciou-je-aj-spomalena-inflacia/` 200; public `/wp-json/` link is advertised by homepage.
- URL/ID: `/YYYY/MM/{slug}/`; WordPress internal ID should be read from JSON-LD/REST where available, with canonical as fallback. Post sitemaps are numbered and category/tag/author/video maps are separate.
- Data: extensive `NewsArticle`, WebPage, Person/NewsMediaOrganization, ImageObject/VideoObject, breadcrumbs and published/modified metadata. Store full body and all media/caption/credit fields. Anonymous SSR worked.
- Adapter: RSS or WordPress REST discovery, sitemap completeness, HTML detail fallback. Near-maximum score; long-run REST pagination/rate behavior still needs observation.

### Aktuality.sk — 96/100 (`C25/S24/A18/R15/O14`)

- Endpoints: homepage/robots 200; generic `/sitemap.xml` 404, while robots exposes Google News, 24h, category/topic/author and time-limited/evergreen sitemap indexes; time-limited index 200 with 195 monthly maps; RSS `/rss-articles.xml` 200 (109 items); list `/spravy/slovensko/` 200; article `/clanok/YLCsyGc/pocasie-predpoved-na-utorok/` 200.
- URL/ID: `/clanok/{opaque_id}/{slug}/`; opaque ID is stable. Use 24h sitemap/RSS for new items and both article indexes for backfill; category cursor was not present in static anchors.
- Data: rich JSON-LD graph including `NewsArticle`, author/publisher/image, WebPage and breadcrumbs plus OG/article timestamps. Parse full body, author, dates, section/tags, image/video and premium/access label. Some content is premium, though the sampled article was directly retrievable.
- Adapter: RSS + 24h sitemap, archive indexes for reconciliation, per-item gate detection. Two anonymous points withheld for mixed premium catalogue.

### SME — 31/100 (`C7/S5/A4/R8/O7`)

- Endpoints: homepage and `/domov` returned 403; robots 200 and declares `https://www.sme.sk/sitemap.xml` plus a news sitemap, but both sitemap and tested `/rss-title` returned 403. Search-visible archive example `https://svet.sme.sk/arch/2025-05-14` also returned 403 directly. Homepage/list/article content is therefore **operationally unverified** for this adapter environment.
- URL/ID: search-visible network uses vertical subdomains, `/arch/YYYY-MM-DD`, and article forms such as `/{vertical}.sme.sk/c/{numeric_id}/{slug}.html`; exact live redirects/selectors were not accepted without a successful fetch.
- Data: indexed pages suggest title/lead/body/byline/date and premium markers, but only 403 shell was observed directly. Do not infer completeness or schema.
- Adapter: keep disabled/circuit-open; a normal anonymous browser smoke may be scheduled to determine whether the block is tool/network-specific. Do not retry aggressively. Low score follows failure of homepage, list, article/RSS and sitemap paths in the live run.

## Розподіл реалізації між незалежними агентами

Кожен source adapter володіє тільки своїм manifest, fixtures, parser і contract tests. Усі source packages можуть виконуватися паралельно після фіксації спільного SDK-контракту; рейтинг не задає чергу. Для 15min, усіх трьох редакцій Delfi, TVNET, Postimees, HVG і TVN24 fixture має явний `access_state`. Для Української правди, 444 і Polskie Radio потрібен обмежений архівний experiment. PAP, article fetch iROZHLAS і SME лишаються вимкненими, доки повторний anonymous smoke не поверне справжній article HTML; RSS-ingestion iROZHLAS можна реалізувати окремо.

Acceptance-fixtures кожного адаптера мають містити: один raw feed item за наявності, один sitemap/index sample, одну list page, одну free article, одне оновлення якщо його вдалося спостерегти та один blocked/partial/premium case де це застосовно. Run завершується fail-closed, коли зникає очікуваний body selector, content length падає до challenge shell, HTTP стає 401/403/429 або discovered/parsed yield істотно відхиляється від rolling baseline.

## Перевірені домени

`suspilne.media`, `pravda.com.ua`, `liga.net`, `ukrinform.ua`, `lrt.lt`, `15min.lt`, `delfi.lt`, `lsm.lv`, `delfi.lv`, `tvnet.lv`, `err.ee`, `postimees.ee`, `delfi.ee`, `polskieradio.pl`, `pap.pl`, `tvn24.pl`, `telex.hu`, `hvg.hu`, `444.hu`, `irozhlas.cz`, `ct24.ceskatelevize.cz`, `seznamzpravy.cz`, `spravy.stvr.sk`, `aktuality.sk`, `sme.sk`.
