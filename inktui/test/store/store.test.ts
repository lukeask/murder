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
import type { ScheduleSnapshotReply } from '../../src/store/tickets/ticketsActions.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

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

/** A canned `state.crow_snapshot` reply with one session. */
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

/**
 * A canned `state.schedule_snapshot` reply (empty buckets + empty gauges). F2: usage is embedded in
 * the schedule snapshot (`usage_gauges`); both the tickets and usage slices read this reply.
 */
function scheduleReply(overrides: Partial<ScheduleSnapshotReply> = {}): ScheduleSnapshotReply {
  return {
    invalidation_key: 'iv-u',
    active_tickets: [],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [],
    ...overrides,
  };
}

/**
 * Build a store wired to a FakeBusClient with the roster + schedule RPCs stubbed. Roster keys on
 * `'agent'`; usage keys on `'queue_row'` (F1 locked map) and reads `state.schedule_snapshot`'s
 * `usage_gauges`. Tests that assert on `rpcCalls` must filter by method or use `toContainEqual`.
 */
function setup(reply: CrowSnapshotReply = crowReply()) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', reply);
  fake.stubRpc('state.schedule_snapshot', scheduleReply());
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('createAppStore — boot & wiring', () => {
  it('subscribes to the bus four times on construction (state.snapshot + conversation.block + conversation.state + error)', () => {
    // C3 had one subscription (state.snapshot); C10 added conversation.block; the queued-message /
    // liveness work added conversation.state; first-run UX added the backend-error → toast route.
    // All unsubscribe on dispose (the contract: dispose tears down all wiring).
    const { fake, dispose } = setup();
    expect(fake.subscriberCount).toBe(4);
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
    // History view slice starts idle too.
    expect(store.getState().history).toEqual({ rows: [], status: 'idle', error: null });
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
    // History actions: refresh + dismiss.
    expect(typeof store.getState().actions.history.refresh).toBe('function');
    expect(typeof store.getState().actions.history.dismiss).toBe('function');
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

    // Roster keys on 'agent' (usage keys on 'queue_row' — F1 map — so it does NOT fire here).
    expect(fake.rpcCalls).toContainEqual({ method: 'state.crow_snapshot', params: {} });
    expect(store.getState().roster.status).toBe('ready');
    expect(store.getState().roster.rows).toHaveLength(1);
    expect(store.getState().roster.rows[0]?.agentId).toBe('a-1');
  });

  it('re-pulls the plans slice on a `plan` state.snapshot and projects the parent field (C11)', async () => {
    const { fake, store } = setup();
    fake.stubRpc('state.plans_snapshot', {
      invalidation_key: 'iv-p',
      plans: [
        { name: 'parent', char_count: 10, updated_at: '2026-06-01T00:00:00' },
        { name: 'child', char_count: 5, updated_at: '2026-06-02T00:00:00', parent: 'parent' },
      ],
    });

    fake.emit(snapshot('plan'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.plans_snapshot', params: {} });
    const plans = store.getState().plans;
    expect(plans.status).toBe('ready');
    expect(plans.rows).toHaveLength(2);
    // The parent linkage is projected onto the row (defaulting an absent parent to null).
    expect(plans.rows.find((r) => r.name === 'parent')?.parent).toBeNull();
    expect(plans.rows.find((r) => r.name === 'child')?.parent).toBe('parent');
  });

  it('re-pulls the history slice on a `history` state.snapshot and projects rows', async () => {
    const { fake, store } = setup();
    fake.stubRpc('state.history_snapshot', {
      invalidation_key: 'iv-h',
      items: [
        {
          item_id: 'collaborator:0',
          text: 'fix the empty pane case',
          target: 'collaborator',
          ts: '2026-06-10T00:00:00',
          status: 'open',
          harness: null,
          conversation_status: 'in_progress',
          resumable: false,
        },
      ],
    });

    fake.emit(snapshot('history'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.history_snapshot', params: {} });
    const history = store.getState().history;
    expect(history.status).toBe('ready');
    expect(history.rows).toHaveLength(1);
    expect(history.rows[0]?.itemId).toBe('collaborator:0');
    expect(history.rows[0]?.text).toBe('fix the empty pane case');
    expect(history.rows[0]?.status).toBe('open');
  });

  it('re-pulls the roster on an `escalation` state.snapshot (escalation counts are JOINed in the crow snapshot)', async () => {
    // finalpush9: the crow snapshot carries `open_escalations`/`max_severity`, so an escalation
    // created/resolved without a coincident `agent` change must still re-pull the roster to keep
    // those counts fresh. `escalation` is a SECOND invalidating entity for the roster slice.
    const { fake, store } = setup();
    const rosterBefore = store.getState().roster;

    fake.emit(snapshot('escalation'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.crow_snapshot', params: {} });
    expect(store.getState().roster.status).toBe('ready');
    // Ref-swapped — escalation subscribers re-render off the same path as `agent` changes.
    expect(store.getState().roster).not.toBe(rosterBefore);
  });

  it('does NOT re-pull roster on an entity event for a different slice', async () => {
    // C6 wired `note`/`report`; C7 wired `ticket`; C11 wired `plan` → plans.refresh; finalpush9
    // wired `escalation` → roster.refresh. Those are no longer "unrelated". `queue_row` routes to
    // usage and never touches roster, so it stays a valid probe that an entity with no roster
    // invalidation leaves the roster (and its rpc call list) untouched.
    const { fake, store } = setup();
    const rosterBefore = store.getState().roster;

    fake.emit(snapshot('queue_row'));
    await flush();

    // No roster rpc was issued (notes/reports/tickets rpc calls may appear for their slices but
    // the *roster* slice must be untouched).
    const rosterCalls = fake.rpcCalls.filter((c) => c.method === 'state.crow_snapshot');
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

  it('survives a malformed state.snapshot (missing/null entity) without crashing or pulling', async () => {
    // The bus is the one untrusted, cross-process/cross-language boundary. A garbage `state.snapshot`
    // with a null/absent `entity` must not throw out of the subscription nor invalidate any slice —
    // the entity comparison simply never matches, so no rpc fires and the store stays usable.
    const { fake, store } = setup();
    const rosterBefore = store.getState().roster;

    expect(() => {
      // Cast through unknown: this is a deliberately ill-typed payload, the kind a buggy producer
      // could put on the wire.
      fake.emit({
        type: 'state.snapshot',
        id: 'evt-bad',
        ts: '2026-06-08T00:00:00Z',
        run_id: 'run-1',
        agent_id: '',
        entity: null,
        key: 'k-bad',
        entity_version: 1,
      } as unknown as StateSnapshotEvent);
    }).not.toThrow();
    await flush();

    expect(fake.rpcCalls).toEqual([]);
    expect(store.getState().roster).toBe(rosterBefore);
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
    fake.stubRpc('state.crow_snapshot', () => {
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
      'state.crow_snapshot',
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
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.notes_snapshot', notesReply());
    const { store } = createAppStore(fake);
    expect(store.getState().notes.status).toBe('idle');

    fake.emit(snapshot('note'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.notes_snapshot', params: {} });
    expect(store.getState().notes.status).toBe('ready');
    expect(store.getState().notes.rows).toHaveLength(1);
    expect(store.getState().notes.rows[0]?.name).toBe('my-note');
  });

  it('ref-swaps ONLY notes on a note event — roster and reports keep identity', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.notes_snapshot', notesReply());
    fake.stubRpc('state.reports_snapshot', reportsReply());
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
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.reports_snapshot', reportsReply());
    const { store } = createAppStore(fake);
    expect(store.getState().reports.status).toBe('idle');

    fake.emit(snapshot('report'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.reports_snapshot', params: {} });
    expect(store.getState().reports.status).toBe('ready');
    expect(store.getState().reports.rows).toHaveLength(1);
    expect(store.getState().reports.rows[0]?.name).toBe('my-report');
  });

  it('ref-swaps ONLY reports on a report event — roster and notes keep identity', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.notes_snapshot', notesReply());
    fake.stubRpc('state.reports_snapshot', reportsReply());
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

function ticketsReply(overrides: Partial<ScheduleSnapshotReply> = {}): ScheduleSnapshotReply {
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
    usage_gauges: [],
    ...overrides,
  };
}

describe('C7 — tickets slice invalidation', () => {
  it('re-pulls tickets on a ticket-entity state.snapshot', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', ticketsReply());
    const { store } = createAppStore(fake);
    expect(store.getState().tickets.status).toBe('idle');

    fake.emit(snapshot('ticket'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.schedule_snapshot', params: {} });
    expect(store.getState().tickets.status).toBe('ready');
    expect(store.getState().tickets.rows).toHaveLength(1);
    expect(store.getState().tickets.rows[0]?.id).toBe('T-1');
  });

  it('flattens active + recent_done + archived into one row list', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc(
      'state.schedule_snapshot',
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
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.notes_snapshot', notesReply());
    fake.stubRpc('state.reports_snapshot', reportsReply());
    fake.stubRpc('state.schedule_snapshot', ticketsReply());
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
  it('re-pulls usage on a queue_row-entity state.snapshot (F1 locked map: queue_row → usage)', async () => {
    // F2: usage reads `state.schedule_snapshot`'s `usage_gauges` (no separate usage RPC).
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc(
      'state.schedule_snapshot',
      scheduleReply({
        usage_gauges: [{ harness: 'claude', window_key: 'h1', pct: 50, t_until_reset_minutes: 10 }],
      }),
    );
    const { store } = createAppStore(fake);
    expect(store.getState().usage.status).toBe('idle');

    fake.emit(snapshot('queue_row'));
    await flush();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.schedule_snapshot', params: {} });
    expect(store.getState().usage.status).toBe('ready');
    expect(store.getState().usage.rows).toHaveLength(1);
    expect(store.getState().usage.rows[0]?.harness).toBe('claude');
  });

  it('does NOT re-pull usage on an agent event (usage keys on queue_row, not agent)', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.notes_snapshot', notesReply());
    fake.stubRpc('state.reports_snapshot', reportsReply());
    fake.stubRpc('state.schedule_snapshot', ticketsReply());
    const { store } = createAppStore(fake);
    const notesBefore = store.getState().notes;
    const reportsBefore = store.getState().reports;
    const ticketsBefore = store.getState().tickets;
    const usageBefore = store.getState().usage;

    fake.emit(snapshot('agent'));
    await flush();

    // notes, reports, tickets, usage are not keyed on 'agent' — they keep identity. (usage +
    // tickets share `state.schedule_snapshot` but both key off non-agent entities.)
    expect(store.getState().notes).toBe(notesBefore);
    expect(store.getState().reports).toBe(reportsBefore);
    expect(store.getState().tickets).toBe(ticketsBefore);
    expect(store.getState().usage).toBe(usageBefore);
    expect(fake.rpcCalls).not.toContainEqual({ method: 'state.schedule_snapshot', params: {} });
    // Only roster ref-swaps on an agent event.
    expect(store.getState().roster.status).toBe('ready');
  });

  it('routes a rejected usage rpc into usage.error, never thrown past the action', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', () => {
      throw new Error('usage down');
    });
    const { store } = createAppStore(fake);

    fake.emit(snapshot('queue_row'));
    await flush();

    expect(store.getState().usage.status).toBe('error');
    expect(store.getState().usage.error).toBe('usage down');
  });
});

// ---- A#7/A#8/C-LM-7: boot-priming — tickets/notes/reports/conversations on connect ----

describe('boot-priming — slice refresh actions exist for all primed domains', () => {
  it('exposes a conversations.refresh action (boot-prime pull)', () => {
    const { store } = setup();
    expect(typeof store.getState().actions.conversations.refresh).toBe('function');
  });

  it('conversations.refresh calls state.conversations_snapshot and hydrates transcripts', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', scheduleReply());
    // Wire shape: ConversationsSnapshot.conversations is a list of ConversationSummary entries.
    // Each entry has agent_id + blocks (ConversationBlockSummary rows — id numeric, payload nested).
    fake.stubRpc('state.conversations_snapshot', {
      conversations: [
        {
          conversation_id: 'conv-1',
          agent_id: 'collaborator',
          harness: 'claude',
          model: 'claude-sonnet-4-6',
          harness_session_id: null,
          live_state: null,
          condensed: null,
          status: 'in_progress',
          blocks: [
            {
              id: 1,
              conversation_id: 'conv-1',
              ordinal: 0,
              kind: 'user_message',
              payload: { type: 'user', text: 'hello' },
              sealed: true,
              service_received_at: '2026-06-09T00:00:00',
            },
          ],
        },
      ],
      as_of: '2026-06-09T00:00:00',
      invalidation_key: 'iv-c',
    });
    const { store } = createAppStore(fake);
    expect(store.getState().conversations.transcripts).toEqual({});

    await store.getState().actions.conversations.refresh();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.conversations_snapshot', params: {} });
    const transcripts = store.getState().conversations.transcripts;
    expect(Object.keys(transcripts)).toContain('collaborator');
    expect(transcripts['collaborator']).toHaveLength(1);
    expect(transcripts['collaborator']?.[0]?.type).toBe('user');
  });

  it('conversations.refresh merges into existing transcripts (does not wipe agent-pane state)', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', scheduleReply());
    fake.stubRpc('state.conversations_snapshot', {
      conversations: [
        {
          conversation_id: 'conv-2',
          agent_id: 'crow-1',
          harness: null,
          model: null,
          harness_session_id: null,
          live_state: null,
          condensed: null,
          status: 'in_progress',
          blocks: [
            {
              id: 2,
              conversation_id: 'conv-2',
              ordinal: 0,
              kind: 'assistant_final',
              payload: { type: 'assistant', text: 'done' },
              sealed: true,
              service_received_at: '2026-06-09T00:00:00',
            },
          ],
        },
      ],
      as_of: '2026-06-09T00:00:00',
      invalidation_key: 'iv-c2',
    });
    const { store } = createAppStore(fake);

    await store.getState().actions.conversations.refresh();

    // activePaneAgentId should be unaffected — refresh only touches transcripts.
    expect(store.getState().conversations.activePaneAgentId).toBeNull();
    expect(store.getState().conversations.transcripts['crow-1']).toHaveLength(1);
  });

  it('conversations.refresh swallows RPC errors — transcripts stay empty, no throw', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', scheduleReply());
    // Handler throws — models a service that does not yet expose state.conversations_snapshot.
    fake.stubRpc('state.conversations_snapshot', () => {
      throw new Error('snapshot service down');
    });
    const { store } = createAppStore(fake);

    // Must not throw; transcripts remain empty.
    await expect(store.getState().actions.conversations.refresh()).resolves.toBeUndefined();
    expect(store.getState().conversations.transcripts).toEqual({});
  });

  it('tickets.refresh, notes.refresh, and reports.refresh are available for priming', () => {
    // Belt-and-suspenders: verify the store exposes the three other primed slice actions so
    // primeSlices in index.tsx can call them at connect time.
    const { store } = setup();
    expect(typeof store.getState().actions.tickets.refresh).toBe('function');
    expect(typeof store.getState().actions.notes.refresh).toBe('function');
    expect(typeof store.getState().actions.reports.refresh).toBe('function');
  });

  it('calling tickets.refresh primes the tickets slice from state.schedule_snapshot', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', ticketsReply());
    const { store } = createAppStore(fake);
    expect(store.getState().tickets.status).toBe('idle');

    await store.getState().actions.tickets.refresh();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.schedule_snapshot', params: {} });
    expect(store.getState().tickets.status).toBe('ready');
    expect(store.getState().tickets.rows).toHaveLength(1);
  });

  it('calling notes.refresh primes the notes slice from state.notes_snapshot', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', scheduleReply());
    fake.stubRpc('state.notes_snapshot', notesReply());
    const { store } = createAppStore(fake);
    expect(store.getState().notes.status).toBe('idle');

    await store.getState().actions.notes.refresh();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.notes_snapshot', params: {} });
    expect(store.getState().notes.status).toBe('ready');
    expect(store.getState().notes.rows).toHaveLength(1);
  });

  it('calling reports.refresh primes the reports slice from state.reports_snapshot', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.crow_snapshot', crowReply());
    fake.stubRpc('state.schedule_snapshot', scheduleReply());
    fake.stubRpc('state.reports_snapshot', reportsReply());
    const { store } = createAppStore(fake);
    expect(store.getState().reports.status).toBe('idle');

    await store.getState().actions.reports.refresh();

    expect(fake.rpcCalls).toContainEqual({ method: 'state.reports_snapshot', params: {} });
    expect(store.getState().reports.status).toBe('ready');
    expect(store.getState().reports.rows).toHaveLength(1);
  });
});

/** Let the FakeBusClient's Promise-routed rpc settle (it resolves on a microtask). */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe('first-run UX — backend error events surface as toasts', () => {
  beforeEach(() => {
    toastStore.getState().clear();
  });
  afterEach(() => {
    toastStore.getState().clear();
  });

  it('routes a bus `error` event to the toast rack with error severity', () => {
    const { fake, dispose } = setup();
    fake.emit({
      type: 'error',
      id: 'evt-err-1',
      ts: '2026-06-12T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'a-1',
      message: 'worker exploded',
      recoverable: true,
    });
    const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
    expect(live).toHaveLength(1);
    expect(live[0]?.text).toBe('worker exploded');
    expect(live[0]?.severity).toBe('error');
    dispose();
  });

  it('stops routing after dispose (the error subscription is torn down)', () => {
    const { fake, dispose } = setup();
    dispose();
    fake.emit({
      type: 'error',
      id: 'evt-err-2',
      ts: '2026-06-12T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'a-1',
      message: 'after dispose',
      recoverable: false,
    });
    expect(toastStore.getState().toasts).toHaveLength(0);
  });
});
