# WP-02 PR2 — implementation report

Branch `wp/02-2-raw-upload-claim`, PR #17. Scope: shared artifact store, fenced uploads,
`FetchHandler`, versioned robots snapshots, orphan sweeper and fetch/storage counters.

## Delivered

- `collector.storage`: lazy aiobotocore S3 client, checksum/size verified put/head/get/stream,
  canonical raw/normalized keys, consumer fake, claimed upload with generation fencing and a
  fenced orphan sweeper.
- `collector.fetch.handler`: source/route preflight, conditional validators, global permits,
  raw persistence plus parse enqueue, 304 lineage, 429 scheduling, restricted browser fallback
  and lazy runtime registration.
- `collector.fetch.robots`: `/robots.txt` normalization, 24-hour default TTL, immutable raw
  history, 404-as-absent, previous snapshot on refresh failure and fail-closed `respect` policy.
- `collector.fetch.metrics`: bounded labels and counters required by §14.1; exporter remains
  owned by WP-12.
- CI `integration-fetch`: PostgreSQL 18 plus source-built pinned MinIO, with skip-to-fail guard.

## Key corrections during implementation

- S3 configuration is lazy so missing object-store credentials fail readiness without hiding
  the database role/login diagnostic.
- Readiness uses `GetBucketLocation`, not `HeadBucket`/`ListBucket`, preserving least privilege.
- Browser fallback is limited to category/detail routes.
- Security review found a no-snapshot robots 403 path that could continue to the page; commit
  `9b94f53` makes it fail closed and adds an integration regression test.

No database migration or contract schema changed. Translation and browser worker PR3 are out of
scope.
