/**
 * React bindings for the input/focus backbone — the thin glue over the framework-agnostic panel and
 * focus stores (rule 4: the stores have no React; this is where React enters).
 *
 * Provides:
 *  - {@link InputStoresProvider} — carries the one panel store + one focus store to the tree.
 *  - {@link usePanelStore} / {@link useFocusStore} — selector hooks (referential stability per
 *    selector, like {@link useAppStore}).
 *  - {@link useEffectiveFocus} — the derived re-home invariant as a hook: returns the *effective*
 *    focus, recomputed from intended focus and live focus geometry. A panel's highlight reads
 *    `useEffectiveFocus() === myId`.
 *  - {@link useMeasureFocus} — registers non-layout focus geometry such as chat.
 *
 * These are the hooks C5's panels copy; nothing here calls the bus or owns input — the root input
 * loop lives in {@link useRootInput}.
 */

import type { DOMElement } from 'ink';
import { createContext, useContext, useEffect, useRef } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { ResolvedBindings } from '../input/bindings.js';
import type { BindingsState, BindingsStoreApi } from '../input/bindingsStore.js';
import type { ChatHistoryState, ChatHistoryStoreApi } from '../input/chatHistoryStore.js';
import type { ChatInputState, ChatInputStoreApi } from '../input/chatInputStore.js';
import type { ChatVimState, ChatVimStoreApi } from '../input/chatVimStore.js';
import {
  buildFocusGraph,
  focusPaneGeometriesFromRects,
  normalizeFocusGraphRecipientTargets,
  resolveEffectiveFocus,
  resolveEffectiveFocusTarget,
} from '../input/focusGraph.js';
import {
  CHAT_FOCUS,
  type FocusId,
  type FocusState,
  type FocusStoreApi,
  type FocusTarget,
} from '../input/focusStore.js';
import type { Rect } from '../input/geometry.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { KeymapRegistryApi, KeymapRegistryState } from '../input/keymapRegistry.js';
import type { ModeState, ModeStoreApi } from '../input/modeStore.js';
import type { PanelState, PanelStoreApi } from '../input/panelStore.js';
import type { PaneScrollBus } from '../input/paneScrollBus.js';
import type { PaneUiState, PaneUiStoreApi } from '../input/paneUiStore.js';
import type { WorkspaceStoreApi, WorkspaceStoreState } from '../input/workspaceStore.js';
import { useTerminalSize } from './useTerminalSize.js';

/** The input stores, carried as one context value so the provider wires them together once. */
export interface InputStores {
  readonly panels: PanelStoreApi;
  readonly focus: FocusStoreApi;
  readonly keymaps: KeymapRegistryApi;
  readonly modes: ModeStoreApi;
  readonly chatInput: ChatInputStoreApi;
  readonly chatHistory: ChatHistoryStoreApi;
  readonly chatVim: ChatVimStoreApi;
  readonly bindings: BindingsStoreApi;
  readonly paneScroll: PaneScrollBus;
  readonly paneUi: PaneUiStoreApi;
  readonly workspace: WorkspaceStoreApi;
}

/** `null` outside a provider so the hooks fail loudly on a wiring bug (mirrors `useAppStore`). */
const InputStoresContext = createContext<InputStores | null>(null);

/** Supplies the input stores to the tree. The app root constructs them (focus store bound to the
 * panel store) and passes them here. */
export const InputStoresProvider = InputStoresContext.Provider;

/** Read both store handles; throws outside a provider. The selector hooks build on this. */
export function useInputStores(): InputStores {
  const stores = useContext(InputStoresContext);
  if (stores === null) {
    throw new Error('input hooks must be used within an <InputStoresProvider>.');
  }
  return stores;
}

