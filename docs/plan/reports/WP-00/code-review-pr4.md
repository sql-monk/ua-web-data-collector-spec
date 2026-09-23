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

## Re-review (gate 3')

Обсяг: лише інкрементальний коміт `9281cdf` («fix(wp-00): close gate-3 findings CR-1..CR-3, L-1, L-2»):
`init-secrets.sh`, `tests/unit/test_secrets_role_dsn.py`, `tests/unit/test_compose_config.py`, runbook.
Read-only. Docker не піднімався. Скрипт запускався копією в scratch-каталозі (Git Bash), тест
`test_crlf_examples_do_not_leak_carriage_returns_into_secrets` — 12 разів послідовно і 8 разів паралельно.

### Статус попередніх знахідок

| # | статус | підстава |
|---|---|---|
| CR-1 / SEC L-1 (помилка генератора ковтається) | **closed** | `new_hex`/`new_keyfile` (`init-secrets.sh:53-63`) присвоюють значення в основному shell і перевіряють формат (`^[0-9a-f]{48}$`; base64 keyfile ≥1000 символів, 756 байтів дають 1008 ≤ 1024, ліміт mongod), інакше `die` → exit 1 до `write_secret`. Покрито всі гілки: `*_password`, `postgres_dsn` (пароль для DSN), `postgres_dsn_*`, `mongo_keyfile`. Тести зі збоєм `openssl` через функцію в `BASH_ENV` перевіряють і повний набір, і хост до PR4 (лише role DSN). Той самий збій `openssl` більше не падає мовчки до python3 чи `/dev/urandom`: скрипт завершується з помилкою, це fail-closed і прийнятно. |
| CR-2 (`postgres_dsn` розходиться з `postgres_password`) | **closed** | `init-secrets.sh:133-137`: DSN є, а пароля немає або файл порожній → `die` з рецептом, нічого не змінюється (тест byte-identical). Коли обидва файли вже є, `:171-177` лише попереджає про розбіжність. Glob-порядок (`postgres_dsn` < `postgres_dsn_*` < `postgres_password`) гарантує, що в гілці пароля DSN уже оброблено. Штатним шляхом стан «DSN без пароля» тепер недосяжний: пароль пишеться до DSN, запис атомарний, запуск під lock. |
| CR-3 (гонка паралельних запусків, неатомарний запис) | **closed** | `mkdir`-lock (`:83-93`) охоплює весь цикл перевірок і записів, тому сценарії (а) з `rmdir` заглушки і (б) з перемежуванням пароля та DSN неможливі. `write_secret` (`:70-76`) пише tmp у тому ж каталозі й робить `mv -f`: rename у межах однієї FS атомарний, а між FS не буває, бо tmp лежить поруч із target. EXIT-trap ставиться лише після захоплення lock, тож чужий lock при timeout не видаляється (є тест). Trap прибирає tmp і lock при `die`/`set -e`/SIGINT/SIGTERM (bash виконує EXIT-trap при завершенні сигналом). `rm -f ""` при порожньому `tmp` повертає rc 0 без виводу (перевірено). |
| Windows / bind-mount | ok | На NTFS MSYS `mv -f` робить rename із заміною. Target замінюється лише тоді, коли порожній файл перегенеровується. Якщо Docker Desktop тримає такий файл відкритим, `mv` падає → `set -e` → exit 1, trap прибирає tmp, тобто fail-loud. На Linux rename над bind-mounted файлом дає новий inode, і працюючий контейнер бачить старий (порожній) файл до рестарту. Секрети однаково читаються лише на старті, тож регресії немає. |
| Stale lock після `kill -9` | ok (див. №3) | Наступний запуск чекає `INIT_SECRETS_LOCK_TIMEOUT` (30 с) і виходить з exit 1 та підказкою `rmdir`. Секрети не пошкоджуються: запис атомарний, напівфайлів немає. Вийти можна командою `rmdir deploy/compose/secrets/.init-secrets.lock`. Нечислове значення timeout дає помилку `[` і той самий `die`. |
| L-2 | accepted | Задокументовано в runbook (`clean-host-start.md:83`). |

### Нові знахідки

