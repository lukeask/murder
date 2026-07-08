/**
 * `switchWorkspace` — the single owner of the workspace switch pipeline (workspaces plan, step 2a).
 *
 * The architecture is snapshot-swapping singletons (see {@link ./workspaceStore.js}): the live
 * stores always ARE the active workspace, and switching means (1) serialize the live stores into
 * the outgoing slot, (2) hydrate the target slot back into them. This module is the only code that
 * knows which store fields participate and in what order they hydrate.
 *
 * ## The bulk-restore seam
 *
 * Serialization reads store state; hydration writes it back with direct `store.setState(...)` on
 * the vanilla handles. That deliberately bypasses the stores' mutation verbs: the verbs express
 * *user edits* (with their side-policies — e.g. `chatInput.insert` resets history-nav), while
 * hydration is a wholesale state transplant that must NOT trigger those policies. This module is
 * the one sanctioned place that does it; everything else keeps using the verbs.
 *
 * ## Hydration ordering
 *
 * 1. App-store stage-pane configuration (conversation pane overrides / active pane / view modes) —
 *    first, so the Stage's pane set is already right when the layout re-derives.
 * 2. Doc pane: open/close through the docView *action* (the body is domain data — re-fetched, not
 *    snapshotted; the pane shows its normal loading state for a beat).
 * 3. Rail panel visibility (`panels.visible`).
 * 4. Pane UI state (scroll/cursor maps) — before focus, so re-mounting panes read their restored
 *    positions on first render.
 * 5. Chat input draft + history-nav.
 * 6. Intended focus — last. It is intent only; the effective focus re-derives once the hydrated
 *    panes re-mount and publish geometry, and collapses to chat until then (the existing re-home
 *    invariant, which is exactly the right fallback for a pane that no longer exists).
 *
 * A `null` snapshot (never-opened slot) hydrates the chat-only fresh-boot layout: no rail panels,
 * no overrides, no doc, empty chat draft, focus on chat.
 *
 * ## Manual multi-workspace smoke checklist (step-5 verification)
 *
 * The pure pipeline is unit-tested (workspaceSwitch.test.ts); these are the things only a live TUI
 * on a real terminal exercises. Set `workspace_count` to 3 in Settings first.
 *
 *  1. **Keybinds resolve (kitty terminal, ctrl modifier).** `Ctrl+Shift+J` cycles forward,
 *     `Ctrl+Shift+K` back — both wrap (3→1, 1→3). `Ctrl+Shift+1/2/3` jump directly; `Ctrl+Shift+4`
 *     (> count) is a silent no-op. (The J vs K asymmetry in `translate.ts` — K rides the shifted
 *     clean-byte path, J the plain-chord path — is the guarded regression; both must work.)
 *  2. **Keybinds under the alt modifier.** `Alt+Shift+J/K` cycle (letters carry shift as the
 *     uppercase char). KNOWN LIMITATION: `Alt+Shift+<digit>` does NOT jump — the alt/meta wire form
 *     drops shift on caseless keys, so direct jump is kitty/ctrl-only under alt (documented in
 *     bindings.ts `commandChord`). J/K cycling is the alt-modifier fallback.
 *  3. **Layout persists per workspace.** Open a distinct layout in each (e.g. WS1 = a plan doc +
 *     chat; WS2 = a rogue transcript; WS3 = a report). Cycle away and back — panel set, the open
 *     doc, focus, list-cursor positions, and transcript/doc SCROLL offset all restore exactly.
 *  4. **Chat draft is per-workspace; history is global.** Type an unsent draft in WS1, switch away
 *     and back — the draft returns. Up-arrow history recall shows entries from every workspace.
 *  5. **Slide animation.** Switching between two previously-opened workspaces slides (J down, K up),
 *     ~300ms, no torn/smeared rows mid-slide. First visit to a never-opened workspace is an instant
 *     switch (no cached frame). Input is inert for the ~300ms slide.
 *  6. **Resize mid-slide.** Trigger a terminal resize during a slide — the animation cancels and the
 *     live view paints at the new size (no stale-geometry frame left on screen).
 *  7. **Indicator widget.** `⟨2/3⟩` shows the active workspace and updates on every switch. Drop
 *     `workspace_count` to 1 in Settings — the indicator disappears (feature fully inert).
 *  8. **Shrink clamp.** From WS3, set `workspace_count` to 2 in Settings — active index clamps into
 *     range (lands on the last remaining workspace) with an instant switch, no crash, no orphan.
 */

