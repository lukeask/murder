"""Sibling that imports the central hub."""

from hub import common_util_gamma, common_util_delta


def other_b_run() -> int:
    return common_util_gamma(1) + common_util_delta(2)
