import { describe, expect, it } from 'vitest';
import { decodeStaticDagStatuses } from '@murder/ui-core/workflowEditor/runState.js';

describe('decodeStaticDagStatuses', () => {
  it('decodes the supported static DAG v1 stage states', () => {
    const statuses = decodeStaticDagStatuses({
      schema_name: 'static_dag',
      schema_version: 1,
      value: {
        stages: [
          { stage_id: 'build', status: 'running' },
          { stage_id: 'review', status: 'waiting_approval' },
          { stage_id: 'done', status: 'succeeded' },
        ],
      },
    });

    expect([...statuses]).toEqual([
      ['build', 'running'],
      ['review', 'waiting_approval'],
      ['done', 'succeeded'],
    ]);
  });

  it('rejects unknown schemas, versions, and status values without throwing', () => {
    expect(
      decodeStaticDagStatuses({
        schema_name: 'other',
        schema_version: 1,
        value: { stages: [{ stage_id: 'build', status: 'running' }] },
      }).size,
    ).toBe(0);
    expect(
      decodeStaticDagStatuses({
        schema_name: 'static_dag',
        schema_version: 2,
        value: { stages: [{ stage_id: 'build', status: 'running' }] },
      }).size,
    ).toBe(0);
    expect(
      decodeStaticDagStatuses({
        schema_name: 'static_dag',
        schema_version: 1,
        value: { stages: [{ stage_id: 'build', status: 'mystery' }] },
      }).size,
    ).toBe(0);
  });
});
