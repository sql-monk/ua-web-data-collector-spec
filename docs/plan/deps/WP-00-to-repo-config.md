# Dependency-запит: WP-00 PR1 → власник repo-level конфігурації (`.markdownlint-cli2.jsonc`)

| Поле | Значення |
|---|---|
| Від | WP-00 PR1 (`wp/00-1-python-ci`) |
| До | оркестратор / власник repo-level конфігів (файл поза owned files WP-00) |
| Файл | `.markdownlint-cli2.jsonc` |
| Стан | resolved — застосовано на `main` (`b3dafd8 chore: allow repeated sibling headings in WP cards (MD024 siblings_only)`); після rebase `wp/00-1-python-ci` hook `markdownlint-cli2` зелений |

## Що потрібно

Додати в `.markdownlint-cli2.jsonc` налаштування правила MD024 у режимі `siblings_only`:

```jsonc
{
  "config": {
    "MD013": false,
    "MD024": { "siblings_only": true },
    "MD060": false
  }
}
```

## Навіщо

PR1 вводить hook `markdownlint-cli2` у `.pre-commit-config.yaml` і job `pre-commit` у CI (картка WP-00, вимога 6). Картки WP (`docs/plan/cards/WP-00.md`) за задумом мають повторювані заголовки «Owned files», «Команди перевірки», «Acceptance» у різних під-PR-розділах; markdownlint за замовчуванням трактує це як MD024 і робить `uv run pre-commit run --all-files` червоним:

```text
docs/plan/cards/WP-00.md:85 error MD024/no-duplicate-heading Multiple headings with the same content [Context: "Owned files"]
docs/plan/cards/WP-00.md:101 error MD024/no-duplicate-heading ... [Context: "Команди перевірки"]
docs/plan/cards/WP-00.md:124 error MD024/no-duplicate-heading ... [Context: "Owned files"]
docs/plan/cards/WP-00.md:136 error MD024/no-duplicate-heading ... [Context: "Команди перевірки"]
docs/plan/cards/WP-00.md:144 error MD024/no-duplicate-heading ... [Context: "Acceptance"]
```

`siblings_only` дозволяє однакові заголовки під різними батьківськими розділами (стандартна настройка для changelog/карток) і не послаблює правило в межах одного розділу. Файл не входить до owned files WP-00, тому зміна не внесена в PR1; до її появи єдина червона перевірка — цей hook на `docs/plan/cards/WP-00.md`.

## Альтернатива, якщо зміну конфігу відхилено

Додати `exclude: ^docs/plan/cards/` до hook `markdownlint-cli2` у `.pre-commit-config.yaml` (owned WP-00) — гірше, бо вимикає lint карток повністю.
