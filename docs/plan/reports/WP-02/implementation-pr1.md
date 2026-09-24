# WP-02 PR1 — URL/origin, Route Guard, SSRF-клієнт, ліміти, retry decisions, permits

Branch: `wp/02-1-http-ssrf-limits` (worktree `.worktrees/wp-02-1`, від `main` `626b7e4`).
Картка: `docs/plan/cards/WP-02.md`, розділ PR1, «Передумови», «Рішення оркестратора» (U-1, O-1,
O-4). HTTP-клієнт — `httpx` згідно з ADR-0008
(`docs/decisions/0008-fetch-core-on-httpx-not-scrapy.md`); ТЗ §3, §5.5, §10 кроки 4–6, §13;
REVIEW R-16, R-33, R-38, R-53.

Коміти:

```text
7f5e874 test(wp-02): cookie jar of the fetch client rejects every cookie
7b469bc fix(wp-02): redact URL-bearing response headers in FetchResult
9a0928a docs(wp-02): ruff-format README example
9265ad1 docs(wp-02): fetch core README draft (SSRF model, limits, retry table, ADR-0008)
cc3404a test(wp-02): PostgreSQL limiter integration for SafeFetcher permits; skip-to-fail enforcement
c9193e8 test(wp-02): offline unit/security tests for fetch core (SSRF, redirects, limits, permits)
ac20ede feat(wp-02): SSRF-guarded HTTPX fetch core with DNS pinning, body limits, retry decisions, permits
eaffc71 build(wp-02): add httpx/httpcore/brotli runtime deps; drop httpx from FORBIDDEN_FOUNDATION_DEPS
```

## Що зроблено

Публічний API (`collector.fetch.*`): `SafeFetcher.fetch(FetchRequest) -> FetchResult`,
`FetchConfig.from_env`, `normalize_url`/`normalize_origin`, `RouteGuard`/`GlobalDenylist`,
`resolve_pinned`/`Resolver`/`SystemResolver`/`PolicyBlocked`, `classify_status`/`classify_error`/
`FetchDecision`/`parse_retry_after`/`plan_retry`/`RetryPlan`, Protocol `OriginPermits` +
`Permit`/`Denied` + тимчасовий `PgOriginPermits`. Опис — `src/collector/fetch/README.md`.

