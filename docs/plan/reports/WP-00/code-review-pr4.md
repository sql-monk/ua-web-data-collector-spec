# WP-00 PR4 — gate 3 code review (`wp/00-4-role-dsn-secrets`)

Обсяг: `git diff main...wp/00-4-role-dsn-secrets` (HEAD `1eb3e27`), картка `docs/plan/cards/WP-00.md` «PR4»,
`docs/plan/reports/WP-00/testing-pr4.md`. Рев'ю read-only; Docker-стек не піднімався. Сценарії
`init-secrets.sh` відтворювались копією скрипта в scratch-каталозі (Git Bash).

## Знахідки

| # | severity | file:line | claim | failure scenario | verdict |
|---|---|---|---|---|---|
| 1 | low | `deploy/compose/secrets/init-secrets.sh:98-101` (`"$(random_hex)"` в аргументі `printf`) | Помилка генератора випадкових чисел у гілці `postgres_dsn_*` ковтається: у command substitution bash не успадковує `errexit`, а `random_hex` завершується `echo` (rc 0). DSN записується з порожнім паролем, скрипт повертає 0, а файл далі вважається «наявним» (`-s`) і не виправляється повторним запуском. Для `*_password` та сама помилка валить скрипт (`random_hex > file` під `set -e`) — поведінка непослідовна. | `openssl` у PATH, але `openssl rand` повертає 1 без виводу (відтворено shim-ом `exit 1`) → `gen postgres_dsn_fetcher`, rc=0, вміст `postgresql://collector_fetcher:@postgres:5432/collector`; другий запуск → `skip (exists)`. Далі `migrate-postgres` падає з `немає пароля` (голосно, без витоку), але рецепт з runbook («запустіть init-secrets.sh») не допомагає — треба вручну видалити файл. Імовірність збою `openssl rand` мала. Виправлення: `pw="$(random_hex)"; [ -n "$pw" ] \|\| exit 1` або `set -o errexit` + `shopt -s inherit_errexit`, чи перевірка довжини в `random_hex`. | CONFIRMED |
| 2 | low | `deploy/compose/secrets/init-secrets.sh:79-90` (гілка `postgres_dsn`) | `postgres_dsn` і `postgres_password` узгоджуються лише коли DSN генерується; наявний непорожній `postgres_dsn` не звіряється з паролем. Оскільки glob обробляє `postgres_dsn` раніше за `postgres_password`, а PR4 тепер вважає порожній файл відсутнім, порожній/відсутній `postgres_password` при наявному DSN перегенерується з новим паролем, і DSN мовчки розходиться з ним. Клас здебільшого існував до PR4 (для відсутнього файла); PR4 розширює його на порожній файл. | `postgres_password` — 0 байт (перерваний попередній запуск/ручне очищення), `postgres_dsn` непорожній → `skip postgres_dsn`, `gen postgres_password` → Postgres init з новим паролем, `migrate-postgres` — `password authentication failed`. Виправлення (опц.): якщо пароль перегенеровано, перегенерувати й `postgres_dsn`, або warn при невідповідності. | PLAUSIBLE |
| 3 | low | `deploy/compose/secrets/init-secrets.sh:50-65, 79-90` | Два одночасні запуски не синхронізовані (немає lock, запис не атомарний `tmp+mv`). (а) Обидва бачать порожній каталог-заглушку, один `rmdir`-ить, другий отримує невдалий `rmdir` і завершується з оманливою помилкою «каталог, а не файл секрету» (exit 1). (б) Перемежування `random_hex > postgres_password` / `cat postgres_password` двох процесів може залишити `postgres_dsn` з паролем, який потім перезаписав інший процес. | Запуск A: пише pA, читає pA; B: пише pB, читає pB, пише DSN(pB); A пише DSN(pA) останнім → `postgres_password=pB`, `postgres_dsn` містить pA → auth failure міграцій. Реалістично лише при паралельному CI/скриптах на одному checkout; для локального dev майже неможливо. | PLAUSIBLE |

Critical/high/medium знахідок немає.

## Вердикт

**approve** — жодної critical/high. Три low не блокують; №1 варто закрити дрібним фіксом
(перевірка непорожнього пароля), №2–3 — на розсуд owner-а WP-00.

## Що перевірено окремо

