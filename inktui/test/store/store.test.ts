/**
 * Store-core tests — the invalidation-granularity proof and the reference test idiom every future
 * slice copies. Driven entirely by {@link FakeBusClient}: emit a `state.snapshot`, assert the named
 * slice (and only it) re-pulled and ref-swapped.
 */

import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import type { Entity, StateSnapshotEvent } from '../../src/bus/protocol.js';
import type { NotesSnapshotReply } from '../../src/store/notes/notesActions.js';
import type { ReportsSnapshotReply } from '../../src/store/reports/reportsActions.js';
import type { CrowSnapshotReply } from '../../src/store/roster/rosterActions.js';
import { createAppStore } from '../../src/store/store.js';
import type { TicketSnapshotReply } from '../../src/store/tickets/ticketsActions.js';
import type { UsageSnapshotReply } from '../../src/store/usage/usageActions.js';

/** A `state.snapshot` event for `entity`, defaulting to the crow-roster entity. */
function snapshot(
  entity: Entity = 'agent',
  overrides: Partial<StateSnapshotEvent> = {},
): StateSnapshotEvent {
  return {
    type: 'state.snapshot',
    id: 'evt-1',
    ts: '2026-06-08T00:00:00Z',
    run_id: 'run-1',
    agent_id: '',
    entity,
    key: 'k-1',
    entity_version: 1,
    ...overrides,
  };
}

/** A canned `crow.get_snapshot` reply with one session. */
function crowReply(overrides: Partial<CrowSnapshotReply> = {}): CrowSnapshotReply {
  return {
    invalidation_key: 'iv-1',
    sessions: [
      {
        agent_id: 'a-1',
        role: 'crow',
        ticket_id: 'T-1',
        ticket_title: 'Title',
        harness: 'claude',
        model: 'anthropic/claude-opus',
        status: 'running',
        session_name: 'sess-1',
      },
    ],
    ...overrides,
  };
}

/** A canned `usage.get_snapshot` reply (empty gauges — sufficient for most tests). */
function usageReply(overrides: Partial<UsageSnapshotReply> = {}): UsageSnapshotReply {
  return { invalidation_key: 'iv-u', gauges: [], ...overrides };
}

/**
 * Build a store wired to a FakeBusClient with the roster + usage RPCs stubbed.
 * NOTE: Both roster and usage key on the `'agent'` entity (C9: usage invalidates with roster
 * since there is no dedicated `'usage'` entity key in the Python protocol). So an `'agent'`
 * snapshot fires BOTH `crow.get_snapshot` and `usage.get_snapshot`. Tests that assert on
 * `rpcCalls` must filter by method or use `toContainEqual` rather than exact equality.
 */
