# Польове дослідження українських каталогів і авторинків

Перевірено 2026-09-22. Усі наведені приклади відкривалися без облікового запису. Перевірка охоплювала головну або категорійну сторінку, `robots.txt`, sitemap, одну картку товару/оголошення та структуровані дані. HTTP-клієнт і звичайний браузер перевірялися окремо, бо успішний браузерний доступ не гарантує доступ без JavaScript.

## Методика та рейтинг

Рейтинг не є пріоритетом розробки й не ділить джерела на базові та додаткові. Це сума п'яти оцінок: покриття 0–25, структуровані канали 0–25, анонімна доступність 0–20, багатство полів 0–15, стабільність URL/розмітки 0–15. Максимум — 100. Поруч із рейтингом завжди зберігаються дата перевірки та складові, інакше число неаудитоване.

Спільний порядок вилучення: sitemap/RSS для discovery, JSON-LD або вбудований стан сторінки для стабільних полів, DOM/HTML для решти. Playwright застосовується тільки коли звичайний HTTP не дає того самого публічного представлення. Реєстрація, login-only API, приватні кабінети й CAPTCHA у v1 не використовуються.

## AUTO.RIA — 91/100

Складові: 24 + 22 + 18 + 15 + 12.

- Вхідні точки: `https://auto.ria.com/uk/legkovie/`, `https://auto.ria.com/robots.txt`; приклад картки — `https://auto.ria.com/uk/auto_bmw_x5_40278307.html`.
- Discovery: у `robots.txt` зафіксовано `https://auto.ria.com/sitemaps/sitemap_2.xml`, `sitemap-final-page-new.xml`, `uk/newauto-sitemap-index.xml`, карти автосервісів і популярних послуг. Індекс містить спеціалізовані карти `after_dtp`, `na_vyplatu`, `avto_na_zapchasti`, `avtoobmen`, `hots`, `kredit`, `photo`, `proven_cars`; дочірні карти можуть бути gzip. Категорійна пагінація — `?page=2`.
- URL: картка вживаного авто — `/uk/auto_{make}_{model}_{numeric_id}.html`; числовий ID є `source_item_id`. Canonical URL треба брати зі сторінки після redirects.
- Структуровані дані: JSON-LD `Vehicle` і `BreadcrumbList`. Перевірені ключі `name`, `description`, `url`, `brand`, `model`, `productionDate`, `vehicleIdentificationNumber`, `mileageFromOdometer`, `vehicleEngine`, `vehicleTransmission`, `fuelType`, `bodyType`, `color`, `numberOfDoors`, `itemCondition`, `aggregateRating`, `offers.price`, `offers.priceCurrency`, `offers.availability`.
- Додаткові публічні поля HTML: область/місто, дата публікації/оновлення, продавець і його тип, опис, комплектація, стан, перевірки, кредит/обмін, фото, відео, держномер, контакти, якщо вони розкриваються без входу.
- Реалізація v1: не використовувати Developers API, бо ключ потребує реєстрації. Discovery — sitemap плюс контрольний обхід категорій; detail — HTTP, а анонімне розкриття контакту окремим low-rate browser job лише за відсутності login/CAPTCHA. Заборонені в `robots.txt` API/GraphQL/VIN-report маршрути не є входами адаптера.

## OLX Авто — 85/100

Складові: 25 + 20 + 15 + 15 + 10.

- Вхідні точки: `https://www.olx.ua/uk/transport/legkovye-avtomobili/`, `https://www.olx.ua/robots.txt`, sitemap із robots — `https://www.olx.ua/sitemap.xml`.
- URL: список категорії допускає `?page=N`; картка — `/d/uk/obyavlenie/{slug}-ID{public_slug_id}.html`. У картці окремо є числовий `ID`, який зберігати як головний `source_item_id`, а `ID...` зі slug — як `public_slug_id`.
- Перевірена картка: `https://www.olx.ua/d/uk/obyavlenie/mercedes-benz-b-class-2010-w245-fl-2-0-at-136-k-s-premium-full-ID11gy2Z.html`. Вона відкрилася без входу, а телефон розкрився кнопкою без запиту авторизації.
- Структуровані дані: JSON-LD `Vehicle` із `name`, `description`, `url`, `image[]`, `category`, `sku`, `brand`, `model`, `productionDate`, `vehicleIdentificationNumber`, `color`, `offers.price`, `offers.priceCurrency`, `offers.availability`, `offers.areaServed`.
- Публічні поля DOM: приватна особа/бізнес, пробіг, умови продажу, розмитнення, VIN, держномер, об'єм і потужність двигуна, країна походження, модифікація, рік, кузов, двері/місця, колір, коробка, привід, паливо й витрата, лакофарбове покриття, технічний стан, комфорт/мультимедіа/безпека/інше, опис, дата, продавець, профіль, географія, фото та телефон після анонімного reveal.
- Доступ: прямий HTTP інколи повертає 403 challenge, тоді як звичайна браузерна сесія показує сторінки. Тому discovery перевіряє sitemap через HTTP, detail має окремий Playwright pool, concurrency 1 і circuit breaker. CAPTCHA не розв'язувати; картку переводити в `blocked_anonymous`.

