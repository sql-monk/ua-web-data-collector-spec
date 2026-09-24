# Ledger виконання

> Точка відновлення після паузи — `docs/plan/HANDOFF.md`.

Стани: `ready` (картка є, залежності `merged`) · `in_progress` · `testing` · `code_review` · `spec_review` · `docs` · `merged <sha>` · `blocked <причина>` · `pending` (картки ще немає).

Оновлюється оркестратором після кожного gate. Звіти етапів — `docs/plan/reports/<WP>/`.

## Хвиля 0

| WP | Під-PR | Branch | Стан | Останній gate | Примітка |
|---|---|---|---|---|---|
| WP-00 | PR1 python+CI | `wp/00-1-python-ci` | merged 7223bec | docs done: `reports/WP-00/docs-pr1.md`; ADR-0001 | CLI = Typer; 127 tests; Linux-паритет підтверджено в Docker |
| WP-00 | PR2 docker/compose | `wp/00-2-docker-compose` | merged 643d41b (PR #2) | усі gates + CI green incl. docker job | ADR-0002; runbooks; 586 tests |
| WP-00 | PR3 web scaffold | `wp/00-3-web-scaffold` | merged 715d54e (PR #4) | CI green: 6/6 jobs incl. web + e2e проти стека | acceptance WP-00 закрито |
| WP-00 | PR4 role DSN secrets | `wp/00-4-role-dsn-secrets` | merged ae63917 (PR #7) | CI green 6/6; testing pass, code review approve (3'), security approve, spec review accept | половина блокера pilot §13 (deps `WP-01A-to-WP-00.md` §4, §6); друга половина — WP-01D PR1b; L-2: REVOKE лише при першому initdb (ручний крок у runbook) |
| WP-00 | PR5 object store secrets | `wp/00-5-object-store-secrets` | ready — наступна партія | — | передумова хвилі 1: MinIO per-role, Mongo per-component URI, секрет перекладу |
| WP-01C | — | `wp/01c-contracts` | merged 1f2fbc8 (PR #1) | docs done; ADR-0003/0004; CI green | worktree `.worktrees/wp-01c`; картка `docs/plan/cards/WP-01C.md` |
| WP-01C | PR2 payload/news contracts | `wp/01c-2-payload-news-contracts` | merged 8fde7c7 (PR #9) | CI green 6/6; testing pass, code review approve, spec review accept | розблоковує WP-01B PR2, WP-01A PR3b, WP-04 PR2; ADR-0003 поправка; `.gitleaksignore` +1 fingerprint |

## Хвиля 1

| WP | Стан | Примітка |
|---|---|---|
| WP-01A | PR1 merged 758c68c (PR #3); PR2 merged 43ee69f (PR #6); PR3 розбито на PR3a–PR3d: PR3a in_progress (передумова хвилі 1), PR3b/3c після PR3a, PR3d поза хвилею 1 | CI green (PR1: python, integration PostgreSQL 18, docker); ADR-0005, ADR-0007, `docs/persistence/postgres.md`; PR2 (`bad6a25`+fixes) — spec-review gate 4 `changes_requested` закрито (SR-1 gitleaks, SR-2 lineage, SR-5, N-1..N-5 fixed; D-1/D-2 задокументовані ADR-0007 + ТЗ §9.1); CI PR2 green 6/6 на `d83b627` |
| WP-01B | card ready 2026-09-24; PR1 `wp/01b-1-mongo-schema` — наступна партія; PR2 ← WP-01C PR2; PR3 ← WP-01D PR1c + WP-01A PR3a + WP-02 PR2 + WP-00 PR5; PR4 ← WP-01A PR3c | reconciler/compactor під `collector_projector`; publisher вимкнений до споживача; відкрито: receipt після restore (до PR3) |
| WP-01D | PR1 merged f87df17 (PR #5); PR1c `wp/01d-1c-handler-plumbing` in_progress (передумова хвилі 1, злиття після WP-01A PR3a); PR1b merged de517cf (PR #8) — runtime на per-component LOGIN-ролях, **блокер pilot §13 (I-1) закрито** разом з WP-00 PR4; залишок S-1: export-worker під `collector_scheduler` до першого export handler (WP-11A) або pilot; PR2 pending | CI green 6/6; ADR-0006, `docs/workers.md`, runbook worker-recovery; флак ризик — `tests/integration/scaling/test_worker_runtime.py::test_self_fencing_fires_when_the_database_hangs_without_raising` нестабільний під паралельним навантаженням (відтворено на WP-01A PR2 verification, не внесений PR2 — `docs/plan/reports/WP-01A/implementation-pr2.md`) |
| WP-02 | PR1 merged `3a416b4` (PR #11, CI green 6/6); PR2 ← PR1 + WP-01A PR3a + WP-01D PR1c + WP-00 PR5; PR3 ← PR2 | 392 unit/security + 9 PG integration; testing/code/security/spec/docs gates accepted; CR-1/CR-2 fixed; HTTPX ADR-0008; clean-host CI відновлено pinned source-build MinIO після видалення Quay manifest; S3-клієнт `src/collector/storage/**` owner WP-02; D-2 умова 3 (`parser_version`) → WP-05 |
| WP-04 | PR1 merged `31fc346` (PR #10, CI green 6/6); PR2 ← PR1 + WP-01C PR2 + WP-01A PR3a/3b + WP-01D PR1c + WP-00 PR5 + WP-02 PR2; PR3 ← PR2 | 1,589 translation tests; ADR-0009; dependency `WP-04-to-WP-01C.md` (`untranslated_content`) до PR2; мови поза 16 перекладаються (рішення користувача); batch GCS — out of scope v1 |

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
| `WP-01A-to-WP-00.md` | WP-01A PR1/PR2 | WP-00 | resolved — п.1–3 у WP-01A PR1/WP-00 PR2; §4 і §6 (I-2 REVOKE PUBLIC) — WP-00 PR4 (PR #7, ae63917); §5 — resolved by orchestrator |
| `WP-01D-to-WP-01A.md` | WP-01D | WP-01A | resolved — LOGIN-ролі і `queue.release` (WP-01A PR2), секрети (WP-00 PR4, PR #7), runtime (WP-01D PR1b, PR #8); I-1 закрито |
| `WP-01A-to-WP-01D.md` | WP-01A PR2 | WP-01D | resolved — §1–§2 у WP-01D PR1b (PR #8, de517cf); §7 N-2 (лічильник доставок outbox) → картка WP-01B (publisher loop) |
| `WP-01D-to-WP-01A.md` §6 | WP-01D PR1b | WP-01A | resolved by orchestrator exception — правка `verify_runtime_login` (session_user, членство); WP-01A підтверджує в PR3 |
| `WP-01A-to-WP-02.md` | WP-01A PR2 | WP-02 | open — умови D-2 `parse_key` для parser-а: ключ normalized artifact = `(sha256, entity_uuid)`, справжній `fetch_id` кожного запиту, зміна схеми = зміна `parser_version` |
