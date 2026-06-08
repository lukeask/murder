/**
 * DocViewMode — the read-only document viewer as an **in-layout C7M mode** (plan / note / report).
 *
 * `enter` on a highlighted plan/note/report toggles showing its doc in the TUI (spec › Starring &
 * document toggling). This is the read-only sibling of {@link ./TicketEditorMode.js}: same in-layout
 * C7M mode recipe (declare a Mode → `enter()` from the panel → keymap captures via the ONE root
 * dispatcher → dismiss restores focus), but it edits nothing — no vim, no `onUncaptured`, no save.
 * It just paints the fetched body inside the Overlay's `inlayout` slot so the surrounding panels stay
 * visible (no `$EDITOR`-blank).
 *
 * ## Toggle semantics (spec: "enter on a shown doc minimizes it and returns highlight to its list;
 * enter again restores")
 *
 *  - `enter` on a list row (handled by the *panel's* keymap) opens the doc: loads it via the
 *    docView action and enters this mode. C7M saves the panel focus.
 *  - `enter` (or `esc`) while the doc is shown → this mode's `dismiss`/`close` intent: exit the mode
 *    (C7M restores focus to the originating list) and close the docView slice. "Enter again restores"
 *    is simply the panel's `enter` opening it afresh — the docView slice still remembers nothing once
 *    closed, so re-opening re-loads, which is correct for a read-only view.
 *
 * ## ONE input owner (rule 5)
 *
 * No second `useInput`. Every key the viewer handles arrives through the single root dispatcher: the
 * declared keymap (`enter`/`esc` to close, `j`/`k`/arrows to scroll) fires the mode's `onIntent`.
 * Scroll offset is mutable closure state (the {@link ./TicketEditorMode.js}/`NewPlanModal` pattern),
 * mutated by the handlers then `refresh()`-ed; the body itself lives in the `docView` slice and is
 * read via the store (rule 3 — the mode never calls the bus; the open/close go through actions).
 */

import { Box, Text } from 'ink';
import { type JSX, useCallback } from 'react';
import { useAppStore, useAppStoreApi } from '../hooks/useAppStore.js';
import { useInputStores } from '../hooks/useInputStores.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import { DOC_DIR, type DocKind } from '../store/docView/docViewSlice.js';
import type { AppStoreApi } from '../store/store.js';

/** Stable mode id so re-entry (for `refresh()`) is idempotent (the modeStore pattern). */
export const DOC_VIEW_MODE_ID = 'doc-view';

/** Lines shown at once in the in-layout viewport (the body scrolls within this window). */
const VIEWPORT_LINES = 14;

/** The viewer's intent union — only navigation + dismiss; nothing edits. */
type DocViewIntent = 'close' | 'scrollDown' | 'scrollUp' | 'pageDown';

/** Mutable closure state — not React state (the {@link ./TicketEditorMode.js} mode pattern). */
interface DocViewUiState {
  scroll: number;
}

/**
 * Build the doc-view {@link Mode}. `presentation: 'inlayout'` keeps surrounding panels visible. The
 * mode is the single input owner (rule 5) — its declared keymap handles every key; there is no
 * second `useInput` and no `onUncaptured` (nothing free-text). `onClose` exits the docView slice
 * (passed in by the opener so close stays an action-dispatch, rule 3).
 *
 * Copies {@link ./TicketEditorMode.js}'s in-layout recipe; the difference is read-only (no edit
 * buffer, no vim, no save) — so it is the simplest possible inlayout mode and a clean reference for
 * any future read-only in-layout surface.
 */
export function docViewMode(
  modes: ModeStoreApi,
  store: AppStoreApi,
  options: { readonly onClose: () => void },
): Mode<DocViewIntent> {
  const id = DOC_VIEW_MODE_ID;
  const ui: DocViewUiState = { scroll: 0 };

  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  /** Total lines in the open doc's body (for clamping scroll). */
  function lineCount(): number {
    const body = store.getState().docView.body;
    return body !== null ? body.split('\n').length : 0;
  }

  function clampScroll(delta: number): void {
    const maxScroll = Math.max(lineCount() - VIEWPORT_LINES, 0);
    ui.scroll = Math.min(Math.max(ui.scroll + delta, 0), maxScroll);
  }

  return {
    id,
    presentation: 'inlayout',
    // No passThrough: the viewer captures everything except the global ctrl-chords (which layer 1
    // handles ahead of layer 0's swallow only because they carry ctrl — see dispatcher.ts). A plain
    // `enter`/`esc`/`j`/`k` here is the viewer's.
    keymap: [
      { chord: { key: { return: true } }, intent: 'close', description: 'minimize' },
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
          // Exit first (C7M restores the list focus), then close the slice (rule 3 — via action).
          modes.getState().exit(id);
          options.onClose();
          return;
        case 'scrollDown':
          clampScroll(1);
          refresh();
          return;
        case 'scrollUp':
          clampScroll(-1);
          refresh();
          return;
        case 'pageDown':
          clampScroll(VIEWPORT_LINES);
          refresh();
          return;
        default:
          return intent satisfies never;
      }
    },
    render: () => <DocViewSurface scroll={ui.scroll} />,
  };
}

