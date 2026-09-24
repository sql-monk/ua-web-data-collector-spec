# Runbook: clean-host start (Docker Compose, single host)

Мета: підняти стек UA Web Data Collector на чистому Linux-хості однією документованою
командою (§16.2, §16.3, R-51). Стан на WP-00 PR3: profiles `core` + `workers` + `gui` —
повна команда §16.2.

## Передумови

- Docker Engine 27+ з Compose plugin v2.30+ (перевірено на Docker 29.8 / Compose v5.5.1);
- доступ до `docker.io` (python, postgres, mongo, pinned Go/Alpine bases), `ghcr.io` (uv) і
  Go module proxy/GitHub (збірка exact MinIO release; Quay image більше не використовується);
- `git clone` репозиторію; `uv`/Python на хості **не потрібні** — image збирається у Docker;
- вільні ресурси: ліміти сумарно **16 CPU / 16 ГіБ** для core+workers за замовчуванням
  (`deploy.resources.limits` у `docker-compose.yml`: postgres 2/2G + mongo 2/2G + minio 1/1G +
  api 1/1G + scheduler 0.5/512M + discovery 0.5/512M + fetch 2×1/1G + parse 2×2/2G +
  projector 1/1G + translation 0.5/512M + export 1/1G + maintenance 0.5/512M; one-shots
  0.5/512M короткочасно); хост може мати менше — ліміти є верхньою межею, не резервуванням.
  Фактичне споживання placeholder-стека — < 1 CPU / ~1.5 ГіБ.

## Кроки

```bash
git clone <repo> collector && cd collector

# 1. Секрети (локальні файли поза git; для не-локального використання змініть значення)
./deploy/compose/secrets/init-secrets.sh

# 2. Профілі за замовчуванням для всіх наступних команд
export COMPOSE_PROFILES=core,workers,gui
export COLLECTOR_GIT_SHA="$(git rev-parse HEAD)"

# 3. Перевірка конфігурації та збірка image `collector`
docker compose config --quiet
docker compose build --pull

# 4. Старт із очікуванням healthy/completed
docker compose up -d --wait
docker compose ps
```

Очікуваний результат `docker compose ps`: `postgres`, `mongo`, `minio`, `api`,
`scheduler`, `gui` і всі `*-worker` — `Up (healthy)`; `migrate-postgres`, `ensure-mongo` та
`ensure-minio` — `Exited (0)`. `gui` — єдиний контейнер з published port (`0.0.0.0:80->8080/tcp`):

```bash
curl -sI http://localhost/                          # сторінка + CSP/security headers
curl -s  http://localhost/api/v1/health/components  # {"status":"ready"} через proxy gui→api
```

Перевірка health API зсередини мережі (порт api назовні не публікується):

```bash
docker compose exec api python -m collector.api.health
# postgres: ok (...)  mongo: ok (writable primary of replica set 'rs0')  minio: ok (liveness HTTP 200)
```

## Секрети: права файлів

`init-secrets.sh` генерує випадкові паролі (`openssl rand -hex 24`) і keyfile; файли мають
режим **0644** свідомо: Compose bind-mount-ить file-secrets з правами хоста, а читають їх
non-root uid контейнерів (999, 10001) — 0600 від користувача хоста дає `Permission denied`
на Linux. Наслідок: секрети читає будь-який локальний користувач хоста з доступом до
каталогу репозиторію — прийнятно лише для single-host MVP (ADR-0002); для спільного хоста
обмежте каталог (`chmod 0700 deploy/compose/secrets` не допоможе контейнерам — потрібні
Swarm secrets, WP-01D).

Окрім паролів stateful-сервісів, скрипт створює вісім DSN: `postgres_dsn` (міграції, пароль =
`postgres_password`) і сім `postgres_dsn_<component>` для runtime-ролей §13 — користувач
`collector_<component>`, у кожного **власний** випадковий пароль. One-shot `migrate-postgres`
виконує `collector db migrate && collector db roles --with-login` і робить ці ролі LOGIN-ролями.
Перевірка після `up -d --wait`:

```bash
docker compose exec -T postgres sh -c 'PGPASSWORD="$(cat /run/secrets/postgres_password)" \
  psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "SELECT rolname FROM pg_roles WHERE rolcanlogin AND rolname <> current_user ORDER BY 1"'
# 7 рядків: collector_api_ro … collector_translation (collector_migrate — NOLOGIN)
```