| Вимога PR1 | Реалізація | Тест |
|---|---|---|
| 1. URL (FR-014), `normalize_origin` — єдиний ключ bucket-а | `fetch/urls.py`: lowercase scheme/host, IDNA (uts46; ASCII-host з `_` — lowercase fallback), без default port/fragment/tracking (ключі з `collector.contracts.identity`), порядок і кодування решти query збережено; userinfo → `UrlError(url_userinfo_forbidden)` | `tests/unit/fetch/test_urls.py` (у т.ч. `HTTP://Example.ORG:80/x` → `http://example.org`, `original` незмінний) |
| 2. Route Guard | `fetch/guard.py::RouteGuard`: лише `http`/`https`, порти 80/443 (override `allowed_ports`), allow/deny regex з manifest на нормалізованому URL, `GlobalDenylist` (O-4: host+subdomains або `re:`; mtime-reload; налаштований, але відсутній файл → fail closed `global_denylist_unavailable`); викликається на кожному hop | `test_guard.py` (patterns, порти, denylist, reload за mtime, fail closed, denylist на redirect hop до DNS, `ws/data/javascript/chrome/ftp/file`) |
| 3. SSRF | `fetch/ssrf.py`: канонізація числових IPv4 (decimal/octal/hex/скорочена; WHATWG «останній label — число»), некоректний числовий host → `url_invalid`, **неканонічна форма навіть публічної IP** → `host_noncanonical_ip`; `forbidden_reason`: metadata, embedded IPv4 у mapped/6to4/NAT64, IPv4-compatible `::/96`, scoped IPv6, unspecified/loopback/link-local/multicast/private/reserved/not-global; `localhost`, `*.localhost`, будь-який trailing dot — за іменем | `test_ssrf.py::test_forbidden_target_is_policy_blocked_without_connect` — 32 параметризовані кейси (усі з картки + `[::7f00:1]`, `224.0.0.1`, `255.255.255.255`, `example.org.`, `postgres:5432`, `1.2.3.4.5`, неканонічний hex публічної IP), кожен: `policy_blocked`, **0 connect**, 0 permits; класифікатор — окремі таблиці |
| 4. DNS pinning, `trust_env=False` | `fetch/transport.py`: `PinnedNetworkBackend` (connect лише на pin `(host, port) → IP`, без pin → `ConnectError`, unix sockets заборонені), `PinnedTransport` — `httpx.AsyncHTTPTransport(trust_env=False)` з пулом httpcore на цьому backend (keepalive 0 — нове з'єднання на hop); `AsyncClient(trust_env=False, follow_redirects=False)` | `test_ssrf.py::test_dns_rebinding_*` (резолв рівно один на hop; connect на перевірену IP; SNI/`Host` — оригінальне ім'я; rebinding на redirect того ж host блокується), `test_transport.py` (пул справді на нашому backend-і — вартовий оновлення httpx), `test_client.py::test_env_proxy_is_ignored` |
| 5. Redirects вручну, ≤ 5 hop | `client.py::_run`: кожен hop — guard → резолв/SSRF → pin → permit нового origin-а; `redirect_origin_unknown` (bucket відсутній, без запиту); `redirect_downgrade`; `too_many_redirects`; відносний/protocol-relative `Location` через `urljoin` (dot-segments), fragment прибирається | `test_redirects.py`: 7 заборонених цілей × 5 статусів, ланцюг public→public→10.0.0.1 (2 запити), `..`, 6 hop → 6 запитів і `too_many_redirects`, 5 hop — проходять, downgrade, cross-origin без bucket, permit нового origin-а, same-origin — той самий permit, denied origin на hop |
| 6. Ліміти body | `fetch/decoding.py`: власне декодування gzip (multi-member)/deflate (zlib і raw)/br з виходом ≤ 64 КБ на крок (`max_length`, brotli>=1.2 `output_buffer_limit`), лічильник розпакованих байтів, окремий ліміт отриманих байтів, ratio-guard (>200 після 1 МБ → `decompression_bomb`); `Content-Length` > ліміту → відмова без читання; sitemap: gzip entity → стиснуті bytes зберігаються, ліміт 100 МБ після розпакування | `test_limits.py`: 20 МБ+ потік без `Content-Length` (respx; генератор ≤ 21 чанк із 100), рівно 20 МБ / +1 байт, `Content-Length: 30000000` (лише заголовки прочитано), gzip-bomb 1 МБ → 1 GiB і brotli-bomb 1 GiB (з ratio-guard і без; `tracemalloc` peak < 48 МБ; потік обірвано), `gzip, gzip` (декод і bomb), sitemap `.xml.gz` рівно 100 МБ → ok (bytes стиснуті), 100 МБ+1 → `body_too_large` |
| 7. Timeout | `httpx.Timeout(read=30, connect=10)`; total 60 с — `asyncio.timeout` на весь fetch + перевірка дедлайну інʼєктованим годинником перед кожним hop і кожним чанком; override `FetchRequest.total_timeout` (manifest, sitemap) | `test_slow_drip_hits_total_timeout_with_simulated_clock` (без реального очікування), `test_hanging_stream_hits_asyncio_total_timeout` (0.05 с), `test_sitemap_total_timeout_override_from_manifest` |
| 8. Conditional GET | `If-None-Match`/`If-Modified-Since` лише на першому hop; 304 → `decision.not_modified`, body не читається | `test_client.py::test_validators_*`, `test_200_with_validators_*`, `test_no_validators_*` |
| 9. Класифікація і retry | `fetch/classify.py`: `classify_status`/`classify_error` → `FetchDecision(outcome, content_access, error_code, reason, retry_after, route_incident, block_origin, browser_candidate, quarantine, not_modified)`; `plan_retry`: 4 спроби, 5 с/30 с/2 хв/10 хв + jitter ≤ 20 %, не менше `Retry-After`, далі dead letter | `test_classify.py` (таблиця 21 статусу + 10 помилок, 200/`b""` ≠ `gone`, 404 без lifecycle-поля, скінченність до 50 спроб, permanent → dead letter) |
| 10. 429/`Retry-After` | `parse_retry_after`: секунди/HTTP-date, default 10 хв, clamp `[5 с, 24 год]`; 429 → `permits.block(origin, now + delay, "http_429")` рівно раз | `test_classify.py::test_retry_after_parsing_and_clamp` (11 кейсів), `test_client.py::test_429_blocks_origin_once_with_clamped_until` (5 кейсів картки); integration — нижче |
| 11. Permits (R-53, O-1) | `fetch/permits.py`: Protocol `OriginPermits`, `PgOriginPermits` (окремі короткі транзакції, `release_permit(owner_instance=…)`, `block_origin(actor=owner)`, bucket відсутній → `Denied("unknown_origin")`); `SafeFetcher` бере permit до connect, звільняє у `finally` через `asyncio.shield`, помилка release не маскує результат | `test_client.py` (Denied → 0 викликів respx і 0 connect; порядок acquire→request; виняток у body, network error, `CancelledError` → release; збій release); integration `tests/integration/fetch/test_permits_pg.py` (7 тестів); вартовий `test_permits_adapter_tripwire.py` |
| 12. Q-002 media | `image/*`, `video/*`, `audio/*` при `COLLECTOR_FETCH_MEDIA_BINARIES=0` → обрив після заголовків, `media_binary_skipped`, `success/metadata_only`, зберігаються тип/заголовки | `test_media_binary_is_skipped_after_headers`, `test_media_binary_is_read_when_enabled` |
| 13. ADR-0008 | `httpx.AsyncClient` + власний network backend, `trust_env=False`, `follow_redirects=False`; відхилень не виявлено (заміна приватного `_pool` у transport — див. «Ризики») | README, `test_transport.py` |
| 14. `httpx` поза `FORBIDDEN_FOUNDATION_DEPS` | `tests/unit/test_foundation_config.py`: `("scrapy", "psycopg")` з коментарем-посиланням на ADR-0008, гілка `if forbidden != "httpx"` прибрана; `pyproject.toml` — `brotli>=1.2`, `httpcore>=1.0.9`, `httpx>=0.28` у `[project.dependencies]` з коментарем «WP-02», `httpx` прибрано з dev-групи | `tests/unit/test_foundation_config.py` (27 passed) |