/** Subscribe to a selected view of the panel store (pass `shallow` for object/set selections). */
export function usePanelStore<T>(
  selector: (state: PanelState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { panels } = useInputStores();
  return useStoreWithEqualityFn(panels, selector, equality);
}

/** Subscribe to a selected view of the focus store. */
export function useFocusStore<T>(
  selector: (state: FocusState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { focus } = useInputStores();
  return useStoreWithEqualityFn(focus, selector, equality);
}

/** Subscribe to a selected view of the keymap registry. The root dispatcher reads the whole map. */
export function useKeymapRegistry<T>(
  selector: (state: KeymapRegistryState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { keymaps } = useInputStores();
  return useStoreWithEqualityFn(keymaps, selector, equality);
}

/** Subscribe to a selected view of the mode store. The {@link ../components/Overlay.js Overlay} reads
 * the stack/active mode; a trigger reads `enter`/`exit`. Pass `shallow` for object selections. */
export function useModeStore<T>(
  selector: (state: ModeState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { modes } = useInputStores();
  return useStoreWithEqualityFn(modes, selector, equality);
}

/** Subscribe to a selected view of the chat-input buffer (C11). The {@link ../components/ChatInput.js
 * ChatInput} reads `s.text` to render the live message + cursor. */
export function useChatInputStore<T>(
  selector: (state: ChatInputState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { chatInput } = useInputStores();
  return useStoreWithEqualityFn(chatInput, selector, equality);
}

/** Subscribe to a selected view of the murder-wide chat-history corpus (chat-input overhaul). The
 * {@link ../components/ChatInput.js ChatInput} does not read it directly; the App boot seed + the
 * handler do (via `getState()`), but the hook is here for symmetry/tests. */
export function useChatHistoryStore<T>(
  selector: (state: ChatHistoryState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { chatHistory } = useInputStores();
  return useStoreWithEqualityFn(chatHistory, selector, equality);
}

/** Subscribe to a selected view of the vim store (chat-input overhaul). The
 * {@link ../components/ChatInput.js ChatInput} reads `s.submode` to render the `· NORMAL`/`· INSERT`
 * border tag when vim mode is on. */
export function useChatVimStore<T>(
  selector: (state: ChatVimState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { chatVim } = useInputStores();
  return useStoreWithEqualityFn(chatVim, selector, equality);
}

/** Subscribe to a selected view of the bindings store. Pass a selector that returns part of
 * {@link BindingsState}; the common case is {@link useBindings} (the resolved table). */
export function useBindingsStore<T>(
  selector: (state: BindingsState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { bindings } = useInputStores();
  return useStoreWithEqualityFn(bindings, selector, equality);
}

/**
 * The resolved binding table — the deep view callers use to ask `chordsFor(id)` / `label(id)` /
 * `matches(...)` without inspecting the modifier. The store swaps in a fresh `resolved` object only
 * when settings actually change, so this is a stable `useMemo`/effect dependency (panels re-register
 * their keymaps only on a real settings change, not every render).
 */
export function useBindings(): ResolvedBindings {
  return useBindingsStore((s) => s.resolved);
}

/** The mouse-wheel scroll command channel. A Stage pane subscribes for its own focus id to receive
 * wheel nudges; {@link useRootInput} emits to the focused/targeted pane. */
export function usePaneScrollBus(): PaneScrollBus {
  return useInputStores().paneScroll;
}

/** Subscribe to a selected view of the workspace store (workspaces plan). The indicator widget
 * selects `{ activeIndex, count }`; the dispatcher reads `getState()` directly. */
export function useWorkspaceStore<T>(
  selector: (state: WorkspaceStoreState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { workspace } = useInputStores();
  return useStoreWithEqualityFn(workspace, selector, equality);
}

/** Subscribe to a selected view of the per-pane UI-state store (scroll/cursor keyed by pane id).
 * The common cases are the shared hooks in {@link ../components/panes/shared/useClampedCursor.js}
 * (`usePaneUiClampedCursor`), {@link ../components/panes/shared/usePaneScrollState.js}
 * (`usePaneScrollState`), and {@link ../components/panes/shared/usePaneExpandedState.js}
 * (`usePaneExpandedState`); select `s.cursors[id]` / `s.scrolls[id]` / `s.expandeds[id]` for a
 * single pane. */
export function usePaneUiStore<T>(
  selector: (state: PaneUiState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { paneUi } = useInputStores();
  return useStoreWithEqualityFn(paneUi, selector, equality);
}

/**
 * Declare a panel's keymap to the registry for as long as the panel is mounted (rule 5: a panel
 * declares keys, never handles raw input). Re-registers when `keymap` changes; unregisters on
 * unmount. THE recipe for making a panel keyboard-driven — a panel calls this once with its
 * `Keymap` and its intent handler, and the root dispatcher routes matching keys to it when focused.
 */
export function usePanelKeymap<Intent extends string>(
  id: FocusId,
  keymap: PanelKeymap<Intent>,
): void {
  const { keymaps } = useInputStores();
  useEffect(() => {
    // The registry stores the erased `PanelKeymap<string>` shape (the dispatcher only needs to fire
    // *a* string intent). A panel's `PanelKeymap<Intent>` is safe to widen on store: every `Intent`
    // is a `string`, and `onIntent` is only ever called with intents drawn from this same keymap.
    keymaps.getState().register(id, keymap as unknown as PanelKeymap);
    return () => keymaps.getState().unregister(id);
  }, [keymaps, id, keymap]);
}

/**
 * The effective focus, as a hook — the re-home invariant applied reactively against the live focus
 * graph. Mounted/painted rectangles are the candidate source; desired panel visibility is not.
 */
export function useEffectiveFocus(): FocusId {
  const intended = useFocusStore((s) => s.intendedId);
  const rects = useFocusStore((s) => s.rects);
  const paneGeometries = useFocusStore((s) => s.paneGeometries);
  const recipientTargets = useFocusStore((s) => s.recipientTargets);
  const graphState = useFocusStore((s) => s.graphState);
  return resolveEffectiveFocus(
    intended,
    buildFocusGraph({
      panes: paneGeometries ?? focusPaneGeometriesFromRects(rects),
      chatRect: rects.get(CHAT_FOCUS) ?? null,
      recipientTargets: normalizeFocusGraphRecipientTargets(recipientTargets),
      state: graphState,
    }),
  );
}

export function useEffectiveFocusTarget(): FocusTarget {
  const intended = useFocusStore((s) => s.intendedId);
  const rects = useFocusStore((s) => s.rects);
  const paneGeometries = useFocusStore((s) => s.paneGeometries);
  const recipientTargets = useFocusStore((s) => s.recipientTargets);
  const graphState = useFocusStore((s) => s.graphState);
  return resolveEffectiveFocusTarget(
    intended,
    buildFocusGraph({
      panes: paneGeometries ?? focusPaneGeometriesFromRects(rects),
      chatRect: rects.get(CHAT_FOCUS) ?? null,
      recipientTargets: normalizeFocusGraphRecipientTargets(recipientTargets),
      state: graphState,
    }),
  ).target;
}

/**
 * Measure a box's **absolute** screen rect by walking the Yoga layout tree.
 *
 * Ink's own `measureElement` returns only `{width, height}` (a box's size, for content-driven
 * layout) — it gives no position, which the directional geometry kernel needs. So we read the
 * computed size off the node's Yoga node and accumulate `getComputedLeft()/Top()` up the
 * `parentNode` chain to absolute terminal coordinates. This is the component-layer rect bridge; the
 * kernel below it stays a pure fn over the {@link Rect} this produces (rule 5). Returns the
 * zero-rect before first layout (matching `measureElement`'s documented pre-layout behaviour), which
 * the geometry kernel handles as "no usable position yet".
 */
function measureRect(node: DOMElement): Rect {
  const yoga = node.yogaNode;
  if (yoga === undefined) {
    return { x: 0, y: 0, width: 0, height: 0 };
  }
  let x = 0;
  let y = 0;
  let current: DOMElement | undefined = node;
  while (current?.yogaNode !== undefined) {
    x += current.yogaNode.getComputedLeft();
    y += current.yogaNode.getComputedTop();
    current = current.parentNode;
  }
  return { x, y, width: yoga.getComputedWidth(), height: yoga.getComputedHeight() };
}

/**
 * Bridge an Ink box's measured absolute rect into the focus store so directional nav can target it.
 * Pass the focusable's id and the ref you put on its `<Box>`; on every layout this measures the box
 * and records the rect (the store dedupes unchanged rects). Allocated panes do not use this; their
 * geometry comes from the layout plan.
 *
 * ## Re-measure under reflow (Phase 2)
 * A terminal resize / orientation flip changes chat's Yoga rect, so `ctrl+h/j/k/l` must score over
 * the NEW geometry. We subscribe to {@link useTerminalSize} HERE so a resize re-renders any measured
 * non-layout focusable and refreshes its rect.
 *
 * ## Unmount cleanup
 * A measured non-layout focusable must drop its rect when it leaves the tree.
 * The cleanup lives in a SEPARATE unmount-only effect (deps `[id, unmeasure]`), NOT folded into the
 * depless measure effect above: that effect's cleanup runs on every render, so unmeasuring there
 * would unmeasure→remeasure each render and transiently drop the pane from the candidate set. This is
 * `id` accepts any {@link FocusId} so tests can use the same hook shape for ad hoc focusables.
 */
export function useMeasureFocus(id: FocusId, ref: React.RefObject<DOMElement | null>): void {
  const measure = useFocusStore((s) => s.measure);
  const unmeasure = useFocusStore((s) => s.unmeasure);
  const markPaneOpened = useFocusStore((s) => s.markPaneOpened);
  const markPaneClosed = useFocusStore((s) => s.markPaneClosed);
  // Subscribe to the live terminal size so resize refreshes measured non-layout focus rects.
  useTerminalSize();
  useEffect(() => {
    if (ref.current !== null) {
      measure(id, measureRect(ref.current));
    }
  });
  // Unmount-only: drop this focusable's rect when it leaves the tree. Deps `[id, unmeasure]` so it
  // does NOT run on every render (see the header note).
  useEffect(() => {
    markPaneOpened(id);
    return () => {
      markPaneClosed(id);
      unmeasure(id);
    };
  }, [id, markPaneClosed, markPaneOpened, unmeasure]);
}

export function usePaneFocusLifecycle(id: FocusId): void {
  const markPaneOpened = useFocusStore((s) => s.markPaneOpened);
  const markPaneClosed = useFocusStore((s) => s.markPaneClosed);
  useEffect(() => {
    markPaneOpened(id);
    return () => {
      markPaneClosed(id);
    };
  }, [id, markPaneClosed, markPaneOpened]);
}

/** A stable ref for a measured non-layout focusable `<Box>`. */
export function useFocusRef(): React.RefObject<DOMElement | null> {
  return useRef<DOMElement | null>(null);
}
