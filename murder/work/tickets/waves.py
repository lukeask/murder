"""Wave + dependency graph helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from murder.work.tickets.schema import Ticket


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
