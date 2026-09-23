# Dependency-запит: WP-01A → WP-02 (контракт parser-а для `record_parse_result`)

| Поле | Значення |
|---|---|
| Від | WP-01A PR2 (`wp/01a-2-artifacts-projection`), gate 4 (`docs/plan/reports/WP-01A/spec-review-pr2.md` §5.2 D-2; `code-review-pr2-r2.md` N-1) |
| До | WP-02 (fetch/parse workers, claimed/verified PUT normalized artifact-ів) |
| Стан | open — вимоги до викликача; схеми й репозиторію не змінює |

`projection.record_parse_result` визначає ідемпотентність за **ідентичністю parse-кроку**
(`projection_tasks.parse_key` = sha256 від `fetch_id`, `raw_sha256`, `parser_version`,
`entity_uuid` і `target_collection`), а не за вмістом artifact. Рішення D-2 коректне лише
за трьох умов на боці parser-а.

## 1. Ключ normalized artifact — функція `(sha256, entity_uuid)`

`object_key` normalized artifact-а має однозначно виводитися з `(sha256(bytes),
entity_uuid)`, наприклад `normalized/<entity_uuid>/<sha256>.json`. Якщо ті самі bytes
тієї самої сутності записати під іншим ключем K2, репозиторій перевикористає рядок K1
(дедуплікація за вмістом). Claim K2 стане `committed`, але посилань на нього не буде, і
`list_orphan_candidates` його не віддасть: об'єкт K2 лишиться в store назавжди, а
`result.artifact.object_key` не дорівнюватиме переданому ключу (N-1).

## 2. `fetch_id` — справжній `fetches.fetch_id` кожного HTTP-запиту

`NormalizedArtifactRef.fetch_id` і `ParseAttemptRecord.fetch_id` мають бути тим самим
значенням — ідентифікатором рядка `fetches` конкретного HTTP-запиту, що приніс raw bytes.
Розбіжність або `None` репозиторій відхиляє `InvalidValueError` (SR-2). Новий fetch того
самого URL — **новий** `fetch_id`: інакше стан A→B→A виглядатиме як повтор і нова
`projection_version` не буде видана. Той самий `fetch_id` дозволений лише для повтору того
самого parse (retry job, replay після crash).

## 3. `target_schema_version` не входить у `parse_key`

Повторний parse того самого fetch тією самою версією парсера під нову схему цільової
collection вважатиметься повтором і поверне старий task. Тому **зміна схеми collection має
супроводжуватися зміною `parser_version`**. Тоді reprojection — це новий parse, нова версія і
новий task.