Спільні вимоги картки:

- **Мережа лише mock.** `tests/unit/fetch/conftest.py` — autouse-фікстура підміняє
  `socket.getaddrinfo` і `asyncio.BaseEventLoop.getaddrinfo` на функцію, що кидає
  (`test_system_resolver_is_blocked_in_unit_tests`). DNS — `FakeResolver`, TCP — `FakeNetwork`
  (`tests/fixtures/fetch/fetch_fakes.py`): `httpcore.AsyncNetworkBackend` без сокетів, сирі
  HTTP/1.1 відповіді розбирає справжній h11, записуються `(ip, port)` і SNI. HTTP-семантика —
  також `respx`.
- **Тест проти мовчазного skip.** `tests/integration/fetch/conftest.py` — під
  `COLLECTOR_TEST_REQUIRE_DOCKER=1` будь-який skip у каталозі стає fail
  (`pytest_runtest_makereport`); `tests/integration/fetch/test_suite_is_enforced.py`. Доведено
  нижче (тимчасовий `pytest.skip` → FAILED; Docker недоступний → ERROR).
- **Anonymous-only / секрети.** Жодних cookies (запит будується без merge cookies, jar відхиляє
  все), жодного `Authorization`, UA стабільний; логи — `redact()`-ований URL, origin, IP, статус;
  `FetchResult.headers` — безпечний перелік, значення через `redact()`
  (`test_logs_and_result_do_not_leak_secrets`, `test_safe_headers_are_redacted`,
  `test_no_cookies_no_authorization_and_stable_user_agent`).

