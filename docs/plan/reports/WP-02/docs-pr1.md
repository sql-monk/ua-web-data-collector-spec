# WP-02 PR1 — документування

## Перевірено й оновлено

- `src/collector/fetch/README.md`: модулі, виклик, SSRF model, body/timeout/permit invariants,
  status/retry table, anonymous-only behavior і тимчасовий adapter O-1.
- `docs/decisions/0008-fetch-core-on-httpx-not-scrapy.md`: рішення HTTPX, DNS pinning,
  streaming limits і інтеграція з WorkerRuntime/PG limiter відповідають коду.
- Reports implementation/testing/code-review/spec-review/security містять фактичні команди,
  findings, виправлення й невирішені residual risks.

## Свідомо відкладено

- Artifact store/upload/robots/handler docs — PR2.
- Browser isolation/runbook — PR3.
- Операційний runbook pause-source — після handler PR2, коли існуватиме реальна команда.

Markdown перевірено загальним pre-commit gate; зовнішніх посилань у нових звітах немає.
