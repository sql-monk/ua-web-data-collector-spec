# UA Web Data Collector — технічне завдання

Репозиторій містить самодостатнє ТЗ на дослідницьку платформу збору українських каталогів та автобазарів, а також новин із 19 країн.

- [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) — вимоги, конкретні сайти, архітектура, технології, зберігання оригіналів і українських перекладів, контракти та поділ робіт між незалежними агентами.
- [docs/research/ua-marketplaces.md](docs/research/ua-marketplaces.md) — live-паспорти 12 українських каталогів і автобазарів: URL, sitemap, схеми сторінок, поля, блокування та рейтинги.
- [docs/research/news-central-baltic.md](docs/research/news-central-baltic.md), [news-western.md](docs/research/news-western.md), [news-southern.md](docs/research/news-southern.md) — live-паспорти 58 новинних джерел у 19 країнах.
- [docs/research/source-registry.yaml](docs/research/source-registry.yaml) — канонічні `source_id`, display names, домени та рейтинги всіх 70 джерел.
- [REVIEW.md](REVIEW.md) — результати критичного рев’ю ТЗ і виправлення.

Платформа призначена для внутрішнього дослідження і збирає всі публічно доступні поля, включно з контактами продавців. V1 звертається до джерел тільки без реєстрації, входу й source API keys. Перед масовим запуском треба підтвердити відкриті питання Q-001—Q-012, насамперед бюджет перекладу, media download, глибину backfill, production topology MongoDB, cadence releases і matching thresholds.

Сховище гібридне: PostgreSQL керує jobs, source state, lineage, новинами та перекладами; MongoDB зберігає поточні документи й історію каталогів/авто; S3/MinIO — незмінні raw і normalized artifacts. Узгодження PostgreSQL і MongoDB виконується через versioned idempotent projection outbox та reconciler, без синхронного dual-write.

ТЗ також визначає окремі source/system timestamps, оборотний merge/unmerge сутностей, verified history compaction, immutable dataset releases, щомісячний capacity/cost forecast і read-only DuckDB research kit поверх Parquet.