function setup(reply: CrowSnapshotReply = crowReply()) {
  const fake = new FakeBusClient();
  fake.stubRpc('crow.get_snapshot', reply);
  fake.stubRpc('usage.get_snapshot', usageReply());
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('createAppStore — boot & wiring', () => {
  it('subscribes to the bus twice on construction (state.snapshot + conversation.block)', () => {
    // C3 had one subscription (state.snapshot); C10 adds a second (conversation.block).
    // Both unsubscribe on dispose (the contract: dispose tears down all wiring).
    const { fake, dispose } = setup();
    expect(fake.subscriberCount).toBe(2);
    dispose();
    expect(fake.subscriberCount).toBe(0);
  });

  it('starts each slice in its idle, pre-fetch state', () => {
    const { store } = setup();
    expect(store.getState().roster).toEqual({ rows: [], status: 'idle', error: null });
    // C6 slices also start idle.
    expect(store.getState().notes).toEqual({ rows: [], status: 'idle', error: null });
    expect(store.getState().reports).toEqual({ rows: [], status: 'idle', error: null });
    // C11 slices start idle/closed.
    expect(store.getState().plans).toEqual({ rows: [], status: 'idle', error: null });
    expect(store.getState().favorites.status).toBe('idle');
    expect(store.getState().favorites.ids.size).toBe(0);
    expect(store.getState().docView).toEqual({
      open: null,
      body: null,
      status: 'idle',
      error: null,
    });
  });

  it('exposes actions grouped by slice', () => {
    const { store } = setup();
    expect(typeof store.getState().actions.roster.refresh).toBe('function');
    // C6 actions.
    expect(typeof store.getState().actions.notes.refresh).toBe('function');
    expect(typeof store.getState().actions.reports.refresh).toBe('function');
    // C7 actions.
    expect(typeof store.getState().actions.tickets.refresh).toBe('function');
    // C9 actions.
    expect(typeof store.getState().actions.usage.refresh).toBe('function');
    // C11 actions.
    expect(typeof store.getState().actions.plans.refresh).toBe('function');
    expect(typeof store.getState().actions.favorites.toggle).toBe('function');
    expect(typeof store.getState().actions.docView.open).toBe('function');
  });

  it('starts the tickets slice in its idle, pre-fetch state', () => {
    const { store } = setup();
    expect(store.getState().tickets).toEqual({ rows: [], status: 'idle', error: null });
  });

  it('starts the usage slice in its idle, pre-fetch state (C9)', () => {
    const { store } = setup();
    expect(store.getState().usage).toEqual({ rows: [], status: 'idle', error: null });
  });
});

describe('event-driven slice invalidation', () => {
  it('re-pulls the roster on its state.snapshot and updates the slice', async () => {
    const { fake, store } = setup();

    fake.emit(snapshot('agent'));
    await flush();

    // Both roster and usage key on 'agent' (C9: usage has no dedicated entity key).
    // Assert on the roster call specifically; usage.get_snapshot will also be present.
    expect(fake.rpcCalls).toContainEqual({ method: 'crow.get_snapshot', params: {} });
    expect(store.getState().roster.status).toBe('ready');
    expect(store.getState().roster.rows).toHaveLength(1);
    expect(store.getState().roster.rows[0]?.agentId).toBe('a-1');
  });

  it('re-pulls the plans slice on a `plan` state.snapshot and projects the parent field (C11)', async () => {
    const { fake, store } = setup();
    fake.stubRpc('plan.get_snapshot', {
      invalidation_key: 'iv-p',
      plans: [
        { name: 'parent', char_count: 10, updated_at: '2026-06-01T00:00:00' },
        { name: 'child', char_count: 5, updated_at: '2026-06-02T00:00:00', parent: 'parent' },
      ],
    });

    fake.emit(snapshot('plan'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'plan.get_snapshot', params: {} });
    const plans = store.getState().plans;
    expect(plans.status).toBe('ready');
    expect(plans.rows).toHaveLength(2);
    // The parent linkage is projected onto the row (defaulting an absent parent to null).
    expect(plans.rows.find((r) => r.name === 'parent')?.parent).toBeNull();
    expect(plans.rows.find((r) => r.name === 'child')?.parent).toBe('parent');
  });

  it('does NOT re-pull roster on an entity event for a different slice', async () => {
    // C6 wired `note`/`report`; C7 wired `ticket`; C11 wired `plan` → plans.refresh. Those are no
    // longer "unrelated". Use `queue_row` and `escalation` (still not wired) to verify that an
    // entity with no registered invalidation leaves the roster (and its rpc call list) untouched.
    const { fake, store } = setup();
    const rosterBefore = store.getState().roster;

    fake.emit(snapshot('queue_row'));
    fake.emit(snapshot('escalation'));
    await flush();

    // No roster rpc was issued (notes/reports/tickets rpc calls may appear for their slices but
    // the *roster* slice must be untouched).
    const rosterCalls = fake.rpcCalls.filter((c) => c.method === 'crow.get_snapshot');
    expect(rosterCalls).toEqual([]);
    // The roster slice object identity is unchanged — no ref-swap, no re-render of roster subscribers.
    expect(store.getState().roster).toBe(rosterBefore);
  });

  it('ignores non-snapshot events entirely', async () => {
    const { fake } = setup();
    fake.emit({
      type: 'heartbeat',
      id: 'h-1',
      ts: '2026-06-08T00:00:00Z',
      run_id: 'r',
      agent_id: 'a',
      state: 'progressing',
      since_change_s: 1,
    });
    await flush();
    expect(fake.rpcCalls).toEqual([]);
  });

  it('ref-swaps ONLY the changed slice — sibling keys keep identity', async () => {
    const { fake, store } = setup();
    const rosterBefore = store.getState().roster;
    const actionsBefore = store.getState().actions;

    fake.emit(snapshot('agent'));
    await flush();

    // roster ref-swapped (its subscribers re-render) ...
    expect(store.getState().roster).not.toBe(rosterBefore);
    // ... but the sibling `actions` object is untouched (its subscribers do not).
    expect(store.getState().actions).toBe(actionsBefore);
  });
});

describe('actions are the only bus caller (rule 3)', () => {
  it('routes a rejected rpc into the slice error field, never thrown past the action', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', () => {
      throw new Error('bus down');
    });
    const { store } = createAppStore(fake);

    fake.emit(snapshot('agent'));
    await flush();

    expect(store.getState().roster.status).toBe('error');
    expect(store.getState().roster.error).toBe('bus down');
  });

  it('marks the slice loading before the rpc resolves', async () => {
    let resolveReply: (r: CrowSnapshotReply) => void = () => {};
    const fake = new FakeBusClient();
    fake.stubRpc(
      'crow.get_snapshot',
      () =>
        new Promise<CrowSnapshotReply>((resolve) => {
          resolveReply = resolve;
        }),
    );
    const { store } = createAppStore(fake);

    fake.emit(snapshot('agent'));
    await flush();
    expect(store.getState().roster.status).toBe('loading');

    resolveReply(crowReply());
    await flush();
    expect(store.getState().roster.status).toBe('ready');
  });
});

