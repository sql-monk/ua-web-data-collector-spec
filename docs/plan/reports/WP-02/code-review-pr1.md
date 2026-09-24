# WP-02 PR1 — code review

Перевірено `git diff main...wp/02-1-http-ssrf-limits` після повторного gate 2 (`cbd3fb8`).
Scope: `src/collector/fetch/**`, unit/integration fetch tests, ADR-0008 і звіти. Review read-only;
наведені сценарії відтворено окремими командами без зміни branch.

## Знахідки

| ID | Severity | Місце | Сценарій і наслідок |
|---|---|---|---|
| CR-1 | **high** | `permits.py:23`, `client.py:164,211-221` | `PgOriginPermits` видає permit на 90 с, але `FetchRequest.total_timeout` має manifest override для sitemap без верхньої межі. Fetch на 120+ с продовжує читати після `lease_expires_at`; інша replica може забрати звільнений expiry slot, тому глобальний concurrency/rate limiter R-53 більше не є верховним. `SafeFetcher` не звіряє expiry ні перед першим connect, ні на redirect/body. Потрібне fail-closed правило: жоден мережевий I/O після expiry; override, що не вкладається у lease з запасом, відхиляється до connect або permit renew-иться fenced API. |
| CR-2 | **medium** | `decoding.py:43-79,81-95,181-205` | Потокові decoder-и не мають `finish()` і `read_body()` не перевіряє завершення stream. Усічений gzip без footer повертає весь decompressed payload з `eof=False`, але fetch класифікує його як success/full; те саме можливо для незавершеного brotli/deflate. Відтворення: `gzip.compress(b'x'*1000)[:-8]` → 1000 bytes, `ZlibDecoder._obj.eof == False`, винятку немає. Це тихе прийняття пошкодженого raw body, що суперечить immutable artifact/replay і класифікації decoding error. |

## Не блокуючі спостереження

- Python regex allow/deny patterns і denylist regex виконуються без timeout. Це operator-owned
  config, але catastrophic pattern може блокувати event loop на attacker-controlled redirect URL;
  до production manifest validation варто додати safe-regex policy або bounded executor.
- `_release()` покладається на DB command timeout; окремого локального deadline немає. Lease
  зрештою fencing-ує slot, але завислий release може затримати завершення task. Перевірити під
  WP-01D `OriginPermitClient`.

## Позитивні результати

- SSRF guard перевіряє кожен hop, усі DNS answers і pin-ить TCP до перевіреної IP; env proxy,
  cookies, userinfo та non-http schemes закриті.
- F-1—F-3 gate 2 виправлено без послаблення тестів; 387 unit/security і 9 PostgreSQL
  integration tests зелені.
- Decompression має bounded 64 KiB output steps, decoded-size і ratio gates; CR-2 стосується
  саме відсутньої перевірки кінця stream.
- Логи/FetchResult використовують redaction і allowlist response headers; request headers/body
  не логуються.

## Вердикт

**changes requested** — CR-1 порушує глобальний leased limiter (R-53), CR-2 допускає silent
corruption. Після виправлення потрібні adversarial unit-тести: expired/too-short permit → 0
connect; permit, що спливає під час body → fetch припиняється; truncated gzip/deflate/brotli →
`content_decoding_error`, не success.

## Повторна перевірка

CR-1 і CR-2 виправлено без зміни попередніх adversarial-тестів:

- `SafeFetcher` до connect звіряє залишок logical fetch із `Permit.lease_expires_at` і 1-секундним
  safety margin. Замалий lease → retryable `permit_lease_too_short`, 0 connect, permit release.
  Sitemap override лишається доступним, але runtime мусить видати lease, що покриває override.
- Decoder protocol отримав `finish()`: zlib/gzip/deflate вимагають `eof`, brotli —
  `is_finished()`. Truncation → quarantined `content_decoding_error`; footer/checksum не
  ігнорується.

```text
$ uv run pytest -q -rs tests/unit/fetch
392 passed in 12.84s
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration -q -rs tests/integration/fetch
9 passed in 7.28s
$ uv run mypy src/collector/fetch
Success: no issues found in 10 source files
$ uv run pre-commit run --all-files
... усі hooks Passed
```

Фінальний verdict gate 3: **approve**. Не блокуючі спостереження лишаються owner-нотатками
для manifest validation / WP-01D permit client.
