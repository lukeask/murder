/**
 * Ticket-detail actions tests — slice/action unit tests.
 *
 * Copied from the C3 store-test idiom. Covers:
 *  1. `open(ticketId)` — loads body + frontmatter, sets slice to ready.
 *  2. `close()` — resets the slice to idle.
 *  3. `setEditedBody()` — updates editedBody (does NOT call the bus).
 *  4. `setScheduleInput()` — updates scheduleInput + scheduleValid inline.
 *  5. `saveBody()` — calls `ticket.save_body` once; updates savedBody on success.
 *  6. `schedule()` — calls `ticket.schedule` once when input is valid; clears scheduleInput.
 *  7. Error paths: open/saveBody/schedule errors land in `ticketDetail.error`.
 *  8. `isValidDuration` — mirrors Python `parse_duration` acceptance/rejection.
 *  9. Sole-RPC-caller invariant: only ticketDetailActions calls the three bus methods.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createAppStore, initialAppState } from '../../src/store/store.js';
import type { TicketDetailReply } from '../../src/store/ticketDetail/ticketDetailActions.js';
import { isValidDuration } from '../../src/store/ticketDetail/ticketDetailActions.js';
import { initialTicketDetailState } from '../../src/store/ticketDetail/ticketDetailSlice.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

/** All live error toasts on the singleton at the current instant (toast test idiom, commit 73d7110). */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

// ── Helpers ─────────────────────────────────────────────────────────────────────────────────────

const TICKET_BODY = `## Plan\nDo the thing.\n\n# Checklist\n- [ ] first item\n- [x] done item\n`;

const DETAIL_REPLY: TicketDetailReply = {
  id: 'T-1',
  title: 'Alpha ticket',
  status: 'in_progress',
  deps: ['T-0', 'T-2'],
  harness: 'claude',
  model: 'anthropic/claude-opus',
  worktree: '.murder/worktrees/t1',
  schedule_at: '2026-06-10T09:00:00',
  body: TICKET_BODY,
  checklist: [
    { text: 'first item', done: false },
    { text: 'done item', done: true },
  ],
};

/** Filter rpcCalls by method name. */
function callsFor(
  fake: FakeBusClient,
  method: string,
): ReadonlyArray<{ method: string; params: unknown }> {
  return fake.rpcCalls.filter((c) => c.method === method);
}

function setup(detailReply: TicketDetailReply = DETAIL_REPLY) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.ticket_detail', detailReply);
  fake.stubRpc('ticket.save_body', { ok: true });
  fake.stubRpc('ticket.schedule', { ok: true });
  // Stub sibling RPCs so the store doesn't reject on side-effects.
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

// ── Boot state ───────────────────────────────────────────────────────────────────────────────────

describe('ticketDetail — initial state', () => {
  it('starts as idle with all nulls', () => {
    const { store, dispose } = setup();
    expect(store.getState().ticketDetail).toEqual(initialTicketDetailState);
    dispose();
  });

  it('initialAppState mirrors the slice initial state', () => {
    expect(initialAppState.ticketDetail).toEqual(initialTicketDetailState);
  });
});

// ── open() ───────────────────────────────────────────────────────────────────────────────────────