Мутаційна перевірка (копія HEAD у scratch, мутація — одна зміна, прогін відповідного модуля):

| Мутант | Результат |
|---|---|
| M1 `ssrf.resolve_pinned` перевіряє лише першу DNS-адресу | `test_mixed_dns_answer_is_blocked_not_filtered` FAILED (1 failed, 69 passed) |
| M2 прибрано ліміт розпакованих байтів | 4 failed у `test_limits.py` (bomb без ratio-guard, `gzip, gzip`, sitemap 100 МБ+1) |
| M3 прибрано перевірку total deadline на чанку | `test_slow_drip_hits_total_timeout_with_simulated_clock` FAILED |
| M4 прибрано release permits у `finally` | 9 failed у `test_client.py` |
| M5 прибрано no-cookie jar | 0 failed — еквівалентний мутант: запит і так будується без merge cookies (другий шар); jar окремо перевіряє `test_client_cookie_jar_rejects_every_cookie` |
| M0 (груба) redirect hop без guard/SSRF | 39 failed із 44 у `test_redirects.py` |

## Команди та вивід

`uv sync --frozen`:

```text
Checked 67 packages in 5ms
```

`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` (HEAD `7f5e874`):

```text
All checks passed!
294 files already formatted
Success: no issues found in 87 source files
```

`uv run pytest tests/unit/fetch` (Windows, HEAD `7f5e874`):

```text
........................................................................ [ 28%]
........................................................................ [ 57%]
........................................................................ [ 86%]
...................................                                      [100%]
251 passed in 7.28s
```

Той самий прогін у Linux-контейнері (`git archive HEAD | docker run -i python:3.13-slim@sha256:8d9d0b8b… sh -c '… uv sync --frozen && uv run pytest tests/unit/fetch'`, pytest-socket на Linux блокує навіть loopback):

```text
Linux 6.18.33.2-microsoft-standard-WSL2
........................................................................ [ 86%]
...................................                                      [100%]
251 passed in 8.93s
```

`COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/fetch -rs` (testcontainers PostgreSQL 18, loopback):

```text
.........                                                                [100%]
9 passed in 108.02s (0:01:48)
```

Без прапорця — enforcement-модуль сам пропускається:

```text
.......ss                                                                [100%]
SKIPPED [1] tests\integration\fetch\test_suite_is_enforced.py:33: перевірка діє лише там, де Docker обіцяний (COLLECTOR_TEST_REQUIRE_DOCKER=1)
SKIPPED [1] tests\integration\fetch\test_suite_is_enforced.py:39: перевірка діє лише там, де Docker обіцяний (COLLECTOR_TEST_REQUIRE_DOCKER=1)
7 passed, 2 skipped in 49.85s
```

Доказ skip → fail (тимчасовий модуль із `pytest.skip("forgotten")`, потім видалений; і
недоступний Docker):

```text
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/fetch/test_tmp_skip.py -q
FAILED tests/integration/fetch/test_tmp_skip.py::test_x - COLLECTOR_TEST_REQU...
1 failed in 0.03s
$ uv run pytest -m integration tests/integration/fetch/test_tmp_skip.py -q
1 skipped in 0.03s
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 DOCKER_HOST=tcp://127.0.0.1:1 uv run pytest -m integration tests/integration/fetch -q
ERROR tests/integration/fetch/test_permits_pg.py::test_block_until_matches_retry_after
ERROR tests/integration/fetch/test_suite_is_enforced.py::test_postgres_is_reachable_so_suite_runs
1 passed, 8 errors in 6.02s
```

`uv run pytest -m "not live"` (Windows; запущено на HEAD `9a0928a` — до двох останніх комітів,
які додали лише редакцію заголовків і два unit-тести; обидва покриті прогоном `tests/unit/fetch`
вище; включає регресію `tests/integration/postgres` і `tests/integration/scaling`):

