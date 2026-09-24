# ADR-0011: Shared verified S3 artifact client and claimed uploads

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Owner | WP-02 |
| Status | accepted |

## Context

Raw responses, normalized payloads, translated bodies, event payloads and archive parts are
immutable objects used by several work packages. PostgreSQL stores lineage and references, but
cannot transactionally commit a remote S3 write. A process can stop after PUT, two producers can
race on the same content, and multipart ETag is not a trustworthy content checksum.

Independent S3 wrappers in each work package would produce incompatible key layouts, checksum
rules and credentials handling. Keeping a database transaction open during network I/O would
also extend row locks across an unbounded failure boundary.

## Decision

- `collector.storage` is the single shared artifact-store boundary. It uses `aiobotocore`, an
  explicit endpoint and bucket, and component credentials read only from a mounted file secret.
- Object identity is SHA-256. Producers store it in `x-amz-meta-sha256`; readers verify metadata,
  size and downloaded bytes. ETag is never accepted as proof of integrity.
- Raw and normalized keys are canonical: `raw/<sha256>` and
  `normalized/<entity_uuid>/<sha256>.json`. Other artifact families own equivalent key helpers
  when their contracts are introduced.
- Upload uses PostgreSQL claim fencing: short acquire transaction, PUT + HEAD outside a
  transaction, then claim commit and domain reference in one short transaction. A stale
  generation restarts the protocol; a live foreign claim does not allow a parallel PUT.
- Bounded `get` and streaming `get_stream` are both part of the public API. The in-memory fake
  follows the same integrity contract and is available to consumer unit tests.
- Delete is not part of normal producer credentials. Orphan cleanup must reacquire a fenced
  claim immediately before deletion and use the maintenance identity.

## Consequences

- A crash may leave an unreferenced object, but cannot create a valid database reference from a
  stale producer. The fenced sweeper removes such objects after a safe grace period.
- Content deduplication is shared across URLs and retries while every fetch attempt retains its
  own lineage row in PostgreSQL.
- Consumers fail closed on missing checksum metadata, corruption, oversize data or missing
  credentials. They do not silently fall back to local disk.
- `aiobotocore` becomes a runtime dependency. Its compatibility and transitive botocore version
  are pinned by `uv.lock` and exercised against the project MinIO image in `integration-fetch`.
- A future switch to another S3-compatible service preserves the package API but still requires
  the same metadata and fencing semantics.

## Related

- `TECHNICAL_SPECIFICATION.md`: §7.3, §9.1, §9.3, §10 p.5, §13.
- `REVIEW.md`: R-33, R-38, R-41.
- `docs/plan/cards/WP-02.md`: PR2.
- `src/collector/storage/README.md`.
- `docs/runbooks/artifact-sweep.md`.
