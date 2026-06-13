"""
Decision function for resource rationing.

Returns True iff resources should be used at current time.
Decision rule: f(t) <= usage -> use (True); f(t) > usage -> don't use (False).
"""

from __future__ import annotations


def _bezier_cubic_y(P0y: float, P1y: float, P2y: float, P3y: float, t: float) -> float:
    """y-coord of cubic Bezier at parameter t in [0,1]."""
    return (1 - t) ** 3 * P0y + 3 * (1 - t) ** 2 * t * P1y + 3 * (1 - t) * t**2 * P2y + t**3 * P3y


def _solve_bezier_t(P0x: float, P1x: float, P2x: float, P3x: float, x_target: float) -> float:
    """
    Solve for t in [0,1] such that the cubic Bezier's x-coord equals x_target.
    Bezier x is monotone in t since control points have increasing x, so use
    bisection.
    """
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        x_mid = (
            (1 - mid) ** 3 * P0x
            + 3 * (1 - mid) ** 2 * mid * P1x
            + 3 * (1 - mid) * mid**2 * P2x
            + mid**3 * P3x
        )
        if x_mid < x_target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _clamp_handles_monotone(
    P0: tuple[float, float],
    slope0: float,
    P3: tuple[float, float],
    slope3: float,
    max_frac: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Shrink handle lengths until y is monotone non-increasing along control polygon."""
    L = P3[0] - P0[0]
    d1 = d2 = max_frac * L
    if d1 + d2 > L:
        scale = L / (d1 + d2)
        d1 *= scale
        d2 *= scale
    for _ in range(20):
        P1y = P0[1] + slope0 * d1
        P2y = P3[1] - slope3 * d2
        ok_left = P1y <= P0[1] + 1e-12
        ok_mid = P2y <= P1y + 1e-12
        ok_right = P3[1] <= P2y + 1e-12
        if ok_left and ok_mid and ok_right:
            break
        if not ok_mid:
            d1 *= 0.85
            d2 *= 0.85
        elif not ok_left:
            d1 *= 0.85
        elif not ok_right:
            d2 *= 0.85
    P1 = (P0[0] + d1, P0[1] + slope0 * d1)
    P2 = (P3[0] - d2, P3[1] - slope3 * d2)
    # _solve_bezier_t relies on x being monotone non-decreasing in t, which holds
    # iff P0x <= P1x <= P2x <= P3x. The d1+d2 <= L clamp above guarantees this for
    # valid inputs; assert it so out-of-range params (e.g. intensity > 1, a
    # tweaked max_frac) trip loudly instead of letting bisection return garbage.
    assert P0[0] <= P1[0] <= P2[0] <= P3[0], (
        f"non-monotone Bezier x: {P0[0]}, {P1[0]}, {P2[0]}, {P3[0]}"
    )
    return P1, P2


def _f_threshold(
    t_until_reset: float,
    t_period: float,
    c_changeoff: float,
    t_alwaysyes: float,
    alwayscutoff: float,
    intensity: float,
) -> float:
    """Compute the threshold f(t_until_reset). h = l = intensity."""
    x = t_until_reset
    B_eff = t_period - t_alwaysyes  # active window length

    if x <= 0 or x >= t_period - t_alwaysyes:
        return 0.0

    x_cap = t_period * (1 - alwayscutoff)

    x_co = c_changeoff * B_eff
    x_co = max(x_co, x_cap)
    x_co = min(x_co, B_eff - 1e-9)

    x_end = B_eff
    f_naive_co = 1 - x_co / t_period
    slope_naive = -1.0 / t_period

    steepen = 1.0 + 3.0 * intensity
    slope_co = slope_naive * steepen
    slope_at_cap = (1 - intensity) * slope_naive
    slope_at_end = (1 - intensity) * slope_naive
    max_frac = 0.35 + 0.45 * intensity

    if x <= x_cap:
        return alwayscutoff

    if x <= x_co:
        P0 = (x_cap, alwayscutoff)
        P3 = (x_co, f_naive_co)
        P1, P2 = _clamp_handles_monotone(P0, slope_at_cap, P3, slope_co, max_frac)
        t = _solve_bezier_t(P0[0], P1[0], P2[0], P3[0], x)
        return _bezier_cubic_y(P0[1], P1[1], P2[1], P3[1], t)

    # x_co < x < x_end
    P0 = (x_co, f_naive_co)
    P3 = (x_end, 0.0)
    P1, P2 = _clamp_handles_monotone(P0, slope_co, P3, slope_at_end, max_frac)
    t = _solve_bezier_t(P0[0], P1[0], P2[0], P3[0], x)
    return _bezier_cubic_y(P0[1], P1[1], P2[1], P3[1], t)


def f(
    usage: float,
    t_until_reset: float,
    t_period: float,
    c_changeoff: float = 0.7,
    t_alwaysyes: float = 15.0,
    alwayscutoff: float = 0.6,
    intensity: float = 1.0,
) -> bool:
    """
    Decide whether to use resources now.

    Args:
        usage: float in [0, 1]. Current fraction of max resources used.
        t_until_reset: float (minutes). Time remaining until reset.
        t_period: float (minutes). Total period length. 300 < t_period < 44640.
        c_changeoff: float in [0, 1]. Fraction of active window where ration->spend transitions.
        t_alwaysyes: float (minutes). Trailing window where we always use.
        alwayscutoff: float in [0, 1]. Cap on the threshold: if we have more than this
            fraction available, just spend regardless.
        intensity: float in [0, 1]. How aggressively f deviates from f_naive.

    Returns:
        bool. True -> use resources. False -> don't use.
    """
    threshold = _f_threshold(
        t_until_reset, t_period, c_changeoff, t_alwaysyes, alwayscutoff, intensity
    )
    return usage >= threshold
