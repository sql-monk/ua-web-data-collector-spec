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
| WP-01A | PR1 merged 758c68c (PR #3); PR2 docs; очікує повторного пострев'ю/PR | CI green (PR1: python, integration PostgreSQL 18, docker); ADR-0005, ADR-0007, `docs/persistence/postgres.md`; PR2 (`bad6a25`+fixes) — spec-review gate 4 `changes_requested` закрито (SR-1 gitleaks, SR-2 lineage, SR-5, N-1..N-5 fixed; D-1/D-2 задокументовані ADR-0007 + ТЗ §9.1); CI job `integration-postgres` для PR2 ще не запускався (PR не відкрито) |
| WP-01B | pending | після WP-01A |
| WP-01D | PR1 merged f87df17 (PR #5); PR2 pending | CI green 6/6; ADR-0006, `docs/workers.md`, runbook worker-recovery; флак ризик — `tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising` нестабільний під паралельним навантаженням (відтворено на WP-01A PR2 verification, не внесений PR2 — `docs/plan/reports/WP-01A/implementation-pr2.md`) |
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
| `WP-01A-to-WP-00.md` | WP-01A PR1/PR2 | WP-00 | п.1–3 (PR1) resolved — застосовано в WP-01A PR1 і WP-00 PR2; §4 (PR2) open — DSN-секрети per component (`postgres_dsn_<component>`, `init-secrets.sh`) і one-shot `migrate-postgres --with-login`; §5 `.gitleaksignore` — resolved by orchestrator. Ризик I-2 (`REVOKE CONNECT, TEMP ON DATABASE … FROM PUBLIC`, security-pr2.md) — owner WP-00, додано пунктом |
| `WP-01D-to-WP-01A.md` | WP-01D | WP-01A | WP-01A part done, pending WP-00/WP-01D — LOGIN-ролі per component і `queue.release` зроблено на боці WP-01A PR2; лишились per-role DSN секрети (WP-00) і перехід runtime-сервісів на них (WP-01D). Ризик I-1 (runtime на superuser DSN до переходу — security-pr2.md) — owner WP-01D/WP-00, блокер pilot |
| `WP-01A-to-WP-01D.md` | WP-01A PR2 | WP-01D | open — перевести `scheduler`/`worker-*` на власні `postgres_dsn_<component>`, замінити тест-вартовий на позитивний тест §13, викликати `verify_runtime_login`; §7 лічильник доставок outbox до `mark_failed` (N-2) |
| `WP-01A-to-WP-02.md` | WP-01A PR2 | WP-02 | open — умови D-2 `parse_key` для parser-а: ключ normalized artifact = `(sha256, entity_uuid)`, справжній `fetch_id` кожного запиту, зміна схеми = зміна `parser_version` |
