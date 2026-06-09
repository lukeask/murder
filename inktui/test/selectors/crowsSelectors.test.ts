/**
 * crowsSelectors tests — type-grouping is a pure transform; no React, no store, no bus.
 *
 * The key invariants (rule 2 proof):
 *  - Groups appear in spec order: collaborator → planners → rogue → ticket.
 *  - Only 'collaborator', 'planner', and 'crow' roles are included;
 *    infrastructure roles ('planning_handler', 'crow_handler', 'notetaker') are excluded.
 *    ('planning_handler' is the handler-process for planners, not a chat participant —
 *    parallel to 'crow_handler'; chat_target_cycle.py includes only role==='planner'.)
 *  - Rogue vs ticket split: role==='crow' with ticketId===null vs ticketId!==null.
 *  - Within a group, rows are sorted by status rank then agentId.
 *  - The component receives pre-grouped sections with display-ready strings.
 */

import {
  CROW_GROUP_LABEL,
  type CrowGroup,
  selectCrowsView,
} from '../../src/selectors/crowsSelectors.js';
import type { RosterRow, RosterState } from '../../src/store/roster/rosterSlice.js';

function row(overrides: Partial<RosterRow> = {}): RosterRow {
  return {
    agentId: 'a-1',
    role: 'crow',
    ticketId: null,
    ticketTitle: null,
    harness: 'claude',
    model: 'anthropic/claude-opus',
    status: 'running',
    session: 'sess-1',
    ...overrides,
  };
}

function state(rows: readonly RosterRow[], overrides: Partial<RosterState> = {}): RosterState {
  return { rows, status: 'ready', error: null, ...overrides };
}

/** Extract group keys from the view in order. */
function groupOrder(s: ReturnType<typeof selectCrowsView>): CrowGroup[] {
  return s.sections.map((sec) => sec.group);
}

