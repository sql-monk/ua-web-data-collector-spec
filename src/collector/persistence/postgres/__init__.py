"""PostgreSQL persistence і міграції (SQLAlchemy 2 async + asyncpg + Alembic); owner — WP-01A.

Структура:

- `config` — DSN з env `COLLECTOR_POSTGRES_DSN` (або `COLLECTOR_POSTGRES_DSN_FILE`);
- `engine` — `AsyncEngine`/`async_sessionmaker`;
- `models` — SQLAlchemy declarative-моделі всіх таблиць §9.1 (джерело істини для Alembic);
- `repositories` — типізовані async-операції над `AsyncSession` (queue, limiter, pools, audit,
  sources); інші WP не пишуть SQL самі, а викликають ці функції;
- `migrations` — програмний Alembic (`upgrade head`, `check`) для CLI `collector db migrate`;
- `roles` — ролі БД §13 і GRANT з `sql/roles.sql` для CLI `collector db roles`;
- `partitions` — helper створення місячних партицій (`audit_log`, далі fetch/event tables).
"""
