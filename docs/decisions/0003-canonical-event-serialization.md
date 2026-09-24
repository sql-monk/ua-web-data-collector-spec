# ADR-0003: Canonical event serialization

| Поле | Значення |
|---|---|
| Date | 2026-09-22 |
| Owner | WP-01C |
| Status | accepted |

## Context

§7.3 вимагає, щоб `AppliedProjectionReceipt` ніс готові до публікації `event_bytes`
разом з `event_media_type` і `event_sha256`, а `ProjectionAcknowledgement` і
reconciler/outbox копіювали ці bytes без повторної серіалізації (R-37, R-42 REVIEW.md).
Це можливо лише якщо серіалізація `DomainChangedEvent` детермінована: та сама подія має
завжди давати ті самі bytes і той самий SHA-256, незалежно від порядку полів моделі,
локалі процесу, версії Pydantic чи платформи (Windows/Linux). ТЗ не фіксує буквального
байтового формату — лише вимагає детермінізму (§7.3) і versioned схеми (§9.4) — тому
конкретний формат є рішенням WP-01C поза буквою ТЗ (§20).

Формат обраний і реалізований у `src/collector/contracts/canonical.py`
(`canonical_json_bytes`, `to_canonical_value`, `format_utc_datetime`, `format_decimal`) і
`src/collector/contracts/events.py` (`encode_event`/`decode_event`,
`EVENT_INLINE_LIMIT_BYTES`). Він застосовується не лише до `domain.changed` bytes, а й до
всіх hash-функцій контрактів (`identity_hash_v1`, `compute_state_hash_v1`, ключі
ідемпотентності) — одна canonical-функція на весь пакет, а не окрема для подій.

Рішення уточнювалось після пострев'ю на етапах код-рев'ю (`docs/plan/reports/WP-01C/code-review.md`,
знахідки CR-01, CR-03, CR-06) і спрощень; фінальна поведінка зафіксована в
`docs/contracts.md`, розділ «Canonical serialization», і повторюється тут як ADR.

## Decision

### Байтовий формат — sorted-keys compact JSON, UTF-8, NFC

`canonical_json_bytes(value)`:

- об'єкти серіалізуються з ключами, відсортованими за Unicode code point
  (`json.dumps(..., sort_keys=True)`);
- без зайвих пробілів — `separators=(",", ":")`;
- `ensure_ascii=False`, результат кодується в UTF-8 (кириличні поля не escape-яться в
  `\uXXXX`, bytes однакові для того самого тексту незалежно від platform locale);
- рядки та ключі об'єктів нормалізуються до Unicode NFC перед кодуванням. Обрано sorted-keys
  compact JSON, а не canonical CBOR/protobuf: інструментарій для дебагу (`jq`, будь-який
  JSON viewer) читає bytes напряму, а формат уже використовується для `identity_hash`/
  `state_hash`, де людяність diff важливіша за компактність.

**Чому саме sorted-keys JSON, а не альтернативи.** Дві причини визначили вибір:

1. Node/reconciler/outbox (§7.3) мають відтворювати ті самі bytes у будь-якому порядку
   полів вхідної Pydantic-моделі — `sort_keys=True` робить це тривіальним і перевіряється
   одним тестом (`test_encode_event_deterministic_and_byte_equivalent_after_round_trip`),
   без залежності від порядку оголошення полів моделі чи версії Pydantic;
2. Формат тексту лишається JSON — SHA-256 і content-addressed зберігання (`event_artifact`,
   §7.3 п.4) не вимагають окремого бінарного кодека; `decode_event` — звичайний
   `model_validate_json`.

### Формат скалярів