## RST.ua — 59/100

Складові: 20 + 6 + 13 + 13 + 7.

- Вхідні точки: `https://rst.ua/ukr/`; приклад списку — `https://rst.ua/ukr/oldcars/toyota/camry/11.html`; картка — `https://rst.ua/ukr/oldcars/toyota/camry/toyota_camry_15015608.html`.
- URL: `/ukr/oldcars/{make}/{model}/{slug}_{numeric_id}.html`; сторінки списку використовують `/{page}.html`. `numeric_id` — стабільний ключ.
- Формат: legacy HTML/Windows-1251; декодування треба робити за фактичним charset до parsing, raw bytes зберігати без перетворення. Залежно від User-Agent HTTP може повернути 403 або порожню octet-stream відповідь, тому потрібні browser smoke й circuit breaker.
- Поля: марка/модель, USD і UAH ціни, рік, пробіг, область/місто, двигун/об'єм/паливо, коробка, привід, кузов, колір, опис, кількість фото, продавець, ознака перевірки, кредитний платіж, обмін, дата/статус. Телефон показаний у UI як reveal-поле, але його успішне анонімне розкриття для RST лишається `operationally_unverified`.
- Структурований контракт на JSON-LD не покладати: адаптер має versioned DOM selectors і text-label parser. Root `sitemap.xml` у перевірці не дав надійного XML-вмісту, тому discovery — категорійні дерева та пагінація з checkpoint.

## Automoto.ua — 91/100

Складові: 23 + 22 + 19 + 14 + 13.

- Вхідні точки: `https://automoto.ua/uk/car`, `https://automoto.ua/robots.txt`, `https://automoto.ua/sitemap.xml`, `https://automoto.ua/sitemaps/images.xml`.
- Sitemap індексує щоденні карти оголошень і gzip-карти каталогів. Приклад дочірньої карти — `https://automoto.ua/sitemaps/ogoloshennya/23.09.2024.xml`; картка після redirect — `https://automoto.ua/uk/Mitsubishi-Pajero-Wagon-2008-Zolotonosha-68792362.html`. Пагінація — `/uk/car?page=2`, перевірено посилання до великих номерів сторінок.
- URL: `/uk/{Make}-{Model}-{Year}-{City}-{numeric_id}.html`; ID з кінця шляху — ключ. Canonical після redirect обов'язковий.
- JSON-LD `Vehicle`: `name`, `description`, `url`, `image`, `brand`, `manufacturer`, `model`, `productionDate`, `dateVehicleFirstRegistered`, `purchaseDate`, `vehicleIdentificationNumber`, `mileageFromOdometer`, `fuelType`, `bodyType`, `color`, `vehicleTransmission`, `vehicleConfiguration`, `vehicleSeatingCapacity`, `numberOfDoors`, `geo`, `offers.priceSpecification`, `offers.seller`, `aggregateRating`, `review`.
- DOM додає опис, телефони/продавця, ціну в кількох валютах, область, комплектацію, стан, фото та посилання на upstream. Оскільки це агрегатор, обов'язкові `upstream_source`, `upstream_item_id/url`, `is_aggregated=true`; не зливати з першоджерелом без provenance.

## Prom.ua — 95/100

Складові: 24 + 23 + 19 + 15 + 14.

