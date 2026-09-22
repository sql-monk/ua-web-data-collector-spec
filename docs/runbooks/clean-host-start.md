# Runbook: clean-host start (Docker Compose, single host)

Мета: підняти стек UA Web Data Collector на чистому Linux-хості однією документованою
командою (§16.2, §16.3, R-51). Стан на WP-00 PR2: profiles `core` + `workers`; `gui`
додається у PR3 (тоді команда стає `--profile core --profile workers --profile gui`).

## Передумови

- Docker Engine 27+ з Compose plugin v2.30+ (перевірено на Docker 29.8 / Compose v5.5.1);
- доступ до registry: `docker.io` (python, postgres, mongo), `ghcr.io` (uv), `quay.io` (minio);
- `git clone` репозиторію; `uv`/Python на хості **не потрібні** — image збирається у Docker;
- вільні ресурси: ліміти сумарно ≈ 12 CPU / 12 ГБ для core+workers за замовчуванням
  (`deploy.resources.limits` у `docker-compose.yml`); хост може мати менше — ліміти є
  верхньою межею, не резервуванням.

## Кроки

```bash
git clone <repo> collector && cd collector

# 1. Секрети (локальні файли поза git; для не-локального використання змініть значення)
./deploy/compose/secrets/init-secrets.sh

# 2. Профілі за замовчуванням для всіх наступних команд
export COMPOSE_PROFILES=core,workers
export COLLECTOR_GIT_SHA="$(git rev-parse HEAD)"

# 3. Перевірка конфігурації та збірка image `collector`
docker compose config --quiet
docker compose build --pull

# 4. Старт із очікуванням healthy/completed
docker compose up -d --wait
docker compose ps
```

Очікуваний результат `docker compose ps`: `postgres`, `mongo`, `minio`, `api`,
`scheduler` і всі `*-worker` — `Up (healthy)`; `migrate-postgres` та `ensure-mongo` —
`Exited (0)`. Перевірка health API зсередини мережі (порт назовні не публікується):

```bash
docker compose exec api python -m collector.api.health
# postgres: ok (...)  mongo: ok (writable primary of replica set 'rs0')  minio: ok (liveness HTTP 200)
```

## Локальна розробка з портами на 127.0.0.1

```bash
docker compose -f docker-compose.yml -f deploy/compose/dev.override.yml up -d --wait
curl -s http://127.0.0.1:8000/api/v1/health/components
```

## Масштабування (§7.5)

```bash
docker compose up -d --no-recreate --scale fetch-worker=4 --scale parse-worker=2
docker compose up -d --no-recreate --scale browser-worker=1   # після canary, профіль browser
```

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
| `service "…-worker" depends on undefined service "postgres"` | запущено `--profile workers` без `core`; додайте `core` |
| `mongo` не стає healthy, у логах «permissions on … keyfile are too open» | keyfile копіюється в tmpfs з 0400 entrypoint-ом; перевірте, що `deploy/compose/secrets/mongo_keyfile` існує і readable |
| `ensure-mongo` `Exited (1)`, `Authentication failed` | `mongo_root_password` змінено після першої ініціалізації volume; скиньте `down -v` або оновіть пароль у Mongo |
| `api` `unhealthy` | `python -m collector.api.health` у контейнері покаже, який компонент `error` |
| secret file `Permission denied` у контейнері | файли секретів мають бути readable для uid 10001/999 (`chmod 0644`) |
