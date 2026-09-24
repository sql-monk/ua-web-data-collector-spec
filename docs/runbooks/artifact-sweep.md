# Runbook: fenced orphan artifact sweep

## Purpose and safety boundary

An orphan is an object left after PUT when the producer stopped before committing its PostgreSQL
reference. Candidate age alone is not authority to delete: an active producer may still be
between PUT, HEAD and commit. The sweep is safe only when it uses `sweep_orphans`, which
reacquires each claim with a new fencing generation immediately before deletion.

Run the sweep only as the maintenance component with its own PostgreSQL DSN and
`minio_maintenance` file secret. Fetch, parser, projector and translation credentials must not
have `DeleteObject`.

## Preconditions

1. Confirm PostgreSQL and MinIO are healthy and their clocks are synchronized.
2. Set `grace` strictly above the maximum configured PUT + HEAD + commit window. The function
   rejects `grace <= minimum_grace`; do not bypass this check.
3. Start with a bounded `limit` and inspect the candidate count. A sudden increase can indicate
   a failing producer rather than ordinary cleanup.
4. Do not delete objects manually from a candidate list. The list is intentionally stale by the
   time it is returned.

## Expected behavior

For every candidate the sweeper attempts to reacquire the claim. A live producer claim is
reported as skipped. After successful reacquisition it deletes the object and releases that
exact generation even if deletion raises. The report contains candidate, deleted and skipped
counts/keys; WP-12 will schedule the operation and export `artifact_orphans_total`.

## Failure handling

- MinIO unavailable or delete denied: stop the run, retain the database claim history, repair
  connectivity/permissions, then rerun. Do not grant delete to a producer identity.
- Many skipped keys: verify producer latency and lease/grace configuration before retrying.
- Producer reports `StaleClaimError`: expected if the sweeper legitimately reacquired an old
  orphan. The producer must reacquire and repeat PUT + HEAD; it must not commit its old result.
- Suspected deletion of a referenced object: pause maintenance, preserve logs and audit rows,
  restore the immutable object from backup using the same key and SHA-256, then run the storage
  contract check before resuming.

## Rollback

Disable the scheduled sweep (owner WP-12). This stops deletion without affecting producers;
unreferenced objects accumulate but referenced data remains available. Reverting application
code does not restore deleted bytes, so material deletion requires backup recovery.
