"""Wave + dependency graph helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from murder.tickets.schema import Ticket


class CycleError(ValueError):
    pass


def topo_partition(tickets: Iterable[Ticket]) -> list[list[Ticket]]:
    """Layer tickets such that all deps of layer N are in layers 0..N-1."""
    by_id: dict[str, Ticket] = {t.id: t for t in tickets}
    indeg: dict[str, int] = {tid: 0 for tid in by_id}
    children: dict[str, list[str]] = defaultdict(list)
    for t in by_id.values():
        for dep in t.deps:
            if dep in by_id:
                indeg[t.id] += 1
                children[dep].append(t.id)

    layers: list[list[Ticket]] = []
    frontier = deque([tid for tid, n in indeg.items() if n == 0])
    placed = 0
    while frontier:
        layer: list[Ticket] = []
        next_frontier: deque[str] = deque()
        # Drain current frontier into one layer.
        while frontier:
            tid = frontier.popleft()
            layer.append(by_id[tid])
            placed += 1
        for t in layer:
            for child in children[t.id]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    next_frontier.append(child)
        layers.append(sorted(layer, key=lambda x: x.id))
        frontier = next_frontier
    if placed != len(by_id):
        raise CycleError("dependency graph contains a cycle")
    return layers


def write_set_conflicts(tickets: Iterable[Ticket]) -> list[tuple[str, str, set[str]]]:
    """Pairs of tickets in the same wave with overlapping write_sets."""
    by_wave: dict[int, list[Ticket]] = defaultdict(list)
    for t in tickets:
        by_wave[t.wave].append(t)
    out: list[tuple[str, str, set[str]]] = []
    for ts in by_wave.values():
        for i, a in enumerate(ts):
            for b in ts[i + 1 :]:
                a_set = {str(p) for p in a.write_set}
                b_set = {str(p) for p in b.write_set}
                overlap = a_set & b_set
                if overlap:
                    lo, hi = sorted([a.id, b.id])
                    out.append((lo, hi, overlap))
    return sorted(out, key=lambda x: (x[0], x[1]))


def misordered_deps(tickets: Iterable[Ticket]) -> list[tuple[str, str]]:
    """Pairs (ticket, dep_id) where dep's wave >= ticket's wave."""
    by_id: dict[str, Ticket] = {t.id: t for t in tickets}
    out: list[tuple[str, str]] = []
    for t in by_id.values():
        for dep in t.deps:
            d = by_id.get(dep)
            if d is not None and d.wave >= t.wave:
                out.append((t.id, dep))
    return sorted(out)


def deps_graph(tickets: Iterable[Ticket]) -> dict[str, set[str]]:
    g: dict[str, set[str]] = defaultdict(set)
    for t in tickets:
        g[t.id] |= set(t.deps)
    return dict(g)
