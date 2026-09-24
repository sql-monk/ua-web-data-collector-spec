# WP-02 PR1 — звіт незалежного тестувальника

Branch `wp/02-1-http-ssrf-limits`, worktree `.worktrees/wp-02-1`, HEAD до тестів `47ea994`,
коміт тестів `d4c7e74`. Контракт: картка WP-02 (PR1, «Рішення оркестратора», спільні вимоги),
ADR-0008, ТЗ §3, §10 кроки 4–6, §13, REVIEW R-16/R-33/R-38/R-53. Рівні §16.1: 1, 3, 8.
`implementation-pr1.md` прочитано лише після власного прогону.

## Команди та дослівний вивід

`uv sync --frozen`

```text
Checked 67 packages in 7ms
```

`uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy src` (HEAD `47ea994`)

```text
All checks passed!
295 files already formatted
Success: no issues found in 87 source files
```

`uv run pytest tests/unit/fetch` (до нових тестів)

```text
251 passed in 33.74s
```

`uv run pytest -m "not live"` (HEAD `47ea994`, один прогін; хост навантажений паралельними
прогонами інших WP)

```text
FAILED tests/integration/scaling/test_runtime_login_adversarial.py::test_process_exits_nonzero_as_superuser_before_any_write[argv0]
FAILED tests/integration/scaling/test_worker_runtime.py::test_self_fencing_cancels_active_tasks_when_the_database_stops_confirming_the_lease
2 failed, 1324 passed, 25 skipped, 8 warnings in 5135.40s (1:25:35)
```

Два падіння — таймінгові тести WP-01D (`tests/integration/scaling`, код PR1 не зачіпають).
Ізольований повтор (позначено окремо, не як доказ зеленості PR1):

```text
$ uv run pytest <два тести вище>
4 passed in 82.08s (0:01:22)
```

25 skipped — gui e2e без стека, enforcement-модулі без прапорця, `test_network_blocked` на Windows
(ті самі, що в `implementation-pr1.md`).

`COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/fetch -rs`

```text
tests\integration\fetch\test_permits_pg.py .......                       [ 77%]
tests\integration\fetch\test_suite_is_enforced.py ..                     [100%]
======================== 9 passed in 87.61s (0:01:27) =========================
```

Skip → fail без Docker (`DOCKER_HOST=tcp://127.0.0.1:1`):

```text
== REQUIRE=1, docker недоступний
E           Failed: Docker недоступний (DockerException: ... [WinError 10061] ...) — integration skip
tests\integration\postgres\conftest.py:82: Failed
1 passed, 8 errors in 5.55s
== без прапорця, docker недоступний
9 skipped in 2.47s
```

Тест-вартовий O-1: фіктивний `collector.core.limiter_runtime` з `OriginPermitClient` у
`sys.modules` → червоний з потрібним повідомленням:

```text
E           Failed: прибрати `PgOriginPermits` після WP-01D PR2 і перейти на `OriginPermitClient` (collector.core.limiter_runtime) — рішення O-1 картки WP-02
tests\unit\fetch\test_permits_adapter_tripwire.py:35: Failed
1 failed in 16.05s
```

(Без фіктивного модуля — зелений; `collector/core/` не містить `limiter_runtime.py`.)

`uv run pytest tests/unit/fetch` після нових тестів (`d4c7e74`)

```text
FAILED tests/unit/fetch/test_adversarial_limits.py::test_sitemap_gzip_bomb_with_split_magic_bytes_is_still_counted
FAILED tests/unit/fetch/test_adversarial_limits.py::test_sitemap_split_magic_legit_gz_is_stored_compressed
FAILED tests/unit/fetch/test_adversarial_retry_logs.py::test_non_ascii_digit_retry_after_does_not_crash_fetch[\xb2]
FAILED tests/unit/fetch/test_adversarial_retry_logs.py::test_non_ascii_digit_retry_after_does_not_crash_fetch[\xb9\xb2\xb3]
FAILED tests/unit/fetch/test_adversarial_ssrf.py::test_forbidden_literal_forms_on_redirect_hop_never_connect[http://0177.0.0.01/-ssrf_forbidden_address]
5 failed, 382 passed in 16.27s
```

`uv run pre-commit run --all-files` (з новими тестами)

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

Linux-контейнер: власний прогін **не виконано** — локальні образи (`collector:dev/ci`) без
`brotli`/dev-залежностей, а `uv sync` у контейнері потребує мережі (заборонено тестувальнику).
Нові тести не мають маркерів `integration`/`live`, не використовують сокетів (лише
`FakeNetwork`/`FakeResolver`, autouse-заборона `getaddrinfo`) і не містять `skip`/`skipif` —
на Linux під `--disable-socket` вони виконуються так само. Linux-вивід реалізатора (251 passed)
стосується лише його тестів.