| # | severity | file:line | claim | failure scenario | verdict |
|---|---|---|---|---|---|
| 1 | low | `tests/unit/test_secrets_role_dsn_adversarial.py:86-92`, `tests/unit/test_secrets_role_dsn.py:190-197` (+ `_git_bash_dir`/`_bash`, які першим обирають `<Git>\bin\bash.exe`) | Причина флейку `test_crlf_examples_do_not_leak_carriage_returns_into_secrets` — оточення плюс тест, а не логіка скрипта. На Windows один запуск скрипта в Git Bash триває 3–7 с, бо кожен `$(…)` і зовнішня утиліта — це повільний MSYS fork. Жорсткий `timeout=60` під навантаженням хоста перевищується. До того ж `<Git>\bin\bash.exe` — лише launcher, тож `Popen.kill()` вбиває launcher, а справжній `usr\bin\bash.exe` працює далі й тримає pipe. Через це `run()` чекає ще ~70 с. | 8 паралельних прогонів тесту (окремі `--basetemp`) → 8/8 `subprocess.TimeoutExpired ... timed out after 60 seconds`, кожен ~134 с. Після цього в усіх 8 каталогах повний набір секретів, lock і tmp прибрано. Отже скрипт завершився штатно вже після kill: це орфан, а не зависання і не спільний стан (кожен тест має свій `tmp_path`, lock у ньому ж). 12 послідовних прогонів зелені (~6.7 с). Race/спільного стану немає, `text=True` з cp1251 декодує вивід без помилок (байта 0x98 у виводі немає). Виправлення: запускати `usr\bin\bash.exe` напряму і/або підняти timeout до 180 с. | CONFIRMED |
| 2 | low | `deploy/compose/secrets/init-secrets.sh:70-76, 149-166` | Фікс додав ~4 процеси на кожен секрет (`$(mktemp)` subshell + `mktemp`, `chmod`, `mv`, `$(printf …)` subshell), що посилює №1. На Linux CI це несуттєво. | Ідлова тривалість на цьому хості: до фіксу 3.4–4.3 с, після 5.5–6.7 с (`9281cdf~1` vs `9281cdf`, два заміри). Під навантаженням різниця множиться. Необов'язкові оптимізації: `printf -v` замість `$(printf …)`, tmp-ім'я через `$$` із `set -o noclobber` замість `$(mktemp)`. | CONFIRMED |
| 3 | low | `docs/runbooks/clean-host-start.md` (немає згадки), `init-secrets.sh:83-92` | Stale lock не має ні PID, ні віку, тож відрізнити живий запуск від мертвого неможливо. Runbook не згадує `.init-secrets.lock`, підказка є лише в тексті помилки. | CI-крок або оператор перериває скрипт `kill -9` (чи крах VM) → кожен наступний запуск через 30 с дає exit 1, доки lock не видалять вручну командою `rmdir`. Стан відновлюваний, підказка в stderr точна, даних не губимо, тож це лише операційне тертя. | CONFIRMED |

Critical/high/medium знахідок немає.

### Вердикт (gate 3')

**approve.** CR-1, CR-2, CR-3 і SEC L-1 закрито, L-2 прийнято й задокументовано. Нових дефектів коректності чи безпеки даних немає. Флейк CRLF-тесту спричиняють таймаут і вбивання launcher-а в тестовій обв'язці та повільний fork Git Bash на Windows. Дефекту скрипта немає. Рекомендація №1 — на розсуд owner-а (Linux CI не зачеплений).

### Що перевірено окремо

- `die` викликається лише в основному shell (`new_hex`/`new_keyfile` не обгорнуті в `$(…)`), тому exit справді завершує скрипт. `exit` у `secret_present` усередині умови `if` теж завершує скрипт.
- Під `set -e` провал `tr`/`printf` у command substitution, переданій аргументом `write_secret`, не спрацював би, але для детермінованих `tr`/`printf` це нереалістично. Присвоєння `password="$(tr …)"` перевіряється `errexit`.
- Гілка `*)` (`minio_root_user`) копіює приклад без CR і з одним завершальним LF. Порожній приклад дав би файл `"\n"`, раніше було б 0 байт. Неактуально.
- Якщо target — symlink на порожній файл, `mv` тепер замінює сам symlink, а не пише крізь нього. Раніше `>` писав у ціль symlink. Поза підтримуваним сценарієм, info.
- Сигнал у вікні між `mkdir` lock і встановленням trap лишає stale lock (див. №3). Вікно — одна інструкція, info.
- Tmp-файли й lock мають імена з крапкою, тож під glob `*.example` не потрапляють, а `.gitignore` (`deploy/compose/secrets/*`) їх ігнорує.
