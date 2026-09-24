# ADR-0012: Metadata-only media collection in v1

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Owner | WP-02 |
| Status | accepted |

## Context

Q-002 asks whether the collector should download binary photos and video or retain only their
URLs and metadata. Binary media materially changes storage capacity, copyright exposure,
network cost, malware-scanning requirements and retention policy. None of those controls is
part of the v1 fetch/parse contract, while article and product extraction only needs references
to media.

## Decision

- v1 does not download response bodies with `image/*`, `video/*` or `audio/*` media types.
- The fetch result is `success` with `content_access=metadata_only` and
  `error_code=media_binary_skipped`; safe response headers remain available for lineage.
- `COLLECTOR_FETCH_MEDIA_BINARIES=0` is the production default. Setting it to `1` is an
  explicit experimental override, not a supported production profile or a change to retention.
- HTML, JSON, XML, RSS and sitemap payloads remain subject to their normal body and
  decompression limits. Page metadata may preserve source media URLs; adapters must not create
  a second HTTP client to download them.

## Consequences

- Raw storage growth and source bandwidth stay bounded by document collection rather than by
  embedded media size.
- The system cannot provide an independent archived copy of a source image or video in v1;
  consumers receive the source URL and available metadata only.
- Enabling production binary collection later requires a new decision covering allowlists,
  malware scanning, content-type verification, object lifecycle, quotas and legal retention.

## Related

- `TECHNICAL_SPECIFICATION.md`: Q-002, §3 p.10, §13.
- `docs/plan/cards/WP-02.md`: PR1 requirement 12 and WP-02 close condition.
- `src/collector/fetch/client.py`: media response classification.
- `src/collector/fetch/README.md`: limits and anonymous-only behavior.
