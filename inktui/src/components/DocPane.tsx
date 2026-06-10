/**
 * DocPane — the read-only document viewer as a focusable **Stage pane** (plan / note / report).
 *
 * ## Why this used to be a mode, and why it isn't anymore (Phase 4b)
 *
 * The read-only doc viewer was an in-layout C7M *mode* ({@link ./DocViewMode.js}'s now-retired
 * `docViewMode`): `enter` on a list row entered a mode that painted the body into the Overlay's
 * `inlayout` slot and captured keys exclusively. That worked, but it fought the Stage model the rest
 * of this refactor settled on: a mode is a focus *takeover* (capture + restore), whereas a document
 * is just another **thing on the Stage you can focus and nav away from**. Phase 4a made the Stage
 * tile focusable panes with dynamic `stage:<...>` focus ids; a doc is the natural sibling of a chat
 * pane, not a modal. So the doc now renders as a {@link Pane} on the Stage (to the RIGHT of the chat
 * panes, stacking below when narrow — see {@link ./Stage.js}), reached by `alt+h/j/k/l` like any pane,
 * and `alt+f`/`alt+<n>` can pull focus away while it stays open (impossible under exclusive capture).
 *
 * What this buys us, all from the Phase 4a derived-focus invariant with NO new mechanism:
 *  - **focus id `stage:doc:<name>`** — a {@link StagePaneId}. The pane registers its measured rect via
 *    {@link useMeasureFocus}; on close it unmounts → `unmeasure` drops the rect → {@link resolveFocus}
 *    re-homes focus to chat (the same re-home a hidden panel gets). Nothing imperatively restores
 *    focus; "focused on a closed doc" is not a representable effective state.
 *  - **keys via the ONE root dispatcher** (rule 5): the focused doc pane declares a keymap to the
 *    registry (`j`/`k`/arrows/`space` to scroll, `enter`/`esc` to close). No `useInput`, no mode
 *    capture — layer 3 routes to the focused pane's keymap exactly as for the chat panes and panels.
 *  - **scroll is local `useState`** (rule 1) — the window offset lives in the pane, not a closure or
 *    the slice. The pane is keyed by the open doc's name in {@link ./Stage.js}, so switching docs
 *    remounts it and resets the offset (and re-registers the keymap under the new id) for free.
 *
 * ## What stayed
 *  - The `docView` slice + actions are UNCHANGED (open loads the body via the per-kind RPC; close
 *    clears it — rule 3). Only the *presentation* moved here from the mode.
 *  - `DOC_DIR` + the `.murder/<dir>/<name>.md` path derivation (shown in the title) stay.
 *  - {@link useDocView} stays the panels' single entry point: `const toggleDoc = useDocView(kind)` then
 *    `toggleDoc(name)` on the `open` intent — the three doc panels (Plans/Notes/Reports) import it
 *    from here (moved from `./DocViewMode.js`, which is retired). Its CONTRACT is unchanged; its body
 *    no longer enters a mode — it focuses the doc pane instead.
 */