// ---- C6: notes + reports slice invalidation ----

function notesReply(overrides: Partial<NotesSnapshotReply> = {}): NotesSnapshotReply {
  return {
    invalidation_key: 'iv-n',
    notes: [{ name: 'my-note', char_count: 100, updated_at: '2026-06-08T00:00:00' }],
    ...overrides,
  };
}

function reportsReply(overrides: Partial<ReportsSnapshotReply> = {}): ReportsSnapshotReply {
  return {
    invalidation_key: 'iv-r',
    reports: [{ name: 'my-report', char_count: 200, updated_at: '2026-06-07T00:00:00' }],
    ...overrides,
  };
}

describe('C6 — notes slice invalidation', () => {
  it('re-pulls notes on a note-entity state.snapshot', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('note.get_snapshot', notesReply());
    const { store } = createAppStore(fake);
    expect(store.getState().notes.status).toBe('idle');

    fake.emit(snapshot('note'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'note.get_snapshot', params: {} });
    expect(store.getState().notes.status).toBe('ready');
    expect(store.getState().notes.rows).toHaveLength(1);
    expect(store.getState().notes.rows[0]?.name).toBe('my-note');
  });

  it('ref-swaps ONLY notes on a note event — roster and reports keep identity', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('note.get_snapshot', notesReply());
    fake.stubRpc('report.get_snapshot', reportsReply());
    const { store } = createAppStore(fake);
    const rosterBefore = store.getState().roster;
    const reportsBefore = store.getState().reports;

    fake.emit(snapshot('note'));
    await flush();

    expect(store.getState().notes).not.toBe(store.getState().roster);
    expect(store.getState().roster).toBe(rosterBefore);
    expect(store.getState().reports).toBe(reportsBefore);
  });
});

describe('C6 — reports slice invalidation', () => {
  it('re-pulls reports on a report-entity state.snapshot', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('report.get_snapshot', reportsReply());
    const { store } = createAppStore(fake);
    expect(store.getState().reports.status).toBe('idle');

    fake.emit(snapshot('report'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'report.get_snapshot', params: {} });
    expect(store.getState().reports.status).toBe('ready');
    expect(store.getState().reports.rows).toHaveLength(1);
    expect(store.getState().reports.rows[0]?.name).toBe('my-report');
  });

  it('ref-swaps ONLY reports on a report event — roster and notes keep identity', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('note.get_snapshot', notesReply());
    fake.stubRpc('report.get_snapshot', reportsReply());
    const { store } = createAppStore(fake);
    const rosterBefore = store.getState().roster;
    const notesBefore = store.getState().notes;

    fake.emit(snapshot('report'));
    await flush();

    expect(store.getState().roster).toBe(rosterBefore);
    expect(store.getState().notes).toBe(notesBefore);
    expect(store.getState().reports.status).toBe('ready');
  });
});

// ---- C7: tickets slice invalidation ----

function ticketsReply(overrides: Partial<TicketSnapshotReply> = {}): TicketSnapshotReply {
  return {
    invalidation_key: 'iv-t',
    active_tickets: [
      {
        id: 'T-1',
        title: 'My ticket',
        status: 'ready',
        last_update_at: '2026-06-08T10:00:00',
        last_update_label: 'user created',
        schedule_at: null,
        harness: 'claude',
        model: 'anthropic/claude-opus',
        pending_dep_ids: [],
      },
    ],
    recent_done_tickets: [],
    archived_tickets: [],
    ...overrides,
  };
}

