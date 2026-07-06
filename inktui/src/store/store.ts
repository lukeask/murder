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
import type {
  BusClient,
  BusEventListener,
  HydrateSnapshots,
  Unsubscribe,
} from '../bus/BusClient.js';
import type {
  ConversationBlockEvent,
  ConversationStateEvent,
  Entity,
  ErrorEvent,
  HydrateTopic,
  StateSnapshotEvent,
} from '../bus/protocol.js';
import { applyThemeRecords, type ThemeRecord } from '../theme/palettes.js';
import {
  applyConversationsSnapshot,
  type ConversationsActions,
  type ConversationsSnapshotReply,
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
import type { CrowSessionDto, CrowSnapshotReply } from './roster/rosterActions.js';
import { createRosterActions, type RosterActions } from './roster/rosterActions.js';
import type { RosterRow } from './roster/rosterSlice.js';
import {
  createRosterSlice,
  initialRosterState,
  ROSTER_ESCALATION_INVALIDATING_ENTITY,
  ROSTER_INVALIDATING_ENTITY,
  type RosterState,
} from './roster/rosterSlice.js';
import type { SettingsWire } from './settings/settingsActions.js';
import { createSettingsActions, type SettingsActions } from './settings/settingsActions.js';
import {
  createSettingsSlice,
  initialSettingsState,
  type SettingsState,
} from './settings/settingsSlice.js';
import { createTemplatesActions, type TemplatesActions } from './templates/templatesActions.js';
import {
  createTemplatesSlice,
  initialTemplatesState,
  type TemplateRecord,
  type TemplatesState,
} from './templates/templatesSlice.js';
import { createThemesActions, type ThemesActions } from './themes/themesActions.js';
import { createThemesSlice, initialThemesState, type ThemesState } from './themes/themesSlice.js';
import {
  createTicketDetailActions,
  type TicketDetailActions,
} from './ticketDetail/ticketDetailActions.js';
import {
  createTicketDetailSlice,
  initialTicketDetailState,
  type TicketDetailState,
} from './ticketDetail/ticketDetailSlice.js';
import type {
  ScheduleSnapshotReply,
  ScheduleUsageGaugeDto,
  TicketDto,
} from './tickets/ticketsActions.js';
import { createTicketsActions, type TicketsActions } from './tickets/ticketsActions.js';
import type { TicketRow } from './tickets/ticketsSlice.js';
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
import type { UsageRow } from './usage/usageSlice.js';
import {
  createUsageSlice,
  initialUsageState,
  USAGE_INVALIDATING_ENTITY,
  type UsageState,
} from './usage/usageSlice.js';
import { createWorkflowsActions, type WorkflowsActions } from './workflows/workflowsActions.js';
import {
  createWorkflowsSlice,
  initialWorkflowsState,
  type WorkflowDef,
  type WorkflowsState,
} from './workflows/workflowsSlice.js';

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
  templates: TemplatesActions;
  themes: ThemesActions;
  workflows: WorkflowsActions;
  docView: DocViewActions;
  settings: SettingsActions;
}

export interface HydrationState {
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  readonly cursor: number | null;
  readonly mode: 'cold' | 'resume' | 'snapshot_fallback' | null;
  readonly error: string | null;
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
  templates: TemplatesState;
  themes: ThemesState;
  workflows: WorkflowsState;
  docView: DocViewState;
  settings: SettingsState;
  hydration: HydrationState;
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

export const initialHydrationState: HydrationState = {
  status: 'idle',
  cursor: null,
  mode: null,
  error: null,
};

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
    ...createTemplatesSlice(...a),
    ...createThemesSlice(...a),
    ...createWorkflowsSlice(...a),
    ...createDocViewSlice(...a),
    ...createSettingsSlice(...a),
    hydration: initialHydrationState,
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
    templates: createTemplatesActions(bus, store),
    themes: createThemesActions(bus, store),
    workflows: createWorkflowsActions(bus, store),
    docView: createDocViewActions(bus, store),
    settings: createSettingsActions(bus, store),
  };
  store.setState({ actions });

  // 3. Invalidation table: entity → the slice refresh it triggers. Usually one entry per slice, but
  //    a slice may be invalidated by more than one entity (the roster carries JOINed escalation
  //    counts, so both `agent` and `escalation` changes re-pull it).
  // Lazy slices (plans/notes/reports/history/transit) back panels that are CLOSED on startup. Their
  // invalidation refresh is GATED on the slice having been fetched at least once (`status !== 'idle'`
  // — the fresh-store boot value is `idle`). The panel's own mount-effect fires the first fetch when
  // it is opened, which moves the slice off `idle`; only then do subsequent `state.snapshot` events
  // re-pull it. This keeps the cold-start `state.snapshot` storm from fetching heavy data for panels
  // nobody opened (transit alone is ~110KB), while a panel that HAS been opened still stays live.
  const invalidations: readonly SliceInvalidation[] = [
    { entity: ROSTER_INVALIDATING_ENTITY, refresh: () => void actions.roster.refresh() },
    { entity: ROSTER_ESCALATION_INVALIDATING_ENTITY, refresh: () => void actions.roster.refresh() },
    {
      entity: PLANS_INVALIDATING_ENTITY,
      refresh: () => {
        if (store.getState().plans.status !== 'idle') void actions.plans.refresh();
      },
    },
    {
      entity: NOTES_INVALIDATING_ENTITY,
      refresh: () => {
        if (store.getState().notes.status !== 'idle') void actions.notes.refresh();
      },
    },
    {
      entity: REPORTS_INVALIDATING_ENTITY,
      refresh: () => {
        if (store.getState().reports.status !== 'idle') void actions.reports.refresh();
      },
    },
    { entity: TICKETS_INVALIDATING_ENTITY, refresh: () => void actions.tickets.refresh() },
    {
      entity: HISTORY_INVALIDATING_ENTITY,
      refresh: () => {
        if (store.getState().history.status !== 'idle') void actions.history.refresh();
      },
    },
    {
      entity: TRANSIT_INVALIDATING_ENTITY,
      refresh: () => {
        if (store.getState().transit.status !== 'idle') void actions.transit.refresh();
      },
    },
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

  const hydration = wireHydration(bus, store, actions, invalidations);

  return {
    store,
    dispose: () => {
      hydration.dispose();
    },
  };
}

