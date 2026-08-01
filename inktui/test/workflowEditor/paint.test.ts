import { describe, expect, it } from 'vitest';
import { createSurface, renderSurface } from '@murder/ui-core/render/cellSurface.js';
import { layoutWorkflow } from '@murder/ui-core/workflowEditor/layout.js';
import type { EditorWorkflow } from '@murder/ui-core/workflowEditor/model.js';
import { paintWorkflow } from '../../src/workflowEditor/paint.js';

const stage = (key: string, id: string, dependsOn: readonly string[] = []) => ({
  key,
  id,
  title: `Title ${id}`,
  instructions: '',
  harness: 'codex',
  model: 'gpt-5',
  worktree: 'feature',
  dependsOn,
  gate: 'auto' as const,
});

function lines(surface: ReturnType<typeof createSurface>): readonly string[] {
  return Array.from({ length: surface.height }, (_, y) =>
    renderSurface(surface, y)
      .map((run) => run.text)
      .join(''),
  );
}

describe('paintWorkflow', () => {
  it('paints semantic node rows, target arrows, and runtime indicators', () => {
    const workflow: EditorWorkflow = {
      name: 'flow',
      definitionVersion: 1,
      description: '',
      mode: 'static',
      stages: [
        { ...stage('a', 'build'), title: 'Build release', instructions: 'Compile packages' },
        stage('b', 'test', ['build']),
      ],
    };
    const surface = createSurface(80, 14);
    const layout = layoutWorkflow(workflow);
    paintWorkflow(surface, layout, { x: 0, y: 0 }, null, {
      runtimeStatuses: new Map([['build', 'running']]),
      runtime: { running: { fg: 'blue' } },
    });
    const output = lines(surface).join('\n');

    // The name rides the top border rail after the definition-order index; the interior belongs to
    // the stage's own prompt, and the runtime row lists harness · model · worktree.
    expect(output).toContain('┌ 1 Build release');
    expect(output).toContain('Compile packages');
    expect(output).toContain('codex · gpt-5');
    expect(output).toContain('└ root ');
    // A runtime status is a badge on the rail (`●` = running), not a word inside the node.
    expect(output).toContain('●');
    expect(output).toContain('▶');
    const buildRect = layout.nodes.get('a')?.rect;
    expect(buildRect).toBeDefined();
    expect(
      buildRect === undefined
        ? undefined
        : surface.cells[buildRect.y * surface.width + buildRect.x]?.style.fg,
    ).toBe('blue');
  });

  it('keeps dangling dependencies visible as a repair stub', () => {
    const workflow: EditorWorkflow = {
      name: 'flow',
      definitionVersion: 1,
      description: '',
      mode: 'static',
      stages: [stage('a', 'target', ['missing'])],
    };
    const surface = createSurface(40, 10);
    paintWorkflow(surface, layoutWorkflow(workflow), { x: 0, y: 0 }, 'a');

    expect(lines(surface).join('\n')).toContain('?───▶');
  });

  it('draws a dashed directional preview while choosing a dependency', () => {
    const workflow: EditorWorkflow = {
      name: 'flow',
      description: '',
      mode: 'static',
      stages: [stage('a', 'build'), stage('b', 'docs')],
    };
    const surface = createSurface(50, 18);
    paintWorkflow(surface, layoutWorkflow(workflow), { x: 0, y: 0 }, 'b', {
      connect: { target: 'b', candidate: 'a', legality: 'add' },
      candidateAdd: { fg: 'green' },
    });

    const output = lines(surface).join('\n');
    expect(output).toContain('┄');
    expect(output).toContain('▷');
  });
});