- Вхідні точки: `https://prom.ua/ua/Mobilnye-telefony`, `https://prom.ua/robots.txt`; sitemap — `sitemap_products-promoted.xml`, `sitemap_products-new.xml`, `sitemap_models.xml`. Product index ділиться на `sitemap_products-new-{n}.xml`.
- URL: `/p{numeric_id}-{slug}.html`; приклад — `https://prom.ua/p3195588533-asics-gel-nyc.html`. Після redirect завжди зберігати canonical і локаль сторінки.
- JSON-LD: `Product`, `BreadcrumbList`, `PaymentMethod`, `LoanOrCredit`. `Product` містить `name`, `description`, `url`, `image`, `sku`, `brand`, а `Offer` — `price`, `priceCurrency`, `availability`, `eligibleQuantity`, `seller`, accepted payment methods.
- DOM/embedded state: категорії, характеристики key/value, варіанти, min order, wholesale tiers, seller/company ID і профіль, рейтинг продавця, статус, місто, способи оплати/доставки, гарантія, контакти якщо анонімно доступні, відгуки, запитання/відповіді, фото/відео.
- Модель даних: `Product` відокремити від `Offer`; seller SKU і Prom product ID не використовувати як глобальний GTIN. Reviews/questions можуть мати окрему пагінацію. Seller API не є каналом v1, бо потребує облікового запису.

## Rozetka — 91/100

Складові: 25 + 24 + 15 + 15 + 12.

- Вхідні точки: `https://rozetka.com.ua/ua/mobile-phones/c80003/`, `https://rozetka.com.ua/robots.txt`. Root `sitemap.xml` під час перевірки повертав 404; discovery має йти через дерево категорій, їх pagination/filter links і перевірені browser snapshots, а не вигаданий sitemap.
- URL категорії — `/ua/{category-slug}/c{category_id}/`; товар — `/ua/{slug}/p{numeric_id}/`. Приклад — `https://rozetka.com.ua/ua/samsung-sm-f976bzkcsek/p605404744/`.
- Перевірена категорія без входу показала кількість товарів, фільтри бренду, продавця, стану, доставки, ціни й рейтингу та пагінацію. HTTP може отримати Cloudflare, але звичайний браузер відкриває сторінку.
- JSON-LD: `Product` і `BreadcrumbList`. Перевірені поля `sku`, `url`, `name`, `description`, `brand`, `category[]`, `image[]`, `offers.itemCondition`, `offers.availability`, `offers.price`, `priceCurrency`, `priceValidUntil`, promotional `priceSpecification`, `aggregateRating`/reviews якщо є.
- DOM: seller, product code, current/old/card price, bonus, stock, variants, характеристики, warranty, payment/delivery by city, services, related products, ratings, reviews/questions, media. Одна модель може мати кілька offers/sellers; не перезаписувати model-level attributes даними конкретної пропозиції.
- Реалізація: HTTP-first із Playwright fallback, browser budget і snapshot tests. Сторінка без входу — допустима; Cloudflare/CAPTCHA — `blocked_anonymous`, без обходу.

## Епіцентр — 94/100

Складові: 25 + 20 + 19 + 15 + 15.

- Вхідні точки: `https://epicentrk.ua/ua/shop/smartfony-i-mobilnye-telefony/`, `https://epicentrk.ua/robots.txt`; картка — `https://epicentrk.ua/ua/shop/zhidkost-weekend-dlya-rozzhiga-500-ml.html`.
- Robots публікує окремі карти власних товарів `upload/sitemap/new/products_ep/products_main_ua.xml`, marketplace `products_mp/products_merchant_ua.xml`, категорій `catalog/catalog.xml`, брендів, відгуків і review images. Дочірні product maps мають суфікс `_000.xml` тощо.
- URL товару — `/ua/shop/{slug}.html`; категорії — `/ua/shop/{category}/`, регіональні варіанти мають місто у шляху. Canonical має прибирати місто/marketing query лише якщо так вказала сторінка.
- Поля: internal product code/SKU, назва, бренд, категорії, опис, характеристики, фото/відео, seller і тип `ep/mp`, current/old price, одиниця продажу, наявність, delivery/pickup, бонуси, гарантія, рейтинг, reviews, questions, related products.
- JSON-LD у перевіреному HTML не був стабільним контрактом, тому використовувати embedded state та DOM selectors; sitemap-поділ `ep`/`mp` переносити у `offer.channel` і provenance.

## ALLO — 88/100

Складові: 24 + 18 + 18 + 15 + 13.

