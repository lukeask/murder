/**
 * Pixel layout seeds for the React Flow canvas.
 * Reuses SCC-DAG ranks from ui-core `layoutWorkflow` — never the TUI cell-surface metrics.
 */

import { layoutWorkflow } from '@murder/ui-core/workflowEditor/layout.js';
import type { EditorWorkflow, StageKey } from '@murder/ui-core/workflowEditor/model.js';

/** Stage block silhouette — denser mass than n8n pastel chips. */
export const STAGE_NODE_WIDTH = 220;
export const STAGE_NODE_HEIGHT = 88;
export const RANK_GAP_PX = 96;
export const ROW_GAP_PX = 40;
export const CANVAS_ORIGIN_X = 48;
export const CANVAS_ORIGIN_Y = 48;

export type NodePosition = { readonly x: number; readonly y: number };
export type PositionMap = ReadonlyMap<StageKey, NodePosition>;

/** Rank-based seed positions. Positions are view-local and are not persisted in v1. */
export function seedPositions(workflow: EditorWorkflow): Map<StageKey, NodePosition> {
  const layout = layoutWorkflow(workflow);
  const positions = new Map<StageKey, NodePosition>();
  layout.ranks.forEach((row, rankIndex) => {
    row.forEach((key, order) => {
      positions.set(key, {
        x: CANVAS_ORIGIN_X + rankIndex * (STAGE_NODE_WIDTH + RANK_GAP_PX),
        y: CANVAS_ORIGIN_Y + order * (STAGE_NODE_HEIGHT + ROW_GAP_PX),
      });
    });
  });
  return positions;
}

/** Keep existing positions for surviving keys; seed only newcomers (and optionally re-layout all). */
export function mergePositions(
  workflow: EditorWorkflow,
  previous: PositionMap,
  opts?: { readonly relayout?: boolean },
): Map<StageKey, NodePosition> {
  if (opts?.relayout === true || previous.size === 0) return seedPositions(workflow);
  const next = new Map<StageKey, NodePosition>();
  const seeded = seedPositions(workflow);
  for (const stage of workflow.stages) {
    next.set(stage.key, previous.get(stage.key) ?? seeded.get(stage.key) ?? { x: 0, y: 0 });
  }
  return next;
}
