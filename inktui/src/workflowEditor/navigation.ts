import type { GraphLayout } from './layout.js';
import type { Rect, StageKey, Viewport } from './model.js';

export function autoPan(
  viewport: Viewport,
  selected: Rect,
  width: number,
  height: number,
): Viewport {
  let x = viewport.x;
  let y = viewport.y;
  if (selected.x < x) x = selected.x;
  else if (selected.x + selected.width > x + width) x = selected.x + selected.width - width;
  if (selected.y < y) y = selected.y;
  else if (selected.y + selected.height > y + height) y = selected.y + selected.height - height;
  return { x: Math.max(0, x), y: Math.max(0, y) };
}

export function nearestNode(
  layout: GraphLayout,
  selected: StageKey,
  direction: 'up' | 'down' | 'dependency' | 'dependent',
): StageKey | null {
  const current = layout.nodes.get(selected);
  if (current === undefined) return null;
  let candidates: readonly StageKey[];
  if (direction === 'up' || direction === 'down') candidates = layout.ranks[current.rank] ?? [];
  else {
    const direct = (
      direction === 'dependency'
        ? (layout.graph.incoming.get(selected) ?? [])
        : (layout.graph.outgoing.get(selected) ?? [])
    ).map((edge) => (direction === 'dependency' ? edge.source : edge.target));
    if (direct.length > 0) {
      candidates = direct;
    } else {
      const adjacentRank = current.rank + (direction === 'dependency' ? -1 : 1);
      candidates = adjacentRank < 0 ? [] : (layout.ranks[adjacentRank] ?? []);
    }
  }
  const filtered = candidates.filter(
    (key) =>
      key !== selected &&
      (direction === 'up'
        ? (layout.nodes.get(key)?.order ?? 0) < current.order
        : direction === 'down'
          ? (layout.nodes.get(key)?.order ?? 0) > current.order
          : true),
  );
  return (
    filtered.sort((a, b) => {
      const ay = Math.abs((layout.nodes.get(a)?.rect.y ?? 0) - current.rect.y);
      const by = Math.abs((layout.nodes.get(b)?.rect.y ?? 0) - current.rect.y);
      return ay - by || a.localeCompare(b);
    })[0] ?? null
  );
}