```text
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:191: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:207: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:217: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:225: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:237: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [4] tests\e2e\test_gui_runtime_contract.py:245: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:263: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:278: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:296: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:302: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_gui_runtime_contract.py:310: gui не відповідає на http://127.0.0.1:80 — підніміть `COMPOSE_PROFILES=core,workers,gui docker compose up -d --wait`
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:49: перевірка діє лише там, де стек обіцяний (COLLECTOR_E2E_REQUIRED=1 — крок job `docker` після `up -d --wait`)
SKIPPED [1] tests\e2e\test_runtime_suite_is_enforced.py:62: перевірка діє лише там, де стек обіцяний (COLLECTOR_E2E_REQUIRED=1 — крок job `docker` після `up -d --wait`)
SKIPPED [1] tests\integration\fetch\test_suite_is_enforced.py:33: перевірка діє лише там, де Docker обіцяний (COLLECTOR_TEST_REQUIRE_DOCKER=1)
SKIPPED [1] tests\integration\fetch\test_suite_is_enforced.py:39: перевірка діє лише там, де Docker обіцяний (COLLECTOR_TEST_REQUIRE_DOCKER=1)
SKIPPED [1] tests\unit\test_network_blocked.py:27: Windows: loopback потрібен asyncio
1324 passed, 25 skipped, 10 warnings in 2965.69s (0:49:25)
exit=0
```

(25 skipped — gui e2e без піднятого стека і прапорцеві enforcement-модулі; усі наявні до PR1,
крім двох нових enforcement-тестів fetch. Вивід перекодовано з cp1251 консолі Windows через
`iconv`, інших змін немає.)

`uv run pre-commit run --all-files`:

```text
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
check yaml...............................................................Passed
check toml...............................................................Passed
check for added large files..............................................Passed
check for merge conflicts................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
ruff format..............................................................Passed
Detect hardcoded secrets.................................................Passed
markdownlint-cli2........................................................Passed
```

Команди картки, що в PR1 не виконувались: `docker compose config/build/up/down` і
`--scale fetch-worker=4` — PR1 не змінює compose/image і handler не реєструє (compose-стек за
інструкцією не піднімався).

## Що не перевірено

- **Реальний TLS/TCP/DNS** — `not testable offline`: мережа в тестах заборонена; реальний
  `httpcore.AnyIOBackend` і `SystemResolver` виконуються лише в runtime. Pinning перевірено на
  рівні `connect_tcp(ip, port)` і `start_tls(server_hostname)` fake backend-а зі справжнім h11 —
  саме це визначає, куди піде TCP.
- **Integration під LOGIN-роллю `collector_fetcher`** — не в PR1 (тести permits ідуть під
  superuser `pg_sessions`); за карткою це тест «Ролі §13» PR2.
- **Мутаційний прогін тестером** і security-review — етапи gate, не реалізатора.
- CI (Linux, повний `-m "not live"`) — не запускався; Linux-паритет перевірено лише для
  `tests/unit/fetch`.

## Ризики

| Ризик | Статус / пропозиція |
|---|---|
| **Розмір PR:** ~969 рядків продуктивного коду (без порожніх, коментарів і docstring; після `ruff format`) проти орієнтиру «≤ ~800» картки | open — рішення gate/оркестратора. Якщо треба різати: PR1a = `urls`/`guard`/`ssrf`/`transport`/`decoding` (~455), PR1b = `classify`/`permits`/`client`/`config` (~514) |
| `PinnedTransport` замінює приватний `AsyncHTTPTransport._pool` (httpx 0.28 не має параметра `network_backend`) | mitigated: `test_transport.py::test_transport_pool_uses_pinned_backend` червоніє, якщо оновлення httpx зламає підміну; `httpx`/`httpcore` pinned у `uv.lock` |
| Lease `PgOriginPermits` = 90 с; при manifest override `total_timeout` для sitemap > 90 с lease спливе посеред fetch і limiter видасть ще один slot | open → PR2: handler має створювати `PgOriginPermits(lease_seconds ≥ total_timeout + запас)` або передавати lease на запит (у Protocol O-1 параметра немає) |
| ADR-0002 / борг #6 HANDOFF (unfixed HIGH CVE zlib у base image): PR1 — перший код, що розпаковує недовірені gzip/deflate body | сигнал security-reviewer-у (тригер перегляду ADR-0002 — merge WP-02 PR1). Пом'якшення в коді: вихід zlib обмежений 64 КБ на крок, ліміт і ratio-guard |
| `brotli` без stubs | `# type: ignore[import-untyped]` з причиною у `decoding.py` (і в тесті) |
| Суворіше за картку: будь-який host із trailing dot і неканонічна числова форма навіть публічної IP блокуються | свідомо (різночитання парсерів = обхід guard-а); легітимні джерела так не посилаються |
| `FetchResult.requested_url`/`final_url`/`hops[].url` — redacted | PR2 бере оригінальний URL із job args; якщо потрібен нередагований final URL для provenance — окреме поле з рішенням security-review |