- `datetime` — лише aware UTC (naive або offset ≠ 0 — помилка `CanonicalEncodingError`,
  дані з таким входом мають бути виправлені на межі адаптера, а не мовчки нормалізовані) →
  `value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"`: завжди 6 цифр
  мікросекунд і 4-значний рік. Обрано `isoformat`, а не `strftime("%Y-%m-%dT...")`: glibc
  `%Y` не доповнює нулями рік < 1000 (CR-06 код-рев'ю: `999-01-01` на Linux vs `0999-01-01`
  на Windows) — `strftime` порушував би задекларовану платформонезалежність;
  `date` окремо кодується `YYYY-MM-DD`;
- `UUID` → lowercase рядок з дефісами (`str(uuid)` вже дає цей формат);
- `Decimal` → `format(value.normalize(), "f")` без експоненти й зайвих нулів
  (`Decimal("1.50")` → `"1.5"`, `Decimal("1E+2")` → `"100"`, `-0` → `"0"`); нескінченний
  або NaN `Decimal`/`float` відхиляється (`CanonicalEncodingError`) — гроші канонічної
  моделі й так не використовують `float` (§5.1, `Money.amount_minor: int`);
- `bytes`/`bytearray`/`memoryview` → base64 (стандартний алфавіт, з padding);
- `Enum` → `.value`; `bool`/`int`/`None` — як у JSON; `set`/`frozenset` — відсортований
  список (сортування за canonical JSON-представленням елемента, не за `hash()`, щоб
  порядок не залежав від `PYTHONHASHSEED`);
- Pydantic-моделі — рекурсивно через `model_dump(mode="python", by_alias=True)`.

### Заборона NFC/casefold-колізій ключів

Дві причини, чому canonical-функція **відхиляє**, а не «останній перемагає», коли два ключі
об'єкта збігаються після нормалізації:

- `to_canonical_value` (загальна функція, `canonical.py`): ключі об'єкта нормалізуються до
  NFC; якщо нормалізований ключ уже є в результаті — `CanonicalEncodingError`. До фіксу
  (код-рев'ю, знахідка CR-03) `{NFD("é"): 1, NFC("é"): 2}` мовчки схлопувалось у `{"é": 2}`
  (або `1` — залежно від порядку ітерації вхідного dict), тобто той самий логічний payload
  давав різні bytes/SHA-256 залежно від порядку полів — це прямо порушує вимогу
  детермінізму §7.3;
- `identity_hash_v1` (`identity.py`) окремо нормалізує ключі стабільних атрибутів через
  NFC + `casefold()` (не лише NFC) — тут та сама колізія можлива й для ASCII-регістру
  (`Brand`/`brand`); знайдено код-рев'ю (CR-04), виправлено так само — `ValueError`.

В обох випадках колізія — помилка виклику (адаптер джерела передав два логічно різні ключі,
що стають одним і тим самим після нормалізації), а не дані, які варто «якось» об'єднати:
тиха втрата значення в hash-і без сигналу — гірше, ніж падіння на межі виклику.

### Ліміт 256 KiB → `event_artifact`

`EVENT_INLINE_LIMIT_BYTES = 256 * 1024`. `encode_event()` рахує `canonical_json_bytes(event)`
і, якщо результат перевищує ліміт, кидає `EventTooLargeError(size)` **до** побудови
`EncodedEvent` — викликач (projector) ловить це і переносить `payload` у immutable artifact
(`DomainChangedEvent.payload_artifact`, `ArtifactRef`), кодуючи подію без inline payload;
`AppliedProjectionReceipt` тоді несе `event_artifact` замість `event_bytes` (рівно одне з
двох — validator моделі). Значення 256 KiB — практичний ліміт для розміру одного Mongo BSON
document/поля в цьому шляху (§9.2 `applied_projection_receipts`), не з ТЗ буквально; воно
з запасом більше за типовий `domain.changed` payload (diff normalized полів однієї сутності).

### SHA-256 і чому bytes зберігаються, а не перераховуються

`EncodedEvent(event_id, event_bytes, event_media_type, event_sha256)` — `event_sha256 =
sha256_hex(event_bytes)`, перевіряється validator-ом моделі при конструюванні
(`_hash_matches`). Receipt (`AppliedProjectionReceipt.event_bytes/event_media_type/event_sha256`)
зберігає вже закодовані bytes у Mongo **разом** з версією projection, що їх створила —
`event_bytes` записуються в тій самій транзакції/commit, що current document і version
record (§7.3 кроки 2–4). Це свідомо, а не оптимізація:

1. Node, що виконує projection, і node, що пізніше публікує подію (outbox/reconciler після
   crash), можуть бути різними процесами; якби reconciler повторно серіалізував подію з
   поточного стану моделі (наприклад, після рестарту з іншою версією Pydantic чи іншим
   порядком полів), bytes могли б відрізнятися від того, що вже потрапило в
   `AppliedProjectionReceipt` при першому commit — SHA-256 у `ProjectionAcknowledgement`
   і consumer-side дедуплікація за `event_id` стали б ненадійними;
   `ProjectionAcknowledgement.from_receipt()` тому копіює `event_sha256`
   (або `event_artifact.sha256`) з receipt, не викликаючи `encode_event` повторно;
2. Ready bytes у Mongo document роблять at-least-once публікацію в outbox тривіальною
   операцією читання поля, без залежності від того, чи доступний увесь контекст для
   повторної побудови `DomainChangedEvent` (наприклад, після compaction чи migration
   моделі) — reconciler публікує саме те, що спостерігав consumer при першому баченні
   receipt, byte-в-byte.

`decode_event(bytes) -> DomainChangedEvent` існує лише для round-trip тестів і
consumer-side дебагу; він не бере участі в шляху receipt → outbox.

### Strict `JsonValue` для payload і блоків current document (CR-01)

`DomainChangedEvent.payload` і блоки `core`/`attributes`/`latest_state`
`CurrentDocumentBase` (§9.2) типізовані рекурсивним `JsonValue = str | int | float(finite) |
bool | None | list[JsonValue] | dict[str, JsonValue]` (strict-режим Pydantic:
`Strict()` для скалярів, `AllowInfNan(False)` для float) — `_base.py`. Це наслідок знахідки
CR-01 код-рев'ю: доти `core`/`attributes`/`latest_state`/`payload` типізувались як
`dict[str, Any]`, і `state_hash`/`event bytes` рахувались canonical-кодуванням *Python*-
значень (`datetime`, `Decimal`, `UUID`, `bytes` проходили crash-safe через `to_canonical_value`),
тоді як після JSON/BSON round-trip (Mongo зберігання, HTTP API) ці типи повертаються як інші
Python-об'єкти (naive datetime без `tz_aware`, мілісекундна точність BSON, рядок замість
`UUID`) — canonical-представлення переставало збігатися, і `state_hash`/event bytes «пливли»
на межі серіалізації: документ, що пройшов запис, міг не пройти повторну валідацію, або
projector після re-read бачив «зміну стану» без зміни даних.

Strict `JsonValue` відхиляє нестрогі JSON-типи **на конструюванні** моделі, а не на
canonical-кодуванні: `datetime`/`Decimal`/`UUID`/`bytes`/NaN/inf у цих блоках — помилка
виклику projector-а (адаптер має конвертувати їх у canonical-рядок/`{"amount_minor",
"currency"}` до запису), а не значення, яке canonical-функція «якось» кодує. Наслідок:
`compute_state_hash_v1`/`encode_event` рахуються над тими самими значеннями, що документ чи
подія матимуть після будь-якого JSON/BSON round-trip — hash стабільний
(`test_state_hash_survives_json_round_trip`, `test_domain_event_payload_is_strict_json`).

## Consequences

- Будь-яка зміна canonical-функції (новий скалярний тип, зміна формату datetime/Decimal,
  зміна поведінки при колізії ключів) змінює *існуючі* hash-значення (`identity_hash`,
  `state_hash`, `event_sha256`) — це нова версія алгоритму (`v2:` prefix за процедурою
  `docs/contracts.md`, розділ 3.2), а не patch поточної; стара функція лишається для
  перевірки вже записаних значень.