- Вхідні точки: `https://allo.ua/ua/products/mobile/`, `https://allo.ua/robots.txt`, `https://allo.ua/sitemap.xml`, `https://allo.ua/ua-sitemap.xml`.
- Root sitemap індексує gzip-карти products/categories/CMS. Перевірений файл — `https://allo.ua/map/secure/products/ua-sitemap1.xml.gz`; приклад legacy URL перенаправив на `https://allo.ua/ua/ru/products/details/Transcend_JetFlash_500_16GB/index.html`.
- URL товарів існують у сучасній і legacy формах; canonical після redirect — ключова частина identity. Категорійні filter routes не вважати новими категоріями без canonical.
- Поля: product code, назва, бренд, category/breadcrumbs, характеристики, варіанти, опис, media/manual URLs, seller `Алло` або marketplace seller, current/old price, stock/preorder, store/city availability, delivery/payment, bonus/credit, rating/reviews/questions.
- Structured data в окремих шаблонах відрізняється; parser спочатку шукає JSON-LD/embedded product state, потім DOM. Product і Offer зберігати окремо, seller обов'язковий.

## Hotline — 96/100

Складові: 25 + 23 + 19 + 15 + 14.

- Вхідні точки: `https://hotline.ua/ua/computer/monitory/`, `https://hotline.ua/robots.txt`, `https://hotline.ua/sitemap/sitemap_uk.xml`.
- Український sitemap розділений на gzip-файли `sitemap_uk0..7.xml.gz`: категорії, міські категорії, model/product pages; перевірені великі maps містили по 50 000 URL. Категорійна пагінація — `?p=2`.
- URL категорії — `/ua/{section}/{category}/`; model/product — `/ua/{section}-{category}/{model_slug}/` або numeric leaf. Приклад — `https://hotline.ua/ua/computer-monitory/neovo_x-19/`.
- JSON-LD `Product` і `BreadcrumbList`: `name`, `description`, `url`, `image`, `brand`, `model`, `sku`, `category`, `isRelatedTo`, `aggregateRating`, `review`, `offers.lowPrice`, `highPrice`, `offerCount`, `priceCurrency`, `availability`.
- DOM: нормалізовані характеристики, model variants, price range, shop offers, seller/shop name, seller rating, stock, delivery, warranty, offer URL, price history/related data, reviews and discussions.
- Hotline — модельний агрегатор: `ProductModel`/`Product` відділити від численних `Offer`. Shop offer URL і timestamp потрібні для кожного observation; не трактувати діапазон ціни як одну пропозицію.

## Comfy — 90/100

Складові: 24 + 24 + 15 + 15 + 12.

- Вхідні точки: `https://comfy.ua/ua/smartfon/`, `https://comfy.ua/robots.txt`, `https://comfy.ua/media/im/sitemap/sitemap.xml`; товар — `https://comfy.ua/ua/smartfon-samsung-galaxy-s26-ultra-12-512gb-black-sm-s948bzkgeuc.html`.
- URL товару — `/ua/{slug}.html`; reviews можуть мати `-otzyvy.html`; категорія використовує `?p=N`. Категорія й товар відкрилися анонімно у звичайному браузері, хоча direct HTTP повертав Cloudflare 403.
- JSON-LD `Product`, `BreadcrumbList`, `Organization`, іноді `VideoObject`. Product містить `name`, `image`, `description`, `brand`, `sku`, `offers.price`, `priceCurrency`, `availability`, shipping/return details, `aggregateRating`, повні `review[]` з автором, датою й текстом.
- DOM: code, variants, повні характеристики, current/old/personal price marker, seller, bonus, city/store stock, delivery, payment/credit, services, warranty, description, reviews/questions, media/video.
- Реалізація: JSON-LD-first у browser response; не входити заради personal price. HTTP circuit breaker не має помилково позначати сторінку приватною: окремо зберігати `http_access=blocked`, `browser_anonymous_access=ok`.

## Foxtrot — 86/100

Складові: 23 + 17 + 19 + 14 + 13.

