# WP-00 PR4 — security review (`wp/00-4-role-dsn-secrets`)

Діапазон: `git diff main...wp/00-4-role-dsn-secrets` (10 commits, до `1eb3e27`). Вхід: картка
`docs/plan/cards/WP-00.md` «PR4», `docs/plan/deps/WP-01A-to-WP-00.md` §4/§6, ТЗ §13 і FR-013,
`docs/plan/reports/WP-01A/security-pr2.md` (I-1, I-2). Рев'ю лише читає: код не змінювався,
Docker-стек не піднімався.

## Знахідки

Формат: `severity | file:line | клас проблеми | сценарій | verdict`.

| ID | Severity | file:line | Клас проблеми | Сценарій | Verdict |
|---|---|---|---|---|---|
| L-1 | low | `deploy/compose/secrets/init-secrets.sh:100` | Помилка генерації секрету ковтається (fail-open у генераторі) | `"$(random_hex)"` стоїть в аргументі `printf`. У command substitution bash не успадковує `errexit` (без `shopt -s inherit_errexit`), до того ж `random_hex` завершується `echo`, тому код повернення завжди 0. Якщо `openssl` є, але падає (наприклад, зламаний `OPENSSL_CONF` у Git Bash на Windows), у файл запишеться DSN з порожнім паролем (`collector_x:@`). Файл непорожній, тож наступні запуски пишуть «skip». Відтворено на bash 5.3 (pipeline, що падає, плюс `echo` у substitution дає порожній рядок, і скрипт працює далі). Слабкого облікового запису це не створює: `login_from_dsn` відхиляє порожній пароль, і `migrate-postgres` завершується з exit 1. Але причина неочевидна, а виправити можна лише вручну видаливши файл. Шлях `*_password` не зачеплений: там `random_hex > file` виконується поза substitution. | не блокує; радимо спершу `pw="$(random_hex)"`, потім перевірити `${#pw}` = 48 (інакше exit 1) і лише тоді `printf` |
| L-2 | low | `deploy/compose/postgres/init/README.md` («Кластер, створений до WP-00 PR4»), `02-revoke-public.sql` | Hardening не застосовується до наявних кластерів | Init-скрипти виконуються лише на порожньому data directory. На dev-хості, піднятому до PR4, PUBLIC і далі має `CONNECT`/`TEMP` на `collector`/`postgres`/`template1`, поки оператор вручну не виконає крок із README. Жоден runtime-компонент цього не перевіряє: `migrate-postgres` ідемпотентно видає LOGIN, але `REVOKE` не робить. | не блокує (dev-only, задокументовано); бажано мати tripwire, наприклад попередження в `db roles --with-login`, якщо в `datacl` є grantee PUBLIC |
| I-1 | info | `docker-compose.yml:339-366` | Найменші привілеї: one-shot тримає всі облікові дані | `migrate-postgres` монтує `postgres_dsn` (superuser `POSTGRES_USER`) і всі сім runtime-DSN. Ескалації привілеїв немає: superuser і так може виставити будь-якій ролі будь-який пароль, тож сім DSN прав не додають. Сервіс one-shot (`restart: "no"`), non-root 10001, `read_only`, `cap_drop: ALL`, мережа лише `backend`. Секрети bind-mount-яться і в шарах контейнера не лишаються. Жоден інший сервіс `postgres_dsn_*` не монтує, переведення runtime-сервісів на ці DSN належить WP-01D PR1b. | прийнято |
| I-2 | info | `init-secrets.sh` (права 0644, `umask 022`) | Файли секретів world-readable на хості | До 0644 додалося ще сім файлів. Це те саме свідоме відхилення (ADR-0002, single-host MVP), що й для наявних секретів: 0600 дав би EACCES для uid контейнерів. На багатокористувацькому хості будь-який локальний користувач прочитає DSN. | прийнято (production: Swarm secrets, Q-013/WP-01D) |
| I-3 | info | `02-revoke-public.sql:21-35` | Повнота REVOKE PUBLIC | Закрито: `collector` (`CONNECT` і `TEMPORARY` з PUBLIC прибрано, `CONNECT` видано восьми group-ролям, `TEMP` нікому), `postgres` і `template1` (`REVOKE ALL`). `template0` має `datallowconn=false`. PostgreSQL 18: `CREATE` на schema `public` у PUBLIC немає з PG15. Відкритими лишаються стандартні defaults: `USAGE` на schema `public`/`pg_catalog`, `EXECUTE` на функції, `USAGE` на `plpgsql`. Без `CONNECT` вони не дають доступу до інших БД. Важливо: `datacl` не копіюється з `template1`, тож будь-яка БД, створена пізніше, знову отримає PUBLIC `CONNECT`/`TEMP` за замовчуванням і потребуватиме власного `REVOKE`. | прийнято; врахувати, якщо в кластері зʼявляться інші БД |
| I-4 | info | `.github/workflows/ci.yml:415,440,451` | Витік паролів у CI | DSN передається через `-e PGDSN` без значення, тому в argv `docker compose` і в лог кроку він не потрапляє. Усередині контейнера `postgres` DSN короткочасно видно в argv `psql` (`/proc`); у CI-контексті це прийнятно. Перевірка на витік шукає plaintext восьми DSN-паролів у `compose logs`, `docker inspect` і `denied.txt`. Не покрито: SCRAM verifier у server log Postgres (якщо `ALTER ROLE` впаде, `log_min_error_statement=error` залогує statement разом із verifier; offline brute force 192-бітного пароля нереальний) і секрети mongo/minio. `::add-mask::` не використовується; це не проблема, поки значення ніде не друкуються. | прийнято; можна додати `::add-mask::` як defense in depth |
| I-5 | info | `security-pr2.md` I-1 (продовження) | Migration role = superuser; runtime на спільному DSN | Міграційний DSN досі веде до superuser `POSTGRES_USER`, а не до `collector_migrate`. Runtime-сервіси до WP-01D PR1b ходять на `postgres_dsn`. PR4 цю частину свідомо не закриває. | блокує non-dev розгортання до WP-01D PR1b, як і було записано раніше |

