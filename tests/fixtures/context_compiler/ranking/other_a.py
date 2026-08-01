"""Sibling that imports the central hub — creates fan-in without task relevance."""

from hub import common_util_alpha, common_util_beta


def other_a_run() -> int:
    return common_util_alpha(1) + common_util_beta(2)