## Acceptance → тест → результат

| Acceptance / вимога PR1 | Тести | Результат |
|---|---|---|
| SSRF: кожна заборонена форма → `policy_blocked`, 0 connect | `test_ssrf.py::test_forbidden_target_*` (32), `test_adversarial_ssrf.py::test_forbidden_literal_forms_never_connect` (34: `::ffff:127.0.0.1`, `::ffff:0.0.0.0`, `0.0.0.0`, `0`, `[::]`, ULA, `fe80::1%25eth0`/`%251`, decimal/octal/hex/short, fullwidth і `。`-крапка, userinfo `evil@127.0.0.1`, `\@`, `file:`/`gopher:`/`dict:`, порти 8080/22/6379) | pass |
| Те саме на redirect hop | `test_redirects.py`, `test_adversarial_ssrf.py::test_forbidden_literal_forms_on_redirect_hop_never_connect` (30) | 0 connect у всіх кейсах; **1 fail класифікації** (`0177.0.0.01` → retryable `network_error`, знахідка F-3) |
| Ланцюг public→public→private, блок на 3-му hop | `test_redirects.py::test_chain_*`, `test_adversarial_ssrf.py::test_redirect_chain_public_public_private_blocks_on_third_hop` (6: DNS private, змішаний DNS, AAAA mapped, AAAA link-local зі scope, літерали) | pass |
| DNS rebinding, один резолв на hop, connect на перевірену IP, SNI/`Host` | `test_ssrf.py::test_dns_rebinding_*`, `test_adversarial_ssrf.py::test_rebinding_*`, `test_each_hop_pins_its_own_host_*` | pass |
| `Host` override / header injection | `test_host_header_cannot_be_overridden_via_url_crlf`, `test_fetch_request_has_no_caller_headers` | pass |
| IDN/punycode homograph | `test_idn_homograph_does_not_match_ascii_allow_pattern`, `test_idn_host_is_resolved_as_punycode_and_ssrf_checked`, `test_idn_redirect_to_homograph_of_denylisted_host_is_not_confused` | pass |
| Env proxy ігнорується | `test_client.py::test_env_proxy_is_ignored` | pass |
| Body 20 МБ рівно / +1 (без CL, з CL, chunked) | `test_limits.py::test_exactly_limit_*`, `test_adversarial_limits.py::test_body_with_exact_content_length_*`, `test_transfer_encoding_chunked_*` | pass |
| Брехливий `Content-Length` | `test_lying_small_content_length_*` (5 з 30 МБ), `test_lying_large_content_length_truncated_body_is_not_success` | pass |
| Gzip/brotli bomb ≤ 20 МБ, пам'ять обмежена | `test_limits.py::test_gzip_bomb_*`, `test_brotli_bomb_*`, `test_adversarial_limits.py::test_gzip_bomb_in_tiny_tcp_segments_is_cut`, `test_gzip_bomb_in_one_big_segment_*`, `test_compressed_content_length_below_limit_*` | pass |
| Sitemap 100 МБ після розпакування | `test_limits.py::test_sitemap_xml_gz_*`, `test_sitemap_gzip_bomb_behind_content_encoding_is_cut` (pass), `test_sitemap_gzip_bomb_with_split_magic_bytes_is_still_counted` | **fail — F-1** |
| Total timeout 60 с на весь fetch | `test_limits.py::test_slow_drip_*`, `test_hanging_*`, `test_adversarial_limits.py::test_total_timeout_spans_redirect_hops`, `test_slow_drip_timeout_releases_permit` | pass |
| Conditional GET, 304 | `test_client.py::test_validators_*`, `test_304_with_last_modified_only`, `test_validators_are_not_forwarded_to_cross_origin_redirect` | pass |
| Класифікація, порожня відповідь ≠ видалення | `test_classify.py`, `test_empty_200_is_retryable_not_gone` (у т.ч. стиснуте порожнє), `test_404_410_are_gone_without_any_lifecycle_field` | pass |
| Retry скінченний за таблицею §10 | `test_classify.py::test_retry_policy_*`, `test_every_retryable_decision_ends_in_dead_letter_after_max_attempts` (10), `test_permanent_errors_are_never_retried` (7) | pass |
| 429/`Retry-After` (секунди, HTTP-date, минуле, величезне) → `block_origin` рівно раз | `test_client.py::test_429_*`, `test_hostile_retry_after_is_clamped_and_blocks_origin_once` (8), `test_429_on_redirect_target_blocks_that_origin_not_the_first`, `test_retry_after_on_non_429_does_not_block_origin`; integration `test_429_from_one_replica_blocks_the_other_without_request` | pass; **`Retry-After: ²` — fail, F-2** |
| Permits (R-53): Denied → 0 запитів, release у `finally` | `test_client.py`, integration `test_permits_pg.py` (7) | pass |
| Тест-вартовий O-1 | `test_permits_adapter_tripwire.py` + демонстрація червоного вище | pass |
| Anonymous-only | `test_client.py::test_no_cookies_*`, `test_client_cookie_jar_*` | pass |
| Secret-log / redaction | `test_client.py::test_logs_*`, `test_safe_headers_are_redacted`, `test_secret_query_values_on_redirect_hop_*`, `test_failed_hop_log_detail_*`, `test_no_request_headers_or_bodies_are_logged` | pass |
| Media off (Q-002) | `test_limits.py::test_media_binary_*` | pass |
| `FORBIDDEN_FOUNDATION_DEPS` без `httpx` | `tests/unit/test_foundation_config.py` (у `-m "not live"`) | pass |
| Skip → fail в integration/fetch | прогін з `DOCKER_HOST=tcp://127.0.0.1:1` вище | pass |
| README з посиланням на ADR-0008 | `src/collector/fetch/README.md` | є |

