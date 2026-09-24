from collector.fetch.metrics import FetchMetrics


def test_fetch_metrics_have_bounded_labels_and_compute_dedup_ratio() -> None:
    metrics = FetchMetrics()

    metrics.observe_http("source_a", 200)
    metrics.observe_http("source_a", 429)
    metrics.observe_policy_block("source_a")
    metrics.observe_raw("source_a", 120, deduplicated=False)
    metrics.observe_raw("source_a", 120, deduplicated=True)
    metrics.observe_orphans(3)

    snapshot = metrics.snapshot()
    assert snapshot.http_requests_total == {("source_a", "2xx"): 1, ("source_a", "4xx"): 1}
    assert snapshot.http_429_total == {"source_a": 1}
    assert snapshot.policy_blocks_total == {"source_a": 1}
    assert snapshot.raw_bytes_total == {"source_a": 240}
    assert snapshot.raw_dedup_ratio("source_a") == 0.5
    assert snapshot.artifact_orphans_total == 3


def test_fetch_metrics_reject_negative_observations() -> None:
    metrics = FetchMetrics()

    try:
        metrics.observe_raw("source_a", -1, deduplicated=False)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("negative raw bytes must fail")
