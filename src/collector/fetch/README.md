# Fetch core (`collector.fetch`)

Owner: WP-02. Спільний fetch layer §7.1: жоден адаптер не створює власного HTTP-клієнта (§11).
HTTP-клієнт — `httpx` згідно з [ADR-0008](../../../docs/decisions/0008-fetch-core-on-httpx-not-scrapy.md)
(«fetch core на HTTPX, не Scrapy»): `httpx.AsyncClient` у тому самому event loop, що
`WorkerRuntime`, власний `httpcore` network backend для DNS pinning, `trust_env=False`,
`follow_redirects=False`. Відхилення від ADR-0008 — лише через оркестратора.

Статус: PR1 і PR2 реалізовані (HTTP/SSRF/ліміти, artifact store, upload claims, robots,
`FetchHandler`); browser worker — PR3.

## Модулі

| Модуль | Що робить |
|---|---|
| `urls.py` | `normalize_url` (FR-014) і `normalize_origin` — **єдиний** ключ `origin_rate_buckets.origin` |
| `guard.py` | `RouteGuard`: лише `http`/`https`, порти 80/443 (override), userinfo заборонено, allow/deny regex з manifest, `GlobalDenylist` з файлу (mtime-reload, fail closed) |
| `ssrf.py` | канонізація числових host-ів, заборонені імена/адреси, `resolve_pinned` (один резолв на hop) |
| `transport.py` | `PinnedNetworkBackend` (TCP лише на перевірену IP), `PinnedTransport` |
| `decoding.py` | потокове декодування gzip/deflate/br з обмеженим виходом, ліміти і ratio-guard |
| `classify.py` | `classify_status`/`classify_error` → `FetchDecision`; `parse_retry_after`; `plan_retry` |
| `permits.py` | Protocol `OriginPermits`, `Permit`/`Denied`, тимчасовий `PgOriginPermits` (O-1) |
| `client.py` | `SafeFetcher.fetch(FetchRequest) -> FetchResult` |
| `config.py` | `FetchConfig.from_env` |
| `robots.py` | versioned robots snapshots, TTL і policy `diagnostic`/`ignore`/`respect` |
| `metrics.py` | bounded in-process counters; експорт належить WP-12 |
| `handler.py` | preflight, conditional GET, robots, raw upload, fetch lineage і наступний job |

## Robots snapshot

Job із `request_kind="robots"` завжди нормалізує URL до `/robots.txt`, використовує той самий
SSRF-клієнт і global origin permit, що й page fetch, та зберігає відповідь як raw artifact без
`parse.raw`. `COLLECTOR_ROBOTS_TTL_SECONDS` типово дорівнює 86400; свіжа версія не запитується
повторно. HTTP 404 фіксується як snapshot «відсутній», а 5xx не витісняє попередню версію.

`robots_policy=diagnostic` (default) і `ignore` не блокують page fetch. `respect` перед page
fetch оновлює прострочений snapshot і застосовує правила до стабільного User-Agent. Заборонений
URL не запитується: у `fetches` пишеться `policy_blocked`, а рішення спирається на immutable raw
snapshot. Невідоме значення policy завершує job як `robots_policy_invalid` без мережевого I/O.

## Метрики

`FetchMetrics` накопичує `http_requests_total{source,status_class}`, `http_429_total`,
`policy_blocks_total`, `raw_bytes_total`, numerator/denominator для `raw_dedup_ratio` та
`artifact_orphans_total`. URL і origin не є labels. WP-12 підключає exporter до цього API.

## Як викликати

```python
fetcher = SafeFetcher(
    config=FetchConfig.from_env(),
    permits=PgOriginPermits(sessions, owner_instance=worker_instance_id),
    guard=RouteGuard(
        allowed_patterns=manifest_allowed,
        denied_patterns=manifest_denied,
        denylist=GlobalDenylist(config.denylist_file),
    ),
)
result = await fetcher.fetch(
    FetchRequest(url, request_kind="page", job_id=task.job_id, if_none_match=etag)
)
```

`fetch()` не кидає винятків для мережевих/політичних відмов — усе в `result.decision`
(`outcome`, `content_access`, `error_code`, `reason`, `retry_after`, `route_incident`,
`block_origin`, `quarantine`); `result.denied` — відмова limiter-а (PR2 → `TaskResult.deferred`).
`asyncio.CancelledError` і непередбачені винятки пробрасуються; permits звільняються у `finally`.

## SSRF-модель (§13)

Кожен hop (початковий запит і кожен redirect, ≤ 5):

1. Route Guard: scheme, userinfo (`url_userinfo_forbidden`), порт, глобальний denylist, patterns.
2. Host: числові форми IPv4 (`2130706433`, `0177.0.0.1`, `0x7f.1`, `127.1`) канонізуються;
   неканонічна форма навіть публічної IP — `host_noncanonical_ip`; `localhost`, `*.localhost`
   і trailing dot — блок за іменем.