const HYDRATE_TOPICS: readonly HydrateTopic[] = ['all'];

function wireHydration(
  bus: BusClient,
  store: AppStoreApi,
  actions: AppActions,
  invalidations: readonly SliceInvalidation[],
): { dispose: () => void } {
  let disposed = false;
  const listener: BusEventListener = (event) => {
    if (disposed) {
      return;
    }
    routeHydratedEvent(event, actions, invalidations);
  };

  store.setState({ hydration: { status: 'loading', cursor: null, mode: null, error: null } });
  let unsubscribeHydrate: Unsubscribe | undefined;
  void bus
    .hydrate(HYDRATE_TOPICS, listener)
    .then((reply) => {
      if (disposed) {
        reply.unsubscribe();
        return;
      }
      applyHydrateSnapshots(store, reply.snapshots);
      for (const replay of reply.replay ?? []) {
        listener(replay.event);
      }
      unsubscribeHydrate = reply.unsubscribe;
      store.setState({
        hydration: {
          status: 'ready',
          cursor: reply.cursor,
          mode: reply.mode ?? null,
          error: null,
        },
      });
    })
    .catch((error: unknown) => {
      if (disposed) return;
      const message = error instanceof Error ? error.message : String(error);
      store.setState({ hydration: { status: 'error', cursor: null, mode: null, error: message } });
    });

  return {
    dispose: () => {
      disposed = true;
      unsubscribeHydrate?.();
    },
  };
}

function routeHydratedEvent(
  event: Parameters<BusEventListener>[0],
  actions: AppActions,
  invalidations: readonly SliceInvalidation[],
): void {
  if (event.type === 'state.snapshot') {
    const snapshot: StateSnapshotEvent = event;
    for (const invalidation of invalidations) {
      if (invalidation.entity === snapshot.entity) {
        invalidation.refresh();
      }
    }
    return;
  }
  if (event.type === 'conversation.block') {
    actions.conversations.applyBlock(event as ConversationBlockEvent);
    return;
  }
  if (event.type === 'conversation.state') {
    actions.conversations.applyState(event as ConversationStateEvent);
    return;
  }
  if (event.type === 'error') {
    const errorEvent: ErrorEvent = event;
    const severity = errorEvent.recoverable ? 'warning' : 'error';
    toastStore.getState().push(errorEvent.message, { severity, ttlMs: 12000 });
  }
}

function applyHydrateSnapshots(store: AppStoreApi, snapshots: HydrateSnapshots): void {
  const crow = snapshotAs<CrowSnapshotReply>(snapshots, 'state.crow_snapshot', 'crow', 'roster');
  if (crow !== undefined) {
    store.setState({
      roster: { rows: crow.sessions.map(toRosterRow), status: 'ready', error: null },
    });
  }

  const schedule = snapshotAs<ScheduleSnapshotReply>(
    snapshots,
    'state.schedule_snapshot',
    'schedule',
  );
  if (schedule !== undefined) {
    store.setState({
      tickets: { rows: projectTickets(schedule), status: 'ready', error: null },
      usage: { rows: schedule.usage_gauges.map(toUsageRow), status: 'ready', error: null },
    });
  }

  const conversations = snapshotAs<ConversationsSnapshotReply>(
    snapshots,
    'state.conversations_snapshot',
    'conversations',
  );
  if (conversations !== undefined) {
    applyConversationsSnapshot(store, conversations);
  }

  const favorites = snapshotAs<{ favorites?: readonly string[] }>(
    snapshots,
    'tui.load_favorites',
    'favorites',
  );
  if (favorites !== undefined) {
    store.setState({
      favorites: { ids: new Set(favorites.favorites ?? []), status: 'ready', error: null },
    });
  }

  const templates = snapshotAs<{ templates?: readonly TemplateRecord[] }>(
    snapshots,
    'tui.load_templates',
    'templates',
  );
  if (templates !== undefined) {
    store.setState({
      templates: { items: templates.templates ?? [], status: 'ready', error: null },
    });
  }

  const themes = snapshotAs<{ themes?: readonly ThemeRecord[] }>(
    snapshots,
    'tui.load_themes',
    'themes',
  );
  if (themes !== undefined) {
    const items = themes.themes ?? [];
    applyThemeRecords(items);
    store.setState({ themes: { items, status: 'ready', error: null } });
  }

  const workflows = snapshotAs<{ workflows?: readonly WorkflowDef[] }>(
    snapshots,
    'tui.load_workflows',
    'workflows',
  );
  if (workflows !== undefined) {
    store.setState({
      workflows: { items: workflows.workflows ?? [], status: 'ready', error: null },
    });
  }

  const settings = snapshotAs<{ settings?: SettingsWire }>(snapshots, 'settings.get', 'settings');
  if (settings !== undefined) {
    store.setState((state) => ({ settings: applySettingsWire(state.settings, settings.settings) }));
  }
}