import type { AppStoreApi } from '../store/store.js';
import { EMPTY_BUFFER } from './chatBuffer.js';
import type { ChatInputStoreApi } from './chatInputStore.js';
import { CHAT_FOCUS } from './focusIds.js';
import type { FocusStoreApi } from './focusStore.js';
import type { PanelStoreApi } from './panelStore.js';
import type { PanelId } from './panels.js';
import type { PaneUiStoreApi } from './paneUiStore.js';
import type {
  CapturedFrame,
  WorkspaceDirection,
  WorkspaceSnapshot,
  WorkspaceStoreApi,
  WorkspaceTransition,
} from './workspaceStore.js';

/** The store handles the pipeline serializes/hydrates. The input-store fields match the
 * {@link ./createInputStores.js InputStoreBundle} names so the app can pass the bundle's fields
 * straight through; tests build a minimal set. */
export interface WorkspaceStores {
  readonly workspace: WorkspaceStoreApi;
  readonly panels: PanelStoreApi;
  readonly focus: FocusStoreApi;
  readonly chatInput: ChatInputStoreApi;
  readonly paneUi: PaneUiStoreApi;
  /** The app store — only its `conversations` pane-config fields and `docView.open` participate;
   * domain data stays global. */
  readonly app: AppStoreApi;
}

/** Environment hooks the pipeline calls but does not own (kept injectable so the pipeline is a
 * pure store transform in tests). */
export interface WorkspaceSwitchOptions {
  /** Grab the current on-screen frame (step 4b's Ink-internals capture). Absent/`null` result =
   * no frame cached, no slide possible. 2a callers omit it. */
  readonly captureFrame?: () => CapturedFrame | null;
  /** Force a full terminal repaint after commit (the app passes
   * `() => forceInkFullRepaint(process.stdout)`; tests omit). */
  readonly repaint?: () => void;
}

/** Serialize the live stores' workspace-scoped state into a plain-JSON snapshot. Pure read. */
export function serializeWorkspaceSnapshot(stores: WorkspaceStores): WorkspaceSnapshot {
  const panels = stores.panels.getState();
  const focus = stores.focus.getState();
  const paneUi = stores.paneUi.getState();
  const chatInput = stores.chatInput.getState();
  const { conversations, docView } = stores.app.getState();
  return {
    panelsVisible: [...panels.visible],
    focusIntendedId: focus.intendedId,
    paneUi: {
      cursors: { ...paneUi.cursors },
      scrolls: { ...paneUi.scrolls },
      expandeds: { ...paneUi.expandeds },
      historyModes: { ...paneUi.historyModes },
      gotoLines: { ...paneUi.gotoLines },
      transitCursors: { ...paneUi.transitCursors },
      gBuffers: { ...paneUi.gBuffers },
    },
    chatInput: {
      buffer: { ...chatInput.buffer },
      historyIndex: chatInput.historyIndex,
      stashedDraft: chatInput.stashedDraft === null ? null : { ...chatInput.stashedDraft },
    },
    conversations: {
      activePaneAgentId: conversations.activePaneAgentId,
      paneOverrides: Object.fromEntries(conversations.paneOverrides),
      paneReapAges: Object.fromEntries(conversations.paneReapAges),
      paneViewModes: { ...conversations.paneViewModes },
    },
    docView: docView.open === null ? null : { ...docView.open },
  };
}

