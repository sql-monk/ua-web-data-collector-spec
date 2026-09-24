# WP-04 PR1 — пострев'ю за ТЗ

Гілка `wp/04-1-segmenter-tm`, перевірений HEAD після синхронізації з `main`:
`git diff main...HEAD`. Scope — лише PR1 картки `docs/plan/cards/WP-04.md`.

## Вердикт

**accept** — blocker/high/medium невідповідностей немає. Вимоги PR1, регресії
R-03—R-05, R-08, R-09, R-20 і локальний acceptance виконані. Зовнішній provider, budget,
PostgreSQL adapter і multilingual golden corpus належать PR2/PR3 і не оцінювалися як пропуски PR1.

## Матриця приймання PR1

| Вимога | Доказ | Статус |
|---|---|---|
| HTML segment/reassemble, protected content, hostile input | `segmenter.py`; fixture- і adversarial-тести, включно з незакритими inline/protected областями, атрибутами й 80k entities | pass |
| NFC/пробіли/control normalization без зміни source | `normalize.py`, golden TM key і collision-тести | pass |
| metadata → `lang` → classifier, confidence, короткі сегменти | `detection.py`, `planner.py`, `test_language_plan.py` | pass |
| `uk` → `not_required`, mixed-language по сегментах | `planner.py`, `pipeline.py`, `test_pipeline.py` | pass |
| TM key рівно з 5 компонентів, canonical SHA-256 | `memory.py`, `tm_key_golden.json`, mutation-тести кожного компонента | pass |
| Versioned glossary і do-not-translate | `glossary/default.yaml`, канонічний content hash, glossary/TM тести | pass |
| Preservation чисел, дат, валют, URL, email, glossary і inline tags | `preservation.py`, mutation/adversarial/performance tests | pass |
| Nullable body та `content_access` planning | `planner.py`, `test_language_plan.py` | pass |
| Q-009: перекладаються лише TM misses | `execute_plan`, v1/v2 та idempotency tests | pass |
| Pure modules без I/O | `test_pure_modules.py`; немає імпортів SQLAlchemy/httpx/asyncpg/pymongo/google | pass |
| Strict typing, lint, no silent skip | mypy/ruff green; 1,589 translation tests, 0 skip/xfail | pass |

## Узгодження суперечності classifier-а

Картка одночасно вимагала обмежити classifier мовами `uk + 16 + extra` і не надсилати
мову поза цим набором provider-у. Вузький classifier не може виконати другу вимогу: pt/nb/bg
класифікувалися як es/nl/ru без прапорця. Реалізація додає 18 sentinel-мов і повертає
`language_unsupported`; близька підтримувана мова допускається лише з
`low_language_confidence`. Це консервативне виконання О-5 і §12.2: невпевненість не
ігнорується. Повний набір Lingua не використано через підтверджені false positive для `uk`.

## Відкриті залежності та межі

- `docs/plan/deps/WP-04-to-WP-01C.md`: до PR2 додати `untranslated_content` до
  `TranslationQualityFlag`. PR1 використовує локальний тип і не блокується.
- Provider/budget/immutable `NewsTranslation` wiring — PR2; golden 16 + extra language pairs
  і human-QA tooling — PR3/WP-06x.
- CI GitHub ще не запускався для цієї гілки; локально після merge `main`: ruff, format,
  mypy і 1,589 translation tests зелені.

## Перевірені розділи

`TECHNICAL_SPECIFICATION.md` §5.4, §10 кроки 11–12, §12.1–12.2, §13, §16.1 п.1,
§17.2 WP-04, FR-016, FR-017; `REVIEW.md` R-03—R-05, R-08, R-09, R-20; картка WP-04
PR1 і спільні вимоги.
