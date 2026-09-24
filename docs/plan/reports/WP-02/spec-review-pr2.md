# WP-02 PR2 — specification review

Verdict: **accept**. Final PR head `2e08b13` passed all 8 GitHub CI jobs and was merged as
`76473d4` in PR #17.

## Acceptance matrix

| Requirement | Status | Evidence |
|---|---|---|
| Shared verified artifact API and fake | evidenced | `collector.storage`, ADR-0011, contract and MinIO tests |
| Claimed/verified PUT, fencing, dedup | evidenced | `upload.py`, `test_claimed_upload.py` |
| Canonical raw/normalized keys and real fetch id | evidenced | `keys.py`, handler parse args tests |
| Versioned robots snapshot and TTL | evidenced | `robots.py`, handler unit/integration tests |
| Preflight, validators, retries, raw persistence | evidenced | `handler.py`, `test_handler.py`, runtime plumbing tests |
| Fenced orphan sweeper | evidenced | `sweeper.py`, fault-injection tests and runbook |
| Required counters in code | evidenced | `metrics.py`, `docs/observability/metrics.md` |
| Least privilege and missing credentials | evidenced | role matrix, lazy readiness and real MinIO CI |
| Q-002 media default off | evidenced | ADR-0012, PR1 media tests |
| No-skip PostgreSQL+MinIO CI | evidenced | final `integration-fetch` job passed in 2m32s; enforcement tests ran under the required flag |

PR2 does not implement browser execution (PR3), scheduled sweeper/exporter (WP-12), parser
schema-version discipline (WP-05) or translation. Dependency `WP-01A-to-WP-02` is therefore
partially resolved: conditions 1-2 are closed here; condition 3 transfers to WP-05.

No acceptance item is missing from PR2 code. All final-head GitHub checks were green before
merge.