// ── Viewer surface (pure presentation, rule 1) ─────────────────────────────────────────────────

/**
 * The doc viewer surface — a pure function of the `docView` slice plus the mode's scroll closure.
 * Reads the open doc + body + status from the slice; paints a scrolled window of the body inside the
 * Overlay's inlayout slot (panels stay visible above). No input capture (the mode owns input).
 */
function DocViewSurface({ scroll }: { readonly scroll: number }): JSX.Element {
  const open = useAppStore((s) => s.docView.open);
  const body = useAppStore((s) => s.docView.body);
  const status = useAppStore((s) => s.docView.status);
  const error = useAppStore((s) => s.docView.error);

  const lines = body !== null ? body.split('\n') : [];
  const window = lines.slice(scroll, scroll + VIEWPORT_LINES);
  const dir = open !== null ? DOC_DIR[open.kind] : '';
  const path = open !== null ? `.murder/${dir}/${open.name}.md` : '';

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="cyan"
      paddingX={1}
      paddingY={0}
      marginTop={1}
    >
      <Box flexDirection="row" columnGap={2}>
        <Text bold color="cyan">
          {'[doc]'}
        </Text>
        {open !== null ? <Text bold>{path}</Text> : <Text dimColor>{'(no document)'}</Text>}
      </Box>

      {status === 'error' && error !== null && <Text color="red">{`error: ${error}`}</Text>}
      {status === 'loading' && <Text dimColor>{'loading…'}</Text>}

      <Box flexDirection="column" height={VIEWPORT_LINES} marginTop={0}>
        {status === 'ready' && lines.length === 0 ? (
          <Text dimColor>{'(empty document)'}</Text>
        ) : (
          window.map((line, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: body lines are position-keyed (markdown can repeat; the windowed index is the stable identity for the visible slice).
            <Text key={scroll + index} wrap="truncate">
              {line === '' ? ' ' : line}
            </Text>
          ))
        )}
      </Box>

      <Box flexDirection="row" columnGap={2}>
        <Text dimColor>
          {`j/k:scroll  space:page  enter/esc:close  (${Math.min(scroll + VIEWPORT_LINES, lines.length)}/${lines.length})`}
        </Text>
      </Box>
    </Box>
  );
}

// ── useDocView hook ─────────────────────────────────────────────────────────────────────────────

/**
 * Hook for the doc panels (Plans/Notes/Reports) — encapsulates the doc-view mode lifecycle so a
 * panel's `'open'` intent stays at the same abstraction level as the rest of its keymap. Returns a
 * `toggleDoc(name)` callback: if the same doc is already open, it closes it (the toggle); otherwise
 * it opens that doc (loads + enters the mode).
 *
 * Rule 3: open/close go through the `docView` store actions; the panel calls `toggleDoc(name)` only.
 */
export function useDocView(kind: DocKind): (name: string) => void {
  const { modes } = useInputStores();
  const store = useAppStoreApi();
  const openAction = useAppStore((s) => s.actions.docView.open);
  const closeAction = useAppStore((s) => s.actions.docView.close);

  return useCallback(
    (name: string) => {
      const current = store.getState().docView.open;
      // Toggle: enter on the already-open doc minimises it.
      if (current !== null && current.kind === kind && current.name === name) {
        modes.getState().exit(DOC_VIEW_MODE_ID);
        closeAction();
        return;
      }
      // Open: load the body (rule 3: action is the only bus caller), then enter the in-layout mode.
      void openAction(kind, name);
      modes.getState().enter(
        docViewMode(modes, store, {
          onClose() {
            closeAction();
          },
        }),
      );
    },
    [modes, store, kind, openAction, closeAction],
  );
}
