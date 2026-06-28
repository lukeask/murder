/**
 * `focusStore` — focus as a state machine with a *derived* candidate set and a *derived* re-home
 * invariant. This is the file that kills the old "nothing highlighted, must ctrl+f" bug class.
 *
 * The smells in the legacy Textual app: three hard-coded per-view focus
 * candidate lists, focus re-homing scattered imperatively after every toggle, and a `check_action`
 * gating table deciding what may be focused where. The cure here is three properties:
 *
 *  1. **One candidate set, derived.** The candidates come from the live focus graph built from
 *     mounted/painted rectangles. Desired panels are not focus candidates until they have geometry.
 *  2. **The re-home invariant is derived, not imperative.** The store holds the *intended* focus
 *     (`intendedId`). The *effective* focus is {@link resolveEffectiveFocus}(intended, graph): if
 *     the intended pane is no longer in the live graph, it resolves to `'chat'`.
 *  3. **No gating.** The store never decides whether a key is *allowed*; it only tracks where focus
 *     is. What a focused panel does with a key is the panel's declared keymap (see keymap.ts).
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink. The store is constructed with the
 * panel store for panel-toggle commands, but focus resolution no longer reads desired visibility.
 * Resolution is pull-based over rect data, which is what makes the invariant a pure function instead
 * of an effect that can race layout.
 *
 * ## Phase 4a — dynamic Stage panes
 *
 * The Stage region tiles chat-history panes that are NOT
 * toggleable panels: they appear/disappear as crows are favorited, not as a `ctrl/alt+<digit>` toggle.
 * They are still focusable (hjkl must reach them), so {@link FocusId} widens beyond the six
 * {@link PanelId}s + chat to include {@link StagePaneId} (`stage:<...>`, e.g. `stage:chat:<agentId>`;
 * Phase 4b adds `stage:doc:<name>` under the same scheme — no further type change needed).
 *
 * A pane's analogue of "is this a live candidate?" is **"does it have a non-zero measured rect right
 * now?"**. A pane that painted has measured itself; a pane that unmounted called
 * {@link FocusState.unmeasure} and dropped its rect. The graph applies that rule uniformly to list
 * panels and Stage panes.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import type { ChatTargetState } from '../layout/paneLayoutTypes.js';
import {
  buildFocusGraph,
  EMPTY_FOCUS_GRAPH_STATE,
  type FocusGraphState,
  navigateFocus,
  refreshFocusGraphState,
  resolveEffectiveFocus,
} from './focusGraph.js';
import { CHAT_FOCUS, type FocusId } from './focusIds.js';
import type { Direction, Rect } from './geometry.js';
import type { PanelStoreApi } from './panelStore.js';

export { CHAT_FOCUS, type FocusId, isStagePaneId, type StagePaneId } from './focusIds.js';

/** Field-wise rect equality — so a re-measure that yields the same position skips the ref-swap. */
function rectsEqual(a: Rect, b: Rect): boolean {
  return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
}

const EMPTY_CHAT_TARGETS: ChatTargetState = {
  activeTargetId: null,
  lockedVisibleTargetIds: [],
  favoriteOnlyTargetIds: [],
  ephemeralTargetId: null,
};

function arrayEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

function chatTargetsEqual(a: ChatTargetState, b: ChatTargetState): boolean {
  return (
    a.activeTargetId === b.activeTargetId &&
    a.ephemeralTargetId === b.ephemeralTargetId &&
    arrayEqual(a.lockedVisibleTargetIds, b.lockedVisibleTargetIds) &&
    arrayEqual(a.favoriteOnlyTargetIds, b.favoriteOnlyTargetIds)
  );
}

function graphStateWithOpenHistory(
  graphState: FocusGraphState,
  openPaneIdsByOpenedAt: readonly FocusId[],
): FocusGraphState {
  return { ...graphState, openPaneIdsByOpenedAt };
}

/** The focus store's state: the intended target plus the focus verbs. The *effective* focus is not
 * stored — read it with {@link selectEffectiveFocus} (or the React hook), which applies the
 * invariant against the live focus graph. */