Рішення в межах картки, які варто підтвердити spec-review:

- guard/SSRF → `error_code="policy_blocked"` + конкретний `reason`; redirect-відмови мають власні
  коди (`redirect_downgrade`, `redirect_origin_unknown`, `too_many_redirects`), як у картці;
- `media_binary_skipped` → `success` + `content_access=metadata_only`; 304/429/policy →
  `content_access=unknown`; 451 → `blocked`; 206 → `partial`;
- permit — один на origin у межах fetch: same-origin redirect не бере другого rate token,
  cross-origin — бере permit нового origin-а (п.5 картки);
- `plan_retry` з `max_attempts=4` використовує кроки 5 с/30 с/2 хв; крок 10 хв діє лише при
  `max_attempts > 4` (у таблиці §10 чотири затримки на чотири спроби — трактування).

## Як вимкнути або відкотити

- Код PR1 ще ніде не викликається runtime-ом (handler — PR2): `revert` комітів безпечний, схема
  БД і контракти не змінювались.
- Відкат залежностей: `git revert eaffc71` повертає `httpx` у dev-групу і в
  `FORBIDDEN_FOUNDATION_DEPS` (разом із revert коду `collector.fetch`, що імпортує httpx).
- Після PR2 — механізми з розділу «Rollback/disable» картки (`NoopHandler`, `--scale
  fetch-worker=0`, pause/circuit/`block_origin`, глобальний denylist через
  `COLLECTOR_FETCH_DENYLIST_FILE`).

## Dependency-запити

Немає. Чужі owned files не редагувались; єдиний файл поза `src/collector/fetch/**`,
`tests/**/fetch/**`, `pyproject.toml`/`uv.lock` і звітом — `tests/unit/test_foundation_config.py`
у межах винятку оркестратора (рядок `FORBIDDEN_FOUNDATION_DEPS` і гілка `if forbidden != "httpx"`).

## Fixes after gate 2

Вхід: `docs/plan/reports/WP-02/testing-pr1.md` (коміти тестувальника `d4c7e74`, `c1441a6` — не
змінювались). Розмір PR прийнято оркестратором. Коміт `8ae0c99 fix(wp-02): close gate-2
findings F-1..F-3`; 5 навмисно червоних тестів тестувальника зелені без змін тестів.

