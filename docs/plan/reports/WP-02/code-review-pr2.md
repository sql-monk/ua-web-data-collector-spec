# WP-02 PR2 — code review

Verdict: **approve after fixes**. Reviewed the complete PR2 diff for transaction boundaries,
idempotency, cancellation/error paths, concurrency fencing and unnecessary coupling.

## Findings

| ID | Severity | Finding | Resolution |
|---|---|---|---|
| CR-1 | high | `robots_policy=respect` could continue when the first robots refresh returned 403 and no previous snapshot existed | fixed in `9b94f53`; page is not requested and robots route is not incorrectly circuit-opened |
| CR-2 | medium | S3 readiness originally depended on a broad bucket probe and eager credentials, obscuring least-privilege/login diagnostics | fixed in `93eecb7` and `650210a`; lazy config plus `GetBucketLocation` |
| CR-3 | medium | Robots artifacts must not enqueue `parse.raw` or trigger page route effects | fixed; explicit `enqueue_parse=False`, `request_variant=robots`, integration assertions |
| CR-4 | low | Metrics could acquire unbounded labels if URL/origin were exposed | fixed by API: only canonical source and computed status class are accepted |

The producer protocol keeps PostgreSQL transactions short, repeats PUT+HEAD after stale fencing,
and commits the claim plus caller reference in one transaction. The sweeper reacquires a fresh
generation before delete and always releases it. No blocking I/O was added to the event loop.