export interface FocusState {
  /** Where the user last asked focus to be. May name a now-unmounted pane; graph resolution
   * collapses that to chat on read. Never trust this directly for rendering a highlight. */
  readonly intendedId: FocusId;
  readonly graphState: FocusGraphState;
  /** Durable pane admission order, updated from mount/unmount rather than layout order. */
  readonly openPaneIdsByOpenedAt: readonly FocusId[];
  readonly chatTargets: ChatTargetState;
  /**
   * The measured screen rect of each focusable, keyed by {@link FocusId}. Populated by components
   * via {@link FocusState.measure} (Ink `measureElement` at the component layer); read only by
   * {@link FocusState.navigate} to run the geometry kernel. Plain data — the kernel stays pure and
   * the store stays the single home of focus state, including the geometry inputs nav needs.
   */
  readonly rects: ReadonlyMap<FocusId, Rect>;
  /** Point focus at a target (`ctrl+<n>` on a panel, `ctrl+f`/`ctrl+s` to chat, a vim-nav result).
   * Stores intent; the effective value is still subject to {@link resolveEffectiveFocus}. */
  focus(id: FocusId): void;
  /** Record a focusable's measured rect. Idempotent for an unchanged rect (keeps map identity so a
   * re-measure on an unrelated re-render does not churn). Called from a component's measure effect. */
  measure(id: FocusId, rect: Rect): void;
  /** Admit a mounted pane into the durable open-history queue. Chat is not a pane and is ignored. */
  markPaneOpened(id: FocusId): void;
  /** Retire an unmounted pane from the durable open-history queue. Chat is ignored. */
  markPaneClosed(id: FocusId): void;
  /** Drop a focusable's rect — a Stage pane calls this on UNMOUNT (its component left the tree). It
   * removes the pane from the rects map, so the next graph build excludes it and effective focus
   * re-homes to chat if needed. Idempotent for an absent id (keeps map identity → no re-render
   * churn). */
  unmeasure(id: FocusId): void;
  /** Publish the current chat-target partition from the app state into focus graph construction. */
  setChatTargets(chatTargets: ChatTargetState): void;
  /** `ctrl+vim`: move focus to the geometric neighbour of the *effective* focus in `direction`,
   * over the mounted candidates' measured rects. No neighbour in that direction → focus unchanged
   * (the layout edge). The whole nav policy is here so the dispatcher just calls `navigate(dir)`. */
  navigate(direction: Direction): void;
}

/** Create a focus store bound to the panel store it resolves against. Starts intending chat — the
 * safe home that is always present, so a freshly booted app already satisfies "exactly one focused".
 */
