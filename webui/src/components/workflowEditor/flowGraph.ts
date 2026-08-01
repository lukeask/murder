/**
 * Pure mapping: EditorWorkflow (+ local positions / issues) → React Flow nodes & edges.
 * Structure edits still go through `applyWorkflowEdit`; this only projects for the canvas.
 */

import type { Edge, Node } from '@xyflow/react';
import { buildEditorGraph } from '@murder/ui-core/workflowEditor/graph.js';
import type {
  EditorIssue,
  EditorStage,
  EditorWorkflow,
  StageKey,
} from '@murder/ui-core/workflowEditor/model.js';
import { validateEditorWorkflow } from '@murder/ui-core/workflowEditor/validate.js';
import type { WorkflowStageStatus } from '@murder/ui-core/workflowEditor/runState.js';
import { type PositionMap, STAGE_NODE_HEIGHT, STAGE_NODE_WIDTH } from './layout.js';

export const STAGE_NODE_TYPE = 'murderStage';
export const DEPENDENCY_EDGE_TYPE = 'murderDependency';

export type StageNodeData = {
  readonly stageKey: StageKey;
  readonly id: string;
  readonly title: string;
  readonly harness: string | null;
  readonly model: string | null;
  readonly gate: EditorStage['gate'];
  readonly issueCount: number;
  readonly hasError: boolean;
  readonly hasWarning: boolean;
  /** Live run status from `decodeStaticDagStatuses` when an active run matches this draft. */
  readonly runStatus?: WorkflowStageStatus;
};

export type DependencyEdgeData = {
  readonly sourceKey: StageKey;
  readonly targetKey: StageKey;
  readonly dependency: string;
  readonly dependencyIndex: number;
  readonly illegal?: boolean;
};

export type StageFlowNode = Node<StageNodeData, typeof STAGE_NODE_TYPE>;
export type DependencyFlowEdge = Edge<DependencyEdgeData>;

function issuesForStage(
  issues: readonly EditorIssue[],
  key: StageKey,
): { readonly count: number; readonly hasError: boolean; readonly hasWarning: boolean } {
  let count = 0;
  let hasError = false;
  let hasWarning = false;
  for (const issue of issues) {
    if (issue.stageKey !== key) continue;
    count += 1;
    if (issue.severity === 'error') hasError = true;
    else hasWarning = true;
  }
  return { count, hasError, hasWarning };
}

/** Project draft → RF elements. Callers own position persistence in `positions`. */
export function workflowToFlow(
  workflow: EditorWorkflow,
  positions: PositionMap,
  opts?: {
    readonly selected?: StageKey | null;
    readonly extraIssues?: readonly EditorIssue[];
    /** stage_id → runtime status (from `decodeStaticDagStatuses`). */
    readonly stageStatuses?: ReadonlyMap<string, WorkflowStageStatus>;
  },
): { readonly nodes: StageFlowNode[]; readonly edges: DependencyFlowEdge[] } {
  const localIssues = validateEditorWorkflow(workflow);
  const issues = opts?.extraIssues === undefined ? localIssues : [...localIssues, ...opts.extraIssues];
  const graph = buildEditorGraph(workflow);
  const selected = opts?.selected ?? null;
  const stageStatuses = opts?.stageStatuses;

  const nodes: StageFlowNode[] = workflow.stages.map((stage) => {
    const pos = positions.get(stage.key) ?? { x: 0, y: 0 };
    const flags = issuesForStage(issues, stage.key);
    const runStatus = stageStatuses?.get(stage.id);
    return {
      id: stage.key,
      type: STAGE_NODE_TYPE,
      position: { x: pos.x, y: pos.y },
      selected: stage.key === selected,
      width: STAGE_NODE_WIDTH,
      height: STAGE_NODE_HEIGHT,
      data: {
        stageKey: stage.key,
        id: stage.id,
        title: stage.title,
        harness: stage.harness,
        model: stage.model,
        gate: stage.gate,
        issueCount: flags.count,
        hasError: flags.hasError,
        hasWarning: flags.hasWarning,
        ...(runStatus === undefined ? {} : { runStatus }),
      },
    };
  });

  const edges: DependencyFlowEdge[] = graph.resolvedEdges.map((edge) => ({
    id: `dep:${edge.source}->${edge.target}:${edge.dependencyIndex}`,
    type: DEPENDENCY_EDGE_TYPE,
    source: edge.source,
    target: edge.target,
    data: {
      sourceKey: edge.source,
      targetKey: edge.target,
      dependency: edge.dependency,
      dependencyIndex: edge.dependencyIndex,
    },
  }));

  return { nodes, edges };
}

/** Edge id → dependency endpoints for delete → toggle-dependency. */
export function parseDependencyEdgeId(
  edgeId: string,
): { readonly source: StageKey; readonly target: StageKey } | null {
  // Format: dep:<sourceKey>-><targetKey>:<dependencyIndex> — keys may contain hyphens.
  if (!edgeId.startsWith('dep:')) return null;
  const body = edgeId.slice(4);
  const arrow = body.indexOf('->');
  if (arrow < 0) return null;
  const source = body.slice(0, arrow);
  const rest = body.slice(arrow + 2);
  const colon = rest.lastIndexOf(':');
  if (colon < 0) return null;
  const target = rest.slice(0, colon);
  if (source.length === 0 || target.length === 0) return null;
  return { source, target };
}
