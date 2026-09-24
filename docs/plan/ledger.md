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
| WP-00 | PR5 object store secrets | `wp/00-5-object-store-secrets` | merged f14d17d (PR #14) | CI green 6/6; testing pass, code/security reviews approve, spec accept, docs pass | MinIO per-role + live permission matrix; Mongo per-component URI; translation placeholder disabled; 3674 local tests |
| WP-01C | — | `wp/01c-contracts` | merged 1f2fbc8 (PR #1) | docs done; ADR-0003/0004; CI green | worktree `.worktrees/wp-01c`; картка `docs/plan/cards/WP-01C.md` |
| WP-01C | PR2 payload/news contracts | `wp/01c-2-payload-news-contracts` | merged 8fde7c7 (PR #9) | CI green 6/6; testing pass, code review approve, spec review accept | розблоковує WP-01B PR2, WP-01A PR3b, WP-04 PR2; ADR-0003 поправка; `.gitleaksignore` +1 fingerprint |

## Хвиля 1

| WP | Стан | Примітка |
|---|---|---|
| WP-01A | PR1 merged 758c68c (PR #3); PR2 merged 43ee69f (PR #6); PR3a merged `9322fc7` (PR #12, CI green 6/6); PR3b/3c після PR3a, PR3d поза хвилею 1 | PR3a: queue/projection defer, outbox N-2/purge, fetch preflight/validators, route counter, retry budget, ack fencing, reconciler queries; 3496 local tests + independent adversarial testing, code/security/spec/docs gates accepted; ADR-0005/0007 і `docs/persistence/postgres.md` актуальні |
| WP-01B | PR1 merged `774f7c7` (PR #15, CI green 7/7); PR2 ← WP-01C PR2; PR3 ← WP-01D PR1c + WP-01A PR3a + WP-02 PR2 + WP-00 PR5; PR4 ← WP-01A PR3c | PR1: 11 collections, frozen validators, 23 indexes, repositories, least-privilege users, Mongo CI; 3857 local tests + 138 Mongo integration; reconciler/compactor під `collector_projector`; відкрито: receipt після restore (до PR3) |
| WP-01D | PR1 merged f87df17 (PR #5); PR1b merged de517cf (PR #8) — runtime на per-component LOGIN-ролях, **блокер pilot §13 (I-1) закрито** разом з WP-00 PR4; PR1c merged `b9bed91` (PR #13, CI green 6/6) — defer/retry schedule, lazy handler registry, projection backend, scheduler ticks; залишок S-1: export-worker під `collector_scheduler` до першого export handler (WP-11A) або pilot; PR2 pending | PR1c: 3607 local tests + 95 scaling, mutation-check, code/security/spec/docs gates; CR-1 cancellation race lease-check fixed. ADR-0006, `docs/workers.md`, runbook worker-recovery |
| WP-02 | PR1 merged `3a416b4` (PR #11, CI green 6/6); PR2 PR #17 final CI pending; PR3 ← PR2 | PR2: verified S3/raw claims, FetchHandler, robots TTL/policy, counters, 3893 local tests; code/security/spec/docs self-review complete, CR-1 fail-closed robots fixed; D-2 conditions 1–2 closed, condition 3 (`parser_version`) → WP-05 |
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
| `WP-01A-to-WP-01D.md` | WP-01A PR2/PR3a | WP-01D/WP-01B | §1–§6 resolved; §7 PostgreSQL N-2 merged у PR #12, повне resolved після publisher loop WP-01B PR3; §8 defer/not-before/fencing API consumed by WP-01D PR1c (PR #13) |
| `WP-01D-to-WP-01A.md` §6 | WP-01D PR1b | WP-01A | resolved by orchestrator exception — правка `verify_runtime_login` (session_user, членство); WP-01A підтверджує в PR3 |
| `WP-01A-to-WP-02.md` | WP-01A PR2 | WP-02/WP-05 | partially resolved — WP-02 PR2 закрив canonical normalized key і справжній `fetch_id`; зміна схеми = зміна `parser_version` передана WP-05 |