export function createFocusStore(
  panels: PanelStoreApi,
  initialIntended: FocusId = CHAT_FOCUS,
): FocusStoreApi {
  const store = createStore<FocusState>()((set, get) => ({
    intendedId: initialIntended,
    graphState: EMPTY_FOCUS_GRAPH_STATE,
    openPaneIdsByOpenedAt: [],
    chatTargets: EMPTY_CHAT_TARGETS,
    rects: new Map<FocusId, Rect>(),
    focus(id) {
      set({ intendedId: id });
    },
    measure(id, rect) {
      set((state) => {
        const prev = state.rects.get(id);
        if (prev !== undefined && rectsEqual(prev, rect)) {
          return state; // unchanged — keep map identity, no re-render churn
        }
        const rects = new Map(state.rects).set(id, rect);
        const graph = buildFocusGraph({
          rects,
          chatTargets: state.chatTargets,
          state: graphStateWithOpenHistory(state.graphState, state.openPaneIdsByOpenedAt),
        });
        return {
          rects,
          graphState: refreshFocusGraphState(
            graph,
            graphStateWithOpenHistory(state.graphState, state.openPaneIdsByOpenedAt),
          ),
        };
      });
    },
    markPaneOpened(id) {
      set((state) => {
        if (id === CHAT_FOCUS || state.openPaneIdsByOpenedAt.includes(id)) {
          return state;
        }
        const openPaneIdsByOpenedAt = [...state.openPaneIdsByOpenedAt, id];
        const graphState = graphStateWithOpenHistory(state.graphState, openPaneIdsByOpenedAt);
        const graph = buildFocusGraph({
          rects: state.rects,
          chatTargets: state.chatTargets,
          state: graphState,
        });
        return {
          openPaneIdsByOpenedAt,
          graphState: refreshFocusGraphState(graph, graphState),
        };
      });
    },
    markPaneClosed(id) {
      set((state) => {
        if (id === CHAT_FOCUS || !state.openPaneIdsByOpenedAt.includes(id)) {
          return state;
        }
        const openPaneIdsByOpenedAt = state.openPaneIdsByOpenedAt.filter((paneId) => paneId !== id);
        const graphState = graphStateWithOpenHistory(state.graphState, openPaneIdsByOpenedAt);
        const graph = buildFocusGraph({
          rects: state.rects,
          chatTargets: state.chatTargets,
          state: graphState,
        });
        return {
          openPaneIdsByOpenedAt,
          graphState: refreshFocusGraphState(graph, graphState),
        };
      });
    },
    unmeasure(id) {
      set((state) => {
        if (!state.rects.has(id)) {
          return state; // absent — keep map identity, no re-render churn
        }
        const next = new Map(state.rects);
        next.delete(id);
        const graph = buildFocusGraph({
          rects: next,
          chatTargets: state.chatTargets,
          state: graphStateWithOpenHistory(state.graphState, state.openPaneIdsByOpenedAt),
        });
        return {
          rects: next,
          graphState: refreshFocusGraphState(
            graph,
            graphStateWithOpenHistory(state.graphState, state.openPaneIdsByOpenedAt),
          ),
        };
      });
    },
    setChatTargets(chatTargets) {
      set((state) => {
        if (chatTargetsEqual(state.chatTargets, chatTargets)) {
          return state;
        }
        const graphState = {
          ...state.graphState,
          openPaneIdsByOpenedAt: state.openPaneIdsByOpenedAt,
          activeTargetId: chatTargets.activeTargetId,
          previousLockedVisibleTargetIds: [...chatTargets.lockedVisibleTargetIds],
          previousFavoriteOnlyTargetIds: [...chatTargets.favoriteOnlyTargetIds],
        };
        const graph = buildFocusGraph({
          rects: state.rects,
          chatTargets,
          state: graphState,
        });
        return {
          chatTargets,
          graphState: refreshFocusGraphState(graph, graphState),
        };
      });
    },
    navigate(direction) {
      const state = get();
      const graph = buildFocusGraph({
        rects: state.rects,
        chatTargets: state.chatTargets,
        state: graphStateWithOpenHistory(state.graphState, state.openPaneIdsByOpenedAt),
      });
      const current = resolveEffectiveFocus(state.intendedId, graph);
      const result = navigateFocus(
        graph,
        current,
        direction,
        graphStateWithOpenHistory(state.graphState, state.openPaneIdsByOpenedAt),
      );
      if (result.focusId !== null) {
        set({ intendedId: result.focusId, graphState: result.state });
        return;
      }
      set({ graphState: result.state });
    },
  }));
  // Keep the panel handle on the store object for existing panel-toggle wiring; focus resolution is
  // graph-backed and does not read panel visibility.
  return Object.assign(store, { panels });
}

/** The focus store handle, carrying its panel store so effective-focus reads need only this one
 * handle. Re-exported so callers don't import `zustand/vanilla`. */
export type FocusStoreApi = StoreApi<FocusState> & { readonly panels: PanelStoreApi };

/**
 * The effective focus right now: the invariant applied to the live intended + focus graph. This is
 * what a highlight reads ("is my border on?") and what the dispatcher reads to route a key to the
 * focused panel. Pure read — no mutation, so it is safe to call in render.
 */
export function selectEffectiveFocus(focus: FocusStoreApi): FocusId {
  const state = focus.getState();
  return resolveEffectiveFocus(
    state.intendedId,
    buildFocusGraph({
      rects: state.rects,
      chatTargets: state.chatTargets,
      state: graphStateWithOpenHistory(state.graphState, state.openPaneIdsByOpenedAt),
    }),
  );
}
