# WP-02 PR2 — specification review

Verdict: **accept, subject to green GitHub CI on the final documentation commit**.

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
| No-skip PostgreSQL+MinIO CI | pending final run | `integration-fetch` job and enforcement tests |

PR2 does not implement browser execution (PR3), scheduled sweeper/exporter (WP-12), parser
schema-version discipline (WP-05) or translation. Dependency `WP-01A-to-WP-02` is therefore
partially resolved: conditions 1-2 are closed here; condition 3 transfers to WP-05.

No acceptance item is missing from PR2 code. Merge remains prohibited until every final-head
GitHub check is green.
