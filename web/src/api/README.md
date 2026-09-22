# `src/api` — клієнт operator API

Каталог містить **заготовку** (WP-00 PR3). Реальний типізований клієнт тут не пишеться
вручну: він **генерується з OpenAPI-специфікації** FastAPI BFF у **WP-11C** (контракт —
§7.7, §8 ТЗ, FR-034).

## Що вже є

| Файл               | Призначення                                                                |
| ------------------ | -------------------------------------------------------------------------- |
| `schemaVersion.ts` | `API_SCHEMA_VERSION` (placeholder) і `API_BASE_PATH = '/api/v1'`           |
| `queryClient.ts`   | `QueryClient` TanStack Query + `apiQueryKey()` — cache key з версією схеми |

Згенерованих файлів у git ще немає, тому імпортувати з `src/api/generated/` поки нема чого.

## Як це працюватиме у WP-11C

1. API віддає специфікацію за `GET /api/v1/openapi.json` (FastAPI, owner WP-11A).
2. Крок генерації (`npm run api:generate`) зберігає специфікацію у `src/api/openapi.json` і
   генерує типи та клієнт у `src/api/generated/` (генератор фіксується в WP-11C: кандидати —
   `openapi-typescript` + typed `fetch`, або `orval`/`hey-api`; вибір — ADR WP-11C).
3. Згенерований код **комітиться** у git і перевіряється в CI кроком «generate → `git diff
--exit-code`»: drift між специфікацією і клієнтом має падати, а не мовчки розходитись.
4. Query hooks кладуться у `src/features/<екран>/`, а не сюди; сюди — лише транспорт і типи.

## Інваріанти, які має зберегти генерований клієнт

- **Same-origin, без base URL.** Усі запити йдуть на відносний `/api/...` — той самий origin,
  що й статика; проксіює nginx (§8). Абсолютний host у клієнті — помилка конфігурації.
- **Cookie-сесія, не токен у коді.** `credentials: 'same-origin'`; access/refresh tokens
  недоступні JS (`HttpOnly` cookie від OIDC BFF, §13). Записувати їх у
  `localStorage`/`sessionStorage` заборонено — ESLint падає (`eslint.config.js`, `STORAGE_BAN`).
- **CSRF.** Кожен мутуючий запит несе CSRF-токен з non-`HttpOnly` cookie у заголовку (§7.7,
  §13); GET/HEAD — без нього.
- **Cache keys з версією.** Ключі будуються через `apiQueryKey()` з `API_SCHEMA_VERSION`.
- **Cursor pagination.** Списки — server-side cursor/filter/sort; клієнт не робить
  offset-пагінації і не тримає повний набір у пам'яті (§7.7).
- **SSE окремо від query cache.** Live counters — `EventSource` з event cursor, heartbeat і
  reconnect; після gap — snapshot refresh через звичайний query (§7.7). Це теж WP-11C.
