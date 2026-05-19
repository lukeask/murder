"""Unit tests for scheduler.usage_threshold_curve threshold curve properties."""

from __future__ import annotations

import pytest

from murder.scheduler.usage_threshold_curve import _f_threshold, f

# ---------------------------------------------------------------------------
# _f_threshold boundary conditions
# ---------------------------------------------------------------------------

T_PERIOD = 10_000.0
T_ALWAYSYES = 15.0
C_CHANGEOFF = 0.7
ALWAYSCUTOFF = 0.6
INTENSITY = 1.0


def threshold(t: float) -> float:
    return _f_threshold(t, T_PERIOD, C_CHANGEOFF, T_ALWAYSYES, ALWAYSCUTOFF, INTENSITY)


def test_threshold_at_zero_is_zero() -> None:
    assert threshold(0.0) == 0.0


def test_threshold_past_period_is_zero() -> None:
    # t >= t_period - t_alwaysyes → always-yes zone
    assert threshold(T_PERIOD - T_ALWAYSYES) == 0.0
    assert threshold(T_PERIOD) == 0.0


def test_threshold_in_alwayscutoff_zone() -> None:
    # For very small t (just above 0), threshold == alwayscutoff
    # x_cap = t_period * (1 - alwayscutoff) = 10000 * 0.4 = 4000
    # At t=1 (well below x_cap), threshold should be alwayscutoff
    assert threshold(1.0) == pytest.approx(ALWAYSCUTOFF)
    assert threshold(3999.0) == pytest.approx(ALWAYSCUTOFF)


def test_threshold_at_end_of_active_window_approaches_zero() -> None:
    # Just before always-yes zone, threshold should be near 0
    # B_eff = t_period - t_alwaysyes = 9985
    # threshold(B_eff - epsilon) → near 0
    val = threshold(T_PERIOD - T_ALWAYSYES - 1.0)
    assert val < 0.05


def test_threshold_is_non_negative() -> None:
    for t in range(0, int(T_PERIOD) + 1, 500):
        assert threshold(float(t)) >= 0.0


def test_threshold_is_at_most_one() -> None:
    for t in range(0, int(T_PERIOD) + 1, 500):
        assert threshold(float(t)) <= 1.0 + 1e-9


def test_threshold_monotone_non_increasing() -> None:
    """More time left → higher (or equal) threshold — conservative early on."""
    # Sample the active portion (above x_cap)
    x_cap = T_PERIOD * (1 - ALWAYSCUTOFF)
    B_eff = T_PERIOD - T_ALWAYSYES
    ts = [x_cap + i * (B_eff - x_cap) / 50 for i in range(51)]
    vals = [threshold(t) for t in ts]
    for i in range(len(vals) - 1):
        # Going left → higher t_until_reset → threshold should not increase
        assert vals[i] >= vals[i + 1] - 1e-9, (
            f"Non-monotone at i={i}: threshold({ts[i]:.1f})={vals[i]:.4f} "
            f"< threshold({ts[i + 1]:.1f})={vals[i + 1]:.4f}"
        )


# ---------------------------------------------------------------------------
# f() decision logic
# ---------------------------------------------------------------------------


def test_f_always_yes_zone() -> None:
    # In always-yes zone (t_until_reset ≥ t_period - t_alwaysyes = 9985), threshold=0
    # → any usage ≥ 0 → True.  This is the "fresh period" zone just after reset.
    assert f(0.0, t_until_reset=9986.0, t_period=T_PERIOD) is True
    assert f(0.01, t_until_reset=9990.0, t_period=T_PERIOD) is True


def test_f_high_usage_always_yes() -> None:
    # At t=4000 (alwayscutoff zone), threshold=0.6; usage=0.8 → True
    assert f(0.8, t_until_reset=4000.0, t_period=T_PERIOD) is True


def test_f_low_usage_early_period() -> None:
    # Near start of period (t_until_reset ≈ t_period - t_alwaysyes), threshold ≈ 0 → True
    # But deep in the conserving zone (t ≈ 7000, threshold ≈ alwayscutoff ≈ 0.6)
    # usage=0.1 → False
    assert f(0.1, t_until_reset=7000.0, t_period=T_PERIOD) is False


def test_f_usage_exactly_at_threshold() -> None:
    # At usage = threshold exactly, result should be True (usage >= threshold)
    t = 4000.0
    th = _f_threshold(t, T_PERIOD, C_CHANGEOFF, T_ALWAYSYES, ALWAYSCUTOFF, INTENSITY)
    assert f(th, t_until_reset=t, t_period=T_PERIOD) is True


def test_f_usage_just_below_threshold() -> None:
    t = 4000.0
    th = _f_threshold(t, T_PERIOD, C_CHANGEOFF, T_ALWAYSYES, ALWAYSCUTOFF, INTENSITY)
    if th > 0.0:
        assert f(th - 0.01, t_until_reset=t, t_period=T_PERIOD) is False


def test_f_different_intensity() -> None:
    # With intensity=0, threshold ≈ f_naive = 1 - t/period; at t=5000, threshold≈0.5
    th_low = _f_threshold(5000.0, T_PERIOD, C_CHANGEOFF, T_ALWAYSYES, ALWAYSCUTOFF, 0.0)
    th_high = _f_threshold(5000.0, T_PERIOD, C_CHANGEOFF, T_ALWAYSYES, ALWAYSCUTOFF, 1.0)
    # Both should be valid thresholds; no strict ordering guaranteed at midpoint,
    # but both must be in [0, 1]
    assert 0.0 <= th_low <= 1.0
    assert 0.0 <= th_high <= 1.0


def test_f_short_period() -> None:
    # Minimal realistic period: 5 hours = 300 minutes
    result = f(0.5, t_until_reset=100.0, t_period=300.0)
    assert isinstance(result, bool)


def test_f_long_period() -> None:
    # Near-max period: 30 days = 43200 minutes
    result = f(0.5, t_until_reset=10000.0, t_period=43200.0)
    assert isinstance(result, bool)