describe('ticketDetailActions.open', () => {
  it('loads body and frontmatter, sets status ready', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    const detail = store.getState().ticketDetail;
    expect(detail.status).toBe('ready');
    expect(detail.ticketId).toBe('T-1');
    expect(detail.savedBody).toBe(TICKET_BODY);
    expect(detail.editedBody).toBe(TICKET_BODY);
    expect(detail.frontmatter).not.toBeNull();
    expect(detail.frontmatter?.title).toBe('Alpha ticket');
    expect(detail.frontmatter?.deps).toBe('T-0, T-2');
    expect(detail.frontmatter?.harness).toBe('claude');
    expect(detail.frontmatter?.model).toBe('anthropic/claude-opus');
    expect(detail.frontmatter?.worktree).toBe('.murder/worktrees/t1');
    expect(detail.scheduleInput).toBe('');
    expect(detail.error).toBeNull();
    dispose();
  });

  it('consumes the new wire fields: status, schedule_at, and array deps from state.ticket_detail', async () => {
    // Field-by-field with the Python TicketDetailSnapshot wire shape: status (TicketStatus value),
    // deps (string[]), schedule_at (ISO string | null). These now ride in the detail snapshot and
    // surface as display-only header context (frontmatter).
    const { store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    const fm = store.getState().ticketDetail.frontmatter;
    expect(fm?.status).toBe('in_progress');
    expect(fm?.scheduleAt).toBe('2026-06-10T09:00:00');
    // deps[] is joined to a display string (header renders it as `deps:<...>`).
    expect(fm?.deps).toBe('T-0, T-2');
    dispose();
  });

  it('checklist rides in the body (C8 line 167) — the body carries the `# Checklist` `[ ]`/`[x]` lines', async () => {
    // The structured `checklist` field is carried for contract fidelity but NOT the editor's source;
    // the editable body is the single source of truth and contains the checklist lines verbatim.
    const { store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    const body = store.getState().ticketDetail.savedBody ?? '';
    expect(body).toContain('# Checklist');
    expect(body).toContain('- [ ] first item');
    expect(body).toContain('- [x] done item');
    dispose();
  });

  it('tolerates a null schedule_at and empty deps[] (nullable header fields)', async () => {
    const { store, dispose } = setup({
      ...DETAIL_REPLY,
      schedule_at: null,
      deps: [],
    });
    await store.getState().actions.ticketDetail.open('T-1');
    const fm = store.getState().ticketDetail.frontmatter;
    expect(fm?.scheduleAt).toBeNull();
    expect(fm?.deps).toBe('');
    dispose();
  });

  it('calls state.ticket_detail exactly once with the ticket_id', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    const calls = callsFor(fake, 'state.ticket_detail');
    expect(calls).toHaveLength(1);
    expect(calls[0]?.params).toEqual({ ticket_id: 'T-1' });
    dispose();
  });

  it('transitions loading → ready', async () => {
    const { store, dispose } = setup();
    const openPromise = store.getState().actions.ticketDetail.open('T-1');
    // Synchronously after dispatch, status should be loading.
    expect(store.getState().ticketDetail.status).toBe('loading');
    await openPromise;
    expect(store.getState().ticketDetail.status).toBe('ready');
    dispose();
  });

  it('sets status error on rejection', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.ticket_detail', () => {
      throw new Error('not found');
    });
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    const { store, dispose } = createAppStore(fake);
    await store.getState().actions.ticketDetail.open('T-99');
    const detail = store.getState().ticketDetail;
    expect(detail.status).toBe('error');
    expect(detail.error).toContain('not found');
    dispose();
  });

  it('does not mutate sibling slices (invariant: only ticketDetail key changes)', async () => {
    const { store, dispose } = setup();
    const rosterBefore = store.getState().roster;
    const ticketsBefore = store.getState().tickets;
    await store.getState().actions.ticketDetail.open('T-1');
    expect(store.getState().roster).toBe(rosterBefore);
    expect(store.getState().tickets).toBe(ticketsBefore);
    dispose();
  });
});

// ── close() ─────────────────────────────────────────────────────────────────────────────────────

describe('ticketDetailActions.close', () => {
  it('resets the slice to idle/null after open', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.close();
    expect(store.getState().ticketDetail).toEqual(initialTicketDetailState);
    dispose();
  });
});

// ── setEditedBody() ──────────────────────────────────────────────────────────────────────────────

describe('ticketDetailActions.setEditedBody', () => {
  it('updates editedBody without calling the bus', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    const saveCallsBefore = callsFor(fake, 'ticket.save_body').length;
    store.getState().actions.ticketDetail.setEditedBody('new body content');
    expect(store.getState().ticketDetail.editedBody).toBe('new body content');
    expect(store.getState().ticketDetail.savedBody).toBe(TICKET_BODY); // savedBody unchanged
    expect(callsFor(fake, 'ticket.save_body').length).toBe(saveCallsBefore); // no bus call
    dispose();
  });
});

// ── setScheduleInput() ───────────────────────────────────────────────────────────────────────────

describe('ticketDetailActions.setScheduleInput', () => {
  it('updates scheduleInput and scheduleValid', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.setScheduleInput('1d4h3m');
    expect(store.getState().ticketDetail.scheduleInput).toBe('1d4h3m');
    expect(store.getState().ticketDetail.scheduleValid).toBe(true);
    dispose();
  });

  it('marks invalid for empty or malformed input', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.setScheduleInput('garbage');
    expect(store.getState().ticketDetail.scheduleValid).toBe(false);
    store.getState().actions.ticketDetail.setScheduleInput('');
    expect(store.getState().ticketDetail.scheduleValid).toBe(false);
    dispose();
  });
});

// ── saveBody() ───────────────────────────────────────────────────────────────────────────────────

