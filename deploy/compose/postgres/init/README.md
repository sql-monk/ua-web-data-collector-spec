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

Паролів у скриптах немає: dev-login (`POSTGRES_USER`) — superuser з compose env/secret і
лише для міграцій. Runtime-ролі стають LOGIN-ролями командою (WP-01A PR2, §13):

```bash
collector db roles --with-login --secrets-dir /run/secrets   # або $COLLECTOR_POSTGRES_ROLE_SECRETS_DIR
```

Вона читає DSN-секрети компонентів `postgres_dsn_<component>` (`scheduler`, `fetcher`,
`parser`, `projector`, `translation`, `api_ro`, `export_ro`), перевіряє, що користувач у
кожному DSN — саме `collector_<component>`, і виконує `ALTER ROLE … LOGIN PASSWORD
'<SCRAM-SHA-256 verifier>'` (відкритий пароль на сервер не передається). Бракує хоч одного
секрету — exit 1 без змін. Повторний запуск ідемпотентний; `collector db roles` без
`--with-login` уже видані логіни не вимикає. Кожен сервіс після цього монтує **свій**
`postgres_dsn_<component>` як `COLLECTOR_POSTGRES_DSN_FILE` (генерація секретів і compose —
dependency-запит `docs/plan/deps/WP-01A-to-WP-00.md` §4). `collector_migrate` LOGIN не отримує.
