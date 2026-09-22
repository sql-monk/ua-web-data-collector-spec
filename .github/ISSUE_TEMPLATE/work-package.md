---
name: Work package (WP) для агента
about: Задача одного work package за §17.3 ТЗ і карткою docs/plan/cards/<WP>.md
title: "WP-XX: <короткий результат>"
labels: work-package
assignees: ""
---

<!-- Кожне поле обов'язкове (§17.3). Порожнє поле = картка не готова (Definition of Ready). -->

## WP і картка

- WP: `WP-XX` (під-PR: `PRn`, якщо є)
- Картка: `docs/plan/cards/WP-XX.md`
- Branch / worktree: `wp/<id>` / `.worktrees/wp-<id>`
- Розділи ТЗ: §…

## Scope

<!-- Що саме робиться в цьому WP. -->

## Out of scope

<!-- Що свідомо не робиться; кому належить. -->

## Файли у власності (owned files)

<!-- Точні шляхи/glob. Усе інше — forbidden. Shared contracts → dependency-запит власнику. -->

## Input contract / version

<!-- Які контракти, схеми, fixtures чи OpenAPI споживаються і якої версії. -->

## Output contract / version

<!-- Що WP віддає іншим: схеми, події, CLI, fixtures, їхня версія. -->

## Fixtures і provenance

<!-- Звідки fixtures, дата зняття, User-Agent, чи анонімізовано; жодних secrets/приватних даних. -->

## Команди перевірки

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not live"
```

## Acceptance criteria

- [ ] …

## Залежності

- Залежить від: …
- Розблоковує: …

## Source coverage

<!-- Для адаптерів: перелік джерел/route і поля §5; для платформи — `n/a`. -->

## Rollback / disable plan

<!-- Feature flag, env, revert merge commit; runtime-ефект. -->
