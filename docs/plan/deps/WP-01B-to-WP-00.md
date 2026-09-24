# Dependency-запит: WP-01B → WP-00 (`db ensure-mongo` реалізовано; compose one-shot і Mongo-секрети)

| Поле | Значення |
|---|---|
| Від | WP-01B PR1 (`wp/01b-1-mongo-schema`) |
| До | WP-00 (owner `tests/unit/test_cli_compose_commands.py`; `docker-compose.yml`, `deploy/compose/**` — WP-00 PR5) |
| Файли | `tests/unit/test_cli_compose_commands.py` (змінено в branch WP-01B за прецедентом `WP-01A-to-WP-00.md` п.1); `docker-compose.yml`, `deploy/compose/secrets/init-secrets.sh`, `deploy/compose/README.md` (запит, не змінювались) |
| Стан | п.1 — зроблено в branch, чекає підтвердження на gate; п.2 — open, для WP-00 PR5 (уже в картці WP-00 PR5 п.2, тут — лише точний контракт CLI) |

## 1. CLI-контракт: `--validators/--indexes` більше не стаб

Картка WP-01B закріплює за WP-01B `src/collector/cli.py` «лише тіло `db ensure-mongo`: гілка
`--validators/--indexes` замість `not_implemented("WP-01B")` і новий прапорець `--users`».
Наслідок для owned-тесту WP-00:

| Тест | Був | Став |
|---|---|---|
| `test_cli_compose_commands.py::test_db_ensure_mongo_validators_indexes_are_stub_after_init` | exit 2 + `not implemented: owned by WP-01B` після ініціалізації RS | `…::test_db_ensure_mongo_validators_indexes_run_after_init` — схема застосовується лише після ініціалізації RS (`replSetInitiate` уже в командах фейкового клієнта), exit 0, без stub-рядка, клієнт закрито; `apply_mongo_schema` підмінено |

Інваріант тесту (порядок «RS → схема», відсутність stub-рядка) збережено; реальний шлях покрито
`tests/integration/mongo/test_cli_ensure_mongo.py`. Модульний docstring файлу оновлено одним
рядком.

## 2. Compose one-shot `ensure-mongo` (WP-00 PR5 п.2, п.4)

Контракт, який реалізовано в CLI і на який має спиратися compose:

- команда: `collector db ensure-mongo --validators --indexes --users`;
- `--users` читає `mongo_uri_projector`, `mongo_uri_compactor`, `mongo_uri_api_ro`,
  `mongo_uri_export_ro` з каталогу `COLLECTOR_MONGO_USER_SECRETS_DIR` (типово `/run/secrets`)
  **до** з'єднання з Mongo; бракує хоч одного — exit 1 без змін. Користувач у URI має бути
  `collector_<component>`, `authSource` — `admin` (або відсутній), пароль — непорожній;
- domain-БД: env `COLLECTOR_MONGO_DATABASE` (типово `collector`); ролі дають права саме на неї,
  тож URI компонентів мають вказувати ту саму БД (шлях URI) або не вказувати жодної;
- міграції шукаються в `migrations/mongo` вгору від cwd (`/app` в image; `Dockerfile` уже
  копіює `migration[s]/` у `/app/migrations`) або в `COLLECTOR_MONGO_MIGRATIONS_DIR`;
- root лише в `ensure-mongo` (як зараз); scheduler, fetch/discovery/browser/parse-worker
  Mongo-секретів не монтують.

**Resolved 2026-09-24:** після merge WP-00 PR5 гілка WP-01B PR1 інтегрувала `main`; compose
типово виконує `collector db ensure-mongo --validators --indexes --users` з Mongo URI secrets.
