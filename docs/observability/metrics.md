# Метрики collector

## Fetch і artifact storage (WP-02)

| Метрика | Тип | Labels | Семантика |
|---|---|---|---|
| `http_requests_total` | counter | `source`, `status_class` | Отримані HTTP-відповіді; class — `1xx`…`5xx` |
| `http_429_total` | counter | `source` | Відповіді 429, що також блокують origin до `Retry-After` |
| `policy_blocks_total` | counter | `source` | Відмови Route Guard, SSRF або robots policy до page request |
| `raw_bytes_total` | counter | `source` | Логічні raw bytes успішних fetch-ів, включно з dedup hits |
| `raw_dedup_ratio` | derived gauge | `source` | `raw_deduplicated_total / raw_uploads_total`; 0 без upload observations |
| `artifact_orphans_total` | counter | — | Кандидати, побачені fenced orphan sweeper-ом |

Код WP-02 накопичує bounded in-process counters у `collector.fetch.metrics.FetchMetrics`.
Повний exporter, persistence/aggregation між replicas, dashboards і alerts належать WP-12.
Повний URL, origin, object key, error text і credentials ніколи не є labels.