describe('C7 — tickets slice invalidation', () => {
  it('re-pulls tickets on a ticket-entity state.snapshot', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('ticket.get_snapshot', ticketsReply());
    const { store } = createAppStore(fake);
    expect(store.getState().tickets.status).toBe('idle');

    fake.emit(snapshot('ticket'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'ticket.get_snapshot', params: {} });
    expect(store.getState().tickets.status).toBe('ready');
    expect(store.getState().tickets.rows).toHaveLength(1);
    expect(store.getState().tickets.rows[0]?.id).toBe('T-1');
  });

  it('flattens active + recent_done + archived into one row list', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc(
      'ticket.get_snapshot',
      ticketsReply({
        active_tickets: [
          {
            id: 'T-1',
            title: 'Active',
            status: 'in_progress',
            last_update_at: '2026-06-08T10:00:00',
            last_update_label: 'started',
            pending_dep_ids: [],
          },
        ],
        recent_done_tickets: [
          {
            id: 'T-2',
            title: 'Done',
            status: 'done',
            last_update_at: '2026-06-07T10:00:00',
            last_update_label: 'finished',
            pending_dep_ids: [],
          },
        ],
        archived_tickets: [
          {
            id: 'T-3',
            title: 'Archived',
            status: 'done',
            last_update_at: '2026-06-06T10:00:00',
            last_update_label: 'archived',
            pending_dep_ids: [],
          },
        ],
      }),
    );
    const { store } = createAppStore(fake);
    await store.getState().actions.tickets.refresh();

    // All three buckets flattened into one list.
    expect(store.getState().tickets.rows).toHaveLength(3);
    const ids = store.getState().tickets.rows.map((r) => r.id);
    expect(ids).toContain('T-1');
    expect(ids).toContain('T-2');
    expect(ids).toContain('T-3');
  });

  it('ref-swaps ONLY tickets on a ticket event — roster, notes, reports keep identity', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('note.get_snapshot', notesReply());
    fake.stubRpc('report.get_snapshot', reportsReply());
    fake.stubRpc('ticket.get_snapshot', ticketsReply());
    const { store } = createAppStore(fake);
    const rosterBefore = store.getState().roster;
    const notesBefore = store.getState().notes;
    const reportsBefore = store.getState().reports;

    fake.emit(snapshot('ticket'));
    await flush();

    expect(store.getState().roster).toBe(rosterBefore);
    expect(store.getState().notes).toBe(notesBefore);
    expect(store.getState().reports).toBe(reportsBefore);
    expect(store.getState().tickets.status).toBe('ready');
  });
});

// ---- C9: usage slice invalidation ----

describe('C9 — usage slice invalidation', () => {
  it('re-pulls usage on an agent-entity state.snapshot (same entity as roster)', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('usage.get_snapshot', {
      invalidation_key: 'iv-u',
      gauges: [{ harness: 'claude', window_key: 'h1', pct: 50, t_until_reset_minutes: 10 }],
    });
    const { store } = createAppStore(fake);
    expect(store.getState().usage.status).toBe('idle');

    fake.emit(snapshot('agent'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'usage.get_snapshot', params: {} });
    expect(store.getState().usage.status).toBe('ready');
    expect(store.getState().usage.rows).toHaveLength(1);
    expect(store.getState().usage.rows[0]?.harness).toBe('claude');
  });

  it('ref-swaps ONLY usage on an agent event — notes, reports, tickets keep identity', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('usage.get_snapshot', { invalidation_key: 'iv-u', gauges: [] });
    fake.stubRpc('note.get_snapshot', notesReply());
    fake.stubRpc('report.get_snapshot', reportsReply());
    fake.stubRpc('ticket.get_snapshot', ticketsReply());
    const { store } = createAppStore(fake);
    const notesBefore = store.getState().notes;
    const reportsBefore = store.getState().reports;
    const ticketsBefore = store.getState().tickets;

    fake.emit(snapshot('agent'));
    await flush();

    // notes, reports, tickets are not keyed on 'agent' — they keep identity.
    expect(store.getState().notes).toBe(notesBefore);
    expect(store.getState().reports).toBe(reportsBefore);
    expect(store.getState().tickets).toBe(ticketsBefore);
    // Both roster and usage ref-swap (both key on 'agent').
    expect(store.getState().usage.status).toBe('ready');
  });

  it('routes a rejected usage rpc into usage.error, never thrown past the action', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('crow.get_snapshot', crowReply());
    fake.stubRpc('usage.get_snapshot', () => {
      throw new Error('usage down');
    });
    const { store } = createAppStore(fake);

    fake.emit(snapshot('agent'));
    await flush();

    expect(store.getState().usage.status).toBe('error');
    expect(store.getState().usage.error).toBe('usage down');
    // The roster should still succeed (each action has its own error isolation).
    expect(store.getState().roster.status).toBe('ready');
  });
});

/** Let the FakeBusClient's Promise-routed rpc settle (it resolves on a microtask). */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
