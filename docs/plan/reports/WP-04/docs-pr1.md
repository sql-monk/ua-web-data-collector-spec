# WP-04 PR1 — документування

Документовано лише реалізований scope PR1; provider/budget, runtime handler, golden corpus і
human QA належать PR2/PR3.

## Створено

| Файл | Зміст |
|---|---|
| `src/collector/translation/README.md` | Потік plan → segment → mask → TM → validate → reassemble, карта модулів, інваріанти розширення й команди перевірки |
| `docs/decisions/0009-segment-level-translation-memory.md` | Q-009: п'ятикомпонентний segment-level TM key, first-write-wins, replay/cost tradeoffs |
| `docs/plan/reports/WP-04/spec-review-pr1.md` | Матриця вимог PR1, межі PR2/PR3 і розв'язання суперечності classifier-а |

## Відкладено за карткою

- `docs/translation-qa.md` — PR3, коли існуватимуть runner, corpus і rubric validator.
- ADR про translation budget (Q-008) — PR2, разом із реальною budget policy.
- Метрики translation у `docs/observability/metrics.md` — PR2/WP-12 після появи handler-а.
- Фінальна traceability «Оригінал + переклад» — після PR2/PR3; поточний рядок уже містить
  контракти WP-01C і лишається partial.

## Перевірка

Документація звірена безпосередньо з API `segmenter.py`, `planner.py`, `preservation.py`,
`memory.py`, `pipeline.py`; тверджень про ще не реалізований provider/runtime немає.
Markdown перевіряється загальним pre-commit gate.