describe('selectCrowsView — grouping and ordering', () => {
  it('groups collaborator, planner, rogue crow, and ticket crow into separate sections', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'c1', role: 'collaborator', ticketId: null }),
        row({ agentId: 'p1', role: 'planner', ticketId: null }),
        row({ agentId: 'r1', role: 'crow', ticketId: null }),
        row({ agentId: 't1', role: 'crow', ticketId: 'T-1' }),
      ]),
    );
    expect(groupOrder(view)).toEqual(['collaborator', 'planners', 'rogue', 'ticket']);
  });

  it('follows spec order: collaborator → planners → rogue → ticket', () => {
    // Provide rows in reverse spec order to confirm the selector re-orders them.
    const view = selectCrowsView(
      state([
        row({ agentId: 't1', role: 'crow', ticketId: 'T-1' }),
        row({ agentId: 'r1', role: 'crow', ticketId: null }),
        row({ agentId: 'p1', role: 'planner', ticketId: null }),
        row({ agentId: 'c1', role: 'collaborator', ticketId: null }),
      ]),
    );
    expect(groupOrder(view)).toEqual(['collaborator', 'planners', 'rogue', 'ticket']);
  });

  it('omits empty groups from sections', () => {
    const view = selectCrowsView(state([row({ agentId: 'r1', role: 'crow', ticketId: null })]));
    expect(groupOrder(view)).toEqual(['rogue']);
  });

  it('excludes planning_handler (handler-process for planners, not a chat participant)', () => {
    // planning_handler is the parallel of crow_handler: infrastructure, not user-facing.
    // chat_target_cycle.py only includes role==='planner'; planning_handler is excluded here.
    const view = selectCrowsView(
      state([row({ agentId: 'ph1', role: 'planning_handler', ticketId: null })]),
    );
    expect(groupOrder(view)).toEqual([]);
    expect(view.isEmpty).toBe(true);
  });

  it('excludes notetaker, crow_handler, and planning_handler roles entirely', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'nt1', role: 'notetaker', ticketId: null }),
        row({ agentId: 'ch1', role: 'crow_handler', ticketId: null }),
        row({ agentId: 'ph1', role: 'planning_handler', ticketId: null }),
        row({ agentId: 'r1', role: 'crow', ticketId: null }),
      ]),
    );
    // Only 'rogue' group should appear; all three infra/handler roles are excluded.
    expect(groupOrder(view)).toEqual(['rogue']);
    expect(view.sections[0]?.rows).toHaveLength(1);
    expect(view.sections[0]?.rows[0]?.agentId).toBe('r1');
  });

  it('splits crow role by ticketId: null → rogue, non-null → ticket', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'r1', role: 'crow', ticketId: null }),
        row({ agentId: 't1', role: 'crow', ticketId: 'T-1' }),
      ]),
    );
    const rogueSection = view.sections.find((s) => s.group === 'rogue');
    const ticketSection = view.sections.find((s) => s.group === 'ticket');
    expect(rogueSection?.rows.map((r) => r.agentId)).toEqual(['r1']);
    expect(ticketSection?.rows.map((r) => r.agentId)).toEqual(['t1']);
  });

  it('sorts rows within a group by status rank then agentId', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'idle-b', role: 'crow', ticketId: null, status: 'idle' }),
        row({ agentId: 'esc', role: 'crow', ticketId: null, status: 'escalating' }),
        row({ agentId: 'idle-a', role: 'crow', ticketId: null, status: 'idle' }),
      ]),
    );
    const rogueSection = view.sections.find((s) => s.group === 'rogue');
    expect(rogueSection?.rows.map((r) => r.agentId)).toEqual(['esc', 'idle-a', 'idle-b']);
  });

  it('uses session name as display name, falling back to agentId', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'a-1', role: 'crow', ticketId: null, session: 'my-session' }),
        row({ agentId: 'a-2', role: 'crow', ticketId: null, session: null }),
      ]),
    );
    const names = view.sections[0]?.rows.map((r) => r.name) ?? [];
    expect(names).toContain('my-session');
    expect(names).toContain('a-2');
  });

  it('uses the correct section label from CROW_GROUP_LABEL', () => {
    const view = selectCrowsView(
      state([row({ agentId: 'c1', role: 'collaborator', ticketId: null })]),
    );
    expect(view.sections[0]?.label).toBe(CROW_GROUP_LABEL.collaborator);
  });

  it('carries load flags through and computes isEmpty', () => {
    expect(selectCrowsView(state([])).isEmpty).toBe(true);
    expect(selectCrowsView(state([row({ role: 'crow', ticketId: null })])).isEmpty).toBe(false);
    const err = selectCrowsView(state([], { status: 'error', error: 'boom' }));
    expect(err.status).toBe('error');
    expect(err.error).toBe('boom');
  });

  it('isEmpty true when all rows are filtered-out internal roles', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'nt1', role: 'notetaker', ticketId: null }),
        row({ agentId: 'ch1', role: 'crow_handler', ticketId: null }),
      ]),
    );
    expect(view.isEmpty).toBe(true);
    expect(view.sections).toHaveLength(0);
  });

  it('does not mutate the input slice (sorts a copy)', () => {
    const rows = [
      row({ agentId: 'b', role: 'crow', ticketId: null, status: 'idle' }),
      row({ agentId: 'a', role: 'crow', ticketId: null, status: 'escalating' }),
    ];
    const original = [...rows];
    selectCrowsView(state(rows));
    expect(rows).toEqual(original);
  });
});

