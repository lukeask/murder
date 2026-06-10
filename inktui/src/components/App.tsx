/**
 * App — the shell that composes the whole TUI, and the composition skeleton every future panel
 * slots into. It is the one place the providers are wired and the single {@link useRootInput} loop is
 * installed; below it, components are pure functions of their slice (rule 1) and never touch input or
 * the bus directly.
 *
 * Layout (the plan's "Approach › Layout", always-visible chrome around two toggleable regions):
 *
 *   ┌ TopBar ──────────────────────────────┐   toggled-panel labels (plans₁ … crows₀)
 *   │ ┌ left region ┐ ┌ right region ┐      │   left  visible iff any of 1–4 are on
 *   │ │ 1 2 3 4     │ │ 9 usage · 0  │      │   right visible iff 9 or 0 are on
 *   │ └─────────────┘ └──────────────┘      │   (usage sits to the left of crows)
 *   │ ChatInput (always visible)            │   the focus home — never toggled off
 *   └ BottomBar ───────────────────────────┘   contextual hints from the focused keymap
 *
 * Wiring, top to bottom:
 *  - {@link App} takes the two store bundles as props (constructed at the entrypoint with the real
 *    `BusClient`/input stores, or by a test with fakes — rule 4: the bus is injected, never imported
 *    by a component). It mounts both context providers and renders {@link Shell} inside them.
 *  - {@link Shell} runs *inside* the providers (so it may read the stores) and installs the single
 *    root input loop with {@link useRootInput}. It is the only caller of that hook.
 *
 * Region/panel pattern: each region maps its panel ids to components, rendering a panel only when it
 * is in the visible set. Every panel id now resolves to a real component (C11 filled the last
 * placeholder, `plans`, with {@link PlansPanel}); the flat {@link RosterPanel} is kept aside as the
 * copyable reference implementation. A later chunk that adds a panel swaps its `renderPanel` case for
 * a real panel copied from `RosterPanel`/`CrowsPanel` — *this file changes only at that case*. That
 * is the skeleton's contract: stable composition, panels filled in independently.
 *
 * C13: `Shell` now wires the `spawn` deferred handler so `ctrl+s` opens the spawn wizard. The
 * spawn handler reads the focus + app store at invocation time to derive the spawn context
 * (focused doc → reference-by-path). See {@link deriveSpawnContext} for the C11 seam note.
 */