## Рівень §16.1 → тести

| Рівень | Тести |
|---|---|
| 1 Unit (URL normalization, retry decisions, SSRF-класифікатор) | `test_urls.py`, `test_classify.py`, `test_ssrf.py` (класифікатор), `test_guard.py`, `test_transport.py`, `test_adversarial_retry_logs.py::test_every_retryable_*`/`test_permanent_errors_*` |
| 3 Integration (PostgreSQL 18, loopback) | `tests/integration/fetch/test_permits_pg.py` (7), `test_suite_is_enforced.py` (2); MinIO — PR2 |
| 8 Security (SSRF redirect, decompression bomb, oversized, secret-log) | `test_ssrf.py`, `test_redirects.py`, `test_limits.py`, `test_client.py` (secrets/cookies), `test_adversarial_ssrf.py`, `test_adversarial_limits.py`, `test_adversarial_retry_logs.py` |

## Додані тести (коміт `d4c7e74`)

- `tests/unit/fetch/test_adversarial_ssrf.py` — 80 кейсів (літерали/числові/IDN/userinfo/scheme/порт на першому запиті й на redirect hop, 3-й hop, rebinding, pinning на кожному hop, CRLF/`Host`, homograph).
- `tests/unit/fetch/test_adversarial_limits.py` — 21 кейс (20 МБ ±1 з CL і chunked, брехливий CL, bombs у малих/одному сегменті, sitemap за `Content-Encoding`, split gzip magic, corrupt gzip, timeout через hop-и, 304/validators, порожня відповідь).
- `tests/unit/fetch/test_adversarial_retry_logs.py` — 35 кейсів (hostile `Retry-After`, 429 на іншому origin, скінченність retry, redaction на кожному hop).

Червоні навмисно (5 кейсів) — документують знахідки F-1…F-3; жодного skip/xfail.

## Mutation-перевірка

Кожна мутація — одна зміна в `src/collector/fetch/*`, прогін, потім `git checkout -- <file>`
(`git status --short src` після кожної — чисто).

