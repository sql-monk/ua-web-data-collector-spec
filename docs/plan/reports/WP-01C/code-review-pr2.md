# WP-01C PR2 — code review

Діф: `git diff main...wp/01c-2-payload-news-contracts` (8 комітів, до `3e835fb`).
Рев'юер: read-only; код не змінювався.

## Знахідки (за severity)

Critical/high/medium — немає.

1. low | `src/collector/contracts/_base.py:55,63` | Ключі `dict[str, JsonValue]` валідуються в lax-режимі: `bytes`-ключ мовчки приводиться до `str`, і колізія `bytes`/`str` дає «останній перемагає» без помилки. Масиви після M-1 strict, а ключі ні, тож захист неповний. | `BoundedJsonObject` з `{b"a": 1, "a": 2}` → `{"a": 2}` без помилки (відтворено через `TypeAdapter(BoundedJsonObject).validate_python`). Parser із помилкою в Python-шляху втрачає значення, а `state_hash` рахується вже з урізаного блоку. JSON-шлях це не зачіпає. Виправлення: `dict[Annotated[str, Strict()], JsonValue]`. | CONFIRMED
2. low | `src/collector/contracts/current.py:27` (`compute_state_hash_v1` через `canonical_json_bytes`) | Canonical form розрізняє `1` і `1.0` (і `0.0`/`-0.0`), тому семантично рівні значення дають різний `state_hash`. | Parser віддає `{"price": 100}` з одного шляху і `{"price": 100.0}` з іншого (наприклад, після `float()` у нормалізаторі) → `state_changed=True`, зайва версія, observation `changed` і `domain.changed` без реальної зміни. Відтворено: `compute_state_hash_v1({"a":1},{},{}) != compute_state_hash_v1({"a":1.0},{},{})`. Варто або явно задокументувати в `docs/contracts.md` §canonical, що тип числа входить у hash, або нормалізувати integral float. | PLAUSIBLE (залежить від поведінки parser-ів)
3. low | `src/collector/contracts/payload.py:74-80`, `records.py:53-55` | `frozen=True` не робить блоки `core`/`attributes`/`latest_state`/`values` immutable: це звичайні `dict`. Мутація після валідації обходить `require_bounded_json` та інваріант `EntityProjectionVersion` «`state_hash` == hash(snapshot)». Патерн успадкований з PR1 (`CurrentDocumentBase`). | `v.snapshot.core["x"] = "y" * 10**7` після `EntityProjectionVersion.model_validate(...)` → запис із неправильним `state_hash` і блоком понад 1 MiB без помилки. Для споживачів варто задокументувати правило «не мутувати» або повторно валідувати перед записом. | CONFIRMED (семантика Pydantic `frozen`)
4. low | `src/collector/contracts/news.py:250-254` | У `NewsTranslation` `title`/`lead`/`body_text` не мають верхньої межі (на відміну від `BoundedJsonObject`/`source_locale_raw`). Порядок `quality_flags` не канонізований. | Провайдер повертає body на кілька МБ inline → рядок PostgreSQL/повідомлення без обмеження, хоча є альтернатива `body_artifact`. `[a, b]` і `[b, a]` дають різні canonical bytes для рівних перекладів. Незначно, бо ключ ідемпотентності від прапорців не залежить. | PLAUSIBLE

## Вердикт

**approve**: critical/high немає, 4 знахідки low.

## Що перевірено окремо

- **Детермінізм/canonical:** `set`/`frozenset`/`tuple` у `JsonValue` відхиляються на всіх рівнях вкладеності (відтворено). Сурогати: у Python-вході (зокрема в ключі) відхиляються через `CanonicalEncodingError` → `ValueError`; JSON-вхід із lone surrogate відхиляє jiter. `NaN` у JSON відхиляється. NFC-колізія ключів дає помилку валідації. `CanonicalEncodingError` — підклас `ValueError`, тож зміна в `canonical.py` не ламає наявних `except ValueError`. Тест `PYTHONHASHSEED` у 4 процесах коректний.
- **`BoundedJsonObject`:** рекурсія `_check_bounded` обмежена глибиною 8 до обчислення canonical bytes. Діапазон int64 перевіряється і для Python-, і для JSON-входу (`2**64` відхилено). Межа блоку 1 MiB > 256 KiB — узгоджено, бо `EventTooLargeError` → `payload_artifact` для `domain.changed`. Payload у сумі ≤ 4 MiB (3 блоки + `observation.values`) < 16 MiB.
- **Versioning:** на `main` немає жодного з нових snapshot-ів чи моделей, тож `1.0` справді ще не злитий: `language_unsupported` і `max_length=64` без bump коректні. Посилення `JsonValue` і `canonical.py` зачіпає лише Python-шлях злитих PR1-моделей, JSON Schema не змінилась. Drift-тест (`test_schema_snapshots.py`) зелений.
- **Pydantic-моделі:** усі на `ContractModel`/`VersionedDocument` (frozen, `extra=forbid`, `validate_default`). Дублювання базових класів немає. Повторно використано `SourceRef`, `Lineage`, `EntityTime`, `Money`, `ContactValue`, `NormalizedArtifactRef`, `translation_idempotency_key`, `compute_state_hash_v1`. Datetime — `UtcDatetime`. Source time nullable (`ReviewQuestionRecord.published_at/updated_at`, `SourceTime`); R-43 перевіряється через `EntityTime.combine` у validator payload.
- **`_id`:** `id: UUID = Field(alias="_id")` з `populate_by_name` — той самий патерн, що `CurrentDocumentBase`. У snapshot-ах `_id` required, `format: uuid`. Для persistence (WP-01B) обов'язковий `by_alias=True`, це задокументовано в `docs/contracts.md`.
- **`check_payload_matches_artifact`:** перевіряє `entity_uuid`, точну рівність `schema_version`, сумісність `entity_kind`→`domain`. `KeyError` для нового `EntityKind` неможливий, поки діє тест `set(ENTITY_KIND_DOMAINS) == set(EntityKind)`.
- **Інваріанти `EntityProjectionVersion`:** перевірено випадки `previous=None`, рівної версії (replay → `applied_to_current=False`), `state_changed` ⇔ hash ≠ previous.
- **`encode_event`:** media type береться з `ClassVar` класу події. `ClassVar` не потрапляє в schema. Циклічного імпорту `events`↔`news` немає.
- **Тести:** 239 тестів PR2 і drift пройшли (`uv run pytest` на 7 файлах, 14 с). Тести перевіряють поведінку (відхилення, round-trip, hash across seeds), а не реалізацію.
