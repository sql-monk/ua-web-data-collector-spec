# WP-02 PR2 — security review

Verdict: **approve after fix `9b94f53`**.

## Reviewed boundaries

- Network requests remain exclusively inside the PR1 SSRF/DNS-pinned client and require a
  PostgreSQL origin permit; robots uses the same path and cannot introduce a second client.
- `respect` policy fails closed when robots cannot be obtained and no prior authoritative
  snapshot exists. A 404 is the only explicit absent-snapshot case that permits collection.
- Robots bytes are loaded through checksum- and size-verifying artifact reads; parsing uses the
  standard-library parser, replacement decoding and no code/XML execution.
- S3 credentials are file-only and lazy, are not logged, and producer policies have no list or
  delete permission. Readiness needs only bucket location.
- Object identity is SHA-256 metadata plus downloaded-byte verification; ETag is diagnostic.
- Metric labels exclude URLs, origins, keys, errors and credentials.
- Browser fallback is restricted to configured policy and category/detail routes; browser
  isolation itself remains PR3.

No new critical/high dependency finding was introduced. Existing base-image risk acceptance in
ADR-0002 remains owned by WP-13 and is not weakened by this PR.