| Мутант | Тест | Результат |
|---|---|---|
| M-A `client.py::_run`: на hop > 0 адреса береться з resolver-а без `resolve_pinned` (redirect hop без SSRF) | `test_redirect_chain_public_public_private_blocks_on_third_hop` | **6 failed** (SUCCESS/`network_error` замість `PERMANENT_FAILURE`; connect на `10.0.0.1`/`::ffff:127.0.0.1`) |
| M-B `decoding.py::count`: `return` одразу (без ліміту розпакованих і ratio-guard) | `test_adversarial_limits.py -k "gzip_bomb or compressed_content_length"` | **5 failed** (`assert None == 'body_too_large'`) |
| M-C `decoding.py::ZlibDecoder.feed`: `decompress(data)` без `max_length` | `test_gzip_bomb_in_one_big_segment_is_cut_without_full_expansion` | **1 failed** (`assert 148787652 < 48 * 1048576` — пік пам'яті 142 МБ) |

## Знахідки

| ID | Severity | file:line | Опис |
|---|---|---|---|
| F-1 | **high** | `src/collector/fetch/decoding.py:178` | Розпізнавання sitemap `.xml.gz` залежить від меж чанків: `entity.startswith(GZIP_MAGIC)` перевіряється лише на **першому** шматку. Сервер, що шле перший TCP-сегмент з 1 байтом (`\x1f`), вимикає внутрішній лічильник: ліміт 100 МБ після розпакування і ratio-guard не діють, 300 МБ-бомба (≈300 КБ) повертається як `success`, `body_compressed=False`, `decoded_bytes` = стиснутий розмір — у raw artifact потрапляє бомба під виглядом звичайного body, а споживач (WP-03) бачить неправильну ознаку. Порушує §13 і вимогу PR1 п.6. Тести: `test_sitemap_gzip_bomb_with_split_magic_bytes_is_still_counted`, `test_sitemap_split_magic_legit_gz_is_stored_compressed`. Виправлення: буферизувати перші 2 байти entity перед рішенням. |
| F-2 | medium | `src/collector/fetch/classify.py:68-69` | `text.isdigit()` приймає не-ASCII цифри (`"²"`, `"¹²³"`), а `int("²")` кидає `ValueError`, який не ловиться в `SafeFetcher.fetch` (`client.py:165-174`) і виходить з `fetch()`. httpx декодує header bytes поза ASCII як latin-1 (`b"\xb2"` → `"²"`), тож hostile origin може зламати fetch відповіддю 429 з таким `Retry-After`: `block_origin` не ставиться (R-53/§10 п.10 — 429 ігнорується, runtime повторить за дефолтним backoff). Тест: `test_non_ascii_digit_retry_after_does_not_crash_fetch`. Виправлення: `text.isascii() and text.isdigit()` (так само `client.py:284` для `Content-Length`, хоча там h11 фільтрує). |
| F-3 | low | `src/collector/fetch/client.py:173-174` (+ `client.py:200`) | `Location`, який відкидає парсер httpx (`client.send` будує `next_request` навіть при `follow_redirects=False`), напр. `http://0177.0.0.01/`, дає `RemoteProtocolError` → `network_error`/**retryable** замість permanent `policy_blocked`/`redirect_location_invalid`. Connect на ціль не відбувається (безпечно), але job тричі повторить hop 1 до origin-а. Тест: `test_forbidden_literal_forms_on_redirect_hop_never_connect[http://0177.0.0.01/-...]`. |
| F-4 | info | `src/collector/workers/handlers.py:161` (owner WP-01D) | `redact()` не прибирає значення `key=`, `X-Amz-Credential=`, `session=`/`sid=` у query; для PR1 не критично (перевірені `token`, `access_token`, `api_key`, `client_secret`, `X-Amz-Signature`, `password` — чисто). Кандидат на dependency-запит до WP-01D. |
| F-5 | info | — | Linux-паритет нових тестів тестувальником не перевірено (офлайн неможливо, див. вище). |
| F-6 | info | `tests/integration/scaling/*` (WP-01D) | Два таймінгові флейки в повному прогоні на навантаженому хості, ізольовано зелені; до PR1 не відносяться. |

## Звірка з `implementation-pr1.md`

- Команди (ruff/format/mypy, `tests/unit/fetch` 251 passed, integration fetch 9 passed, skip→fail,
  pre-commit) — підтверджено моїм прогоном. Повний `-m "not live"`: реалізатор — 1324 passed/0
  failed; у мене — 1324 passed + 2 флейки WP-01D (ізольовано зелені).
- Заява «sitemap: gzip entity → ліміт 100 МБ після розпакування» (вимога 6) — **не
  підтверджується** для розбитого на чанки потоку (F-1). Мутаційний прогін реалізатора (M2) цю
  гілку не покриває.
- «`Retry-After`: сміттєвий → default 10 хв» — не для не-ASCII цифр (F-2).
- «guard/SSRF на redirect → permanent» — не для Location, відкинутого httpx (F-3).
- Решта таблиці вимог (pinning, rebinding, redirects, permits, cookies, media, timeout,
  conditional GET, класифікація, вартовий O-1) — підтверджено, у т.ч. мутаціями M-A/M-C.

## Вердикт

**fail** — F-1 (high: обхід ліміту розпакування sitemap, §13 / PR1 п.6) і F-2 (medium: hostile
`Retry-After` ламає fetch і обходить `block_origin`). Після виправлення F-1/F-2 (і бажано F-3)
червоні тести з `d4c7e74` мають стати зеленими без змін.

## Повторна перевірка після виправлень

Перевірений коміт `8ae0c99`; тести тестувальника не змінювалися. F-1, F-2 і F-3 відтворено
зеленими разом з усім fetch-набором:

```text
$ uv run ruff check src/collector/fetch tests/unit/fetch
All checks passed!
$ uv run ruff format --check src/collector/fetch tests/unit/fetch
24 files already formatted
$ uv run mypy src/collector/fetch
Success: no issues found in 10 source files
$ uv run pytest -q -rs tests/unit/fetch
387 passed in 11.73s
$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration -q -rs tests/integration/fetch
9 passed in 31.11s
```

Результат повторного gate 2: **pass**. F-1—F-3 закриті без послаблення або видалення
adversarial-тестів. F-4—F-6 лишаються інформаційними й не блокують PR1.
