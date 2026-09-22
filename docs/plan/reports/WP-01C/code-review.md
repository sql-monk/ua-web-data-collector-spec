# WP-01C — код-рев'ю (`wp/01c-contracts`)

| Поле | Значення |
|---|---|
| WP | WP-01C «Shared data contracts» |
| Branch / worktree | `wp/01c-contracts` / `.worktrees/wp-01c`, HEAD `a2622df` (fix T-01/T-02/T-06 у `44c8aa3`) |
| Diff | `git diff main...HEAD` без `docs/plan/reports/**`: `src/collector/contracts/**` (15 модулів), `schemas/**` (32 snapshot), `src/collector/cli.py` (група `contracts`), `src/collector/core/version.py`, `pyproject.toml`, `.github/workflows/ci.yml`, тести |
| Рев'юер | wp-code-reviewer (read-only; єдиний запис — цей файл) |
| Середовище | Windows 11, CPython 3.13.9, pydantic 2.13.5 |
| **Вердикт** | **approve** — critical/high немає; 2 medium (детермінізм `state_hash` на межі серіалізації; квадратичний `project_groups`) рекомендовано виправити до появи споживачів (WP-01B/резолюція), 8 low |

Питання рев'ю: «Чи код правильний, безпечний для даних і не складніший, ніж потрібно?» Відповідність ТЗ — пострев'ю; явні розбіжності позначено `spec-mismatch`.

## 1. Знахідки

Формат: `severity | file:line | claim | failure scenario | verdict`.

### Medium

**CR-01** | medium | `src/collector/contracts/current.py:193-208, 262-271` (`compute_state_hash_v1`, `CurrentDocumentBase._consistent`) | `core/attributes/latest_state` типізовані як `dict[str, Any]`, а `state_hash` рахується canonical-кодуванням Python-значень; validator при читанні перераховує hash. Для не-JSON скалярів (`datetime`, `Decimal`, `UUID`, `bytes`) canonical-представлення (`…T12:00:00.000000Z`, `"1.5"`) не збігається з тим, що дає Pydantic JSON/BSON після збереження (`…T12:00:00Z`, naive datetime з pymongo без `tz_aware`, BSON ms-precision, `Decimal128`) — hash «пливе» на межі серіалізації. | Вхід: `core={"published_at": datetime(2026,9,1,12,tzinfo=UTC)}`, `state_hash=compute_state_hash_v1(core, {}, {})` → модель конструюється; `CurrentDocumentBase.model_validate_json(doc.model_dump_json(by_alias=True))` → `ValidationError: state_hash не збігається…`. Naive datetime (як повертає pymongo) у `core` → `CanonicalEncodingError` замість валідації. Результат: документ, що пройшов validation при записі, не читається назад, або projector після re-read бачить «зміну стану» без зміни даних (зайві observation/`domain.changed`). | **CONFIRMED** (probe, pydantic 2.13.5)
Рекомендація: зробити blocks strict-JSON — рекурсивний validator/тип `JsonValue` без `Any` (дозволено лише `str|int|float|bool|None|list|dict`), який відхиляє `datetime/Decimal/UUID/bytes/Enum`; те саме для `DomainChangedEvent.payload`. Альтернатива (слабша): рахувати hash над `model_dump(mode="json")`-формою і зафіксувати це в `docs/contracts.md` §7. Це minor-сумісна зміна (JSON Schema для `object` не змінюється).

**CR-02** | medium | `src/collector/contracts/resolution.py:136-146` (`project_groups`) | Для кожного `merge/manual_link` цикл `for entity, current_group in membership.items(): if any(membership.get(m) == current_group for m in members)` проходить усі відомі membership-и → O(D · E · M). Replay §9.8 («global group є materialized projection послідовності decisions») — саме повний прогін по всій історії. | Probe: N disjoint merge по 2 members: 1000 → 1.2 s, 4000 → 18.5 s, 8000 → 74 s (квадратично); 100k рішень → години. | **CONFIRMED**
Рекомендація: тримати зворотний індекс `groups: dict[UUID, set[UUID]]` поряд із `membership` і збирати `affected` як `members ∪ ⋃ groups[membership[m]]` — O(|affected|) на рішення; `_pairs(affected) & blocked` можна замінити на `any((x, y) in blocked …)` лише для пар, що містять новий member. Поведінка і порядок виходу не змінюються (є тести replay).

### Low