describe('selectCrowsView — crow-health on row views', () => {
  // The row view carries the ported health classification. All four branches are live now that
  // RosterRow carries openEscalations, maxSeverity, and lastSeen (A#6 fix).
  function healthOf(s: ReturnType<typeof selectCrowsView>, agentId: string): string | undefined {
    for (const section of s.sections) {
      for (const r of section.rows) {
        if (r.agentId === agentId) return r.health;
      }
    }
    return undefined;
  }

  it('classifies each row from its status', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'run', role: 'crow', ticketId: null, status: 'running' }),
        row({ agentId: 'fail', role: 'crow', ticketId: null, status: 'failed' }),
        row({ agentId: 'esc', role: 'crow', ticketId: null, status: 'escalating' }),
        row({ agentId: 'done', role: 'crow', ticketId: null, status: 'done' }),
      ]),
    );
    expect(healthOf(view, 'run')).toBe('green');
    expect(healthOf(view, 'fail')).toBe('red');
    expect(healthOf(view, 'esc')).toBe('red');
    expect(healthOf(view, 'done')).toBe('neutral');
  });
});

describe('selectCrowsView — rich-field health plumbing (A#6)', () => {
  // Proves the DTO→RosterRow→classifyCrowHealth pipeline actually uses open_escalations,
  // max_severity, and last_seen. Before A#6 all three branches were hardcoded to defaults and
  // could never fire from real wire data.

  const NOW = 1_000_000_000_000; // fixed epoch-ms for determinism

  function healthOf(s: ReturnType<typeof selectCrowsView>, agentId: string): string | undefined {
    for (const section of s.sections) {
      for (const r of section.rows) {
        if (r.agentId === agentId) return r.health;
      }
    }
    return undefined;
  }

  it('RED when openEscalations > 0, even for a running crow (escalation-RED branch)', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'esc-crow', role: 'crow', ticketId: 'T-1', status: 'running',
              openEscalations: 1, maxSeverity: 0 }),
      ]),
      NOW,
    );
    expect(healthOf(view, 'esc-crow')).toBe('red');
  });

  it('RED when maxSeverity >= 2, even with zero open escalations (severity-RED branch)', () => {
    const view = selectCrowsView(
      state([
        row({ agentId: 'sev-crow', role: 'crow', ticketId: 'T-2', status: 'idle',
              openEscalations: 0, maxSeverity: 2 }),
      ]),
      NOW,
    );
    expect(healthOf(view, 'sev-crow')).toBe('red');
  });

  it('YELLOW when running crow last_seen > 60s ago (stuck-heartbeat branch)', () => {
    const staleIso = new Date(NOW - 90_000).toISOString(); // 90s ago → stuck
    const view = selectCrowsView(
      state([
        row({ agentId: 'stuck-crow', role: 'crow', ticketId: null, status: 'running',
              lastSeen: staleIso, openEscalations: 0, maxSeverity: 0 }),
      ]),
      NOW,
    );
    expect(healthOf(view, 'stuck-crow')).toBe('yellow');
  });

  it('GREEN when running crow last_seen is recent (< 60s), no escalations', () => {
    const recentIso = new Date(NOW - 10_000).toISOString(); // 10s ago → not stuck
    const view = selectCrowsView(
      state([
        row({ agentId: 'healthy-crow', role: 'crow', ticketId: null, status: 'running',
              lastSeen: recentIso, openEscalations: 0, maxSeverity: 0 }),
      ]),
      NOW,
    );
    expect(healthOf(view, 'healthy-crow')).toBe('green');
  });

  it('GREEN for a running crow with no rich fields (optional-field backward-compat)', () => {
    // Rows built without the new fields (e.g. existing tests) default to 0/null and stay GREEN.
    const view = selectCrowsView(
      state([row({ agentId: 'basic', role: 'crow', ticketId: null, status: 'running' })]),
      NOW,
    );
    expect(healthOf(view, 'basic')).toBe('green');
  });

  it('escalation-RED wins over stuck-YELLOW (precedence)', () => {
    const staleIso = new Date(NOW - 90_000).toISOString();
    const view = selectCrowsView(
      state([
        row({ agentId: 'esc-stuck', role: 'crow', ticketId: 'T-3', status: 'running',
              lastSeen: staleIso, openEscalations: 1, maxSeverity: 0 }),
      ]),
      NOW,
    );
    expect(healthOf(view, 'esc-stuck')).toBe('red');
  });
});
