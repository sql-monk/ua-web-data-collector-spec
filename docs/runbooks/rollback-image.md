# Runbook: rollback image `collector` (Docker Compose, single host)

Мета: відкотити application image `collector` (`api`, `scheduler`, workers, CLI) до
попередньо відомої-робочої версії за tag або digest, перевірити OCI-labels і повернути
compose-стек до попереднього стану без втрати даних named volumes (§7.5, ADR-0002).

## Контекст і обмеження

- `COLLECTOR_IMAGE` (default `collector:dev`, CI — `collector:ci`) — єдина точка керування
  версією application image (`docker-compose.yml`, anchor `x-collector-image`). Vendor images
  (`postgres`, `mongo`, `minio`) вже pinned `tag@digest` у базовому файлі — їхній rollback =
  правка digest у `docker-compose.yml` і крок 3 нижче (без окремої процедури).
- **Відоме обмеження (ADR-0002, знахідка пострев'ю F-3, owner WP-14):** `collector` зараз має
  лише mutable local/CI tag, без публікації в registry з immutable digest. Тому rollback «за
  digest» на одному хості можливий лише якщо потрібний image ще є в локальному Docker image
  cache, або зібраний повторно з попереднього Git SHA (детерміновано за `uv.lock`); rollback
  pull з зовнішнього registry за digest — `operationally unverified`, owner WP-14 (registry
  pipeline, поза PR2).

## 1. Визначити поточну версію

```bash
docker compose ps --format '{{.Name}} {{.Image}}'
docker inspect collector:dev --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}'
docker inspect collector:dev --format \
  '{{index .Config.Labels "ua.collector.schema-version"}}'
```

Формат перевірено (`testing-pr2.md`, §1.2): `docker inspect collector:dev --format
'…Healthcheck=…'` повертає, серед іншого, `Labels: org.opencontainers.image.revision=<git
sha>, org.opencontainers.image.version=…, ua.collector.schema-version=…`.

## 2. Отримати image потрібної версії

Варіант А — image ще в локальному cache (`docker images collector`):

```bash
docker tag collector:dev collector:rollback-backup   # опційно: зберегти поточний перед заміною
docker tag <previous-image-id-or-tag> collector:dev  # локальний rollback
```

Варіант Б — детермінований ребілд з попереднього Git SHA (`uv.lock` фіксує залежності):

```bash
git worktree add /tmp/collector-rollback <previous-sha>
cd /tmp/collector-rollback
COLLECTOR_GIT_SHA=<previous-sha> COLLECTOR_IMAGE=collector:dev docker compose build --pull
cd - && git worktree remove /tmp/collector-rollback
```

Варіант В (після появи registry pipeline, owner WP-14) — pull за digest:

```bash
docker pull <registry>/collector@sha256:<digest>
export COLLECTOR_IMAGE=<registry>/collector@sha256:<digest>
```

Варіант В `operationally unverified` — публікація digest у registry ще не реалізована (F-3).

## 3. Застосувати без ребілду, не втрачаючи дані

```bash
docker compose up -d --no-build --wait
docker compose ps
```

`--no-build` не дає Compose перезібрати image, навіть коли `build:` присутній поряд з
`image:` у файлі (`docker compose up --help`, Compose v5.5.1). Named volumes
(`collector_postgres-data`, `collector_mongo-data`, `collector_mongo-config`,
`collector_minio-data`) не видаляються ні `up`, ні `restart` — лише явний `down -v`
(`docs/runbooks/clean-host-start.md`, «Restart без втрати даних»); заміна image сама по собі
даних stateful-сервісів не чіпає.

## 4. Перевірити labels після rollback

```bash
docker inspect $(docker compose ps -q api) --format \
  '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

Очікується Git SHA попередньої версії, а не поточний HEAD; якщо збігається з HEAD —
`docker compose up` використав старий контейнер без заміни image (перевірте `docker images
collector` і крок 2).

## 5. Відкат самого `docker-compose.yml` до попередньої версії

Коли відкату потребує не лише image, а й конфігурація compose (наприклад, попередній набір
profiles/мереж):

```bash
git log --oneline -- docker-compose.yml deploy/compose   # знайти потрібний commit
git checkout <previous-commit> -- docker-compose.yml deploy/compose
docker compose config --quiet                             # перевірити валідність конфігу
docker compose up -d --wait
```

`name: collector` і назви named volumes фіксовані у файлі (ADR-0002, «Обмеження: фіксоване
ім'я проєкту `collector`») і не залежать від вмісту сервісів — тому відкат конфігурації сам
по собі не видаляє дані. **Не виконуйте `down -v`** під час rollback. Після завершення
rollback-вікна поверніть робочу копію: `git checkout HEAD -- docker-compose.yml
deploy/compose` (або змерджіть потрібну гілку).

## Обмеження / не покриває цей runbook

- Rollback схеми БД (PostgreSQL migrations, MongoDB validators/indexes) — forward-only у PR2
  (`migrate-postgres` — стаб); down-migrations і сумісність схеми — owner WP-01A/WP-01B.
- Rollback vendor images (`postgres`/`mongo`/`minio`) — зміна `tag@digest` у
  `docker-compose.yml`, той самий крок 3 (без ребілду).
- Автоматизований release/registry rollback (`collector release verify`, digest з registry
  у GUI-керованому процесі) — WP-11A/WP-14, поза PR2.