`REVOKE … FROM PUBLIC` на БД кластера (`deploy/compose/postgres/init/02-revoke-public.sql`)
виконується **лише при першому initdb** — на порожньому томі `postgres-data`. На кластері,
створеному до WP-00 PR4, PUBLIC і далі має CONNECT/TEMP, доки оператор один раз не виконає
команду з `deploy/compose/postgres/init/README.md` («Кластер, створений до WP-00 PR4») або
`docker compose down -v`. Автоматичної перевірки немає — прийнятий ризик (security-pr4 L-2,
owner WP-00 / оператор, 2026-09-24).

Секрети, створені до WP-00 PR4, лишаються (скрипт не перезаписує наявні файли) — повторний
запуск `init-secrets.sh` лише додасть сім нових DSN.

### MinIO, MongoDB і провайдер перекладу: облікові дані компонентів (WP-00 PR5)

Той самий `init-secrets.sh` генерує ще шість `minio_<component>` (`access_key=collector-<component>`
і випадковий 40-hex `secret_key`), чотири `mongo_uri_<component>` (власний пароль кожному) і
створює **порожній** `google_translation_credentials` (credential провайдера перекладу вписує
оператор; порожній = переклад вимкнено). На хості до PR5 повторний запуск лише додасть ці файли.
Формати, мапа споживачів і ротація — `deploy/compose/README.md`, розділ «Секрети».

One-shot `ensure-minio` створює buckets і користувачів MinIO з policies. Перевірка після
`up -d --wait` (з контейнера `ensure-minio`-образу, секрети лише з файлів, у argv — нічого):

```bash
docker compose run --rm --no-deps --entrypoint bash ensure-minio -c '
  export MC_HOST_c="http://$(cat /run/secrets/minio_root_user):$(cat /run/secrets/minio_root_password)@minio:9000"
  mc ls c; mc admin user list c'
# buckets archive/ events/ normalized/ raw/ translated/; шість enabled collector-<component>
```

`ensure-mongo` поки лише ініціалізує replica set: `--validators --indexes --users` вмикає
`COLLECTOR_ENSURE_MONGO_SCHEMA=1` після merge WP-01B PR1 (до того прапорців у CLI немає, і
one-shot завершився б помилкою).

### Завислий lock `init-secrets.sh` (`.init-secrets.lock`)

Скрипт серіалізує паралельні запуски каталогом `deploy/compose/secrets/.init-secrets.lock`, а
PID власника записує в `.init-secrets.lock/pid`. Після звичайного завершення, помилки,
Ctrl+C чи `kill` (SIGTERM) lock прибирається сам. Лишається він лише після `kill -9`, краху
VM або обриву сесії посеред запуску. Тоді кожен наступний запуск через 30 с (змінна
`INIT_SECRETS_LOCK_TIMEOUT`) завершується помилкою
`error: інший init-secrets.sh тримає …/.init-secrets.lock понад 30 с (PID власника: N)`.

Як розпізнати й прибрати:

```bash
cat deploy/compose/secrets/.init-secrets.lock/pid      # PID власника
ps -p "$(cat deploy/compose/secrets/.init-secrets.lock/pid)" || echo "процесу немає — lock завислий"
rm -r deploy/compose/secrets/.init-secrets.lock        # лише якщо процесу немає
./deploy/compose/secrets/init-secrets.sh
```

Автоматично скрипт завислий lock не знімає, і це свідомо. Два запуски, що одночасно визнали
lock мертвим, могли б зняти вже новий, живий lock. До того ж PID у Git Bash (MSYS) не
збігається з PID Windows. Секрети при завислому lock не пошкоджуються: кожен файл пишеться
атомарно (tmp + `mv`), напівзаписаних файлів не буває. Тимчасові `.<name>.tmp.*`, якщо
лишилися після `kill -9`, можна видалити.

## Локальна розробка з портами на 127.0.0.1

```bash
docker compose -f docker-compose.yml -f deploy/compose/dev.override.yml up -d --wait
curl -s http://127.0.0.1:8000/api/v1/health/components
```

## Масштабування (§7.5)

```bash
docker compose up -d --no-recreate --scale fetch-worker=4 --scale parse-worker=2
# browser-worker живе у профілі `browser`: додайте його до COMPOSE_PROFILES (після canary, §8).
COMPOSE_PROFILES=core,workers,browser docker compose up -d --no-recreate --scale browser-worker=1
```

Прапорець `--profile <name>` **замінює** значення `COMPOSE_PROFILES`, а не доповнює його:
`docker compose --profile browser up …` активує лише `browser` без `core` і падає з
`depends on undefined service "postgres"`. Або перелічуйте всі профілі прапорцями
(`--profile core --profile workers --profile browser`), або задавайте повний список у
`COMPOSE_PROFILES`.

## Restart без втрати даних

`docker compose restart` перезапускає контейнери; named volumes (`collector_postgres-data`,
`collector_mongo-data`, `collector_mongo-config`, `collector_minio-data`) зберігаються.
Workers завершуються по SIGTERM (drain) у межах `stop_grace_period` 120 с.

