"""Wave validation: topo, write-set conflicts, misordered deps."""

from __future__ import annotations

import pytest


def test_topo_partition_simple_chain() -> None:
    # TODO(M4): three tickets t1→t2→t3; layers = [[t1],[t2],[t3]].
    pytest.skip("M4 stub")


def test_topo_raises_on_cycle() -> None:
    # TODO(M4): t1→t2→t1; expect ValueError or custom CycleError.
    pytest.skip("M4 stub")


def test_write_set_conflict_within_wave() -> None:
    # TODO(M4): t1 and t2 both wave=1, both touch src/foo.py → reported.
    pytest.skip("M4 stub")


def test_write_set_no_conflict_across_waves() -> None:
    # TODO(M4): t1 wave 1 + t2 wave 2 both touch src/foo.py → not reported.
    pytest.skip("M4 stub")


def test_misordered_deps_flagged() -> None:
    # TODO(M4): t_w1 depends on t_w2 → flagged.
    pytest.skip("M4 stub")
