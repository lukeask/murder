import { describe, expect, it } from 'vitest';
import {
  createSurface,
  drawBox,
  drawHorizontal,
  drawVertical,
  renderSurface,
} from '../../src/render/cellSurface.js';
import { layoutWorkflow } from '../../src/workflowEditor/layout.js';
import type { EditorWorkflow } from '../../src/workflowEditor/model.js';
import {
  E,
  glyphForMask,
  N,
  routeEdges,
  S,
  segmentMasks,
  W,
} from '../../src/workflowEditor/routing.js';

describe('workflow graph raster helpers', () => {
  it('composes perpendicular segment masks into a crossing', () => {
    const masks = segmentMasks([
      { x: 0, y: 1 },
      { x: 2, y: 1 },
      { x: 2, y: 3 },
    ]);
    expect(glyphForMask(masks.get('2,1') ?? 0)).toBe('┐');
    expect(glyphForMask(N | E | S | W)).toBe('┼');
  });
  it('merges connectivity when low-level line primitives cross', () => {
    const surface = createSurface(5, 5);
    drawHorizontal(surface, 0, 4, 2);
    drawVertical(surface, 2, 0, 4);

    expect(
      renderSurface(surface, 2)
        .map((run) => run.text)
        .join(''),
    ).toBe('──┼──');
  });
  it('clips box drawing at the surface edge', () => {
    const surface = createSurface(3, 2);
    drawBox(surface, { x: -1, y: 0, width: 4, height: 3 });
    expect(
      renderSurface(surface, 0)
        .map((run) => run.text)
        .join(''),
    ).toContain('┐');
  });
  it('routes long edges through every inter-rank gutter deterministically', () => {
    const stage = (key: string, dependsOn: readonly string[] = []) => ({
      key,
      id: key,
      title: key,
      instructions: '',
      harness: 'codex',
      model: 'gpt-5',
      worktree: null,
      dependsOn,
      gate: 'auto' as const,
    });
    const workflow: EditorWorkflow = {
      name: 'flow',
      definitionVersion: 1,
      description: '',
      mode: 'static',
      stages: [stage('root'), stage('middle', ['root']), stage('leaf', ['middle', 'root'])],
    };
    const layout = layoutWorkflow(workflow);
    const first = routeEdges(layout);
    const long = first.edges.find((edge) => edge.source === 'root' && edge.target === 'leaf');

    expect(long?.points.length).toBeGreaterThanOrEqual(6);
    expect(new Set(long?.points.map((point) => point.x)).size).toBeGreaterThanOrEqual(4);
    expect(routeEdges(layout)).toEqual(first);
  });
});