- **`init-secrets.sh`, ідемпотентність і частковий набір.** Порядок glob (`postgres_dsn` <
  `postgres_dsn_*` < `postgres_password`) коректний: пароль генерується в гілці `postgres_dsn`,
  потім `skip`. Наявні непорожні файли не перезаписуються (тести byte-identical + мутації в
  testing-pr4.md). Хост до PR4 → додаються лише сім нових DSN. Регенерація втраченого
  `postgres_dsn_<c>` безпечна: `apply_logins` щоразу ставить новий SCRAM verifier з DSN
  (`roles.py` `ALTER ROLE … PASSWORD`), тож повторний `up` само-лікує роль.
- **Каталоги-заглушки / порожні файли / інші типи.** Порожній каталог → `rmdir` + генерація;
  непорожній каталог чи не-regular файл → exit 1 з підказкою; порожній файл → регенерація.
  `exit` у функції в умові `if` завершує скрипт — перевірено читанням. Якщо заглушка лишилась,
  `load_role_logins` (`is_file()`) дає exit 1 до будь-яких змін БД.
- **Права файлів.** `umask 022` + явний `chmod 0644` після кожного запису (і для
  `postgres_password`, згенерованого в гілці DSN). 0644 — задокументоване відхилення ADR-0002;
  нових відхилень PR4 не додає.
- **`migrate-postgres`: `sh -c 'collector db migrate && exec collector db roles --with-login'`.**
  Exit code — перша невдала команда; `db roles` не стартує після збою міграцій; `exec` передає
  rc `db roles` (перевірено тестами зі stub-ом у справжньому `sh` і ручним `docker compose run`
  у testing-pr4.md). Команда не містить секретів → нічого в `docker inspect`. `init: true` з
  anchor-а — tini PID 1; переривання міграції по SIGTERM рівноцінне SIGKILL, Alembic
  транзакційний.
- **`02-revoke-public.sql`.** Виконується лише при першому initdb, як superuser, у
  `POSTGRES_DB` (`current_database()` коректний, не захардкоджено `collector`; випадок
  `POSTGRES_DB=postgres` обробляється гілкою `db_name <> current_database()`). Ролі вже є
  (`01-` раніше за алфавітом). Healthcheck `pg_isready` не автентифікується; `migrate-postgres`
  і поточні споживачі `postgres_dsn` ходять superuser-ом (обхід CONNECT-перевірки); runtime-ролі
  отримують явний `GRANT CONNECT`. TEMP-таблиць у `src/` немає (grep). `REVOKE` на `template1`
  не впливає на ACL нових БД (`CREATE DATABASE` не копіює `datacl`). Наявні кластери —
  ручний рецепт у README (пароль через env у `exec`, не argv). Спостереження: `roles.sql`
  (`db roles`) не видає `CONNECT`, тож при перестворенні ролі поза initdb CONNECT доведеться
  видати вручну — поза обсягом PR4.
- **Витоки паролів.** DSN у CI передається через `-e PGDSN` без значення (не в argv/лозі);
  `echo` друкує лише імена ролей і ACL; `denied.txt` містить лише текст помилки psql. Лог
  `migrate-postgres` маскує пароль (`***`), `RoleLogin.__repr__` і `RoleLoginError` не містять
  DSN/verifier. Grep паролів усіх 8 DSN по `compose logs`/`docker inspect`/`denied.txt` — у CI.
- **CI workflow.** Heredoc-термінатори після зняття YAML-відступу — у колонці 0; `cmd && { …;
  exit 1; }` під `set -e` не спрацьовує хибно; `test` порівнює точний набір LOGIN-ролей і
  `temp:false`. Крок іде після генерації секретів тим самим `init-secrets.sh`.
- **Тести.** Скрипт і `sh -c`-команда виконуються по-справжньому (subprocess, stub лише для
  `collector`), не моки. Тести `02-revoke-public.sql` у unit-рівні — текстові (regex по SQL);
  поведінка SQL перевіряється лише Docker-кроком CI — прийнятно для init-скрипта.
- **Не перевірено:** Linux runner CI (Docker не піднімався за умовою); `POSTGRES_DB`/`HOST`
  береться з env у момент генерації, а не з `.env` Compose — поведінка успадкована від PR2.