function snapshotAs<T>(snapshots: HydrateSnapshots, ...keys: readonly string[]): T | undefined {
  for (const key of keys) {
    if (Object.hasOwn(snapshots, key)) {
      return snapshots[key] as unknown as T;
    }
  }
  return undefined;
}

function toRosterRow(session: CrowSessionDto): RosterRow {
  return {
    agentId: session.agent_id,
    role: session.role,
    ticketId: session.ticket_id ?? null,
    ticketTitle: session.ticket_title ?? null,
    harness: session.harness ?? null,
    model: session.model ?? null,
    status: session.status,
    session: session.session_name ?? null,
    worktreePath: session.worktree_path ?? null,
    lastSeen: session.last_seen ?? null,
    openEscalations: session.open_escalations ?? 0,
    maxSeverity: session.max_severity ?? 0,
  };
}

function projectTickets(reply: ScheduleSnapshotReply): readonly TicketRow[] {
  return [...reply.active_tickets, ...reply.recent_done_tickets, ...reply.archived_tickets].map(
    toTicketRow,
  );
}

function toTicketRow(dto: TicketDto): TicketRow {
  return {
    id: dto.id,
    title: dto.title,
    status: dto.status,
    lastUpdateAt: dto.last_update_at,
    lastUpdateLabel: dto.last_update_label,
    scheduleAt: dto.schedule_at ?? null,
    harness: dto.harness ?? null,
    model: dto.model ?? null,
    pendingDepIds: dto.pending_dep_ids,
    parent: dto.parent ?? null,
  };
}

function toUsageRow(dto: ScheduleUsageGaugeDto): UsageRow {
  return {
    harness: dto.harness,
    windowKey: dto.window_key,
    pct: dto.pct,
    tUntilResetMinutes: dto.t_until_reset_minutes,
    tPeriodMinutes: dto.t_period_minutes ?? 0,
    steering: dto.steering ?? 'auto',
  };
}

function applySettingsWire(prev: SettingsState, wire: SettingsWire | undefined): SettingsState {
  if (wire === undefined) {
    return { ...prev, status: 'ready', error: null };
  }
  return {
    theme: wire.theme ?? prev.theme,
    modifier: wire.modifier ?? prev.modifier,
    keyOverrides: wire.key_overrides ?? prev.keyOverrides,
    paneGap: wire.pane_gap ?? prev.paneGap,
    vimMode: wire.vim_mode ?? prev.vimMode,
    defaultChatViewMode: wire.default_chat_view_mode ?? prev.defaultChatViewMode,
    startupRogue: 'startup_rogue' in wire ? wire.startup_rogue : prev.startupRogue,
    collaboratorHarness:
      'collaborator_harness' in wire ? wire.collaborator_harness : prev.collaboratorHarness,
    plannerHarness: 'planner_harness' in wire ? wire.planner_harness : prev.plannerHarness,
    crowHarnesses: 'crow_harnesses' in wire ? wire.crow_harnesses : prev.crowHarnesses,
    effectiveCollaboratorHarness:
      wire.effective_collaborator_harness ?? prev.effectiveCollaboratorHarness,
    effectivePlannerHarness: wire.effective_planner_harness ?? prev.effectivePlannerHarness,
    effectiveCrowHarnesses: wire.effective_crow_harnesses ?? prev.effectiveCrowHarnesses,
    llm: wire.llm ?? prev.llm,
    llmEnv: wire.llm_env ?? prev.llmEnv,
    status: 'ready',
    error: null,
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
  | 'templates'
  | 'themes'
  | 'workflows'
  | 'docView'
  | 'settings'
  | 'hydration'
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
  templates: initialTemplatesState,
  themes: initialThemesState,
  workflows: initialWorkflowsState,
  docView: initialDocViewState,
  settings: initialSettingsState,
  hydration: initialHydrationState,
};
