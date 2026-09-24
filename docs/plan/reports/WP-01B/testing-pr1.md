# WP-01B PR1 — незалежне тестування

| Поле | Значення |
|---|---|
| Scope | Mongo collections, validators, indexes, repositories, users, Compose integration |
| Рівень | unit + integration на справжньому MongoDB 8.0 replica set + clean-host Compose |
| Вердикт | **PASS** |

## Знахідки

Початковий adversarial-run: **6 failed, 105 passed**. Дві помилки були ізоляцією тестових
даних; чотири падіння довели дві продуктові прогалини:

1. `schema_version` не був обов'язковим у current validator, хоча §9.2 вимагає поле в кожному
   current document. Виправлено через `ValidatorRecipe.required_properties` і frozen asset.
2. Index з однаковими name/keys/unique, але `sparse`, `partialFilterExpression` або `collation`,
   помилково вважався еквівалентним. Виправлено повним semantic comparison.

Тестові fixtures доповнено іншими unique-полями collection, щоб кожен index-тест ізолював саме
цільовий constraint.

## Докази

```text
$ uv run pytest -q tests/unit/persistence/mongo tests/unit/test_cli_compose_commands.py
219 passed

$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m integration tests/integration/mongo -rs
138 passed in 61s

$ docker compose --profile core --profile workers up -d --wait --build
all long-running services healthy; ensure-postgres/ensure-mongo/ensure-minio exited 0

$ docker compose --profile core --profile workers up -d --wait
all services healthy; Mongo migrations none (up to date); indexes created=0 present=23

$ COLLECTOR_TEST_REQUIRE_DOCKER=1 uv run pytest -m "not live" -q
3857 passed, 23 skipped, 8 warnings in 662.59s (0:11:02)
```

Перший clean-host schema-run: `0001_collections_validators_warn` і
`0002_validators_error` застосовано, 23 indexes створено, users `collector_projector`,
`collector_compactor`, `collector_api_ro`, `collector_export_ro` застосовано.

## Mutation check

З `IndexSpec.matches()` тимчасово прибрано перевірку semantic options:

```text
$ uv run pytest -q tests/integration/mongo/test_adversarial_pr1.py::test_same_name_keys_unique_but_other_options_is_explicit_error
3 failed: DID NOT RAISE IndexConflictError
```

Після відновлення коду: `3 passed`. Мутацію не комітилось.

## Регресійна стабілізація поза Mongo scope

Перший повний прогін зупинився на `test_five_defers_in_a_row_never_dead_letter_a_job_with_four_attempts`:
після п'ятого звіту тест пересував керований clock ще раз, роблячи доступною шосту видачу, і
runtime законно встигав claim-нути її до точного assert. Тест тепер пересуває clock лише між
п'ятьма заявленими видачами. Ізольовано: **3 послідовні прогони passed**; після цього весь набір
дав 3857 passed. Product runtime не змінювався.

## Acceptance

| Вимога PR1 | Доказ | Статус |
|---|---|---|
| Два ідентичні ensure-run | clean-host + повторний Compose run | pass |
| 11 standard collections, indexes = manifest | integration schema/adversarial tests | pass |
| `warn → error`, invalid document rejected | migration integration tests | pass |
| UUID/date BSON mapping, frozen validators | unit + integration | pass |
| Unique keys реально блокують duplicates | integration tests | pass |
| Least-privilege users, password rotation | role integration tests | pass |
| Stable receipt keyset pagination | repository integration tests | pass |
| No silent skips in required job | `COLLECTOR_TEST_REQUIRE_DOCKER=1`, 138 passed | pass |

PR1 acceptance виконано. Повний WP-01B не завершений: projector/replay/reconcile/compaction —
PR2—PR4.
