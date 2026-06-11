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

import { Box, type DOMElement, measureElement, Text } from 'ink';
import { type JSX, memo, useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useAppStore, useAppStoreApi } from '../hooks/useAppStore.js';
import { type GotoIntent, useGotoLine } from '../hooks/useGotoLine.js';
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
import { useTheme } from '../theme/themeStore.js';
import { Pane } from './Pane.js';

/** The Stage focus id for an open document pane. The single place the `stage:doc:` scheme is minted
 * (the sibling of {@link ./Stage.js}'s `stage:chat:` helper), so the id format stays consistent. */
export function docPaneFocusId(name: string): StagePaneId {
  return `stage:doc:${name}`;
}

/** Fallback window height before the fill box has been measured (first paint, or a sizeless non-TTY
 * test render where Yoga reports 0) — the old fixed `VIEWPORT_LINES`. Once {@link measureElement}
 * reports a real height the measured value drives the window (the Ledger fill-box pattern). */
const FALLBACK_HEIGHT = 14;
/** How many lines `j`/`k`/arrows scroll per press; `space`/`pageUp` page by a full window. */
const SCROLL_STEP = 1;

// ---------------------------------------------------------------------------
// Pure window + scrollbar math (the test seam, mirroring Ledger's computeWindow)
// ---------------------------------------------------------------------------

/** The visible slice of `lines` for a scroll offset, clamped so a short body can't strand the window
 * past its end. `height` is the measured (or fallback) number of rows the fill box can show. */
export function computeDocWindow(
  total: number,
  scroll: number,
  height: number,
): { start: number; end: number; maxScroll: number } {
  const h = Math.max(height, 1);
  const maxScroll = Math.max(total - h, 0);
  const start = Math.min(Math.max(scroll, 0), maxScroll);
  return { start, end: start + h, maxScroll };
}

/** Scrollbar thumb geometry for a window of `height` rows over `total` content lines at `scroll`.
 * `null` when the content fits (no scrollbar drawn). Thumb size is proportional to the visible
 * fraction (min 1 cell); its offset maps the scroll fraction across the free track. */
export function computeScrollThumb(
  total: number,
  scroll: number,
  height: number,
): { size: number; offset: number } | null {
  const h = Math.max(height, 1);
  if (total <= h) {
    return null;
  }
  const maxScroll = total - h;
  const size = Math.max(1, Math.round((h * h) / total));
  const clampedScroll = Math.min(Math.max(scroll, 0), maxScroll);
  const offset = maxScroll > 0 ? Math.round((clampedScroll / maxScroll) * (h - size)) : 0;
  return { size, offset: Math.min(offset, h - size) };
}

/** The `.murder/<dir>/<name>.md` path for the open doc — the title shown inline on the Pane's border
 * and the same path the spawn wizard references (derived identically in {@link ./App.js}). */
export function docPath(open: OpenDoc): string {
  return `.murder/${DOC_DIR[open.kind]}/${open.name}.md`;
}

/** The doc pane's intents: scroll the body window, or close the doc. `enter`/`esc` both close (the
 * old mode treated `enter`-on-shown as "minimise", which closes the slice — same effect). */