**CR-03** | low | `src/collector/contracts/canonical.py:177-184` (`to_canonical_value`, Mapping) | Ключі нормалізуються до NFC після вставки в `result` → два ключі, що відрізняються лише формою (NFD/NFC), схлопуються, «перемагає» останній за порядком ітерації вхідного dict. Порушує заявлену незалежність від порядку полів і тихо втрачає значення в hash/event bytes. | `{NFD("é"): 1, NFC("é"): 2}` → `{"é":2}`; той самий mapping у зворотному порядку → `{"é":1}`; bytes/sha256 різні. | **CONFIRMED**
Рекомендація: `if key_nfc in result: raise CanonicalEncodingError("дубльований ключ після NFC")`.

**CR-04** | low | `src/collector/contracts/identity.py:183-190` (`identity_hash_v1`) | Та сама колізія для ключів після `casefold` + NFC: `{"Brand": "A", "brand": "B"}` → hash залежить від порядку вставки. | `identity_hash_v1(url, {"Brand":"A","brand":"B"}) != identity_hash_v1(url, {"brand":"B","Brand":"A"})`. | **CONFIRMED**
Рекомендація: відхиляти колізію ключів (`ValueError`) — це помилка викликача, а не дані.

**CR-05** | low | `src/collector/contracts/release.py:306-307` (`quality_report`, `reconciliation_result: JsonObject \| ArtifactRef \| None`) | Smart-union з `dict[str, Any]` першим: dict-вхід (JSON, Mongo, `transition_release` через `model_dump→model_validate`) завжди стає `dict`, `ArtifactRef` ніколи не відновлюється. Immutability-guard не страждає (порівнює JSON), але тип посилання втрачено — `release verify` (WP-11A) муситиме sniff-ити ключі. | `ReleaseManifest(..., quality_report=ArtifactRef(...))` → `type(m.quality_report)` = `ArtifactRef`; після `model_validate_json(m.model_dump_json())` і після `transition_release(m, BUILDING)` — `dict`. | **CONFIRMED**
Рекомендація: окремі поля `quality_report` / `quality_report_artifact` (як `payload`/`payload_artifact` у події, з xor-validator) або tagged union з discriminator. Зараз — до появи споживачів — це minor-сумісна зміна; після merge стане major.

**CR-06** | low | `src/collector/contracts/canonical.py:121, 137` (`DATETIME_FORMAT`, `strftime`) | `%Y` делегується platform `strftime`: glibc не доповнює рік < 1000 нулями (`999-01-01…`), Windows/CPython-обгортка дає `0999`. Claim «незалежно від платформи» не виконується для edge-років; практичні дані > 1970, тому лише ризик. | Linux vs Windows для `datetime(999,1,1,tzinfo=UTC)` → різні canonical bytes/hash. | **PLAUSIBLE** (на Windows перевірено `0999…`; Linux не запускався)
Рекомендація: не залежати від `strftime`: `value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"` (isoformat завжди 4-значний рік, 6 цифр мікросекунд).

**CR-07** | low | `src/collector/contracts/values.py:259, 308-314` (`E164_PATTERN` не використовується; `_matches_e164`) | Ручна перевірка через `str.isdigit()` приймає не-ASCII цифри (арабо-індійські тощо), хоча E.164 — лише ASCII `[0-9]`; константа `E164_PATTERN` — мертвий код. | `ContactValue(kind=PHONE, raw="x", normalized="+٣٨٠١٢")` приймається. | **CONFIRMED**
Рекомендація: `normalized: Annotated[str, StringConstraints(pattern=E164_PATTERN)]`-перевірка у validator (`re.fullmatch`) або видалити константу.

**CR-08** | low | `src/collector/contracts/values.py:269-278` (`Money.amount_minor`; T-04 тестувальника) | Lax-режим: `True` → `1`, `"100"` → `100`. Для грошей bool→int — маскування помилки парсера; before-validator ловить лише float. | `Money(amount_minor=True, currency="UAH").amount_minor == 1`. | **CONFIRMED** (probe)
Рекомендація: `amount_minor: int = Field(strict=True, …)` — тоді float/bool/str відхиляються самим Pydantic, а `_reject_float_amount` можна прибрати (спрощення). Snapshot не змінюється (`{"type":"integer"}`).

**CR-09** | low | `src/collector/contracts/resolution.py:52-73, 122-134` (`ResolutionDecision`, `project_groups`) — `spec-mismatch`/уточнення §9.8 | (а) `decision_version` — лише ключ сортування: усі версії одного `decision_id` replay-яться як окремі рішення, а `supersedes_decision_id == decision_id` заборонено → немає способу «виправити» рішення новою версією; (б) supersession транзитивна і безумовна: якщо C supersede B, а B supersede A(`manual_block`), A лишається пропущеним — скасування скасування не відновлює блок. Обидва варіанти детерміновані й задокументовані в docstring, але семантика `decision_version`/ланцюжків не зафіксована ні в ТЗ, ні в `docs/contracts.md`. | (а) `[merge v1, reject v2]` з одним `decision_id` → група створена, обидва applied; (б) `[block A, reject B⊃A, reject C⊃B, merge]` → `blocked_pairs=[]`, merge applied. | **CONFIRMED** (поведінка), семантика — до пострев'ю
Рекомендація: зафіксувати в `docs/contracts.md` §10 (або: replay лише останньої `decision_version` кожного `decision_id`; supersession обчислювати лише з не-superseded рішень).

