# WP-02 PR1 — security review

Перевірено threat model §13 для HTTP fetch core: SSRF через URL/redirect/DNS rebinding,
decompression bomb/oversized body, anonymous-session leakage, proxy bypass, secret logging,
leased origin limiter і hostile response headers.

## Вердикт

**approve**. Після виправлень gate 2/3 немає blocker/high/medium security findings.

## Контролі

- Лише `http/https`, userinfo і нестандартні порти блокуються до DNS/connect.
- Усі DNS answers мають бути global; private/loopback/link-local/metadata/embedded IPv4,
  numeric IPv4 variants і scoped IPv6 блокуються.
- TCP pin-иться на щойно перевірену IP; `Host`/SNI зберігають вихідне ім'я; env proxies off.
- Кожний redirect повторює guard/DNS/permit; HTTPS downgrade і origin без bucket — permanent.
- Body декодується bounded chunks, має decoded-size/ratio limits і обов'язковий EOF/checksum
  stream; truncated gzip/deflate/brotli карантиниться.
- Permit видається до connect і мусить покривати весь залишок fetch; замалий lease → 0 connect.
- Cookie jar відхиляє cookies, Authorization не додається, request headers/body не логуються;
  response headers — allowlist + redaction.
- 429 блокує origin у PostgreSQL; retry скінченний.

## Residual risks

| Ризик | Оцінка | Owner / дія |
|---|---|---|
| Operator-defined regex може бути патологічним на довгому redirect URL | low, trusted config | WP-02 PR2: manifest validation/safe-regex policy або bounded execution |
| Release permit покладається на DB command timeout | low, lease self-expires | WP-01D `OriginPermitClient`: bounded release; не маскувати основний результат |
| Відомі HIGH CVE base image (zlib/perl) | tracked, не внесені цим PR | ADR-0002/WP-13; bounded decoder зменшує blast radius, але не заміняє оновлення image |
| HTTPX/httpcore private pool wiring може змінитися при upgrade | low, pinned lock | transport contract tests + dependency update review |

## Доказ

392 unit/security tests, включно з SSRF/redirect/bombs/truncation/log redaction, та 9
PostgreSQL integration tests. Mutation tests доводять, що зняття redirect guard, decoded-byte
ліміту або bounded decompression робить suite червоним. Live external requests не виконувалися.