import { Text } from 'ink';
import { type JSX, memo, useCallback, useMemo, useState } from 'react';
import { useAppStore, useAppStoreApi } from '../hooks/useAppStore.js';
import {
  useEffectiveFocus,
  useFocusRef,
  useInputStores,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import type { FocusId, StagePaneId } from '../input/focusStore.js';
import type { PanelKeymap } from '../input/keymap.js';
import { DOC_DIR, type DocKind, type OpenDoc } from '../store/docView/docViewSlice.js';
import { theme } from '../theme.js';
import { Pane } from './Pane.js';

/** The Stage focus id for an open document pane. The single place the `stage:doc:` scheme is minted
 * (the sibling of {@link ./Stage.js}'s `stage:chat:` helper), so the id format stays consistent. */
export function docPaneFocusId(name: string): StagePaneId {
  return `stage:doc:${name}`;
}

/** Lines shown at once in the doc pane's scroll window (the body scrolls within this window). Ported
 * from the retired mode's `VIEWPORT_LINES`; a real measured window is a later refinement (Ledger-style). */
const VIEWPORT_LINES = 14;
/** How many lines `j`/`k`/arrows scroll per press; `space` pages by a full window. */
const SCROLL_STEP = 1;

/** The `.murder/<dir>/<name>.md` path for the open doc — the title shown inline on the Pane's border
 * and the same path the spawn wizard references (derived identically in {@link ./App.js}). */
export function docPath(open: OpenDoc): string {
  return `.murder/${DOC_DIR[open.kind]}/${open.name}.md`;
}

/** The doc pane's intents: scroll the body window, or close the doc. `enter`/`esc` both close (the
 * old mode treated `enter`-on-shown as "minimise", which closes the slice — same effect). */
type DocIntent = 'close' | 'scrollDown' | 'scrollUp' | 'pageDown';

// ---------------------------------------------------------------------------
// StageDocPane
// ---------------------------------------------------------------------------

/**
 * The open document as a focusable Stage {@link Pane}. Owns its scroll window (`useState`, rule 1),
 * declares its scroll/close keymap to the registry ONLY while focused (so a blurred doc pane doesn't
 * claim `j`/`enter`), and flips the Pane's focus color when it holds the effective focus. The Pane's
 * outer box carries the focus ref so {@link useMeasureFocus} registers the whole bordered region's
 * rect for directional nav — the same recipe as the chat panes and the list panels.
 *
 * Pure function of the `docView` slice (rule 1/2 — the body/status come from the slice, formatted as
 * raw markdown lines here only for windowing; close goes through the slice action, rule 3). Mounted
 * by {@link ./Stage.js} only when `docView.open !== null`, and keyed there by the doc name so opening
 * a different doc remounts this (resetting scroll + re-registering the keymap under the new id).
 */
export const StageDocPane = memo(function StageDocPane({
  open,
}: {
  readonly open: OpenDoc;
}): JSX.Element {
  const focusId: FocusId = docPaneFocusId(open.name);
  const body = useAppStore((s) => s.docView.body);
  const status = useAppStore((s) => s.docView.status);
  const error = useAppStore((s) => s.docView.error);
  const closeAction = useAppStore((s) => s.actions.docView.close);

  // Focus highlight + rect registration — the panel recipe with the Stage-pane focus id. On unmount
  // (close) useMeasureFocus drops the rect → resolveFocus re-homes focus to chat.
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === focusId;
  useMeasureFocus(focusId, ref);

  // Local scroll offset (rule 1): the first visible line. Clamped to the body length on render so a
  // shorter body can't strand the window past its end.
  const [scroll, setScroll] = useState(0);
  const lines = body !== null ? body.split('\n') : [];
  const maxScroll = Math.max(lines.length - VIEWPORT_LINES, 0);
  const clamped = Math.min(scroll, maxScroll);
  const window = lines.slice(clamped, clamped + VIEWPORT_LINES);

  // Scroll/close keymap (rule 5: declared, not handled). `alt+j`/`alt+k` are the global directional
  // layer (pane-to-pane), so they never reach here — plain `j`/`k`/arrows/`space` are this pane's.
  // Registered only while focused; memoised on the scroll bound + close action so the handler closes
  // over a fresh `maxScroll` without re-registering every render.
  const keymap: PanelKeymap<DocIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { key: { return: true } }, intent: 'close', description: 'close' },
        { chord: { key: { escape: true } }, intent: 'close', description: 'close' },
        { chord: { input: 'j' }, intent: 'scrollDown', description: 'scroll down' },
        { chord: { key: { downArrow: true } }, intent: 'scrollDown', description: 'scroll down' },
        { chord: { input: 'k' }, intent: 'scrollUp', description: 'scroll up' },
        { chord: { key: { upArrow: true } }, intent: 'scrollUp', description: 'scroll up' },
        { chord: { input: ' ' }, intent: 'pageDown', description: 'page down' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'close':
            // Close via the slice action (rule 3) — unmounting the pane re-homes focus to chat.
            closeAction();
            return;
          case 'scrollDown':
            setScroll((s) => Math.min(s + SCROLL_STEP, maxScroll));
            return;
          case 'scrollUp':
            setScroll((s) => Math.max(s - SCROLL_STEP, 0));
            return;
          case 'pageDown':
            setScroll((s) => Math.min(s + VIEWPORT_LINES, maxScroll));
            return;
          default:
            return intent satisfies never;
        }
      },
    }),
    [maxScroll, closeAction],
  );
  usePanelKeymap(focusId, focused ? keymap : EMPTY_KEYMAP);

  return (
    <Pane
      ref={ref}
      title={docPath(open)}
      focused={focused}
      titleExtra={<Text dimColor>[doc]</Text>}
    >
      {status === 'error' && error !== null && <Text color={theme.error}>{`error: ${error}`}</Text>}
      {status === 'loading' && <Text dimColor>loading…</Text>}
      {clamped > 0 && <Text dimColor>…</Text>}
      {status === 'ready' && lines.length === 0 ? (
        <Text dimColor>(empty document)</Text>
      ) : (
        window.map((line, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed (markdown can repeat; the windowed index is the stable identity for the visible slice).
          <Text key={clamped + index} wrap="truncate">
            {line === '' ? ' ' : line}
          </Text>
        ))
      )}
      {clamped + VIEWPORT_LINES < lines.length && <Text dimColor>…</Text>}
    </Pane>
  );
});

