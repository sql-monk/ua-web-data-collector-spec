# ADR-0008: Fetch core будується на HTTPX, Scrapy не використовується

| Поле | Значення |
|---|---|
| Date | 2026-09-24 |
| Owner | WP-02 |
| Status | accepted |

## Context

§8 ТЗ (версія 1.4) називав Scrapy 2.13.x «основним HTTP crawler» для fetch layer, а
`tests/unit/test_foundation_config.py::FORBIDDEN_FOUNDATION_DEPS` водночас забороняв `httpx` як
runtime-залежність фундаменту — картка WP-02 (розділ O-2 «Scrapy vs HTTPX, конфлікт із §8»)
зафіксувала це як суперечність, яку треба вирішити до старту PR1.

Fetch core WP-02 має три вимоги, які Scrapy не покриває без істотної переробки його моделі
виконання:

1. **SSRF-guard і DNS pinning на кожному redirect hop.** Scrapy резолвить DNS і виконує
   redirect-и через Twisted-реактор і власний downloader middleware; щоб перевіряти resolved IP
   на приватність/loopback/link-local перед кожним TCP-з'єднанням (а не лише на початковому
   hostname), потрібен контроль над transport-рівнем HTTP-клієнта. `httpx` дозволяє підмінити
   `httpcore` transport (custom `AsyncHTTPTransport`), який резолвить DNS сам і перевіряє IP до
   встановлення з'єднання — і повторює цю перевірку на кожному hop redirect-а, бо `httpx` не
   слідує redirect-ам всередині transport-у автоматично.
2. **Потоковий обрив body.** Ліміти 20 МБ (сторінка) і 100 МБ (sitemap) вимагають читання
   response body чанками з обривом з'єднання, щойно ліміт перевищено, без буферизації повного
   тіла в пам'яті. `httpx.AsyncClient.stream()` дає прямий контроль над цим циклом в тому самому
   coroutine, що виконує fetch; Scrapy буферизує тіло через власний `Response` і
   `DOWNLOAD_MAXSIZE` перериває **після** отримання, а не в процесі читання чанка.
3. **Інтеграція з WorkerRuntime (WP-01D) і PostgreSQL origin limiter (WP-01A/WP-01D, R-53).**
   `WorkerRuntime` (ADR-0006) керує lease, self-fencing, drain і heartbeat через `asyncio` event
   loop одного процесу; глобальний limiter concurrency/rate за origin (`docs/decisions/0005-postgres-queue-and-outbox.md`)
   видає permit через ту саму сесію PostgreSQL, яку тримає runtime. Twisted-реактор Scrapy — окремий
   event loop, що виконується паралельно з `asyncio` (через `asyncio.SelectorEventLoop`
   adapter або окремий процес): scheduler, throttling (`AutoThrottle`) і concurrency control
   Scrapy дублюють ту саму відповідальність, яку вже несе limiter і runtime, і два незалежні
   планувальники конкурентних запитів до того самого origin не composable — Scrapy не знає про
   permit з PostgreSQL, і limiter не бачить внутрішню чергу Scrapy.

## Decision

Fetch core (`src/collector/fetch/`) будується на `httpx.AsyncClient` (async, у тому самому
event loop, що `WorkerRuntime`) як єдиному HTTP-клієнті. Scrapy не використовується в жодному
компоненті fetch layer. Ключові властивості клієнта:

- custom `httpcore`-based transport для DNS pinning і перевірки resolved IP на кожному
  redirect hop (SSRF-guard), `trust_env=False` (не довіряти системним proxy/env);
- ручна обробка redirect-ів (по одному hop за раз через сам `httpx`-transport), а не вбудований
  `follow_redirects` без перевірки;
- `stream()` для читання тіла response чанками з обривом при перевищенні ліміту розміру (20 МБ
  сторінка, 100 МБ sitemap);
- concurrency і rate керуються зовнішнім PostgreSQL origin limiter (WP-01A/WP-01D, R-53) через
  permit/lease, а не внутрішнім throttling клієнта; retry — табличний backoff §10, керований
  `WorkerRuntime`/`queue.retry`, а не middleware Scrapy.

## Consequences

- **Sitemap spider Scrapy не використовується.** Парсинг sitemap (включно з лімітом 100 МБ і
  streaming XML) реалізує WP-03 власним кодом на базі того самого `httpx`-клієнта fetch core.
- **`AutoThrottle` Scrapy не використовується.** Адаптивний rate limiting замінює глобальний
  PostgreSQL origin limiter (WP-01A/WP-01D, `docs/decisions/0005-postgres-queue-and-outbox.md`, R-53) — рішення про
  дозвіл наступного запиту приймається централізовано для всіх worker-інстансів одного origin,
  а не per-process, як у `AutoThrottle`.
- **Retry-механізм Scrapy (`RetryMiddleware`) не використовується.** Табличний backoff §10
  (5 с / 30 с / 2 хв / 10 хв) реалізує WP-02 через `queue.retry`/`TaskResult.not_before`, під
  контролем `WorkerRuntime` (WP-01D) — задокументовано в `WP-02-to-WP-01D.md`.
- **Middleware ecosystem Scrapy (proxy rotation, cookies, item pipelines тощо) недоступна.**
  Функціонал, який потрібен fetch core, реалізується явно в `src/collector/fetch/` (SSRF-guard,
  conditional GET/ETag, artifact upload) — без generic middleware-шару; те, що не потрібно
  (proxy rotation, cookies persistence поза Playwright-сесією), свідомо не переноситься.
- **Нові залежності.** `httpx` (async HTTP client) — знімається з `FORBIDDEN_FOUNDATION_DEPS`
  у `tests/unit/test_foundation_config.py` dependency-запитом WP-02 → WP-00
  (`WP-02-to-WP-00.md`, пункт 1); `scrapy`/`psycopg` лишаються забороненими. S3-клієнт
  (`aiobotocore` чи `minio`) — окреме рішення оркестратора, розділ O-3 картки WP-02, не
  фіксується цим ADR.
- **§8 ТЗ виправлено.** Рядок «Scrapy 2.13.x — основний HTTP crawler» замінено на HTTPX як
  основний HTTP-клієнт fetch core, з посиланням на цей ADR (§8, редакція від 2026-09-24).

## Related

- ТЗ: §8 (технології), §10 (retry/backoff таблиця), §13 (SSRF-модель, лімітер origin), §20
  (формат ADR).
- Картка: `docs/plan/cards/WP-02.md` (O-2 «Scrapy vs HTTPX, конфлікт із §8»,
  `WP-02-to-WP-00.md`, `WP-02-to-WP-01A.md`, `WP-02-to-WP-01D.md`).
- Пов'язані рішення: `docs/decisions/0005-postgres-queue-and-outbox.md` (глобальний PostgreSQL origin limiter, R-53),
  `docs/decisions/0006-worker-lease-fencing-and-liveness.md` (WorkerRuntime lease, self-fencing, drain).