3. Один резолв усіх A/AAAA; блок, якщо **хоча б одна** адреса не глобальна: loopback,
   private/ULA, link-local, CGNAT, unspecified, multicast, reserved, broadcast, cloud metadata,
   IPv4-mapped/compatible, NAT64/6to4 з вбудованою забороненою IPv4, scoped IPv6. Docker-імена
   (`minio`, `postgres`) резолвляться у 172.16/12 → блок через private.
4. Pin перевіреної IP у network backend: TCP відкривається лише на неї, SNI і `Host` —
   оригінальне ім'я; повторного резолву бібліотекою немає (DNS rebinding).
5. Permit origin-а (новий origin — новий permit; bucket відсутній → `redirect_origin_unknown`,
   на першому hop — `origin_unknown`). Downgrade `https→http` — `redirect_downgrade`.

Guard/SSRF-відмова → `error_code="policy_blocked"`, конкретна причина — `reason`.

## Ліміти й timeout

- Body 20 МБ **розпакованих** байтів (`COLLECTOR_FETCH_MAX_BODY_BYTES`); `Content-Length` понад
  ліміт — відмова без читання; перевищення в потоці — обрив з'єднання, `body_too_large`,
  `quarantine=True`. Ratio-guard: розпаковано/отримано > 200 після 1 МБ → `decompression_bomb`.
- Sitemap: 100 МБ після розпакування (`COLLECTOR_FETCH_MAX_SITEMAP_BYTES`), зокрема `.xml.gz`
  як `application/gzip` без `Content-Encoding`; raw bytes зберігаються стиснутими.
- Media (`image/*`, `video/*`, `audio/*`) при `COLLECTOR_FETCH_MEDIA_BINARIES=0` (default, Q-002)
  — обрив після заголовків, `media_binary_skipped`, `content_access=metadata_only`.
- Timeout: connect 10 с, read 30 с, total 60 с на весь fetch. Sitemap override дозволений,
  лише якщо виданий permit lease покриває весь override із запасом 1 с; інакше fetch повертає
  retryable `permit_lease_too_short` до TCP connect.
- Кожен gzip/deflate/brotli stream мусить завершитися валідним EOF/footer; truncation дає
  quarantined `content_decoding_error`, навіть якщо decoder уже видав видимий текст.

## Класифікація і retry (§10, §5.5)

| Відповідь | outcome | content_access | error_code |
|---|---|---|---|
| 2xx з body | success | full (206 — partial) | — |
| 304 | success (`not_modified`) | unknown | — |
| 2xx з порожнім body | retryable | unknown | `empty_body` (не `gone`, не видалення) |
| 408/425/500/502/503/504, network, DNS, timeout | retryable | unknown | `http_<n>`/`network_error`/`dns_error`/`timeout` |
| 429 | retryable + `block_origin` | unknown | `http_429` |
| 401/403 | permanent + `route_incident` | blocked | `http_401`/`http_403` |
| 404/410 | permanent | gone (лише у `fetches`, без `entity_lifecycle`) | `http_404`/`http_410` |
| guard/SSRF | permanent | unknown | `policy_blocked` |

`plan_retry`: максимум 4 спроби, backoff 5 с / 30 с / 2 хв (+ jitter до 20 %; крок 10 хв —
лише якщо `max_attempts` > 4), не менше за `Retry-After`; після 4-ї спроби або permanent — dead
letter. Виконання (`not_before`) — PR2 через runtime WP-01D і `queue.retry` WP-01A. `Retry-After`: секунди або HTTP-date, clamp
`[5 с, 24 год]`, відсутній/сміттєвий/від'ємний → 10 хв.

## Anonymous-only (Q-007) і логи

Стабільний `User-Agent` (`COLLECTOR_FETCH_USER_AGENT`, типово `UAWebDataCollector/<version>`),
жодних cookies між запитами, жодного `Authorization`. Логи — redacted URL, origin, IP, статус;
заголовки не логуються; `FetchResult.headers` містить лише безпечний перелік.

## Тимчасовий адаптер permits (O-1)

`PgOriginPermits` — окремі короткі транзакції над `limiter.acquire_permit`/`release_permit`/
`block_origin` WP-01A. Прибрати після WP-01D PR2 (`OriginPermitClient`); вартовий —
`tests/unit/fetch/test_permits_adapter_tripwire.py`.

Для manifest `total_timeout=N` runtime створює `PgOriginPermits(..., lease_seconds>N+1)`.
Автоматично продовжувати lease під час активного HTTP без fencing заборонено.
