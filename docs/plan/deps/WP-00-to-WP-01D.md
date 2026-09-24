# Dependency: WP-00 PR5 → WP-01D (`depends_on` на `ensure-minio`; тест-вартовий DSN)

| Поле | Значення |
|---|---|
| Від | WP-00 PR5 (`wp/00-5-object-store-secrets`) |
| Кому | WP-01D (owner `x-worker`, `scheduler`, `*-worker` у `docker-compose.yml`); `api` — orchestrator (після WP-00 PR5 owner сервісу не визначений, до WP-11A) |
| Дата | 2026-09-24 |
| Статус | п.1–2 — resolved by orchestrator, 2026-09-24 (виконано в WP-00 PR5) |
| Блокує | п.1 — не блокує merge WP-00 PR5, потрібне до WP-02 PR2; **п.2 — блокує зелений `pytest -m "not live"` гілки WP-00 PR5** |

## 1. `depends_on: ensure-minio` — resolved by orchestrator, 2026-09-24

Рішення: дозволено WP-00 PR5 для всіх споживачів MinIO і `api` (мінімальні правки в сервісах
WP-01D). Виконано: `api`, `discovery-`, `fetch-`, `browser-`, `parse-`, `translation-`,
`export-`, `maintenance-worker` (для сервісів, що успадковували `depends_on` з `x-worker`, блок
повторено явно + `ensure-minio`); вартовий
`test_secrets_object_store.py::test_every_minio_consumer_waits_for_ensure_minio`.

### Що потрібно

Додати в `depends_on` сервісів, які монтують `minio_<component>`, умову

```yaml
      ensure-minio:
        condition: service_completed_successfully
```

для: `discovery-worker`, `fetch-worker`, `browser-worker`, `parse-worker`, `translation-worker`,
`export-worker`, `maintenance-worker` і `api`.

### Навіщо

WP-00 PR5 створює користувачів і buckets MinIO one-shot-ом `ensure-minio`. Картка PR5 дозволяє
змінювати в цих сервісах лише ключі `secrets:`/`environment:`; `depends_on` — лише для
`projector-worker` (зроблено). Без умови сервіс може стартувати раніше, ніж `ensure-minio`
створить його користувача: перший `PutObject` отримає `InvalidAccessKeyId`, а readiness
(`check_ready` WP-02) буде хибно-негативною до рестарту. Зараз це не проявляється, бо жоден
runtime ще не ходить у MinIO з обліковими даними (WP-02 PR2 додає першого).

### Тести, які треба оновити разом зі зміною

- `tests/unit/test_compose_config.py::test_readiness_waits_for_one_shots` — прибрати виняток
  `ONE_SHOTS - {"ensure-minio"}` для `api`;
- `tests/unit/test_compose_config_adversarial.py::test_worker_readiness_dependencies_are_declared_in_depends_on`
  — додати `ensure-minio: service_completed_successfully` для MinIO-споживачів.

## 2. `tests/unit/workers/test_db_login.py::test_compose_mounts_the_dsn_of_the_role_the_process_verifies` — resolved by orchestrator, 2026-09-24

Рішення: разовий виняток WP-00 PR5. Інваріант уточнено до «рівно один PostgreSQL DSN (per-role
або міграційний рахуються разом) — роль, яку перевіряє процес; міграційного `postgres_dsn` немає»;
інші типи секретів дозволені. Додано негативні кейси
`test_dsn_invariant_rejects_extra_foreign_or_missing_postgres_dsn` (два per-role DSN, +міграційний,
чужа роль, без DSN) і позитивний `test_dsn_invariant_allows_other_component_secrets`.

Тест (owner WP-01D, forbidden для WP-00 PR5) перевіряє `services[name]["secrets"] == [secret]`,
тобто що runtime-сервіс монтує **лише** DSN. WP-00 PR5 за карткою (п.1–4) додає workers
`minio_<component>` / `mongo_uri_<component>` / `google_translation_credentials`, тож на гілці
`wp/00-5-object-store-secrets` тест червоний (єдиний провал `pytest -m "not live"`).

Пропозиція (інваріант «сервіс монтує DSN саме тієї ролі, яку перевіряє процес» зберігається):

```python
dsn_secrets = [s for s in services[name]["secrets"] if s.startswith("postgres_dsn")]
assert dsn_secrets == [secret], name
```

Повну мапу «сервіс → рівно ці secrets» після PR5 тримає
`tests/unit/test_compose_config_adversarial.py::test_runtime_services_mount_exactly_their_own_credentials`
(owner WP-00), тому послаблення тут не прибирає least-privilege перевірку. Варіант для
оркестратора: разовий виняток WP-00 PR5 на цей рядок (як у PR1b для дзеркальних рядків compose).
