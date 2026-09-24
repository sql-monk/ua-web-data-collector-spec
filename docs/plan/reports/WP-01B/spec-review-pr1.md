# WP-01B PR1 — приймання за ТЗ

Вердикт: **ACCEPTED for PR1**. Це не закриває весь WP-01B; PR2—PR4 лишаються обов'язковими.

## Матриця PR1

| Вимога | Доказ | Статус |
|---|---|---|
| Єдиний owner Mongo validators/index migrations | `migrations/mongo/**`, `schema.py`, card ownership | pass |
| 11 standard collections §9.2 | migration 0001 + integration manifest test | pass |
| Validators лише для наявних snapshots | current + receipt assets; negative coverage решти | pass |
| Обов'язковий `schema_version` | asset + `test_current_requires_schema_version_per_spec_9_2` | pass |
| Forward-only checksum migrations | drift/interruption/idempotency tests | pass |
| `warn → error` без partial switch | migration tests | pass |
| Index manifest, unique keys, index budget | 23 indexes; conflict/extra/semantic-option tests | pass |
| Repositories + keyset без `skip` | repository tests, `ix_committed_cursor` | pass |
| primary/majority/snapshot concerns (R-34) | `client.py` + client/integration tests | pass |
| Component credentials §13 | users matrix + positive/negative role tests | pass |
| Clean-host readiness | Compose startup and idempotent repeat | pass |
| Linux CI Mongo job | `.github/workflows/ci.yml`; має бути зеленим до merge | pending CI gate |

## DoD §18 для межі PR1

1. Код, міграції, manifests і frozen assets є; type/lint checks входять у pre-commit/CI.
2. Unit/integration/adversarial тести є; required Mongo job не допускає skip.
3. Idempotency і повторний clean-host запуск доведені.
4. Least privilege і secret handling перевірені окремо.
5. Документація та ADR додані; rollback — лише forward fix.
6. Traceability оновлено нижче.

Пункти повного WP про projector crash-window, 3-1-2, restore/reconcile й compaction свідомо
`not applicable` до PR1 і лишаються acceptance PR2—PR4.

## Регресії REVIEW.md

- R-24: bounded-context Mongo projection збережено; PostgreSQL payload не дублює.
- R-25/R-36/R-37/R-42: contracts/indexes/receipts підготовлені; runtime invariants — PR2/PR3.
- R-34: primary/majority/snapshot і explicit retry settings присутні; transaction retry — PR2.
- R-44: compaction/retention — PR4, не заявлено виконаним у PR1.

Незакрите Q-010 використало safe default і зафіксоване ADR-0010.