**CR-10** | low | `src/collector/contracts/temporal.py:197-208` (`_next_strictly_later`) | O(n²) при багатьох версіях з однаковим значенням осі (8000 версій з одним `effective_at` → 2.9 s). Для версій однієї сутності — прийнятно; згадано, щоб не переносити підхід у bulk-exporter. | — | CONFIRMED (probe), не блокує

### Спрощення / повторне використання (без severity, для впорядкування до merge або follow-up)

- `NonEmptyStr` оголошено двічі (`resolution.py:22`, `release.py:23`) — винести в `_base.py`.
- `EncodedEvent._hash_matches` дублює перевірку ліміту з `encode_event` (`events.py:297-298` і `309-310`); достатньо однієї у validator.
- `EntityTime._source_time_not_fetched_at` дублює `ingested_at < fetched_at` із `SystemTime._ordered` (`temporal.py:91-93`).
- `SourceRegistry.ids` — property, що будує `frozenset` з 70 записів на кожну валідацію `SourceIdentity` (`source_registry.py:329-332`); `functools.cached_property` (frozen-модель) або кешувати у `known_source_ids`.
- `core/version.py` імпортує весь пакет `collector.contracts` (усі 15 модулів, включно з `schema_export`) заради рядка `CONTRACTS_VERSION`; допустимо, але `collector version` тепер тягне побудову всіх моделей — можна тримати константу в `_base.py`.
- `entity_id_timestamp`: `ms / 1000` (float) → `datetime.fromtimestamp` — коректно завдяки округленню до мкс, але `datetime(1970,1,1,tzinfo=UTC) + timedelta(milliseconds=ms)` точний без міркувань про float.
- `_candidate_roots()` перебирає всі батьківські каталоги встановленого пакета до кореня ФС (у site-packages це `…/lib/docs/research/…`, `/docs/research/…`): краще обмежитися repo-root-евристикою (наявність `pyproject.toml`) або лише env + cwd.

## 2. Оцінка відкритих знахідок тестування

| ID | Оцінка |
|---|---|
| T-01 (superseded mutable) | виправлено у `44c8aa3` (`_IMMUTABLE_STATES`, гілка для `superseded`); xfail знято, `test_superseded_manifest_stays_immutable_like_published` зелений. |
| T-02 (`state` через `**changes`) | виправлено (`_RESERVED_TRANSITION_KEYS` + `{**changes, "state": target}`). |
| T-04 (Money lax) | підтримую → CR-08 (low): `strict=True`, прибрати before-validator. |
| T-05 (`SourceInclusion.source_id` без реєстру) | погоджуюся з тестувальником: свідомо (manifest несе `source_registry_version`, джерело могло існувати в старішому реєстрі); достатньо docstring. Info, не знахідка. |
| T-06 (naive `now` у `can_commit`) | виправлено (`ValueError`). |
| T-07 (ADR-0003) | етап 5, поза цим рев'ю. |

## 3. Що перевірено окремо

Команди (worktree, HEAD `a2622df`): `uv run pytest -m "not live"` → 454 passed, 1 skipped (успадкований Windows skip); `uv run mypy src` → clean; `uv run ruff check .` → clean; `uv run collector contracts export --check` → без drift; `grep "type: ignore|noqa" src/collector/contracts` → порожньо (`mypy --strict` без ignore).

