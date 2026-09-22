# JSON Schema snapshots shared-контрактів

Згенеровані файли; **не редагувати вручну**. Джерело — Pydantic-моделі
`src/collector/contracts/**` (owner WP-01C, `docs/contracts.md`).

## Генерація і перевірка

```bash
uv run collector contracts export           # перегенерувати всі snapshot-и у schemas/
uv run collector contracts export --check   # exit 1, якщо файл відрізняється від моделі (drift)
```

CI виконує `tests/contract/contracts/test_schema_snapshots.py` — той самий drift-check.
Після будь-якої зміни моделі (включно з docstring, який потрапляє у `description`)
перегенеруйте та закомітьте snapshot-и.

## Структура

`schemas/<group>/<name>.v<major>.json` — JSON Schema draft 2020-12, ключі відсортовані,
indent 2, LF; `$id` = `https://ua-collector.local/schemas/<group>/<name>.v<major>.json`,
`x-contract-version` = повна версія `major.minor` моделі.

| Група | Вміст | Споживачі |
|---|---|---|
| `common/` | value objects і shared блоки (identity, temporal, money/contacts, artifact refs, upload claim, lineage) | усі WP |
| `events/` | повідомлення між компонентами: `projection_command`, `projection_acknowledgement`, `domain_changed_event`, `encoded_event` | WP-01A (outbox), WP-01D, consumers |
| `mongo/` | документи MongoDB: `current_document_base`, `applied_projection_receipt` — з них WP-01B генерує `$jsonSchema` validators | WP-01B, WP-07, WP-09 |
| `releases/` | `release_manifest`, `release_part`, watermark/inclusion/versions, `resolution_decision`, `resolution_snapshot` | WP-11A, WP-11C |

Major піднімається лише для breaking-змін (§9.4) — тоді з'являється новий файл `*.v2.json`;
minor-зміни оновлюють файл на місці. Процедура — `docs/contracts.md`, розділ 3.