/**
 * Hydrate a snapshot into the live stores (see the module header for the ordering rationale).
 * `null` = never-opened slot → the chat-only fresh-boot layout.
 */
export function hydrateWorkspaceSnapshot(
  stores: WorkspaceStores,
  snapshot: WorkspaceSnapshot | null,
): void {
  // 1. Stage pane configuration (app store, conversations slice). Intent maps only — transcripts,
  //    meta, summaries etc. are global domain data and untouched.
  stores.app.setState((state) => ({
    conversations: {
      ...state.conversations,
      activePaneAgentId: snapshot?.conversations.activePaneAgentId ?? null,
      paneOverrides: new Map(Object.entries(snapshot?.conversations.paneOverrides ?? {})),
      paneReapAges: new Map(Object.entries(snapshot?.conversations.paneReapAges ?? {})),
      paneViewModes: { ...(snapshot?.conversations.paneViewModes ?? {}) },
    },
  }));

  // 2. Doc pane — through the action (not setState) because the body must be re-fetched; the
  //    snapshot only carries identity. Skipped when the same doc is already open (switching between
  //    two workspaces showing the same doc must not flash a reload).
  const currentDoc = stores.app.getState().docView.open;
  const nextDoc = snapshot?.docView ?? null;
  if (nextDoc === null) {
    if (currentDoc !== null) {
      stores.app.getState().actions.docView.close();
    }
  } else if (
    currentDoc === null ||
    currentDoc.kind !== nextDoc.kind ||
    currentDoc.name !== nextDoc.name
  ) {
    void stores.app.getState().actions.docView.open(nextDoc.kind, nextDoc.name);
  }

  // 3. Rail panel visibility.
  stores.panels.setState({ visible: new Set<PanelId>(snapshot?.panelsVisible ?? []) });

  // 4. Pane UI state — replaced wholesale (a fresh workspace starts every pane at the top).
  stores.paneUi.setState({
    cursors: { ...(snapshot?.paneUi.cursors ?? {}) },
    scrolls: { ...(snapshot?.paneUi.scrolls ?? {}) },
    expandeds: { ...(snapshot?.paneUi.expandeds ?? {}) },
    historyModes: { ...(snapshot?.paneUi.historyModes ?? {}) },
    gotoLines: { ...(snapshot?.paneUi.gotoLines ?? {}) },
    transitCursors: { ...(snapshot?.paneUi.transitCursors ?? {}) },
    gBuffers: { ...(snapshot?.paneUi.gBuffers ?? {}) },
  });

  // 5. Chat input draft + history-nav, restored verbatim (setState, not the verbs — insert/clear
  //    carry edit policies a transplant must not trigger). The flat text/cursor mirrors are synced
  //    exactly as the store's own verbs do.
  const buffer = snapshot?.chatInput.buffer ?? EMPTY_BUFFER;
  stores.chatInput.setState({
    buffer,
    text: buffer.text,
    cursor: buffer.cursor,
    historyIndex: snapshot?.chatInput.historyIndex ?? null,
    stashedDraft: snapshot?.chatInput.stashedDraft ?? null,
  });

  // 6. Intended focus, last. Effective focus re-resolves against the live graph as the hydrated
  //    panes re-mount; until then it collapses to chat (the standing re-home invariant).
  stores.focus.getState().focus(snapshot?.focusIntendedId ?? CHAT_FOCUS);
}

