/**
 * The app store — composition root for every domain slice, and the home of the event-driven
 * slice-invalidation wiring that replaced the old poll-everything `IngestionCoordinator`.
 *
 * Shape (locked here as THE reference):
 *  - **One root store, many slices.** Each domain contributes a top-level key (`roster`, `notes`,
 *    `reports`, `tickets`, …). A change ref-swaps just that key's object, so `useStore(s => s.roster,
 *    shallow)` re-renders only roster subscribers — slice-granular re-render for free, no hand-rolled
 *    diff. (Contrast the Python store, which polled every snapshot every tick.)
 *  - **Each domain is a thin shell over the shared list-slice factory** (`./listSlice.ts`). The
 *    identical `{ rows, status, error }` shape + loading/error/ref-swap mechanics live there once;
 *    a domain supplies only its row type, RPC method, and DTO→rows `project` fn. The four current
 *    slices differ only in those; tickets' 3-bucket flatten is just its `project` (no special case).
 *  - **Actions hang off `state.actions`,** grouped by slice. Components dispatch
 *    `useAppStore(s => s.actions.roster.refresh)`; nothing but an action calls the bus (rule 3).
 *  - **Built with `zustand/vanilla` `createStore`,** not the React `create` — the store layer must
 *    stay framework-agnostic (rule 4). The React binding lives in `src/hooks/`.
 *
 * Event-driven invalidation (the data-flow contract):
 *   on construction the store subscribes to the bus, filtered to `state.snapshot` events. Each event
 *   is key-only — it names the {@link Entity} that changed. The store maps that entity to the slice
 *   whose `*_INVALIDATING_ENTITY` matches and calls that slice's refresh action, which re-pulls and
 *   ref-swaps only itself. An unrelated entity matches no slice → no re-pull. This is the whole
 *   perf story: the wire carries change granularity; the store never polls and never deep-diffs.
 *
 * To add slice X: first write its three thin-shell files (`xSlice.ts`/`xActions.ts` +
 * `xSelectors.ts`) over the shared `./listSlice.ts` factory — copy the roster files and supply
 * only X's row type, RPC method (+ reply type), and `project` fn (see `roster/rosterSlice.ts` for
 * the canonical example). Then wire it here (≈5 local edits, all additive — the compiler guides you):
 *   1. import `createXSlice`, `createXActions`, `X_INVALIDATING_ENTITY`, `initialXState`, `XState`;
 *   2. add `x: XState;` to the `AppStore` interface and `x: XActions;` to `AppActions`;
 *   3. spread `...createXSlice(...a)` into the store initializer;
 *   4. add `x: createXActions(bus, store)` to the `actions` object and one entry to `invalidations`;
 *   5. add `x: initialXState` to `initialAppState` (keep it mirroring every slice).
 * The pattern does not fan out — no dispatch/wiring logic changes, only these additions, and the
 * `{ rows, status, error }` mechanics are never re-derived (they come from the factory).
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import type { BusClient } from '../bus/BusClient.js';
import type {
  ConversationBlockEvent,
  ConversationStateEvent,
  Entity,
  ErrorEvent,
  StateSnapshotEvent,
} from '../bus/protocol.js';
import {
  type ConversationsActions,
  createConversationsActions,
} from './conversations/conversationsActions.js';
import {
  type ConversationsState,
  createConversationsSlice,
  initialConversationsState,
} from './conversations/conversationsSlice.js';
import { createDocViewActions, type DocViewActions } from './docView/docViewActions.js';
import {
  createDocViewSlice,
  type DocViewState,
  initialDocViewState,
} from './docView/docViewSlice.js';
import { createFavoritesActions, type FavoritesActions } from './favorites/favoritesActions.js';
import {
  createFavoritesSlice,
  type FavoritesState,
  initialFavoritesState,
} from './favorites/favoritesSlice.js';
import { createHistoryActions, type HistoryActions } from './history/historyActions.js';
import {
  createHistorySlice,
  HISTORY_INVALIDATING_ENTITY,
  type HistoryState,
  initialHistoryState,
} from './history/historySlice.js';
import { createNotesActions, type NotesActions } from './notes/notesActions.js';
import {
  createNotesSlice,
  initialNotesState,
  NOTES_INVALIDATING_ENTITY,
  type NotesState,
} from './notes/notesSlice.js';
import { createPlansActions, type PlansActions } from './plans/plansActions.js';
import {
  createPlansSlice,
  initialPlansState,
  PLANS_INVALIDATING_ENTITY,
  type PlansState,
} from './plans/plansSlice.js';
import { createReportsActions, type ReportsActions } from './reports/reportsActions.js';
import {
  createReportsSlice,
  initialReportsState,
  REPORTS_INVALIDATING_ENTITY,
  type ReportsState,
} from './reports/reportsSlice.js';
import { createRosterActions, type RosterActions } from './roster/rosterActions.js';
import {
  createRosterSlice,
  initialRosterState,
  ROSTER_ESCALATION_INVALIDATING_ENTITY,
  ROSTER_INVALIDATING_ENTITY,
  type RosterState,
} from './roster/rosterSlice.js';
import { createSettingsActions, type SettingsActions } from './settings/settingsActions.js';
import {
  createSettingsSlice,
  initialSettingsState,
  type SettingsState,
} from './settings/settingsSlice.js';
import {
  createTicketDetailActions,
  type TicketDetailActions,
} from './ticketDetail/ticketDetailActions.js';
import {
  createTicketDetailSlice,
  initialTicketDetailState,
  type TicketDetailState,
} from './ticketDetail/ticketDetailSlice.js';
import { createTicketsActions, type TicketsActions } from './tickets/ticketsActions.js';
import {
  createTicketsSlice,
  initialTicketsState,
  TICKETS_INVALIDATING_ENTITY,
  type TicketsState,
} from './tickets/ticketsSlice.js';
import { toastStore } from './toast/toastStore.js';
import { createTransitActions, type TransitActions } from './transit/transitActions.js';
import {
  createTransitSlice,
  initialTransitState,
  TRANSIT_INVALIDATING_ENTITY,
  type TransitState,
} from './transit/transitSlice.js';
import { createUsageActions, type UsageActions } from './usage/usageActions.js';
import {
  createUsageSlice,
  initialUsageState,
  USAGE_INVALIDATING_ENTITY,
  type UsageState,
} from './usage/usageSlice.js';

/** Every slice's actions, grouped by domain. Components dispatch through here; the bus is reached
 * only via these (rule 3). One key per slice — copy the `roster` line to add a domain. */
