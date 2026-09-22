# Ledger виконання

Стани: `ready` (картка є, залежності `merged`) · `in_progress` · `testing` · `code_review` · `spec_review` · `docs` · `merged <sha>` · `blocked <причина>` · `pending` (картки ще немає).

Оновлюється оркестратором після кожного gate. Звіти етапів — `docs/plan/reports/<WP>/`.

## Хвиля 0

| WP | Під-PR | Branch | Стан | Останній gate | Примітка |
|---|---|---|---|---|---|
| WP-00 | PR1 python+CI | `wp/00-1-python-ci` | merged 7223bec | docs done: `reports/WP-00/docs-pr1.md`; ADR-0001 | CLI = Typer; 127 tests; Linux-паритет підтверджено в Docker |
| WP-00 | PR2 docker/compose | `wp/00-2-docker-compose` | in_progress (fix after gate 2) | тестування pass + H-1 mongo boot race: `reports/WP-00/testing-pr2.md`; 210 tests | worktree `.worktrees/wp-00-2` |
| WP-00 | PR3 web scaffold | `wp/00-3-web-scaffold` | pending | — | після merge PR2 |
| WP-01C | — | `wp/01c-contracts` | merged 1f2fbc8 (PR #1) | docs done; ADR-0003/0004; CI green | worktree `.worktrees/wp-01c`; картка `docs/plan/cards/WP-01C.md` |

## Хвиля 1

| WP | Стан | Примітка |
|---|---|---|
| WP-01A | in_progress (PR1) | картка `docs/plan/cards/WP-01A.md`; worktree `.worktrees/wp-01a` |
| WP-01B | pending | після WP-01A |
| WP-01D | pending | після WP-00, WP-01A |
| WP-02 | pending | після WP-01A |
| WP-04 | pending | після WP-01A |

## Хвиля 2

| WP | Стан |
|---|---|
| WP-03 | pending |
| WP-05 | pending |
| WP-07 | pending |
| WP-09 | pending |

## Хвиля 3

| WP | Стан |
|---|---|
| WP-06A–G | pending |
| WP-08A–D | pending |
| WP-10A–H | pending |
| WP-11A | pending |

## Хвилі 4–5

| WP | Стан |
|---|---|
| WP-11B | pending |
| WP-11C | pending |
| WP-12 | pending |
| WP-13 | pending |
| WP-14 | pending |

## Dependency-запити

| Файл | Від | До | Стан |
|---|---|---|---|
| `WP-00-to-repo-config.md` | WP-00 PR1 | orchestrator | resolved — MD024 siblings_only у `.markdownlint-cli2.jsonc` (main) |
| `WP-01C-to-WP-00.md` | WP-01C | WP-00 | resolved — п.1–3 застосовано у `wp/01c-contracts` за рішенням оркестратора; Dockerfile COPY реєстру → PR2 |
