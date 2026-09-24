# Dependency: WP-00 PR5 → WP-01D (compose `depends_on` на `ensure-minio`)

| Поле | Значення |
|---|---|
| Від | WP-00 PR5 (`wp/00-5-object-store-secrets`) |
| Кому | WP-01D (owner `x-worker`, `scheduler`, `*-worker` у `docker-compose.yml`); `api` — orchestrator (після WP-00 PR5 owner сервісу не визначений, до WP-11A) |
| Дата | 2026-09-24 |
| Статус | open |
| Блокує | не блокує merge WP-00 PR5; потрібне до WP-02 PR2 (`check_ready` проти реального MinIO-користувача) |

## Що потрібно

Додати в `depends_on` сервісів, які монтують `minio_<component>`, умову

```yaml
      ensure-minio:
        condition: service_completed_successfully
```

для: `discovery-worker`, `fetch-worker`, `browser-worker`, `parse-worker`, `translation-worker`,
`export-worker`, `maintenance-worker` і `api`.

## Навіщо

WP-00 PR5 створює користувачів і buckets MinIO one-shot-ом `ensure-minio`. Картка PR5 дозволяє
змінювати в цих сервісах лише ключі `secrets:`/`environment:`; `depends_on` — лише для
`projector-worker` (зроблено). Без умови сервіс може стартувати раніше, ніж `ensure-minio`
створить його користувача: перший `PutObject` отримає `InvalidAccessKeyId`, а readiness
(`check_ready` WP-02) буде хибно-негативною до рестарту. Зараз це не проявляється, бо жоден
runtime ще не ходить у MinIO з обліковими даними (WP-02 PR2 додає першого).

## Тести, які треба оновити разом зі зміною

- `tests/unit/test_compose_config.py::test_readiness_waits_for_one_shots` — прибрати виняток
  `ONE_SHOTS - {"ensure-minio"}` для `api`;
- `tests/unit/test_compose_config_adversarial.py::test_worker_readiness_dependencies_are_declared_in_depends_on`
  — додати `ensure-minio: service_completed_successfully` для MinIO-споживачів.
