import { describe, expect, it } from 'vitest';
import { computePaneLayout } from '../../src/layout/paneLayout.js';
import type { PaneRequest } from '../../src/layout/paneLayoutTypes.js';

function request(overrides: Partial<PaneRequest> = {}): PaneRequest {
  return {
    id: 'usage',
    kind: 'usage',
    region: 'rightAligned',
    sizing: {
      min: { width: 20, height: 5 },
      preferred: { width: 34, height: 13 },
    },
    reapPriority: 40,
    orderKey: 0,
    source: { type: 'panel', panelId: 'usage' },
    ...overrides,
  };
}

describe('computePaneLayout', () => {
  it('admits a high-width low-height usage pane at its compact minimum', () => {
    const plan = computePaneLayout({
      terminal: { width: 80, height: 5 },
      chrome: { topBar: 0, bottomBar: 0, chatInput: 0 },
      body: { width: 80, height: 5 },
      orientation: 'portrait',
      gap: 0,
      requests: [request()],
      focusedPaneId: 'usage',
    });

    expect(plan.denials).toEqual([]);
    expect(plan.allocations).toHaveLength(1);
    expect(plan.allocations[0]?.rect).toMatchObject({ width: 80, height: 5 });
    expect(plan.allocations[0]?.presentation.constraints.verticallyCramped).toBe(true);
  });

  it('tiles side-region panes horizontally in portrait when width can buy back height', () => {
    const usage = request({
      id: 'usage',
      kind: 'usage',
      sizing: {
        min: { width: 20, height: 5 },
        preferred: { width: 34, height: 13 },
      },
      orderKey: 0,
    });
    const crows = request({
      id: 'crows',
      kind: 'listPane',
      sizing: {
        min: { width: 18, height: 7 },
        preferred: { width: 34, height: 13 },
      },
      orderKey: 1,
      source: { type: 'panel', panelId: 'crows' },
    });

    const plan = computePaneLayout({
      terminal: { width: 80, height: 7 },
      chrome: { topBar: 0, bottomBar: 0, chatInput: 0 },
      body: { width: 80, height: 7 },
      orientation: 'portrait',
      gap: 1,
      requests: [usage, crows],
    });

    expect(plan.denials).toEqual([]);
    expect(plan.allocations.map((allocation) => allocation.request.id)).toEqual(['usage', 'crows']);
    expect(plan.allocations[0]?.rect.y).toBe(0);
    expect(plan.allocations[1]?.rect.y).toBe(0);
    expect(plan.allocations[0]?.rect.height).toBe(7);
    expect(plan.allocations[1]?.rect.height).toBe(7);
    expect((plan.allocations[0]?.rect.width ?? 0) + (plan.allocations[1]?.rect.width ?? 0)).toBe(
      79,
    );
  });

  it('wraps side-region panes in portrait instead of cutting when width is tight and height exists', () => {
    const panels = ['notes', 'plans', 'reports'].map((id, index) =>
      request({
        id,
        kind: 'listPane',
        sizing: {
          min: { width: 25, height: 5 },
          preferred: { width: 25, height: 5 },
        },
        orderKey: index,
        source: { type: 'panel', panelId: id as 'notes' | 'plans' | 'reports' },
      }),
    );

    const plan = computePaneLayout({
      terminal: { width: 25, height: 17 },
      chrome: { topBar: 0, bottomBar: 0, chatInput: 0 },
      body: { width: 25, height: 17 },
      orientation: 'portrait',
      gap: 1,
      requests: panels,
    });

    expect(plan.denials).toEqual([]);
    expect(plan.allocations.map((allocation) => allocation.request.id)).toEqual([
      'notes',
      'plans',
      'reports',
    ]);
    expect(plan.allocations.map((allocation) => allocation.rect)).toEqual([
      { x: 0, y: 0, width: 25, height: 5 },
      { x: 0, y: 6, width: 25, height: 5 },
      { x: 0, y: 12, width: 25, height: 5 },
    ]);
  });

  it('cuts a taller blocker instead of losing a short-wide pane that can fit', () => {
    const usage = request({
      id: 'usage',
      kind: 'usage',
      sizing: {
        min: { width: 20, height: 5 },
        preferred: { width: 34, height: 13 },
      },
      reapPriority: 46,
      orderKey: 6,
      source: { type: 'panel', panelId: 'usage' },
    });
    const tree = request({
      id: 'tree',
      kind: 'tree',
      sizing: {
        min: { width: 25, height: 10 },
        preferred: { width: 40, height: 13 },
      },
      reapPriority: 45,
      orderKey: 5,
      source: { type: 'panel', panelId: 'tree' },
    });

    const plan = computePaneLayout({
      terminal: { width: 80, height: 5 },
      chrome: { topBar: 0, bottomBar: 0, chatInput: 0 },
      body: { width: 80, height: 5 },
      orientation: 'portrait',
      gap: 0,
      requests: [tree, usage],
      focusedPaneId: 'usage',
    });

    expect(plan.allocations.map((allocation) => allocation.request.id)).toEqual(['usage']);
    expect(plan.denials.map((denial) => denial.request.id)).toEqual(['tree']);
    expect(plan.denials[0]?.reason).toBe('preemptedByReapPriority');
  });
});
