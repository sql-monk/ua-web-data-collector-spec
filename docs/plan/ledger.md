# Ledger виконання

Стани: `ready` (картка є, залежності `merged`) · `in_progress` · `testing` · `code_review` · `spec_review` · `docs` · `merged <sha>` · `blocked <причина>` · `pending` (картки ще немає).

Оновлюється оркестратором після кожного gate. Звіти етапів — `docs/plan/reports/<WP>/`.

## Хвиля 0

| WP | Під-PR | Branch | Стан | Останній gate | Примітка |
|---|---|---|---|---|---|
| WP-00 | PR1 python+CI | `wp/00-1-python-ci` | merged 7223bec | docs done: `reports/WP-00/docs-pr1.md`; ADR-0001 | CLI = Typer; 127 tests; Linux-паритет підтверджено в Docker |
| WP-00 | PR2 docker/compose | `wp/00-2-docker-compose` | merged 643d41b (PR #2) | усі gates + CI green incl. docker job | ADR-0002; runbooks; 586 tests |
| WP-00 | PR3 web scaffold | `wp/00-3-web-scaffold` | merged 715d54e (PR #4) | CI green: 6/6 jobs incl. web + e2e проти стека | **WP-00 закрито повністю** |
| WP-01C | — | `wp/01c-contracts` | merged 1f2fbc8 (PR #1) | docs done; ADR-0003/0004; CI green | worktree `.worktrees/wp-01c`; картка `docs/plan/cards/WP-01C.md` |

## Хвиля 1

| WP | Стан | Примітка |
|---|---|---|
| WP-01A | PR1 merged 758c68c (PR #3); PR2 pending | CI green (python, integration PostgreSQL 18, docker); ADR-0005, `docs/persistence/postgres.md` |
| WP-01B | pending | після WP-01A |
| WP-01D | docs (PR1) | пострев'ю changes_requested → закрито (liveness probe: healthcheck 6.07→0.44 с, стек 22 с); 873 + 33 integration |
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
| `WP-01C-to-WP-00.md` | WP-01C | WP-00 | resolved — п.1–3 застосовано у `wp/01c-contracts`; Dockerfile COPY реєстру → PR2 |
| `WP-01A-to-WP-00.md` | WP-01A | WP-00 | resolved — застосовано в WP-01A PR1 і WP-00 PR2 |
| `WP-01D-to-WP-01A.md` | WP-01D | WP-01A | open (пріоритет) — LOGIN-ролі per component + per-role DSN (§13, рантайм-доказ: усі 8 процесів superuser); `queue.release` без інкременту attempt |
