# Ledger виконання

Стани: `ready` (картка є, залежності `merged`) · `in_progress` · `testing` · `code_review` · `spec_review` · `docs` · `merged <sha>` · `blocked <причина>` · `pending` (картки ще немає).

Оновлюється оркестратором після кожного gate. Звіти етапів — `docs/plan/reports/<WP>/`.

## Хвиля 0

| WP | Під-PR | Branch | Стан | Останній gate | Примітка |
|---|---|---|---|---|---|
| WP-00 | PR1 python+CI | `wp/00-1-python-ci` | docs | пострев'ю accept (0 missing, 5 partial → наступні етапи): `reports/WP-00/spec-review-pr1.md` | картка `docs/plan/cards/WP-00.md`; CLI = Typer |
| WP-00 | PR2 docker/compose | `wp/00-2-docker-compose` | pending | — | після merge PR1 |
| WP-00 | PR3 web scaffold | `wp/00-3-web-scaffold` | pending | — | після merge PR2 |
| WP-01C | — | `wp/01c-contracts` | pending | — | стартує з `wp/00-1` після появи layout |

## Хвиля 1

| WP | Стан | Примітка |
|---|---|---|
| WP-01A | pending | після WP-00, WP-01C |
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