- Вхідні точки: `https://www.foxtrot.com.ua/uk/shop/mobilnye_telefonya.html`, `https://www.foxtrot.com.ua/robots.txt`, `https://www.foxtrot.com.ua/sitemap.xml`.
- Root sitemap містить карти main/stores/regions/brands/holidays/tags/portals; product discovery додатково проходить категорії. Пагінація — `?page=2`. Приклад картки — `https://www.foxtrot.com.ua/uk/shop/smartfoniy-i-mobilniye-telefoniy-samsung-sm-a576b-galaxy-a57-5g-12512gb-dbh-awesome-navy.html`.
- URL товару — `/uk/shop/{category-and-product-slug}.html`; категорії також закінчуються `.html`, тому тип сторінки визначати breadcrumbs/structured state, не regex шляху.
- Поля: internal code/SKU, назва, бренд, category, характеристики, current/old price, stock/preorder, store/city availability, delivery, credit, cashback/promotions, rating/reviews, warranty, description, media/manuals, related products.
- JSON-LD не був стабільно виявлений у перевіреній картці; першими джерелами є embedded application state і DOM. Не вгадувати product sitemap endpoint: використовувати лише URL із robots/root index або категорій.

## MOYO — 80/100

Складові: 21 + 15 + 19 + 14 + 11.

- Вхідні точки: `https://www.moyo.ua/ua/telecommunication/smart/`, `https://www.moyo.ua/robots.txt`, human maps `https://www.moyo.ua/ua/sitemap.html` і `https://www.moyo.ua/ua/productsmap.html`.
- Root XML sitemap під час перевірки повернув 404. Категорія має `?page=2` і rel-next. Product URL — `/ua/{slug}/{numeric_id}.html`; приклад — `https://www.moyo.ua/ua/smartfon_samsung_galaxy_a17_8_256gb_black_sm-a175fzkeeuc_/658669.html`.
- Поля: numeric ID/code, name, brand, category, characteristics, variants, description, media/video/manuals, current/old price, availability, city/store pickup, delivery, payment/credit, warranty, bonus, rating, reviews/questions, seller.
- Перевірене JSON-LD стабільно містило site-level `Organization` і `WebSite`, але не гарантувало `Product`; parser спирається на embedded product state/DOM, а human products map — лише seed, не доказ повноти.

## Зведена таблиця

| Джерело | Домен | Рейтинг | Discovery v1 | Detail v1 | Анонімний стан |
|---|---|---:|---|---|---|
| AUTO.RIA | авто | 91 | sitemap + категорії | HTTP, browser для reveal | підтверджено |
| OLX Авто | авто | 85 | sitemap + категорії | Playwright fallback | підтверджено; HTTP 403 можливий |
| RST.ua | авто | 59 | категорійний crawl | legacy HTML/browser fallback | нестабільний за User-Agent |
| Automoto.ua | авто | 91 | sitemap index/gzip | HTTP + JSON-LD | підтверджено |
| Prom.ua | каталог | 95 | product/model sitemap | HTTP + JSON-LD/DOM | підтверджено |
| Rozetka | каталог | 91 | category tree | HTTP/Playwright + JSON-LD | підтверджено; HTTP challenge можливий |
| Епіцентр | каталог | 94 | розділені XML maps | HTTP + state/DOM | підтверджено |
| ALLO | каталог | 88 | XML/gzip maps | HTTP + state/DOM | підтверджено |
| Hotline | каталог | 96 | XML/gzip maps | HTTP + JSON-LD | підтверджено |
| Comfy | каталог | 90 | sitemap | Playwright + JSON-LD | підтверджено; HTTP 403 можливий |
| Foxtrot | каталог | 86 | sitemap + categories | HTTP + state/DOM | підтверджено |
| MOYO | каталог | 80 | human maps + categories | HTTP + state/DOM | підтверджено |

## Неперевірені або нестабільні аспекти

- Повнота історичного покриття кожної sitemap перевірена вибірково, а не шляхом повного завантаження всіх URL.
- Для RST.ua потрібен окремий charset/anti-empty-response fixture; XML sitemap не підтверджений як надійний канал.
- У Rozetka, OLX і Comfy потрібен тижневий low-rate smoke, щоб виміряти частку HTTP challenge і вартість browser pool.
- Анонімне розкриття контактів підтверджено на вибірковій OLX-картці. Для кожного іншого сайту adapter acceptance окремо доводить, що reveal не вимагає login/CAPTCHA; інакше поле лишається `not_public_anonymously`.
- Pagination/filter URL можуть змінюватися; canonical, next-link і yield перевіряються на кожному live smoke, а не зашиваються без fixture.
