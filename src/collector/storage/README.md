# `collector.storage` — immutable artifact store

This package is the shared S3/MinIO boundary for WP-01B, WP-02, WP-04 and WP-05. Consumers
must import the public names from `collector.storage`; private client/session details are not a
compatibility contract.

## Public contract

- `StorageSettings.from_env()` reads `COLLECTOR_MINIO_URL`, optional
  `COLLECTOR_MINIO_REGION`, and the component-specific file named by
  `COLLECTOR_MINIO_CREDENTIALS_FILE`. The file has exactly `access_key=...` and
  `secret_key=...`; credentials are never accepted in the endpoint URL.
- `S3ArtifactStore` and `ArtifactStore` expose explicit-bucket `put`, `head`, bounded `get`,
  streaming `get_stream`, `delete`, and `check_ready` operations.
- `FakeArtifactStore` in `collector.storage.testing` implements the same integrity behavior for
  unit tests. It is not a production fallback.
- `raw_object_key(sha256)` returns `raw/<sha256>`.
- `normalized_object_key(entity_uuid, sha256)` returns
  `normalized/<entity_uuid>/<sha256>.json`. `put_normalized` additionally verifies the bytes,
  declared size, digest, and `s3://<bucket>/<canonical-key>` URI. A target collection schema
  change must change `parser_version`; WP-05 owns that parse/release check.

Every producer supplies lowercase SHA-256 and the store writes it as `x-amz-meta-sha256`.
Readers verify metadata, declared size, and the digest of the bytes they actually receive.
ETag is diagnostic only because multipart ETag is not a content checksum. A bounded read checks
HEAD before GET and also enforces the limit while streaming, so replacement/race behavior cannot
return oversized bytes.

## Claimed upload protocol

`ClaimedUploader` keeps network I/O outside PostgreSQL transactions:

1. acquire the `artifact_upload_claims` row in a short transaction;
2. PUT and HEAD-verify the object outside the transaction;
3. commit the fenced claim and the caller's reference callback in one transaction.

A stale generation repeats acquisition and PUT/HEAD. A live claim owned by another producer
raises `UploadBusyError`; it never causes a parallel PUT. A committed content-addressed claim is
verified and reused. Callers must make their reference callback idempotent.

Only maintenance credentials may delete. `sweep_orphans` treats the candidate query as a hint,
reacquires each claim as the sweeper, deletes, and releases that generation. Its grace period
must be strictly longer than the configured PUT + HEAD + commit window.

## Buckets and permissions

Buckets are always explicit (`raw`, `normalized`, `translated`, `events`, `archive`). Runtime
services receive only their own file secret from Docker Compose. Fetcher writes raw; parser reads
raw and writes normalized; projector reads normalized and writes events; translation reads
normalized and writes translated; API/export are read-only; maintenance has delete permission.

See `docs/decisions/0011-shared-s3-artifact-client.md` and
`docs/runbooks/artifact-sweep.md`.
