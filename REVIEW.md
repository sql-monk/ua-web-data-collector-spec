# Критичне рев’ю технічного завдання

Дата рев’ю: 2026-09-22

Об’єкт: `TECHNICAL_SPECIFICATION.md`

Результат: усі критичні й суттєві зауваження виправлено у версії 1.1.

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

## Підсумкова перевірка узгодженості

- Scope ↔ поля: узгоджено — всі публічні поля мають доменні або extension-контракти.
- Країни ↔ мови ↔ translation provider: узгоджено — усі перелічені мови підтримані основним provider; українська не перекладається повторно.
- Архітектура ↔ функціональні вимоги: узгоджено — для кожного етапу є компонент, state і failure path.
- Джерела ↔ адаптери: узгоджено — кожне джерело має окремий owner/subpackage або доказаний стан `blocked_anonymous`.
- SLO ↔ метрики/алерти: узгоджено — окремо вимірюються crawl freshness і translation latency.
- Зберігання ↔ відтворюваність: узгоджено — raw, original, cleaned і translated artifacts immutable та пов’язані hash/version.
- Приймання ↔ тести: узгоджено — offline E2E, bounded live smoke, field coverage і multilingual QA мають числові пороги.

## Залишкові відкриті рішення

Вони не є дефектами ТЗ і мають safe default у §20: перший дослідницький сценарій, завантаження media binaries, шардінг повного каталогу, глибина backfill, retention та бюджет інфраструктури/перекладу. Source API keys не є відкритим рішенням v1.