- Node-и, що читають `event_bytes`/`event_artifact` з receipt (outbox, reconciler, будь-який
  consumer §7.3), не мають права re-серіалізувати подію для отримання «канонічних» bytes —
  контракт `should_emit_domain_changed`/`ProjectionAcknowledgement.from_receipt` гарантує
  byte-equivalence лише для bytes, що вже лежать у Mongo; повторна серіалізація з іншого
  представлення моделі *не* гарантовано дає ті самі bytes (лише той самий *логічний* payload).
- Strict `JsonValue` означає, що будь-який домен (WP-07 vehicle `core`, WP-09 catalog
  `core`) мусить конвертувати час/гроші/UUID у canonical-рядок/примітиви перед записом у
  `core`/`attributes`/`latest_state` — не може покластися на Pydantic-конверсію типів у
  цих полях; `Money`/`ContactValue`/тимчасові поля лишаються окремими value objects поза
  strict-JSON блоками, коли потрібна типізована робота з ними.
- NFC/casefold-колізія ключів (адаптер передав `Brand` і `brand`, або NFC/NFD форми того
  самого символу) — тепер `ValueError`/`CanonicalEncodingError` замість тихої втрати
  значення; адаптери джерел (WP-06/08/10) мають нормалізувати ключі стабільних атрибутів
  до виклику `identity_hash_v1`, а не покладатися на те, що контракт «якось» вирішить
  колізію.
- Ліміт 256 KiB — точка розширення в майбутньому (наприклад, якщо середній розмір
  normalized diff зросте): підняти ліміт — сумісна (minor) зміна константи, доки формат
  `event_artifact` лишається fallback-ом; знизити ліміт — потенційно breaking для вже
  записаних receipt.

## Amendment 2026-09-24 (WP-01C PR2)

`encode_event` кодує дві події: `DomainChangedEvent` і `NewsVersionCreatedEvent`
(`collector.contracts.news`, outbox `news.version_created`, WP-01A PR3b → WP-04). Тип параметра —
`PublishableEvent = DomainChangedEvent | NewsVersionCreatedEvent`; media type береться з
`ClassVar media_type` класу події (`application/vnd.ua-collector.domain-changed.v1+json` /
`application/vnd.ua-collector.news-version-created.v1+json`). **Байтовий формат не змінився**:
той самий `canonical_json_bytes`, ті самі правила скалярів, ліміт 256 KiB і `EncodedEvent`
(перевірено handwritten golden у `testing-pr2.md`). Зворотне декодування — `decode_event`
(domain.changed) і `decode_news_version_created`. PR2 також посилив strict `JsonValue`: масив —
лише `list`, ключ — лише `str`; одиночний сурогат → `CanonicalEncodingError`
(`docs/contracts.md` §6–7).

## Related

- Реалізація: `src/collector/contracts/canonical.py`, `src/collector/contracts/events.py`,
  `src/collector/contracts/_base.py` (`JsonValue`/`JsonObject`), `src/collector/contracts/current.py`
  (`compute_state_hash_v1`), `src/collector/contracts/identity.py` (`identity_hash_v1`).
- Документація: `docs/contracts.md` (розділи «Canonical serialization», «`state_hash` v1»,
  «Projection / receipt / event»).
- Звіти: `docs/plan/reports/WP-01C/code-review.md` (CR-01, CR-03, CR-04, CR-06),
  `docs/plan/reports/WP-01C/implementation.md` («Відповіді на код-рев'ю»),
  `docs/plan/reports/WP-01C/spec-review.md` (accept).
- Тести: `tests/unit/contracts/test_events.py`, `tests/unit/contracts/test_code_review_fixes.py`,
  `tests/unit/contracts/test_adversarial.py`, `tests/unit/contracts/test_current.py`.