/**
 * Switch to workspace `targetIndex` — the plan's six-step pipeline.
 *
 * No-op when a transition is in flight, the target is the active workspace, or the target is out
 * of range (jump past `count` is a no-op by spec).
 *
 * ## The slide is cosmetic; the switch always commits here
 *
 * When the target slot has a cached frame AND we captured a source frame of the same geometry, a
 * slide transition begins — but the stores are hydrated and `activeIndex` committed in this very
 * call regardless. The transition is only paint state: while it is non-null the Shell renders the
 * {@link ../components/WorkspaceSlideOverlay.js WorkspaceSlideOverlay} (which animates the two
 * captured frames, then clears the transition and repaints) instead of the live layout, and the
 * dispatcher blocks all input. Keeping the commit synchronous means a transition can never strand
 * the app between workspaces (a cancel — e.g. resize — only skips the remaining animation), and
 * headless callers (tests) that never mount the overlay simply never begin one, because they don't
 * supply `captureFrame`.
 */
export function switchWorkspace(
  stores: WorkspaceStores,
  targetIndex: number,
  direction: WorkspaceDirection,
  options: WorkspaceSwitchOptions = {},
): void {
  const ws = stores.workspace.getState();
  // 1. Guards.
  if (ws.transition !== null) {
    return;
  }
  if (!Number.isInteger(targetIndex) || targetIndex < 0 || targetIndex >= ws.count) {
    return;
  }
  if (targetIndex === ws.activeIndex) {
    return;
  }
  // 2. Capture the outgoing frame (the Ink-internals grab in ../terminal/captureFrame.ts; null =
  //    internals shifted / nothing rendered / size unknown — no slide, but the switch proceeds).
  const fromFrame = options.captureFrame?.() ?? null;
  // 3. Serialize the live stores into the outgoing slot (frame cached for a future slide back).
  ws.saveSlot(ws.activeIndex, serializeWorkspaceSnapshot(stores), fromFrame);
  // 4. Slide eligibility: both frames present and the SAME geometry (a target frame cached before a
  //    resize is stale pixels at the wrong size — skip the slide, the instant switch is correct).
  const target = stores.workspace.getState().slots[targetIndex] ?? null;
  const toFrame = target?.lastFrame ?? null;
  const transition: WorkspaceTransition | null =
    fromFrame !== null &&
    toFrame !== null &&
    toFrame.columns === fromFrame.columns &&
    toFrame.rows === fromFrame.rows
      ? { fromFrame, toFrame, direction, startedAt: Date.now() }
      : null;
  // 5. Hydrate the target slot (null snapshot = chat-only fresh-boot layout).
  hydrateWorkspaceSnapshot(stores, target?.snapshot ?? null);
  // 6. Commit. With a slide: begin the transition (the overlay animates over the already-committed
  //    switch, then clears it and repaints — repainting here would paint the layout under the
  //    overlay's first frame). Without: clear any stale transition and repaint immediately.
  const committed = stores.workspace.getState();
  committed.setActiveIndex(targetIndex);
  if (transition !== null) {
    committed.beginTransition(transition);
  } else {
    committed.clearTransition();
    options.repaint?.();
  }
}

/**
 * React to a `workspace_count` settings change (step 2c calls this, not `setCount` directly).
 * Growing adds never-opened slots. Shrinking drops slots above the new count; when the *active*
 * workspace is dropped, the active index clamps to the last surviving workspace and its snapshot
 * hydrates instantly (no animation, per the resolved question). The dropped live layout is NOT
 * serialized — its slot no longer exists, and domain data is global so nothing is lost.
 */
export function applyWorkspaceCount(
  stores: WorkspaceStores,
  count: number,
  options: WorkspaceSwitchOptions = {},
): void {
  const ws = stores.workspace.getState();
  const next = Math.max(1, Math.floor(count));
  if (next === ws.count) {
    return;
  }
  const activeDropped = ws.activeIndex >= next;
  ws.setCount(next);
  if (!activeDropped) {
    return;
  }
  // The active workspace was dropped: land on the last surviving one. setCount already clamped
  // activeIndex; we still need its snapshot in the live stores.
  const slot = stores.workspace.getState().slots[next - 1] ?? null;
  hydrateWorkspaceSnapshot(stores, slot?.snapshot ?? null);
  stores.workspace.getState().clearTransition();
  options.repaint?.();
}