export interface AppActions {
  roster: RosterActions;
  plans: PlansActions;
  notes: NotesActions;
  reports: ReportsActions;
  tickets: TicketsActions;
  history: HistoryActions;
  transit: TransitActions;
  usage: UsageActions;
  ticketDetail: TicketDetailActions;
  conversations: ConversationsActions;
  favorites: FavoritesActions;
  docView: DocViewActions;
  settings: SettingsActions;
}

/**
 * The combined store state: one key per domain slice, plus `actions`. This is the type every
 * selector and hook is generic over. Slices are flat top-level keys (not nested under `slices`) so a
 * ref-swap of one key is trivially shallow-comparable by a selector.
 */
export interface AppStore {
  roster: RosterState;
  plans: PlansState;
  notes: NotesState;
  reports: ReportsState;
  tickets: TicketsState;
  history: HistoryState;
  transit: TransitState;
  usage: UsageState;
  ticketDetail: TicketDetailState;
  conversations: ConversationsState;
  favorites: FavoritesState;
  docView: DocViewState;
  settings: SettingsState;
  actions: AppActions;
}

/** The store handle. Re-exported so callers don't reach into `zustand/vanilla` directly. */
export type AppStoreApi = StoreApi<AppStore>;

/**
 * A slice's refresh entry: which {@link Entity} invalidates it, and the action to run on that event.
 * The invalidation loop iterates a list of these — adding a slice is appending one entry, never
 * touching the dispatch logic.
 */
interface SliceInvalidation {
  readonly entity: Entity;
  readonly refresh: () => void;
}

/**
 * Create the app store with an injected {@link BusClient} (rule 4 — tests pass `FakeBusClient`, prod
 * passes `UdsBusClient`; the store has no idea which). Returns the vanilla store handle and the bus
 * subscription disposer so the owner (the app entrypoint) can tear the wiring down cleanly.
 *
 * The store is created first, then actions are built against its handle, then the bus subscription
 * is opened — so an event that fires the instant we subscribe finds the actions already in place.
 */