type DocIntent = 'close' | 'scrollDown' | 'scrollUp' | 'pageDown' | 'pageUp' | 'spawnPlanner';

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
  const theme = useTheme();
  const focusId: FocusId = docPaneFocusId(open.name);
  const body = useAppStore((s) => s.docView.body);
  const status = useAppStore((s) => s.docView.status);
  const error = useAppStore((s) => s.docView.error);
  const closeAction = useAppStore((s) => s.actions.docView.close);
  const spawnPlanner = useAppStore((s) => s.actions.plans.spawnPlanner);

  // Focus highlight + rect registration — the panel recipe with the Stage-pane focus id. On unmount
  // (close) useMeasureFocus drops the rect → resolveFocus re-homes focus to chat.
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === focusId;
  useMeasureFocus(focusId, ref);

  // Local scroll offset (rule 1): the first visible line. Clamped to the body length on render so a
  // shorter body can't strand the window past its end.
  const [scroll, setScroll] = useState(0);
  const lines = body !== null ? body.split('\n') : [];

  // Measured window height — the Ledger fill-box pattern. The content fill box (below) is row-count-
  // independent (flexGrow, NOT flexShrink), so `measureElement` reports the room we HAVE, not the rows
  // we drew; the guarded setter writes only on a real change so a stable layout settles in one extra
  // render and never loops. `0` (first paint / sizeless test render) falls back to FALLBACK_HEIGHT.
  const boxRef = useRef<DOMElement | null>(null);
  const [measuredHeight, setMeasuredHeight] = useState(0);
  useLayoutEffect(() => {
    if (boxRef.current === null) {
      return;
    }
    const { height } = measureElement(boxRef.current);
    if (height !== measuredHeight) {
      setMeasuredHeight(height);
    }
  });
  const effectiveHeight = measuredHeight > 0 ? measuredHeight : FALLBACK_HEIGHT;

  const {
    start: clamped,
    end,
    maxScroll,
  } = computeDocWindow(lines.length, scroll, effectiveHeight);
  const window = lines.slice(clamped, end);
  const thumb = computeScrollThumb(lines.length, clamped, effectiveHeight);

  // `g<digits>` go-to-line (the shared gesture — see useGotoLine): each digit jumps live, putting the
  // 1-based target line at the top of the window, clamped to the scroll range.
  const jump = useCallback((line: number) => setScroll(Math.min(line - 1, maxScroll)), [maxScroll]);
  const goto = useGotoLine(jump);

  // Scroll/close keymap (rule 5: declared, not handled). `alt+j`/`alt+k` are the global directional
  // layer (pane-to-pane), so they never reach here — plain `j`/`k`/arrows/`space` are this pane's.
  // Registered only while focused; memoised on the scroll bound + close action so the handler closes
  // over a fresh `maxScroll` without re-registering every render. The goto entries are spread FIRST
  // so a live `g` capture's digits/`enter`/`esc` win over the pane's own chords (`enter` must end the
  // capture, not close the doc).
  const keymap: PanelKeymap<DocIntent | GotoIntent> = useMemo(
    () => ({
      keymap: [
        ...goto.entries,
        { chord: { key: { return: true } }, intent: 'close', description: 'close' },
        { chord: { key: { escape: true } }, intent: 'close', description: 'close' },
        { chord: { input: 'j' }, intent: 'scrollDown', description: 'scroll down' },
        { chord: { key: { downArrow: true } }, intent: 'scrollDown', description: 'scroll down' },
        { chord: { input: 'k' }, intent: 'scrollUp', description: 'scroll up' },
        { chord: { key: { upArrow: true } }, intent: 'scrollUp', description: 'scroll up' },
        { chord: { input: ' ' }, intent: 'pageDown', description: 'page down' },
        { chord: { input: 'b' }, intent: 'pageUp', description: 'page up' },
        // `p` spawns a planning agent over the staged PLAN — the same intent the Plans panel binds
        // (both route through `actions.plans.spawnPlanner`). Declared only for `kind === 'plan'`:
        // the entry is kind-gated DATA in the one shared doc pane, not a forked variant — a staged
        // note/report simply doesn't declare the key.
        ...(open.kind === 'plan'
          ? [
              {
                chord: { input: 'p' },
                intent: 'spawnPlanner',
                description: 'spawn planner',
              } as const,
            ]
          : []),
      ],
      onIntent(intent) {
        // Goto intents are consumed by the gesture; any OTHER intent ends a live capture and then
        // acts with its normal meaning (the useGotoLine contract). `handle` returning false proves
        // the intent is the pane's own, so the narrowing cast below is sound.
        if (goto.handle(intent)) {
          return;
        }
        goto.clear();
        const docIntent = intent as DocIntent;
        switch (docIntent) {
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
            setScroll((s) => Math.min(s + effectiveHeight, maxScroll));
            return;
          case 'pageUp':
            setScroll((s) => Math.max(s - effectiveHeight, 0));
            return;
          case 'spawnPlanner':
            void spawnPlanner(open.name);
            return;
          default:
            return docIntent satisfies never;
        }
      },
    }),
    // `open` is per-mount constant (the Stage keys this pane by doc name), so it isn't a churn risk.
    [maxScroll, effectiveHeight, closeAction, spawnPlanner, open, goto],
  );
  usePanelKeymap(focusId, focused ? keymap : EMPTY_KEYMAP);

  return (
    // The right border doubles as the scroll track (the Pane's `scrollbar` prop) — no separate
    // scrollbar column, so the content keeps the default right gutter.
    <Pane
      ref={ref}
      title={docPath(open)}
      focused={focused}
      titleExtra={
        <>
          <Text dimColor>[doc]</Text>
          {/* Live `g<digits>` capture indicator — shows the line number as it is typed. */}
          {goto.pending !== null && <Text color={theme.warning}>{` g${goto.pending}`}</Text>}
        </>
      }
      scrollbar={{ height: effectiveHeight, thumb }}
    >
      {/* Fill box: sizes to the Pane's inner content area regardless of line count (flexGrow + clip),
          so `measureElement` reports the room we HAVE, not the rows we drew (the Ledger pattern). */}
      <Box ref={boxRef} flexDirection="column" flexGrow={1} minHeight={0} overflow="hidden">
        {status === 'error' && error !== null && (
          <Text color={theme.error}>{`error: ${error}`}</Text>
        )}
        {status === 'loading' && <Text dimColor>loading…</Text>}
        {status === 'ready' && lines.length === 0 ? (
          <Text dimColor>(empty document)</Text>
        ) : (
          window.map((line, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed (markdown can repeat; the windowed index is the stable identity for the visible slice).
            <Text key={clamped + index}>{line === '' ? ' ' : line}</Text>
          ))
        )}
      </Box>
    </Pane>
  );
});

/** A stable empty keymap for a blurred doc pane (so the registration identity doesn't churn). Typed
 * to the pane's full intent union so the `focused ? keymap : EMPTY_KEYMAP` ternary is one type. */
const EMPTY_KEYMAP: PanelKeymap<DocIntent | GotoIntent> = { keymap: [], onIntent() {} };

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
