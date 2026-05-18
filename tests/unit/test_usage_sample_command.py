"""Cookbook + edge checks for shared harness usage sample command metadata."""

from murder.usage_sample_command import (
    HARNESS_USAGE_SAMPLE_KIND,
    TRIGGER_USAGE_MANUAL_KEY,
    TRIGGER_USAGE_SERVICE_INTERVAL,
    USAGE_PROBE_TARGET,
    USAGE_SAMPLE_POLL_INTERVAL_S,
    harness_usage_sample_payload,
)


def test_harness_usage_sample_payload_shape() -> None:
    assert harness_usage_sample_payload(trigger="x") == {"trigger": "x"}


def test_constants_align_with_usage_probe_worker() -> None:
    """UsageProbeWorker accepts this kind; target matches worker spec name."""
    assert USAGE_PROBE_TARGET == "usage-probe"
    assert HARNESS_USAGE_SAMPLE_KIND == "state.harness_usage.sample"
    assert TRIGGER_USAGE_MANUAL_KEY == "manual_u_key"
    assert TRIGGER_USAGE_SERVICE_INTERVAL == "interval_10m"
    assert USAGE_SAMPLE_POLL_INTERVAL_S == 600.0
