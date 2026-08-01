/** Defensive decoder for the v1 static DAG runtime state. Unknown schemas/states remain harmless. */
export type WorkflowStageStatus =
  | 'blocked'
  | 'ready'
  | 'requested'
  | 'running'
  | 'waiting_approval'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

const STATUSES = new Set<WorkflowStageStatus>([
  'blocked',
  'ready',
  'requested',
  'running',
  'waiting_approval',
  'succeeded',
  'failed',
  'cancelled',
]);

export function decodeStaticDagStatuses(state: {
  readonly schema_name: string;
  readonly schema_version: number;
  readonly value: unknown;
}): ReadonlyMap<string, WorkflowStageStatus> {
  const result = new Map<string, WorkflowStageStatus>();
  if (
    state.schema_name !== 'static_dag' ||
    state.schema_version !== 1 ||
    typeof state.value !== 'object' ||
    state.value === null
  )
    return result;
  const stages = (state.value as { readonly stages?: unknown }).stages;
  if (!Array.isArray(stages)) return result;
  for (const item of stages) {
    if (typeof item !== 'object' || item === null) continue;
    const record = item as { readonly stage_id?: unknown; readonly status?: unknown };
    if (
      typeof record.stage_id === 'string' &&
      typeof record.status === 'string' &&
      STATUSES.has(record.status as WorkflowStageStatus)
    )
      result.set(record.stage_id, record.status as WorkflowStageStatus);
  }
  return result;
}