- **(а) `encode_event` / canonical.** `bool` перевіряється до `int`; `Enum` → `.value`; NaN/inf → `CanonicalEncodingError`; `Decimal` через `normalize()+format("f")` (`1E+2`→`"100"`, `-0`→`"0"`); `bytes/bytearray/memoryview` → base64; `set` сортується за JSON-репрезентацією; nested models через `model_dump(mode="python", by_alias=True)`; float repr — платформонезалежний `repr`; `sort_keys` — порядок code point. Round-trip `encode → decode → encode` byte-equivalent, бо datetime/Decimal/UUID стають рядками, які кодуються так само. Залежність від версії Pydantic — лише через `model_dump`, який для цих типів стабільний; snapshot-и JSON Schema залежать від версії pydantic, але `uv.lock` фіксує 2.13.5 і CI використовує `--frozen`. Ризики: CR-03 (NFC-колізія ключів), CR-06 (`%Y`), `-0.0` vs `0.0` дають різні bytes (семантично різні float, прийнятно).
- **(б) `compute_state_hash_v1`.** `{"a":"1"}` ≠ `{"a":1}` ≠ `{"a":1.0}` — типізовано, як у JSON/BSON; NFC для рядків і ключів; `None` ≠ відсутній ключ; порядок списків значущий. Проблема — лише не-JSON скаляри (CR-01).
- **(в) UUIDv7.** Розкладка бітів RFC 9562 коректна (`0x7<<76`, `0b10<<62`; probe: `version==7`, `variant==RFC 4122` для 40k значень). Лічильник у `rand_a` стартує з 11 випадкових бітів (запас на 2048 інкрементів), переповнення → `+1 ms`; крок годинника назад → гілка `elif` (лічильник), тож послідовність строго зростає (probe: 5000 значень при зафіксованому годиннику, потім годинник назад — монотонно). `threading.Lock` покриває весь критичний блок; 8 потоків × 5000 → без дублікатів. Монотонність між процесами не гарантується і не вимагається карткою.
- **(г) registry loader.** Не читається при імпорті (тест `test_import_contracts_reads_no_data_files…` + lazy `import yaml`); `lru_cache` за `path` (для `None` — один запис); відсутній файл → `SourceRegistryError(RuntimeError)`, який Pydantic не перетворює на `ValidationError` — помилка конфігурації видима, а не «невалідний source_id». Env-шлях перевіряється на `is_file`. Кеш ігнорує зміну env після першого завантаження — задокументовано.
- **(д) `build_intervals`/`project_groups`.** Сортування стабільне з tiebreak за `projection_version`/`decision_id.int`; результати відсортовані; `set`-ітерація не впливає на вихід (сортований). Складність — CR-02, CR-10. Edge cases: одна версія (обидві верхні межі `None`), дублікати `effective_at`/`ingested_at`, unmerge без merge, dangling `supersedes` — покриті тестами тестувальника.
- **(е) release state machine.** 7 дозволених / 29 заборонених переходів; `published` і `superseded` immutable (для `superseded` — лише одноразове проставлення `superseding_release_id`); `transition_release` ревалідує через `model_dump→model_validate` (model_copy validation не пропускає); `release_id` незмінний. `failed` термінальний, але не immutable — карткою не вимагається.
- **(є) Pydantic-конфіг.** `frozen=True`, `extra="forbid"`, `validate_default=True` на базі; `strict=True` лише на `CurrentDocumentBase.schema_version` (CR-08 — додати для `Money`). `SourceRegistry*` перевизначають `extra="ignore"` — конфіг наслідується з merge. JSON Schema: `mode="validation"`, `required` відсортовано, `sort_keys`, LF, `\r\n`-толерантне порівняння; `$defs` іменуються за класами — перейменування класу = drift (очікувано). `check_compatibility` ловить видалення/перейменування property, нові required, зміну типу/обмежень, видалені enum і `$defs`.
- **(ж) структура.** Модулі 90–250 рядків, без god-модуля; дублювання — лише пункти розділу «Спрощення». Зайвих абстракцій не виявлено; `ExportedContract` dataclass + tuple-реєстр — адекватно.
- **(з) типізація.** `mypy --strict` чистий без ignore; `Any` лише в `JsonObject/JsonValue` (див. CR-01).
- **Тести.** Перевіряють поведінку (bytes, hash, snapshot, переходи), не реалізацію; mutation-перевірки тестувальника (M1–M6) підтверджують чутливість. `xfail` T-01/T-02 знято після фіксу.
- **Дані/безпека.** Гроші — `int + currency`; timestamps — aware UTC з відхиленням (не нормалізацією); source time nullable і не підміняється `fetched_at` (validator R-43). Мережевого/файлового I/O у контрактах немає, крім read-only YAML loader (тест). Secrets/контакти в логах — не застосовно (логування відсутнє).
- У worktree є незакомічена зміна `docs/plan/reports/WP-01C/implementation.md` (status ` M`) — не входить у diff і не оцінювалась.

## 4. Вердикт

**approve.** Critical/high відсутні. Рекомендовано до merge (або першим follow-up до появи споживачів WP-01B/WP-01A): CR-01 (strict-JSON блоки current document/event payload — інакше `state_hash` не переживає межу серіалізації) та CR-02 (індекс груп у `project_groups`); CR-05 варто вирішити до merge, бо після нього це стане major-зміною. Решта — low/спрощення на розсуд owner-а.
