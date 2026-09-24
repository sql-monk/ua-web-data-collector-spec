# WP-02 PR1 — пострев'ю за ТЗ

Scope: `git diff main...wp/02-1-http-ssrf-limits`, лише PR1 картки WP-02. Перевірено ТЗ
§3, §7.1, §7.6, §10 кроки 4–6, §13, §16.1 п.1/3/8, FR-004/005/014 та REVIEW
R-16/R-33/R-38/R-53.

## Вердикт

**accept** — blocker/high/medium невідповідностей після gate 2/3 немає. Artifact upload,
robots snapshot, handler і browser fallback належать PR2/PR3 та не є пропусками PR1.

## Матриця acceptance

| Вимога PR1 | Реалізація і доказ | Статус |
|---|---|---|
| URL/origin FR-014 | `urls.py`; IDNA/default port/tracking/original tests | pass |
| Route Guard кожного hop | `guard.py`; allow/deny/global denylist, scheme/port/userinfo tests | pass |
| SSRF усіх A/AAAA і числових форм | `ssrf.py`; 100+ literal/DNS/redirect adversarial cases | pass |
| DNS pinning, SNI/Host, no env proxy | `transport.py`; fake httpcore backend і rebinding mutations | pass |
| Redirect policy, cross-origin permit | `client.py`; private/downgrade/unknown-origin/6-hop tests | pass |
| 20/100 MiB, gzip/deflate/br, ratio guard | bounded decoder + EOF validation; bomb/truncation tests | pass |
| connect/read/total timeout | HTTPX timeouts + logical deadline; sitemap override мусить вкладатися у permit lease | pass |
| Conditional GET / 304 | validators лише першого hop, no body on 304 | pass |
| Status/retry semantics | `classify.py`; table, finite retry, empty body ≠ gone | pass |
| 429 і глобальне блокування | parsed/clamped `Retry-After`; two-replica PostgreSQL test | pass |
| Leased permits R-53 | acquire before connect, lease covers logical fetch, release finally/cancel | pass |
| Media off Q-002 | header-only `media_binary_skipped` | pass |
| HTTPX / ADR-0008 | runtime deps і custom transport; Scrapy лишається forbidden | pass |
| Без silent skip | integration enforcement test і mutation evidence | pass |

## Межі й залежності

- `PgOriginPermits` тимчасовий до WP-01D PR2; tripwire тест є.
- Sitemap timeout override > 89 с потребує відповідно довшого `lease_seconds`; це fail-closed,
  а не автоматичне подовження lease без fencing.
- Manifest regex validation/timeout і bounded DB release — residual notes code/security review;
  owner наступних wiring PR — WP-02 PR2 / WP-01D.
- Linux unit та clean-host Docker підтверджує GitHub CI перед merge.

Локальний доказ після останніх виправлень: 392 unit/security tests, 9 PostgreSQL integration,
mypy strict і всі pre-commit hooks зелені.

Після merge актуального `main` виконано також повний no-stack gate:
`3079 passed, 1 skipped, 326 deselected` за 5:37; єдиний skip — платформний Windows-тест
loopback поза WP-02. Ruff, format, mypy (99 source files) і всі pre-commit hooks — green.