import { Box, type DOMElement, measureElement } from 'ink';
import { type JSX, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { BusClient } from '../bus/BusClient.js';
import { AppStoreProvider, useAppStore, useAppStoreApi } from '../hooks/useAppStore.js';
import { useBodyLayout } from '../hooks/useBodyLayout.js';
import { BusClientProvider, useBusClient } from '../hooks/useBusClient.js';
import {
  type InputStores,
  InputStoresProvider,
  useInputStores,
  useModeStore,
} from '../hooks/useInputStores.js';
import { useOrientation } from '../hooks/useOrientation.js';
import { type TerminalEvents, useRootInput } from '../hooks/useRootInput.js';
import { useTerminalSize } from '../hooks/useTerminalSize.js';
import type { ActionId } from '../input/bindings.js';
import { expandSpans, spanIds } from '../input/chatInputStore.js';
import { readClipboardImage } from '../input/clipboardImage.js';
import type { ChatInputHandler } from '../input/dispatcher.js';
import { selectActiveMode } from '../input/modeStore.js';
import type { PanelId } from '../input/panels.js';
import type { UsageTier } from '../layout/budget.js';
import { selectActiveAgentId } from '../selectors/conversationsSelectors.js';
import { createHarnessModelsActions } from '../store/dialogs/harnessModelsActions.js';
import { createSpawnActions } from '../store/dialogs/spawnActions.js';
import { createWorktreeOptionsActions } from '../store/dialogs/worktreeOptionsActions.js';
import { DOC_DIR } from '../store/docView/docViewSlice.js';
import {
  createImageDraftStore,
  type ImageDraftStoreApi,
} from '../store/imageDraft/imageDraftStore.js';
import type { SettingsModifier } from '../store/settings/settingsSlice.js';
import type { AppStoreApi } from '../store/store.js';
import { toastStore } from '../store/toast/toastStore.js';
import { BottomBar, useBottomBarLines } from './BottomBar.js';
import { ChatInput } from './ChatInput.js';
import { CrowsPanel } from './CrowsPanel.js';
import { NotesPanel } from './NotesPanel.js';
import { Overlay, presentationHidesLayout } from './Overlay.js';
import { PlansPanel } from './PlansPanel.js';
import { Rail } from './Rail.js';
import { ReportsPanel } from './ReportsPanel.js';
import type { SpawnContext } from './SpawnWizardModal.js';
import { spawnWizardMode } from './SpawnWizardModal.js';
import { Stage } from './Stage.js';
import { TicketsPanel } from './TicketsPanel.js';
import { Toast } from './Toast.js';
import { TopBar } from './TopBar.js';
import { UsagePanel } from './UsagePanel.js';

/** The left region's panels in screen order (plan: `1` plans · `2` notes · `3` reports · `4`
 * tickets). The right region (plan: `9` usage · `0` crows — usage left of crows). One ordered list
 * per region so the shell renders panels left-to-right without re-deriving order from {@link PANELS}. */
const LEFT_PANELS: readonly PanelId[] = ['plans', 'notes', 'reports', 'tickets'];
const RIGHT_PANELS: readonly PanelId[] = ['usage', 'crows'];

/**
 * Render one panel by id — the single dispatch from a {@link PanelId} to its component. Every id
 * resolves to a real panel (`plans`, `notes`, `reports`, `tickets`, `usage`, `crows`); a future
 * chunk that adds a panel adds its `case` here, copying `RosterPanel`/`CrowsPanel`, and nothing else
 * in the shell changes. Defined as a function (not inline) so the swap is one localised edit.
 *
 * The `usageTier` (L4, R9) is threaded in so the {@link UsagePanel} renders the mini/medium/large
 * gauge variant the right-rail width allots it — the budget engine ({@link useBodyLayout}) classifies
 * the rail width into a tier and the Shell passes it here, the single place a layout signal reaches a
 * panel. No other panel needs it, so it's a plain extra argument rather than threading the whole
 * {@link BodyLayout} through.
 */
function renderPanel(id: PanelId, usageTier: UsageTier): JSX.Element {
  switch (id) {
    case 'crows':
      // C9: CrowsPanel replaces the RosterPanel reference implementation here. The original
      // RosterPanel remains as the copy-reference; only this `case` changes.
      // Phase 4a: the favorited-crow chat-history panes MOVED out of here into the {@link ./Stage.js}
      // center region (they used to stack below CrowsPanel via the retired CrowChatPanel). The crows
      // panel `0` is now just the roster pane; the chat panes live in the Stage and are reached by
      // hjkl directional focus, not the crows toggle.
      return <CrowsPanel />;
    case 'plans':
      // C11: PlansPanel fills the last placeholder — parent-plan indentation + star + doc-view.
      return <PlansPanel />;
    case 'notes':
      return <NotesPanel />;
    case 'reports':
      return <ReportsPanel />;
    case 'tickets':
      return <TicketsPanel />;
    case 'usage':
      // C9: UsagePanel fills the right-region slot. Usage sits to the LEFT of crows because
      // RIGHT_PANELS = ['usage', 'crows'] (App.tsx line 59) — array order = left-to-right.
      // L4: the gauge tier is the largest variant the right-rail width allows (R9).
      return <UsagePanel tier={usageTier} />;
    default:
      return id satisfies never;
  }
}

/**
 * Derive the spawn context from the app store at `ctrl+s` invocation time. Returns a
 * {@link SpawnContext} when a document (plan / note / report) is the **focused doc**, else `null`
 * (no context step shown in the wizard).
 *
 * ## Focused-doc = the OPEN doc-view (C11 — replaces C13's first-row proxy)
 * The spec's "focused-doc-wins — list row or opened doc widget alike" resolves cleanly here: when
 * `ctrl+s` fires the spawn wizard, focus is on **chat** (that is the only focus where `ctrl+s`
 * spawns rather than stars — see dispatcher.ts), so there is no live list cursor to read. The
 * "focused doc" is therefore whatever doc the user last *opened* in the doc-view Stage pane
 * ({@link ./DocPane.js}), held in the `docView` slice. Reading it replaces C13's first-row proxy
 * with the real selected doc, and needs no lifted cursor — the open doc is already shared state with
 * a real identity (`{ kind, name }`), so panel cursors stay local (rule 1).
 *
 * If no doc is open, there is no focused doc and the wizard skips the context step.
 *
 * ## Reference-by-path (locked mechanism)
 * The returned `path` is `.murder/<dir>/<name>.md` (the dir from {@link DOC_DIR}). The wizard builds
 * `"Please read ${path} before starting."` — the rogue reads the file, not an inlined body.
 */
export function deriveSpawnContext(appStore: AppStoreApi): SpawnContext | null {
  const open = appStore.getState().docView.open;
  if (open === null) {
    return null;
  }
  const dir = DOC_DIR[open.kind];
  return { title: open.name, path: `.murder/${dir}/${open.name}.md` };
}

/**
 * Build the **persistent chat-input handler** (C11, part F) over the chat buffer store + the app
 * store. This is the layer-2 chat short-circuit's consumer (see {@link ../input/dispatcher.js}'s
 * {@link ChatInputHandler}): chat is the permanent focus home, so it is NOT a transient mode — the
 * buffer lives in `chatInput` and this handler edits it on each keystroke and sends on Enter.
 *
 * Routing (rule 3): on Enter, the active agent id is derived from the conversations + roster slices
 * via {@link selectActiveAgentId} (the discriminated-union identity — no conversation-id parsing),
 * and the message is sent through `actions.conversations.send` (the sole bus caller). The buffer is
 * cleared after dispatch. An empty buffer or no active agent makes Enter a no-op (still consumed, so
 * the dispatcher reports handled — Enter belongs to the chat field).
 *
 * Pure factory over the two stores: no React, exported so a test drives the keystroke→send path
 * directly. Global ctrl-chords never reach here (layer 1 preempts layer 2), so this can treat every
 * event it sees as chat text — it returns `false` only for control keys it does not own (so an
 * unhandled control key isn't falsely reported as consumed).
 */
export function makeChatInputHandler(
  chatInput: InputStores['chatInput'],
  appStore: AppStoreApi,
  imageDraft: ImageDraftStoreApi,
): ChatInputHandler {
  return {
    handleKey(input, key): boolean {
      // Enter → send the buffer to the active agent, then clear. Always consumed (Enter is chat's).
      //
      // F9 submit-while-uploading policy (per-state, because the states differ by *path availability*
      // — the full path only comes back from the server on resolve):
      //  - any span still `uploading` → BLOCK the send (no path yet to expand): info toast, keep the
      //    buffer intact so nothing is lost. Least-surprising: the user just waits a beat.
      //  - `done` spans  → expanded to `![image]({path})` via `expandSpans`.
      //  - `failed` spans → STRIPPED from the outgoing markdown (their id is absent from `pathsById`),
      //    so a failed upload never traps the buffer; the in-text marker the user saw is simply dropped.
      if (key.return === true) {
        const buffer = chatInput.getState().text;
        if (buffer.length > 0) {
          const draftState = imageDraft.getState();
          const ids = spanIds(buffer);
          const stillUploading = ids.some((id) => draftState.drafts[id]?.status === 'uploading');
          if (stillUploading) {
            // Block: leave the buffer untouched and tell the user why (consumed — Enter is chat's).
            toastStore.getState().push('image still uploading…', { ttlMs: 2000 });
            return true;
          }
          const message = expandSpans(buffer, draftState.pathsById());
          if (message.length > 0) {
            const agentId = selectActiveAgentId(
              appStore.getState().conversations,
              appStore.getState().roster,
              appStore.getState().favorites,
            );
            if (agentId !== null) {
              void appStore.getState().actions.conversations.send(agentId, message);
            }
          }
          // Drop the drafts now that they're expanded/stripped — the buffer is clearing.
          for (const id of ids) {
            imageDraft.getState().drop(id);
          }
        }
        chatInput.getState().clear();
        return true;
      }
      // ctrl+v → image paste. Read the clipboard client-side; if it holds an image, mint a draft +
      // wrap a span into the buffer (the label→file binding is known instantly — see imageDraftStore).
      // If no image, decline (return false): plain-text paste is the terminal's job, not ours.
      if (key.ctrl === true && input === 'v') {
        void readClipboardImage().then((image) => {
          if (image === null) {
            return;
          }
          const id = imageDraft.getState().paste(image.bytes, image.ext);
          chatInput.getState().appendImageSpan(id);
        });
        return true;
      }
      // Backspace/Delete → delete at the trailing edge. A trailing image span is removed whole and its
      // id returned, so we drop its imageDraftStore entry (cancel/ignore the in-flight upload). Consumed.
      if (key.backspace === true || key.delete === true) {
        const removedId = chatInput.getState().backspace();
        if (removedId !== null) {
          imageDraft.getState().drop(removedId);
        }
        return true;
      }
      // A printable character (no modifier, single visible char) → append. Consumed.
      // Reject empty/modified input: those are control keys the chat field doesn't own (return
      // `false` so the dispatcher doesn't falsely report them handled).
      if (input.length > 0 && key.ctrl !== true && key.meta !== true && key.escape !== true) {
        chatInput.getState().append(input);
        return true;
      }
      return false;
    },
  };
}

/**
 * The shell body — runs inside both providers so it can read the stores. Installs the one root input
 * loop, then lays out the always-visible chrome (top bar, chat input, bottom bar) around the
 * orientation-aware Body.
 *
 * ## Orientation-aware Body (Phase 2)
 * The Body is `[ Rail(left) · Stage · Rail(right) ]`, laid out along an axis chosen by the single
 * {@link useOrientation} call here (one source of truth, threaded to both Rails so they never
 * diverge mid-tree — see the hook's handoff note):
 *  - landscape → Body is a `row`: the rails sit on the left/right of a center Stage (each Rail then
 *    stacks its own panels in a column);
 *  - portrait  → Body is a `column`: the rails stack above/below the Stage (each Rail lays its own
 *    panels out in a row).
 * Each Rail collapses to nothing when it has no visible panels, so the Stage grows to fill whatever
 * the rails leave (full width when both rails are off).
 *
 * ## Stage slot (Phase 4a)
 * The center hosts the {@link ./Stage.js Stage}: the favorited-crow chat-history Panes, each a
 * focusable Stage pane reachable by `alt+h/j/k/l`. It grows to fill whatever the rails leave (full
 * width when both rails are off) and clips its own overflow. Phase 4b adds open-document panes to the
 * Stage's right; the `docView` slice is untouched here.
 *
 * C13: wires the `spawn` deferred handler so `ctrl+s` opens the spawn wizard. The handler reads the
 * app store at invocation time (not during render) so it always sees current state.
 *
 * C11: `ctrl+s` is dual-purpose (spawn from chat; star from a panel) — the dispatcher routes it, so
 * this `spawn` handler still only fires when chat is focused (see dispatcher.ts). The spawn context
 * is the OPEN doc-view (focused-doc), via {@link deriveSpawnContext}. C11 also loads the persisted
 * favorites once on mount via the favorites action.
 */
function Shell({
  project,
  terminalEvents,
}: {
  readonly project?: string | undefined;
  /** The kitty stdin shim's chord channel (Phase 2), passed from the live entrypoint like `bus`. The
   * root input loop subscribes to it; omitted in smoke/tests (no shim → no side-channel chords). */
  readonly terminalEvents?: TerminalEvents | undefined;
}): JSX.Element {
  const { modes, chatInput, bindings } = useInputStores();
  const appStore = useAppStoreApi();
  const bus = useBusClient();
  const loadFavorites = useAppStore((s) => s.actions.favorites.load);
  const loadSettings = useAppStore((s) => s.actions.settings.load);
  // Live terminal height — bounds the root box so the frame always fits one screen (see the return).
  const { rows } = useTerminalSize();
  // The ONE orientation read (rule: one source of truth) — threaded to both Rails and the Body axis.
  const orientation = useOrientation();
  // L4c-fix2: portrait budgets the rows axis, so it must know the height the Body region actually
  // occupies = `rows − topbar − ChatInput − footer`. Two of those are MEASURED and one is COMPUTED:
  //   • topbar + ChatInput — measured via `measureElement` on their `flexShrink={0}` boxes. They are
  //     stable (no wrap), so Yoga's height matches what they draw; ChatInput is measured (not
  //     hardcoded) because it grows with image-draft/attachment rows.
  //   • footer (BottomBar) — COMPUTED, not measured. Ink reports a wrapped flex-row / percentage-width
  //     Text as 1 line even when the terminal draws 2, so `measureElement` on the footer is unreliable
  //     (it returned 4 for a 5-row chrome at 78×50, making the Body 1 row too tall and the bottom rail
  //     strip clip its border into the chat input). Instead the footer packs its hints into N explicit
  //     single-line rows; `useBottomBarLines().length` is that exact N, shared with the BottomBar
  //     render so the count and the height accounting can never disagree.
  // The `useLayoutEffect` is GUARDED (writes only on a real change) so it settles in one extra render
  // and never loops. Before the first measurement the heights are 0 → `bodyHeight` is 0 →
  // `useBodyLayout` falls back to terminal `rows` and self-corrects (non-TTY tests report 0 and stay
  // on the fallback). Landscape ignores this height entirely (it budgets the width axis).
  const topbarRef = useRef<DOMElement | null>(null);
  const chatInputRef = useRef<DOMElement | null>(null);
  const [topbarHeight, setTopbarHeight] = useState(0);
  const [chatInputHeight, setChatInputHeight] = useState(0);
  useLayoutEffect(() => {
    if (topbarRef.current !== null) {
      const { height } = measureElement(topbarRef.current);
      if (height !== topbarHeight) {
        setTopbarHeight(height);
      }
    }
    if (chatInputRef.current !== null) {
      const { height } = measureElement(chatInputRef.current);
      if (height !== chatInputHeight) {
        setChatInputHeight(height);
      }
    }
  });
  // Footer row count (computed — see above). Shared with the BottomBar render via the same hook.
  const footerLines = useBottomBarLines().length;
  // The Body's true available height = terminal rows − topbar − ChatInput − footer rows. Only
  // meaningful once topbar + ChatInput have been measured (>0); before that it is 0 and `useBodyLayout`
  // falls back to the terminal `rows` (self-correcting on the next layout). `max(0, …)` so a transient
  // over-measure can never make the portrait total negative.
  const bodyHeight =
    topbarHeight > 0 && chatInputHeight > 0
      ? Math.max(0, rows - topbarHeight - chatInputHeight - footerLines)
      : 0;
  // The responsive cell budget (R1–R7): explicit rail widths/heights + the Stage's ≥60% floor,
  // computed from the live terminal size, orientation, and each rail's natural content width. One
  // call here, threaded down to both Rails and the Stage — the single source of truth for the Body's
  // sizing, mirroring the single `useOrientation()` read. Portrait budgets against the MEASURED Body
  // height (L4c) so nothing overflows into the chrome; landscape is unaffected (budgets width).
  const bodyLayout = useBodyLayout(bodyHeight);
  // The {@link PanelId} → component dispatch, closing over the current usage tier (L4/R9) so the
  // UsagePanel renders the mini/medium/large gauge its right-rail width supports. Memoised on the tier
  // so a Rail (which is `memo`-free but cheap) only re-derives when the tier actually changes, not on
  // every Body re-render. Passed to BOTH Rails; only the usage case reads the tier.
  const dispatchPanel = useMemo(
    () =>
      (id: PanelId): JSX.Element =>
        renderPanel(id, bodyLayout.usageTier),
    [bodyLayout.usageTier],
  );

  // F9: the image-draft store owns the `image.upload` bus call + the FIFO upload queue (it writes a
  // file, doesn't mutate a conversation — so rule 3's "send lives in conversations" doesn't apply).
  // One instance per bus; toasts go to the app singleton. Stable across renders (the bus is stable).
  const imageDraft = useMemo(() => createImageDraftStore(bus, toastStore), [bus]);

  // Load persisted favorites once on mount (rule 3: via the action). Fire-and-forget; a rejection
  // (e.g. the modeled-not-live prefs RPC against a real bus) lands in the slice's error and leaves
  // favorites at their defaults — never crashes the shell.
  useEffect(() => {
    void loadFavorites();
  }, [loadFavorites]);

  // Phase 3: load persisted settings once on mount, next to favorites (rule 3: via the action).
  // Same fire-and-forget contract — a rejection lands in the slice's error and leaves settings at
  // their defaults (alt modifier, no rebinds), never crashing the shell.
  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  // Phase 3: the settings → input bridge. The settings slice owns the persisted preferences; the
  // input layer's bindingsStore must stay bus-free (it has no idea the bus exists), so this one
  // subscription mirrors { modifier, keyOverrides } onto it whenever either changes. It runs once
  // at mount (priming the bindings store from the loaded settings) and then on every settings
  // change (the optimistic `update` path → instant dispatcher/keymap/footer reaction).
  //
  // theme bridge wired in phase 5 (the theme store is built in a parallel phase; settings.theme is
  // loaded into the slice here but not yet mirrored anywhere).
  useEffect(() => {
    const sync = (modifier: SettingsModifier, keyOverrides: Record<string, string>): void => {
      const bindingsState = bindings.getState();
      bindingsState.setModifier(modifier);
      // The slice stores keyOverrides opaquely (string keys); the bindings store narrows them onto
      // the ActionId union. A stray non-ActionId key is harmless (resolveBindings ignores it).
      bindingsState.setOverrides(keyOverrides as Partial<Record<ActionId, string>>);
    };
    const current = appStore.getState().settings;
    sync(current.modifier, current.keyOverrides as Record<string, string>);
    return appStore.subscribe((state, prev) => {
      if (
        state.settings.modifier !== prev.settings.modifier ||
        state.settings.keyOverrides !== prev.settings.keyOverrides
      ) {
        sync(state.settings.modifier, state.settings.keyOverrides as Record<string, string>);
      }
    });
  }, [appStore, bindings]);

  // `ctrl+s` → open the spawn wizard (only fires when chat is focused; see dispatcher.ts). Reads the
  // store imperatively at call time (getState()) so no stale closure; stores are stable references.
  const spawnHandler = (): void => {
    // The focused doc is the open doc-view (C11 — replaces C13's first-row proxy).
    const spawnContext = deriveSpawnContext(appStore);
    const actions = createSpawnActions(bus);
    const modelActions = createHarnessModelsActions(bus);
    const worktreeActions = createWorktreeOptionsActions(bus);
    modes
      .getState()
      .enter(spawnWizardMode(modes, actions, { spawnContext, modelActions, worktreeActions }));
  };

  // The single root input loop for the whole app (rule 5) — installed exactly once, here.
  // C13: `spawn` wired to the spawn wizard handler. C11: `chatInput` wired to the persistent
  // chat-input handler (buffers chars, sends on Enter to the active agent). Global ctrl-chords still
  // preempt it (layer 1 < layer 2), so the user can summon panels mid-message.
  useRootInput(
    {
      spawn: spawnHandler,
      chatInput: makeChatInputHandler(chatInput, appStore, imageDraft),
    },
    terminalEvents,
  );

  // A full-screen mode (C14 tmux) replaces the whole layout: when one is active the shell renders
  // only the {@link Overlay} (which paints the full-viewport surface), suppressing its own bars and
  // panels. `modal`/`inlayout` modes keep the layout — the overlay draws over/within it. The
  // suppression predicate lives with the presentation data ({@link presentationHidesLayout}), not
  // hardcoded here, so a new full-screen-like presentation is honoured without editing the shell.
  useModeStore((s) => s.stack);
  const active = selectActiveMode(modes);
  if (active !== null && presentationHidesLayout(active.presentation)) {
    return <Overlay />;
  }
  // Bound the whole app to the terminal height: a frame taller than the screen breaks Ink's in-place
  // redraw (it can only erase up to the screen height, so each re-render stacks a fresh full copy into
  // scrollback). This is the standard header/scroll/footer flex idiom:
  //  - root: fixed `height={rows}` + `overflow="hidden"` — the final safety clip, so nothing is ever
  //    drawn past the screen even if a child mis-measures.
  //  - chrome (top bar, chat, bottom bar): `flexShrink={0}` — keep their natural height, never squeezed.
  //  - middle region: `flexGrow={1} flexBasis={0}` — take *exactly* the remaining space (basis 0 so its
  //    tall content doesn't push the chrome out of the box), and `overflow="hidden"` clips the panels.
  // Without `flexBasis={0}` the region's basis is its (huge) content height, Yoga never bounds it, the
  // chrome is shoved past `rows`, and nothing clips — which is the "still way too tall" failure.
  return (
    <Box flexDirection="column" width="100%" height={rows} overflow="hidden">
      <Box ref={topbarRef} flexShrink={0} flexDirection="column">
        <TopBar project={project} />
      </Box>
      {/* Orientation-aware Body: landscape lays the rails + Stage out in a row (side-by-side),
          portrait stacks them in a column. `flexBasis={0}` so the Body's tall content can't push the
          chrome past `rows` (see the comment above); `overflow="hidden"` is the clip. */}
      <Box
        flexDirection={orientation === 'landscape' ? 'row' : 'column'}
        columnGap={orientation === 'landscape' ? 1 : 0}
        rowGap={orientation === 'portrait' ? 1 : 0}
        flexGrow={1}
        flexBasis={0}
        minHeight={0}
        overflow="hidden"
      >
        <Rail
          side="left"
          orientation={orientation}
          panels={LEFT_PANELS}
          renderPanel={dispatchPanel}
          // Explicit, budget-computed cross-axis size — only as wide as its widest ledger row (R1/R2),
          // compressed (so trailing columns drop) when the Stage's 60% floor needs the room (R3).
          cells={bodyLayout.leftRailCells}
        />
        {/* Phase 4a: the Stage center region — tiles the favorited-crow chat-history Panes, growing to
            fill whatever the rails leave (full width when both rails are off). It carries the budget
            floor (R3/R4) so it can never be sized below its guaranteed ≥60% share. Phase 4b adds
            doc-view panes to its right; the doc slice is untouched here. The Stage itself clips/grows. */}
        <Stage minCells={bodyLayout.stageCells} axis={bodyLayout.axis} />
        <Rail
          side="right"
          orientation={orientation}
          panels={RIGHT_PANELS}
          renderPanel={dispatchPanel}
          // The right rail (usage · crows) is sized to the crow-ledger width when crows are on (R6),
          // computed relative to the live terminal — no `"24%"` absolute anymore (R5).
          cells={bodyLayout.rightRailCells}
        />
      </Box>
      <Box flexShrink={0} flexDirection="column">
        <Box ref={chatInputRef} flexDirection="column">
          <ChatInput />
        </Box>
        <BottomBar />
      </Box>
      <Overlay />
      {/* F9: the transient toast rack — bottom-right, subtle. Last child so it rides below the bars;
          reads the toastStore singleton (pushed by the conversations send action + the image slice). */}
      <Toast />
    </Box>
  );
}

/**
 * The app root. Mounts the store + input-store + bus-client providers (all bundles injected as
 * props — rule 4) and renders the shell inside them. This is the whole composition: providers wrap,
 * {@link Shell} composes, panels fill in.
 *
 * `bus` is threaded here so the {@link TmuxFrame} component can open a transient subscription
 * via {@link BusClientProvider} / {@link useBusClient}. Actions remain the only domain-data path
 * (rule 3); the bus context is for streaming display data only (C14 tmux frames).
 */
export function App({
  store,
  inputStores,
  bus,
  project,
  terminalEvents,
}: {
  readonly store: AppStoreApi;
  readonly inputStores: InputStores;
  readonly bus: BusClient;
  /** Current project/repo name for the top-bar branding; from `MURDER_PROJECT` (see index.tsx). */
  readonly project?: string | undefined;
  /** The kitty stdin shim's chord channel (Phase 2), injected at the live entrypoint like `bus`.
   * Omitted in smoke/tests. */
  readonly terminalEvents?: TerminalEvents | undefined;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <BusClientProvider value={bus}>
          <Shell project={project} terminalEvents={terminalEvents} />
        </BusClientProvider>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}