export function createAppStore(bus: BusClient): {
  store: AppStoreApi;
  dispose: () => void;
} {
  // 1. State: compose slices. Each `createXSlice` contributes its own keys; `actions` is filled in
  //    once the handle exists (below), so it starts as a typed placeholder.
  const store = createStore<AppStore>()((...a) => ({
    ...createRosterSlice(...a),
    ...createPlansSlice(...a),
    ...createNotesSlice(...a),
    ...createReportsSlice(...a),
    ...createTicketsSlice(...a),
    ...createHistorySlice(...a),
    ...createTransitSlice(...a),
    ...createUsageSlice(...a),
    ...createTicketDetailSlice(...a),
    ...createConversationsSlice(...a),
    ...createFavoritesSlice(...a),
    ...createDocViewSlice(...a),
    ...createSettingsSlice(...a),
    // Placeholder; replaced in step 2 now that we have the handle the actions need to `setState`.
    actions: undefined as unknown as AppActions,
  }));

  // 2. Actions: bound to the bus + the live handle. This is the only place the bus is wired in.
  const actions: AppActions = {
    roster: createRosterActions(bus, store),
    plans: createPlansActions(bus, store),
    notes: createNotesActions(bus, store),
    reports: createReportsActions(bus, store),
    tickets: createTicketsActions(bus, store),
    history: createHistoryActions(bus, store),
    transit: createTransitActions(bus, store),
    usage: createUsageActions(bus, store),
    ticketDetail: createTicketDetailActions(bus, store),
    conversations: createConversationsActions(bus, store),
    favorites: createFavoritesActions(bus, store),
    docView: createDocViewActions(bus, store),
    settings: createSettingsActions(bus, store),
  };
  store.setState({ actions });

  // 3. Invalidation table: entity → the slice refresh it triggers. Usually one entry per slice, but
  //    a slice may be invalidated by more than one entity (the roster carries JOINed escalation
  //    counts, so both `agent` and `escalation` changes re-pull it).
  const invalidations: readonly SliceInvalidation[] = [
    { entity: ROSTER_INVALIDATING_ENTITY, refresh: () => void actions.roster.refresh() },
    { entity: ROSTER_ESCALATION_INVALIDATING_ENTITY, refresh: () => void actions.roster.refresh() },
    { entity: PLANS_INVALIDATING_ENTITY, refresh: () => void actions.plans.refresh() },
    { entity: NOTES_INVALIDATING_ENTITY, refresh: () => void actions.notes.refresh() },
    { entity: REPORTS_INVALIDATING_ENTITY, refresh: () => void actions.reports.refresh() },
    { entity: TICKETS_INVALIDATING_ENTITY, refresh: () => void actions.tickets.refresh() },
    { entity: HISTORY_INVALIDATING_ENTITY, refresh: () => void actions.history.refresh() },
    { entity: TRANSIT_INVALIDATING_ENTITY, refresh: () => void actions.transit.refresh() },
    { entity: USAGE_INVALIDATING_ENTITY, refresh: () => void actions.usage.refresh() },
  ];

  // A-D4 exhaustiveness guard. The `invalidations` table above is hand-maintained, and its entries
  // are typed `Entity` (widened), so nothing forces every Entity value to be wired. This parallel
  // record is literal-keyed by Entity: omitting a key — or adding a value to the `Entity` union in
  // protocol.ts without wiring it — is a COMPILE error here. Runtime-inert; exists only for tsc.
  // Mirror any change to `invalidations` here (and vice versa).
  const _INVALIDATION_COVERAGE = {
    ticket: TICKETS_INVALIDATING_ENTITY,
    agent: ROSTER_INVALIDATING_ENTITY,
    plan: PLANS_INVALIDATING_ENTITY,
    note: NOTES_INVALIDATING_ENTITY,
    report: REPORTS_INVALIDATING_ENTITY,
    escalation: ROSTER_ESCALATION_INVALIDATING_ENTITY,
    queue_row: USAGE_INVALIDATING_ENTITY,
    history: HISTORY_INVALIDATING_ENTITY,
    transit: TRANSIT_INVALIDATING_ENTITY,
  } satisfies Record<Entity, Entity>;
  void _INVALIDATION_COVERAGE;

  // 4. Event-driven invalidation. Subscribe filtered to `state.snapshot`; on each, re-pull exactly
  //    the slice(s) the named entity invalidates. No poll loop, no deep-diff — the event's `entity`
  //    is the change granularity. `void` the promise: invalidation is fire-and-forget (the action
  //    routes its own errors into the slice's `error` field).
  const unsubscribeSnapshot = bus.subscribe(
    (event) => {
      // The server filter already narrows to `state.snapshot`, but re-narrow in code so the type
      // refines to `StateSnapshotEvent` (and a fake/test that emits unfiltered is handled correctly).
      if (event.type !== 'state.snapshot') {
        return;
      }
      const snapshot: StateSnapshotEvent = event;
      for (const invalidation of invalidations) {
        if (invalidation.entity === snapshot.entity) {
          invalidation.refresh();
        }
      }
    },
    { type: 'state.snapshot' },
  );

  // 5. Conversation-block subscription. A SECOND bus.subscribe filtered to `conversation.block`
  //    routes content-bearing events to the conversations slice. `conversation` is NOT in the Entity
  //    union, so these events never flow through the snapshot invalidation loop above — they need
  //    their own subscription. The action is `applyBlock` (pure setState, no bus call).
  const unsubscribeConversations = bus.subscribe(
    (event) => {
      if (event.type !== 'conversation.block') {
        return;
      }
      const blockEvent: ConversationBlockEvent = event;
      actions.conversations.applyBlock(blockEvent);
    },
    { type: 'conversation.block' },
  );

  // 6. Conversation-state subscription. Same additive content-event seam as conversation.block:
  //    per-agent liveness (live_state) + the queued-but-undelivered message, routed to the
  //    conversations slice's meta map via `applyState` (pure setState, no bus call).
  const unsubscribeConversationState = bus.subscribe(
    (event) => {
      if (event.type !== 'conversation.state') {
        return;
      }
      const stateEvent: ConversationStateEvent = event;
      actions.conversations.applyState(stateEvent);
    },
    { type: 'conversation.state' },
  );

  // 7. Backend-error subscription. The service publishes `error` events for failures that have no
  //    owning RPC caller (worker crashes, command failures, …). Nothing else subscribes to them, so
  //    without this they are silently dropped at the dispatch layer. Route them to the toast rack —
  //    the ambient-feedback primitive — with error severity and a longer TTL (an error deserves more
  //    than a 2.5s glance). The traceback is deliberately NOT shown: the message is the user-facing
  //    truth; the service logs carry the rest.
  const unsubscribeErrors = bus.subscribe(
    (event) => {
      if (event.type !== 'error') {
        return;
      }
      const errorEvent: ErrorEvent = event;
      toastStore.getState().push(errorEvent.message, { severity: 'error', ttlMs: 6000 });
    },
    { type: 'error' },
  );

  return {
    store,
    dispose: () => {
      unsubscribeSnapshot();
      unsubscribeConversations();
      unsubscribeConversationState();
      unsubscribeErrors();
    },
  };
}

/** The pre-fetch state of a freshly created store — exported so a test (or a hook's default) can
 * assert the boot value without reconstructing it. Mirrors each slice's `initialXState`. */
export const initialAppState: Pick<
  AppStore,
  | 'roster'
  | 'plans'
  | 'notes'
  | 'reports'
  | 'tickets'
  | 'history'
  | 'transit'
  | 'usage'
  | 'ticketDetail'
  | 'conversations'
  | 'favorites'
  | 'docView'
  | 'settings'
> = {
  roster: initialRosterState,
  plans: initialPlansState,
  notes: initialNotesState,
  reports: initialReportsState,
  tickets: initialTicketsState,
  history: initialHistoryState,
  transit: initialTransitState,
  usage: initialUsageState,
  ticketDetail: initialTicketDetailState,
  conversations: initialConversationsState,
  favorites: initialFavoritesState,
  docView: initialDocViewState,
  settings: initialSettingsState,
};