Critical/high/medium знахідок немає.

## Вердикт

**approve.** Блокуючих знахідок (critical/high) немає. L-1 варто виправити в межах PR або follow-up
до pilot, бо це дешевий фікс. §13 «REVOKE PUBLIC» для БД `collector` виконано, `security-pr2.md`
I-2 закрито для нових кластерів.

## Що перевірено

- **Ентропія й джерело випадковості.** `random_hex` дає 24 байти (192 біти) з `openssl rand`,
  fallback — `secrets.token_hex` або `/dev/urandom`. Кожен із семи DSN отримує окремий виклик,
  тобто власний пароль. Hex складається з printable ASCII, тож це сумісно з verifier без SASLprep.
  Тести `test_secrets_role_dsn*.py` перевіряють попарну різницю паролів.
- **Права файлів, ідемпотентність і `.gitignore`.** Файли створюються з 0644 (див. I-2).
  `secret_present` прибирає порожні каталоги-заглушки Docker і перегенеровує порожні файли,
  непорожні не перезаписує. `.gitignore` ігнорує `deploy/compose/secrets/*`, крім `*.example`
  та `init-secrets.sh`; `git check-ignore` для `postgres_dsn_fetcher` дає збіг.
- **`.example`.** Сім нових файлів містять лише плейсхолдер `<random_hex_48>`/`<POSTGRES_DB>` і
  не копіюються (гілка `postgres_dsn_*` генерує значення).
- **Найменші привілеї.** Див. I-1. `environment` містить лише `*_FILE`, паролів в env/argv
  compose немає.
- **SCRAM.** `POSTGRES_INITDB_ARGS` задає scram-sha-256 для host і local.
  `apply_logins` ставить verifier (16-байтна salt, 4096 ітерацій), plaintext на сервер не
  надсилається. У помилках `ALTER ROLE` фігурують лише роль і SQLSTATE. `login_from_dsn`
  відхиляє порожній і не-ASCII пароль (див. L-1).
- **REVOKE PUBLIC.** Див. I-3. Скрипт виконується після `01-roles.sql` за алфавітом, а якщо
  ролі відсутні, `GRANT` падає — поведінка fail-closed. `pg_isready` не автентифікується,
  superuser оминає перевірку `CONNECT`, тому healthcheck і entrypoint продовжують працювати.
  CI перевіряє порожній PUBLIC ACL на трьох БД, `TEMP=false` для `collector_fetcher` і
  відмову `permission denied for database "postgres"`.
- **`migrate-postgres`.** Команда через `sh -c` з `&&` і `exec`: exit code зберігається,
  `db roles --with-login` не запускається, якщо міграції впали.
- **gitleaks** 8.x: `gitleaks git --log-opts=main..HEAD` — 10 commits, no leaks found.
  `gitleaks dir` знаходив false positive лише в `.venv/` (сторонні пакети), у файлах diff нічого.
  CI job `gitleaks` не змінено, він лишається активним. Синтетичний DSN
  `rotated-by-operator` в adversarial-тесті не є секретом.
- **Поза diff:** SSRF, ліміти body, XML, GUI/OIDC/CSP, контейнери інших сервісів, нові
  залежності (не додавались). `trivy`/`pip-audit` не встановлені, для цього diff вони не потрібні.
