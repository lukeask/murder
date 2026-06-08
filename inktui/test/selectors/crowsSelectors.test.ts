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
