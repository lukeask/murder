/**
 * Pure projection unit tests for roster and schedule snapshots.
 */

import type { CrowSnapshotReply } from '@murder/ui-core/store/roster/rosterActions.js';
import { projectRosterSnapshot } from '@murder/ui-core/store/roster/rosterProjection.js';
import { projectScheduleSnapshot } from '@murder/ui-core/store/tickets/scheduleProjection.js';
import type { ScheduleSnapshotReply } from '@murder/ui-core/store/tickets/ticketsActions.js';

describe('projectRosterSnapshot', () => {
  it('maps sessions and applies defaults for optional wire fields', () => {
    const reply: CrowSnapshotReply = {
      invalidation_key: 'iv',
      sessions: [
        {
          agent_id: 'a-1',
          role: 'crow',
          status: 'running',
        },
        {
          agent_id: 'a-2',
          role: 'planner',
          status: 'idle',
          ticket_id: 'T-1',
          ticket_title: 'Title',
          harness: 'codex',
          model: 'gpt',
          display_name: 'p-1',
          session_id: 'sess-1',
          worktree_path: '/wt',
          last_seen: '2026-01-01T00:00:00Z',
          open_escalations: 2,
          max_severity: 3,
        },
      ],
    };

    const { roster } = projectRosterSnapshot(reply);
    expect(roster.status).toBe('ready');
    expect(roster.error).toBeNull();
    expect(roster.rows).toEqual([
      {
        agentId: 'a-1',
        role: 'crow',
        ticketId: null,
        ticketTitle: null,
        harness: null,
        model: null,
        status: 'running',
        session: null,
        worktreePath: null,
        lastSeen: null,
        openEscalations: 0,
        maxSeverity: 0,
      },
      {
        agentId: 'a-2',
        role: 'planner',
        ticketId: 'T-1',
        ticketTitle: 'Title',
        harness: 'codex',
        model: 'gpt',
        status: 'idle',
        session: 'p-1',
        sessionId: 'sess-1',
        worktreePath: '/wt',
        lastSeen: '2026-01-01T00:00:00Z',
        openEscalations: 2,
        maxSeverity: 3,
      },
    ]);
  });
});

describe('projectScheduleSnapshot', () => {
  it('flattens buckets in active → recent_done → archived order and maps usage defaults', () => {
    const reply: ScheduleSnapshotReply = {
      invalidation_key: 'iv',
      active_tickets: [
        {
          id: 'A',
          title: 'Active',
          status: 'running',
          last_update_at: '2026-01-02T00:00:00Z',
          last_update_label: 'up',
          pending_dep_ids: [],
        },
      ],
      recent_done_tickets: [
        {
          id: 'B',
          title: 'Done',
          status: 'done',
          last_update_at: '2026-01-01T00:00:00Z',
          last_update_label: 'done',
          pending_dep_ids: ['X'],
          schedule_at: 'soon',
          harness: 'claude',
          model: 'opus',
          parent: 'A',
        },
      ],
      archived_tickets: [
        {
          id: 'C',
          title: 'Arch',
          status: 'archived',
          last_update_at: '2025-01-01T00:00:00Z',
          last_update_label: 'old',
          pending_dep_ids: [],
        },
      ],
      usage_gauges: [
        {
          harness: 'codex',
          window_key: '5h',
          pct: 40,
          t_until_reset_minutes: 10,
        },
        {
          harness: 'claude',
          window_key: '5h',
          pct: 80,
          t_until_reset_minutes: 5,
          t_period_minutes: 300,
          steering: 'pause',
          fetched_at: '2026-01-01T00:00:00Z',
        },
      ],
    };

    const { tickets, usage } = projectScheduleSnapshot(reply);
    expect(tickets.rows.map((r) => r.id)).toEqual(['A', 'B', 'C']);
    expect(tickets.rows[1]).toMatchObject({
      scheduleAt: 'soon',
      harness: 'claude',
      model: 'opus',
      pendingDepIds: ['X'],
      parent: 'A',
    });
    expect(tickets.rows[0]).toMatchObject({
      scheduleAt: null,
      harness: null,
      model: null,
      parent: null,
    });
    expect(usage.rows).toEqual([
      {
        harness: 'codex',
        windowKey: '5h',
        pct: 40,
        tUntilResetMinutes: 10,
        tPeriodMinutes: 0,
        steering: 'auto',
        fetchedAt: null,
      },
      {
        harness: 'claude',
        windowKey: '5h',
        pct: 80,
        tUntilResetMinutes: 5,
        tPeriodMinutes: 300,
        steering: 'pause',
        fetchedAt: '2026-01-01T00:00:00Z',
      },
    ]);
    expect(tickets.status).toBe('ready');
    expect(usage.status).toBe('ready');
  });
});
