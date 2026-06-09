/**
 * `focusStore` — focus as a state machine with a *derived* candidate set and a *derived* re-home
 * invariant. This is the file that kills the old "nothing highlighted, must ctrl+f" bug class.
 *
 * The smells in the legacy Textual app: three hard-coded per-view focus
 * candidate lists, focus re-homing scattered imperatively after every toggle, and a `check_action`
 * gating table deciding what may be focused where. The cure here is three properties:
 *
 *  1. **One candidate set, derived.** The candidates are `[...visiblePanels, 'chat']` computed from
 *     the panel store's visible set — never a stored list that can fall out of sync. The chat input
 *     is *always* a candidate, so there is always somewhere to be.
 *  2. **The re-home invariant is derived, not imperative.** The store holds the *intended* focus
 *     (`intendedId`). The *effective* focus is {@link resolveFocus}(intended, visible): if the
 *     intended panel is no longer visible, it resolves to `'chat'`. Nothing re-homes by calling a
 *     setter after a toggle — hiding a panel can't leave focus dangling because "focused on a hidden
 *     panel" is not a representable effective state. "Always exactly one border highlighted" is a
 *     theorem about `resolveFocus`, not a thing code must remember to maintain.
 *  3. **No gating.** The store never decides whether a key is *allowed*; it only tracks where focus
 *     is. What a focused panel does with a key is the panel's declared keymap (see keymap.ts).
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink. The store is constructed with the
 * panel store so it can read the visible set when resolving; it subscribes to nothing and schedules
 * no effects — resolution is pull-based (computed on read), which is what makes the invariant a pure
 * function instead of an effect that can race a toggle.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import {
  type Direction,
  directionalFocusTarget,
  type FocusCandidate,
  type Rect,
} from './geometry.js';
import type { PanelStoreApi } from './panelStore.js';
import { PANEL_IDS, type PanelId } from './panels.js';

/** The chat input — always present, always a focus candidate, the re-home destination. A literal,
 * not a `PanelId`, because it is not a toggleable panel: it can never be hidden. */
export const CHAT_FOCUS = 'chat' as const;

/** Field-wise rect equality — so a re-measure that yields the same position skips the ref-swap. */
function rectsEqual(a: Rect, b: Rect): boolean {
  return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
}

/** Anything that can hold focus: a panel (when visible) or the always-present chat input. */
export type FocusId = PanelId | typeof CHAT_FOCUS;

/**
 * Resolve *intended* focus to *effective* focus against the visible set — the re-home invariant as
 * a pure function. If the intended target is a panel that is not visible, focus falls home to chat.
 * `'chat'` always resolves to itself (it can't be hidden). This is the only place the invariant is
 * expressed; every reader goes through it, so "focused on a hidden panel" never escapes.
 */
export function resolveFocus(intended: FocusId, visible: ReadonlySet<PanelId>): FocusId {
  if (intended === CHAT_FOCUS) {
    return CHAT_FOCUS;
  }
  return visible.has(intended) ? intended : CHAT_FOCUS;
}

/**
 * The derived candidate set: the visible panels in screen order, then chat. Screen order (not
 * insertion order) so `ctrl+vim` navigation and any ring traversal are spatially stable. Chat is
 * last by convention (it sits at the bottom of the layout). Pure — computed from the visible set,
 * never stored.
 */
export function focusCandidates(visible: ReadonlySet<PanelId>): readonly FocusId[] {
  const panels = PANEL_IDS.filter((id) => visible.has(id));
  return [...panels, CHAT_FOCUS];
}

/** The focus store's state: the intended target plus the focus verbs. The *effective* focus is not
 * stored — read it with {@link selectEffectiveFocus} (or the React hook), which applies the
 * invariant against the live visible set. */
export interface FocusState {
  /** Where the user last asked focus to be. May name a now-hidden panel; {@link resolveFocus}
   * collapses that to chat on read. Never trust this directly for rendering a highlight. */
  readonly intendedId: FocusId;
  /**
   * The measured screen rect of each focusable, keyed by {@link FocusId}. Populated by components
   * via {@link FocusState.measure} (Ink `measureElement` at the component layer); read only by
   * {@link FocusState.navigate} to run the geometry kernel. Plain data — the kernel stays pure and
   * the store stays the single home of focus state, including the geometry inputs nav needs.
   */
  readonly rects: ReadonlyMap<FocusId, Rect>;
  /** Point focus at a target (`ctrl+<n>` on a panel, `ctrl+f`/`ctrl+s` to chat, a vim-nav result).
   * Stores intent; the effective value is still subject to {@link resolveFocus}. */
  focus(id: FocusId): void;
  /** Record a focusable's measured rect. Idempotent for an unchanged rect (keeps map identity so a
   * re-measure on an unrelated re-render does not churn). Called from a component's measure effect. */
  measure(id: FocusId, rect: Rect): void;
  /** `ctrl+vim`: move focus to the geometric neighbour of the *effective* focus in `direction`,
   * over the visible candidates' measured rects. No neighbour in that direction → focus unchanged
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
        return { rects: new Map(state.rects).set(id, rect) };
      });
    },
    navigate(direction) {
      // Candidates are the *effective* visible set + chat, in screen order (the geometry kernel's
      // final tiebreak is declaration order), each paired with its measured rect. A focusable with
      // no rect yet (not painted/measured) is dropped — it has no position to navigate to.
      const visible = panels.getState().visible;
      const current = resolveFocus(get().intendedId, visible);
      const rects = get().rects;
      const candidates: FocusCandidate<FocusId>[] = [];
      for (const id of focusCandidates(visible)) {
        const rect = rects.get(id);
        if (rect !== undefined) {
          candidates.push({ id, rect });
        }
      }
      const target = directionalFocusTarget(direction, current, candidates);
      if (target !== null) {
        set({ intendedId: target });
      }
    },
  }));
  // The panel store is captured for {@link selectEffectiveFocus}; no subscription/effect is needed
  // because resolution is pull-based. We hang the panel handle on the store object so the selector
  // and the React hook can resolve without the caller threading both stores everywhere.
  return Object.assign(store, { panels });
}

/** The focus store handle, carrying its panel store so effective-focus reads need only this one
 * handle. Re-exported so callers don't import `zustand/vanilla`. */
export type FocusStoreApi = StoreApi<FocusState> & { readonly panels: PanelStoreApi };

/**
 * The effective focus right now: the invariant applied to the live intended + visible state. This is
 * what a highlight reads ("is my border on?") and what the dispatcher reads to route a key to the
 * focused panel. Pure read across both stores — no mutation, so it is safe to call in render.
 */
export function selectEffectiveFocus(focus: FocusStoreApi): FocusId {
  return resolveFocus(focus.getState().intendedId, focus.panels.getState().visible);
}