describe('ticketDetailActions.saveBody', () => {
  // The toast singleton is shared global state; reset it between cases (toastStore's own idiom).
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('calls ticket.save_body with the edited body and updates savedBody', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.setEditedBody('## Updated body');
    await store.getState().actions.ticketDetail.saveBody();
    const calls = callsFor(fake, 'ticket.save_body');
    expect(calls).toHaveLength(1);
    expect(calls[0]?.params).toEqual({
      ticket_id: 'T-1',
      body: '## Updated body',
    });
    expect(store.getState().ticketDetail.savedBody).toBe('## Updated body');
    expect(store.getState().ticketDetail.status).toBe('ready');
    dispose();
  });

  it('does nothing when no ticket is open (ticketId is null)', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.ticketDetail.saveBody();
    expect(callsFor(fake, 'ticket.save_body')).toHaveLength(0);
    dispose();
  });

  it('sets status error on save failure', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('state.ticket_detail', DETAIL_REPLY);
    fake.stubRpc('ticket.save_body', () => {
      throw new Error('write failed');
    });
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    const { store, dispose } = createAppStore(fake);
    await store.getState().actions.ticketDetail.open('T-1');
    await store.getState().actions.ticketDetail.saveBody();
    expect(store.getState().ticketDetail.status).toBe('error');
    expect(store.getState().ticketDetail.error).toContain('write failed');
    dispose();
  });

  it('routes a soft-fail (resolved {ok:false, error}) to the error path + error toast, NOT success', async () => {
    // The service can RESOLVE (not reject) with {handled:true, ok:false, error} — e.g. ticket not
    // found (orchestrator.py save_ticket_body). Without the ok===false guard this would take the
    // success branch → savedBody updated, status 'ready' → silent data loss. Prove it now errors.
    const fake = new FakeBusClient();
    fake.stubRpc('state.ticket_detail', DETAIL_REPLY);
    fake.stubRpc('ticket.save_body', { ok: false, error: 'ticket not found: T-1' });
    fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
    const { store, dispose } = createAppStore(fake);
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.setEditedBody('## Updated body');
    await store.getState().actions.ticketDetail.saveBody();

    // Did NOT take the success branch: status is error, savedBody unchanged from the loaded body.
    expect(store.getState().ticketDetail.status).toBe('error');
    expect(store.getState().ticketDetail.error).toBe('ticket not found: T-1');
    expect(store.getState().ticketDetail.savedBody).toBe(TICKET_BODY);

    // Surfaced via the SAME mechanism as write-RPC rejections: a global error toast.
    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('ticket not found: T-1');
    dispose();
  });

  it('a successful save ({ok:true}) pushes NO error toast', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.setEditedBody('## Updated body');
    await store.getState().actions.ticketDetail.saveBody();
    expect(store.getState().ticketDetail.status).toBe('ready');
    expect(errorToasts()).toHaveLength(0);
    dispose();
  });
});

// ── schedule() ───────────────────────────────────────────────────────────────────────────────────

describe('ticketDetailActions.schedule', () => {
  it('calls ticket.schedule and clears scheduleInput on success', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.setScheduleInput('2d4h');
    await store.getState().actions.ticketDetail.schedule();
    const calls = callsFor(fake, 'ticket.schedule');
    expect(calls).toHaveLength(1);
    expect(calls[0]?.params).toEqual({
      ticket_id: 'T-1',
      duration: '2d4h',
    });
    expect(store.getState().ticketDetail.scheduleInput).toBe('');
    expect(store.getState().ticketDetail.scheduleValid).toBe(false);
    dispose();
  });

  it('does nothing when scheduleInput is invalid', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.ticketDetail.open('T-1');
    store.getState().actions.ticketDetail.setScheduleInput('not-valid');
    await store.getState().actions.ticketDetail.schedule();
    expect(callsFor(fake, 'ticket.schedule')).toHaveLength(0);
    dispose();
  });

  it('does nothing when no ticket is open', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.ticketDetail.schedule();
    expect(callsFor(fake, 'ticket.schedule')).toHaveLength(0);
    dispose();
  });
});

// ── isValidDuration ──────────────────────────────────────────────────────────────────────────────

describe('isValidDuration — mirrors murder/work/duration.py parse_duration', () => {
  // DRIFT RISK (code-review jun13): this is a TS re-implementation pinned by TS expectations ONLY.
  // Unlike the DTO/conversation goldens (which the Python suite regenerates into test/fixtures/ and
  // re-asserts, so Python-side drift fails a Python test), there is NO cross-language anchor here.
  // If `parse_duration` adds/changes an accepted form, these cases stay green while the TUI silently
  // diverges from the backend. Follow-up: adopt the golden pattern — have the Python duration test
  // emit its accepted/rejected corpus into a fixture this file imports — instead of hand-mirroring.
  // Accepted forms (from the Python module docstring).
  it.each([
    ['1d4h3m', true],
    ['1h1m', true],
    ['34m', true],
    ['1h', true],
    ['2d', true],
    ['1d', true],
    ['10h', true],
    ['100m', true],
  ])('accepts %s', (input, expected) => {
    expect(isValidDuration(input)).toBe(expected);
  });

  // Rejected forms.
  it.each([
    ['', false],
    ['   ', false],
    ['34', false], // bare number with no unit
    ['5w', false], // unknown unit
    ['-1h', false], // negative
    ['3m1h', false], // out-of-order
    ['1h1h', false], // duplicate unit
    ['garbage', false],
    ['abc', false],
    ['1d 4h', false], // spaces not allowed
  ])('rejects %s', (input, expected) => {
    expect(isValidDuration(input)).toBe(expected);
  });
});
