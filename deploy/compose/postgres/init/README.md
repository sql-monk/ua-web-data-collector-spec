# `postgres/init` — SQL, що виконується при першому старті кластера

Тека монтується у `postgres` як `/docker-entrypoint-initdb.d:ro`: офіційний entrypoint
виконує `*.sql`/`*.sh` звідси **лише коли data directory порожня** (перший `up` після
`down -v`), в алфавітному порядку.

Файли ролей (`01-roles.sql` — NOLOGIN group-ролі per-component §13: `collector_migrate`,
`collector_app_rw`, `collector_app_ro`, …) належать **WP-01A** і з'являться після merge
WP-01A PR1 (`docs/plan/deps/WP-01A-to-WP-00.md`, п.2). WP-00 сюди SQL не копіює — порожня
тека означає, що Postgres просто не виконує нічого.

Зміна ролей після першого старту не застосовується автоматично: або `docker compose down -v`
(втрата даних), або `collector db roles` (WP-01A) проти живого кластера.
