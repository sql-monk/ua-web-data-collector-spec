# `postgres/init` — SQL, що виконується при першому старті кластера

Тека монтується у `postgres` як `/docker-entrypoint-initdb.d:ro`: офіційний entrypoint
виконує `*.sql`/`*.sh` звідси **лише коли data directory порожня** (перший `up` після
`down -v`), в алфавітному порядку.

- `01-roles.sql` (WP-01A) — створює NOLOGIN group-ролі §13 (`collector_migrate`,
  `collector_scheduler`, `collector_fetcher`, `collector_parser`, `collector_projector`,
  `collector_translation`, `collector_api_ro`, `collector_export_ro`), щоб `collector db roles`
  (GRANT) і login-користувачі могли посилатися на них одразу. Це лише перша частина
  канонічного скрипта `src/collector/persistence/postgres/sql/roles.sql`: GRANT потребує
  таблиць, які з'являються після `collector db migrate`.

Порядок у dev: `docker compose up -d postgres` → `collector db migrate` → `collector db roles`.

Зміна ролей після першого старту не застосовується автоматично: або `docker compose down -v`
(втрата даних), або `collector db roles` проти живого кластера (ідемпотентно, і саме так це
робить one-shot `migrate-postgres`).

Паролів у скриптах немає: dev-login (`POSTGRES_USER`) — superuser з compose env/secret;
runtime-login-користувачі створює оператор як членів ролей
(`CREATE ROLE ... LOGIN IN ROLE collector_fetcher`).
