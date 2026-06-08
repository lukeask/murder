/**
 * React bindings for the input/focus backbone — the thin glue over the framework-agnostic panel and
 * focus stores (rule 4: the stores have no React; this is where React enters).
 *
 * Provides:
 *  - {@link InputStoresProvider} — carries the one panel store + one focus store to the tree.
 *  - {@link usePanelStore} / {@link useFocusStore} — selector hooks (referential stability per
 *    selector, like {@link useAppStore}).
 *  - {@link useEffectiveFocus} — the derived re-home invariant as a hook: returns the *effective*
 *    focus, recomputed whenever the intended focus or the visible set changes. A panel's highlight
 *    reads `useEffectiveFocus() === myId`.
 *  - {@link useMeasureFocus} — registers a component's measured rect with the focus store so
 *    directional nav has geometry (the Ink `measureElement` bridge at the component layer).
 *
 * These are the hooks C5's panels copy; nothing here calls the bus or owns input — the root input
 * loop lives in {@link useRootInput}.
 */

import type { DOMElement } from 'ink';
import { createContext, useContext, useEffect, useRef } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import {
  type FocusId,
  type FocusState,
  type FocusStoreApi,
  resolveFocus,
} from '../input/focusStore.js';
import type { Rect } from '../input/geometry.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { KeymapRegistryApi, KeymapRegistryState } from '../input/keymapRegistry.js';
import type { ModeState, ModeStoreApi } from '../input/modeStore.js';
import type { PanelState, PanelStoreApi } from '../input/panelStore.js';
import type { PanelId } from '../input/panels.js';

/** The input stores, carried as one context value so the provider wires them together once. */
export interface InputStores {
  readonly panels: PanelStoreApi;
  readonly focus: FocusStoreApi;
  readonly keymaps: KeymapRegistryApi;
  readonly modes: ModeStoreApi;
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

/**
 * Declare a panel's keymap to the registry for as long as the panel is mounted (rule 5: a panel
 * declares keys, never handles raw input). Re-registers when `keymap` changes; unregisters on
 * unmount. THE recipe for making a panel keyboard-driven — a panel calls this once with its
 * `Keymap` and its intent handler, and the root dispatcher routes matching keys to it when focused.
 */
export function usePanelKeymap<Intent extends string>(
  id: PanelId,
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
 * The effective focus, as a hook — the re-home invariant applied reactively. Subscribes to both the
 * intended focus and the visible set; recomputes {@link resolveFocus} when either changes, so a
 * panel going hidden re-homes the highlight to chat on the very next render with no imperative call.
 * This is the hook a panel uses to know if its border is the highlighted one.
 */
export function useEffectiveFocus(): FocusId {
  const intended = useFocusStore((s) => s.intendedId);
  const visible = usePanelStore((s) => s.visible);
  return resolveFocus(intended, visible);
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
 * and records the rect (the store dedupes unchanged rects). C5's panels call this once per panel.
 */
export function useMeasureFocus(id: FocusId, ref: React.RefObject<DOMElement | null>): void {
  const measure = useFocusStore((s) => s.measure);
  useEffect(() => {
    if (ref.current !== null) {
      measure(id, measureRect(ref.current));
    }
  });
}

/** A stable ref for a measured focusable `<Box>`. Sugar so a panel writes `const ref = useFocusRef()`
 * and spreads it, rather than importing `useRef`/`DOMElement` itself. */
export function useFocusRef(): React.RefObject<DOMElement | null> {
  return useRef<DOMElement | null>(null);
}
