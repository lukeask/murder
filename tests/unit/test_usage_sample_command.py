from __future__ import annotations

from murder import usage_sample_command


def test_jittered_usage_poll_interval_uses_20_percent_bounds(monkeypatch) -> None:
    calls: list[tuple[float, float]] = []

    def _uniform(low: float, high: float) -> float:
        calls.append((low, high))
        return high

    monkeypatch.setattr(usage_sample_command.random, "uniform", _uniform)

    interval = usage_sample_command.jittered_usage_poll_interval_s()

    assert calls == [(480.0, 720.0)]
    assert interval == 720.0