| Знахідка | Виправлення | Тест (тестувальника) |
|---|---|---|
| F-1 high — sitemap `.xml.gz` розпізнавався лише за першим мережевим чанком; перший байт `\x1f` окремим сегментом вимикав ліміт 100 МБ після розпакування і ratio-guard | `decoding.py::read_body`: для `request_kind=sitemap` рішення «entity — gzip-файл» приймається за **накопиченим префіксом** entity (≥ 2 байти після `Content-Encoding`), незалежно від меж чанків; до рішення bytes буферизуються (≤ 1 байт), entity коротший за magic — не gzip. Коли дані стиснені (будь-який `Content-Encoding` або gzip-файл sitemap), ліміт і ratio-guard рахують **розпаковані** байти; для нестиснених — отримані. Deflate/brotli як «файли» sitemap не визначені (sitemaps.org — лише gzip); як `Content-Encoding` вони декодуються з тим самим лічильником | `test_adversarial_limits.py::test_sitemap_gzip_bomb_with_split_magic_bytes_is_still_counted`, `::test_sitemap_split_magic_legit_gz_is_stored_compressed` |
| F-2 medium — `Retry-After: ²` → `ValueError` з `fetch()`, 429 без `block_origin` | `classify.py::parse_retry_after`: секунди лише з ASCII-цифр (`isascii() and isdigit()`), інакше HTTP-date, інакше default 10 хв; `block_origin` ставиться завжди для 429. Те саме для `Content-Length` у `client.py::_complete` | `test_adversarial_retry_logs.py::test_non_ascii_digit_retry_after_does_not_crash_fetch[²]`, `[¹²³]` |
| F-3 low — `Location`, який відкидає парсер httpx (`http://0177.0.0.01/`), давав retryable `network_error` | `client.py::_detach_location` — response event hook клієнта забирає `Location` redirect-відповіді в `response.extensions` до того, як httpx будує `next_request`; далі hop іде через наш guard/SSRF і класифікується як `policy_blocked` (`ssrf_forbidden_address`) | `test_adversarial_ssrf.py::test_forbidden_literal_forms_on_redirect_hop_never_connect[http://0177.0.0.01/-ssrf_forbidden_address]` |

**zlib CVE (ADR-0002, борг #6 HANDOFF; тригер перегляду — цей PR).** Розпакування недовірених
body обмежене на кожному кроці: `src/collector/fetch/decoding.py:67`
(`ZlibDecoder.feed` → `zlib.decompressobj().decompress(data, CHUNK_LIMIT)`, `max_length` =
64 КБ, решта входу — `unconsumed_tail`; gzip, multi-member gzip, zlib, raw deflate і внутрішній
gzip sitemap), `:87` і `:91` (`brotli.Decompressor.process(..., output_buffer_limit=CHUNK_LIMIT)`).
Після кожного шматка — ліміт розпакованих байтів (20 МБ / 100 МБ sitemap) і ratio-guard
(> 200 після 1 МБ). httpx-декодери (`zlib` без `max_length`) не використовуються: body читається
`aiter_raw()`. Це обмежує обсяг виходу й пам'ять, але не усуває вразливість самої бібліотеки zlib
на зловмисному потоці — рішення щодо risk acceptance/блоку лишається за security-reviewer (WP-13).

Команди (HEAD `8ae0c99`):

`uv run ruff check . && uv run ruff format --check . && uv run mypy src`:

```text
All checks passed!
299 files already formatted
Success: no issues found in 87 source files
```

`uv run pytest tests/unit/fetch` (разом із тестами тестувальника):

```text
387 passed in 13.37s
```

`COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/fetch -rs`:

```text
.........                                                                [100%]
9 passed in 111.38s (0:01:51)
```

`uv run pre-commit run --all-files`: усі hooks `Passed` (end-of-file, trailing whitespace, yaml,
toml, large files, merge conflict, private key, ruff check, ruff format, gitleaks, markdownlint).

## Fixes after gate 3

Джерело: `docs/plan/reports/WP-02/code-review-pr1.md`.

| Знахідка | Виправлення | Доказ |
|---|---|---|
| CR-1 high — manifest total-timeout міг пережити 90-секундний permit lease | Перед connect порівнюється весь залишок logical fetch із `lease_expires_at` + 1 с margin. Замалий lease → retryable `permit_lease_too_short`, без TCP. Для sitemap override runtime задає відповідно довший `lease_seconds` | `test_timeout_override_must_fit_inside_permit_lease`; існуючий `test_sitemap_total_timeout_override_from_manifest` тепер явно видає lease 310 с для override 300 с |
| CR-2 medium — truncated compressed stream приймався як success | `Decoder.finish()` вимагає zlib/gzip/deflate EOF і brotli `is_finished`; checksum/footer truncation → `content_decoding_error`, quarantine | `test_truncated_compressed_stream_is_not_accepted_as_full[gzip,deflate,br]` |

Після виправлень: 392 unit/security tests і 9 PostgreSQL integration tests passed; mypy strict,
ruff/format і всі pre-commit hooks зелені. Попередні gate-2 тести не змінювались.