/** A stable empty keymap for a blurred doc pane (so the registration identity doesn't churn). Typed
 * `PanelKeymap<DocIntent>` so the `focused ? keymap : EMPTY_KEYMAP` ternary is one type. */
const EMPTY_KEYMAP: PanelKeymap<DocIntent> = { keymap: [], onIntent() {} };

// ---------------------------------------------------------------------------
// useDocView hook
// ---------------------------------------------------------------------------

/**
 * Hook for the doc panels (Plans/Notes/Reports) — keeps a panel's `'open'` intent at the same
 * abstraction level as the rest of its keymap. Returns a `toggleDoc(name)` callback:
 *  - **open:** dispatch the existing `docView.open` action (load the body — rule 3) AND focus the doc
 *    pane (`focus.focus('stage:doc:'+name)`). It does NOT enter a mode anymore — the doc is a Stage
 *    pane ({@link StageDocPane}). The focus intent persists while the pane mounts + measures (it
 *    momentarily resolves to chat until the rect lands, then snaps to the doc — the same path a chat
 *    pane takes when a crow is favorited).
 *  - **toggle closed:** if the SAME doc is already open, dispatch `docView.close`. That unmounts the
 *    pane; its `useMeasureFocus` cleanup drops the rect and {@link resolveFocus} re-homes focus to
 *    chat (the derived re-home invariant — no imperative focus restore).
 *
 * Rule 3: open/close go through the `docView` store actions; the panel calls `toggleDoc(name)` only.
 */
export function useDocView(kind: DocKind): (name: string) => void {
  const { focus } = useInputStores();
  const store = useAppStoreApi();
  const openAction = useAppStore((s) => s.actions.docView.open);
  const closeAction = useAppStore((s) => s.actions.docView.close);

  return useCallback(
    (name: string) => {
      const current = store.getState().docView.open;
      // Toggle: open on the already-open doc closes it (focus re-homes to chat on unmount).
      if (current !== null && current.kind === kind && current.name === name) {
        closeAction();
        return;
      }
      // Open: load the body (rule 3: the action is the only bus caller), then focus the doc pane.
      void openAction(kind, name);
      focus.getState().focus(docPaneFocusId(name));
    },
    [focus, store, kind, openAction, closeAction],
  );
}
