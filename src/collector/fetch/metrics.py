"""In-process fetch counters; the exporter is owned by WP-12.

Labels are deliberately bounded: only canonical ``source_id`` and HTTP status class are
accepted.  URLs, origins and error messages must never become metric labels.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class FetchMetricsSnapshot:
    http_requests_total: dict[tuple[str, str], int]
    http_429_total: dict[str, int]
    policy_blocks_total: dict[str, int]
    raw_bytes_total: dict[str, int]
    raw_uploads_total: dict[str, int]
    raw_deduplicated_total: dict[str, int]
    artifact_orphans_total: int

    def raw_dedup_ratio(self, source: str) -> float:
        total = self.raw_uploads_total.get(source, 0)
        return self.raw_deduplicated_total.get(source, 0) / total if total else 0.0


class FetchMetrics:
    """Small thread-safe registry with a stable API for the future metrics exporter."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._http: Counter[tuple[str, str]] = Counter()
        self._http_429: Counter[str] = Counter()
        self._policy_blocks: Counter[str] = Counter()
        self._raw_bytes: Counter[str] = Counter()
        self._raw_uploads: Counter[str] = Counter()
        self._raw_deduplicated: Counter[str] = Counter()
        self._artifact_orphans = 0

    def observe_http(self, source: str, status: int) -> None:
        status_class = f"{status // 100}xx" if 100 <= status <= 599 else "other"
        with self._lock:
            self._http[source, status_class] += 1
            if status == 429:
                self._http_429[source] += 1

    def observe_policy_block(self, source: str) -> None:
        with self._lock:
            self._policy_blocks[source] += 1

    def observe_raw(self, source: str, size: int, *, deduplicated: bool) -> None:
        if size < 0:
            raise ValueError("raw size must be non-negative")
        with self._lock:
            self._raw_bytes[source] += size
            self._raw_uploads[source] += 1
            if deduplicated:
                self._raw_deduplicated[source] += 1

    def observe_orphans(self, count: int) -> None:
        if count < 0:
            raise ValueError("orphan count must be non-negative")
        with self._lock:
            self._artifact_orphans += count

    def snapshot(self) -> FetchMetricsSnapshot:
        with self._lock:
            return FetchMetricsSnapshot(
                dict(self._http),
                dict(self._http_429),
                dict(self._policy_blocks),
                dict(self._raw_bytes),
                dict(self._raw_uploads),
                dict(self._raw_deduplicated),
                self._artifact_orphans,
            )


FETCH_METRICS = FetchMetrics()

__all__ = ["FETCH_METRICS", "FetchMetrics", "FetchMetricsSnapshot"]