## Зупинка

```bash
docker compose down            # контейнери й мережі; дані у volumes лишаються
docker compose down -v         # + видалення volumes (усі дані!)
```

## Типові проблеми

| Симптом | Причина / дія |
|---|---|
| `service "…-worker" depends on undefined service "postgres"` | активовано профіль без `core` (напр. `--profile workers` або `--profile browser` — прапорець замінює `COMPOSE_PROFILES`); додайте `core` |
| `up -d --wait` завершився з кодом 1, `mongo` має `RestartCount` > 0, стек далі стає healthy | init-фаза entrypoint mongo (виправлено healthcheck-ом у PR2 gate 2, 10/10 циклів зелені); якщо повториться — повторний `docker compose up -d --wait` штатний (ідемпотентний), повідомте owner WP-00/WP-01B з `docker events` |
| `mongo` не стає healthy, у логах «permissions on … keyfile are too open» | keyfile копіюється в tmpfs з 0400 entrypoint-ом; перевірте, що `deploy/compose/secrets/mongo_keyfile` існує і readable |
| `ensure-mongo` `Exited (1)`, `Authentication failed` | `mongo_root_password` змінено після першої ініціалізації volume; скиньте `down -v` або оновіть пароль у Mongo |
| `api` `unhealthy` | `python -m collector.api.health` у контейнері покаже, який компонент `error` |
| `gui` `unhealthy`, але `curl http://localhost/` віддає сторінку | так і задумано: healthcheck `gui` — це **readiness** (nginx + `api` через proxy), тому падіння `api` робить `gui` unhealthy за ~45 с (`interval 15s × retries 3`), хоча статика далі 200. Діагностика: `curl -s http://localhost/api/v1/health/components` → `not_ready` означає проблему в `api`, не в nginx. Liveness самого nginx — окремий `curl -s http://localhost/healthz` → `ok` (не залежить від `api`). Після відновлення `api` gui стає healthy сам (перевірено — ~20 с) |
| `up -d --wait` падає з `container collector-gui-1 is unhealthy` | той самий механізм: на деградованому стеку (`api` не healthy) команда з `--wait` впаде свідомо. Спершу полагодьте `api` (`docker compose logs api`, `docker compose exec api python -m collector.api.health`), потім повторіть `up -d --wait` — вона ідемпотентна |
| порт 80 на хості зайнятий | `GUI_PORT=8081 docker compose up -d --wait` |
| після `git pull` (хост до WP-00 PR4) `up` без `init-secrets.sh`: `migrate-postgres` `Exited (1)` або помилка монтування secret; у `deploy/compose/secrets/` з'явились **каталоги** `postgres_dsn_<component>/` | Compose не знайшов файл секрету, і Docker Desktop створив на його місці порожній каталог (на Linux engine `up` натомість падає з помилкою про відсутній файл). Запустіть `./deploy/compose/secrets/init-secrets.sh`: він прибирає порожні каталоги-заглушки (`fix   … прибрано`) і додає лише відсутні секрети, наявні не чіпає; потім `docker compose up -d --wait`. Якщо скрипт зупинився з `error: … каталог, а не файл секрету` — каталог не порожній: перевірте вміст, видаліть його (`rm -r`) і повторіть |
| `migrate-postgres` `Exited (1)`, `у /run/secrets бракує DSN-секретів: …` | у контейнер не змонтовано частину `postgres_dsn_<component>` (напр. власний override без них): поверніть монтування всіх семи в `migrate-postgres` |
| `ensure-minio` `Exited (1)`, `…/minio_<component>: access_key має бути …` або `secret_key має бути 40 hex-символів` | файл секрету змінено вручну або він з іншого формату; видаліть його, запустіть `init-secrets.sh`, потім `docker compose up -d ensure-minio` і перестворіть сервіси, що його монтують |
| `ensure-minio` `Exited (1)`, `collector-<component>: очікувалась лише policy …` | користувачу вручну прикріплено ще одну policy; `mc admin policy detach <alias> <policy> --user collector-<component>` і повторіть `up` |
| `ensure-mongo` `Exited (2)`, `COLLECTOR_ENSURE_MONGO_SCHEMA має бути 0 або 1` | задайте `0` або `1` (або приберіть змінну — типово `0`) |
| `ensure-mongo` `Exited (2)` після `COLLECTOR_ENSURE_MONGO_SCHEMA=1` | CLI ще без `--users` (WP-01B PR1 не злитий) — поверніть `0` |
| secret file `Permission denied` у контейнері | файли секретів мають бути readable для uid 10001/999 (`chmod 0644`) |
