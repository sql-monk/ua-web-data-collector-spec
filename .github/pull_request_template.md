<!-- markdownlint-disable MD041 -- тіло PR не має H1 -->
<!-- Шаблон PR за §17.1 ТЗ і docs/IMPLEMENTATION_PLAN.md §10. Заповнюй усі розділи; "n/a" — з причиною. -->

## WP

- WP / під-PR: `WP-XX` / `PRn`
- Картка: `docs/plan/cards/WP-XX.md`
- Розділи ТЗ: §…

## Зміни

<!-- Що зроблено; лише scope одного WP, без сторонніх правок. -->

## Тести (з виводом команд)

<!-- Фактичний вивід команд перевірки з картки. «Тести пройшли» без виводу не приймається. -->

```text
uv run pytest -m "not live"
...
```

## Fixture provenance

<!-- Джерело, дата, User-Agent, анонімізація; або "нових fixtures немає". -->

## Ризики

<!-- Технічні, операційні, правові (джерела, rate limits, контакти). -->

## Як вимкнути / відкотити

<!-- Feature flag, env, config, revert merge commit; чи є runtime-ефект. -->

## Що не перевірено live

<!-- Що працює лише на fixtures; що вимагає canary з дозволу користувача. -->

## Звіти етапів

- Реалізація: `docs/plan/reports/WP-XX/implementation.md`
- Тестування: `docs/plan/reports/WP-XX/testing.md`
- Код-рев'ю: `docs/plan/reports/WP-XX/code-review.md`
- Пострев'ю за ТЗ: `docs/plan/reports/WP-XX/spec-review.md`

## Чекліст DoD (§18)

- [ ] formatter, lint, types, тести зелені локально і в CI
- [ ] secret scan чистий; жодного `.env`/секрету/приватних контактів у diff і fixtures
- [ ] зміни схем мають migration і compatibility evidence (або n/a)
- [ ] документація/метрики/runbook оновлені (або n/a)
- [ ] dependency-запити до інших owners створені в `docs/plan/deps/` (або n/a)
