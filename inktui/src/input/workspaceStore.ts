/**
 * `workspaceStore` — the N-virtual-workspaces state (workspaces plan, step 2a).
 *
 * ## Snapshot-swapping singletons
 *
 * Every existing store stays a singleton that always represents the **live** workspace. This store
 * is the only code that knows workspaces exist: it holds the slot array of *inactive* workspaces'
 * serialized snapshots, which workspace is active, and the (step 4b) slide-transition state. The
 * switch pipeline ({@link ./workspaceSwitch.js}) serializes the live stores into the outgoing slot
 * and hydrates the incoming slot back into them; nothing else in the app changes behavior. At
 * `count == 1` the whole feature is inert — no snapshot is ever taken and behavior is identical to
 * a build without workspaces.
 *
 * ## What a snapshot holds (and what it never holds)
 *
 * {@link WorkspaceSnapshot} is per-workspace **layout/UI intent** only: the visible panel set, the
 * stage pane configuration (conversation pane overrides / active pane / view modes, open doc),
 * intended focus, every pane's hoisted scroll/cursor state ({@link ./paneUiStore.js}), and the chat
 * input draft with its history-nav state. Domain data (roster, conversations, settings, docs
 * bodies), the murder-wide `chatHistory.entries` corpus, the mode stack, toasts, bindings, and
 * terminal caps are GLOBAL — never snapshotted, shared by every workspace.
 *
 * Every snapshot field is plain JSON-serializable data (resolved question: persisting slots to user
 * config later must be cheap), which the round-trip test pins.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink — the same idiom as
 * {@link ./paneUiStore.js}/{@link ./chatInputStore.js}.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import type { ChatViewMode } from '../store/conversations/conversationsSlice.js';
import type { DocKind } from '../store/docView/docViewSlice.js';
import type { BufferState } from './chatBuffer.js';
import type { FocusId } from './focusIds.js';
import type { PanelId } from './panels.js';
import type { PaneUiState } from './paneUiStore.js';

/**
 * A captured text frame — what a workspace last showed on screen, grabbed at switch-away (step 4b's
 * `captureCurrentFrame()`; the 2a pipeline stubs it `null`). Used only as slide-animation source
 * material, never as truth: the real view repaints from the live tree at commit.
 */
export interface CapturedFrame {
  /** The frame text (newline-separated rows, ANSI included). */
  readonly text: string;
  /** Terminal size the frame was captured at — a stale size skips the slide (resize invalidates). */
  readonly columns: number;
  readonly rows: number;
}

/** Which way a switch travels — J (next) slides one way, K (prev) the other. Jumps derive it from
 * index order. */
export type WorkspaceDirection = 'next' | 'prev';

/** In-flight slide state (step 4b). Non-null blocks workspace keybinds (and all input) until the
 * commit clears it — the pipeline ignores switch requests while a transition is up. */
export interface WorkspaceTransition {
  readonly fromFrame: CapturedFrame;
  readonly toFrame: CapturedFrame;
  readonly direction: WorkspaceDirection;
  /** `Date.now()` at start — the tick loop eases against it (toast pattern). */
  readonly startedAt: number;
}

/** The pane-UI maps a snapshot carries — exactly {@link PaneUiState}'s data fields (no verbs). */
export type PaneUiSnapshot = Pick<
  PaneUiState,
  'cursors' | 'scrolls' | 'expandeds' | 'historyModes' | 'gotoLines' | 'transitCursors' | 'gBuffers'
>;

/**
 * One workspace's serialized layout/UI intent. Written by
 * {@link ./workspaceSwitch.js serializeWorkspaceSnapshot} on switch-away, read by
 * `hydrateWorkspaceSnapshot` on switch-to. All fields are plain JSON data (no Sets/Maps — the
 * live stores' Set/Map fields serialize to arrays/records).
 */
export interface WorkspaceSnapshot {
  /** `panels.visible` as an array (the toggled-on rail panels). */
  readonly panelsVisible: readonly PanelId[];
  /** `focus.intendedId` — intent only; effective focus re-derives against the live graph after the
   * hydrated panes re-mount and re-measure. */
  readonly focusIntendedId: FocusId;
  /** All of {@link ./paneUiStore.js}'s keyed state — scroll offsets, list cursors, per-panel flags. */
  readonly paneUi: PaneUiSnapshot;
  /** The chat input draft + cursor + history-nav state (per-workspace drafts fall out of this; the
   * recall corpus `chatHistory.entries` stays global). */
  readonly chatInput: {
    readonly buffer: BufferState;
    readonly historyIndex: number | null;
    readonly stashedDraft: BufferState | null;
  };
  /** Stage transcript-pane configuration from the app store's conversations slice (intent maps, not
   * transcript data): explicit open/close overrides, the pinned active pane, per-pane view modes,
   * and reap ages (Maps serialized to records). */
  readonly conversations: {
    readonly activePaneAgentId: string | null;
    readonly paneOverrides: Readonly<Record<string, boolean>>;
    readonly paneReapAges: Readonly<Record<string, number>>;
    readonly paneViewModes: Readonly<Record<string, ChatViewMode>>;
  };
  /** The open stage doc pane's identity (`docView.open`), or `null` when closed. The body is domain
   * data — hydration re-fetches it through the docView action, never snapshots it. */
  readonly docView: { readonly kind: DocKind; readonly name: string } | null;
}

/** One workspace slot. The *live* workspace's slot is stale while active — it is only rewritten at
 * switch-away. */
export interface WorkspaceSlot {
  /** `null` = never opened; hydrating it means the chat-only fresh-boot layout. */
  readonly snapshot: WorkspaceSnapshot | null;
  /** The text frame from the last time this workspace was on screen; `null` = never shown (skip the
   * slide, switch instantly). */
  readonly lastFrame: CapturedFrame | null;
}

/** The workspace store's state + verbs. Mutation only via the verbs; the switch *pipeline* (which
 * verb calls happen in what order around serialize/hydrate) lives in {@link ./workspaceSwitch.js}. */
export interface WorkspaceStoreState {
  /** How many workspaces exist. Mirrors `settings.workspace_count` (step 2c); 1 = feature inert. */
  readonly count: number;
  /** The active workspace, 0-based. Invariant: `0 <= activeIndex < count` (setCount clamps). */
  readonly activeIndex: number;
  /** `length == count`. The active slot's contents are stale while that workspace is live. */
  readonly slots: readonly WorkspaceSlot[];
  /** Non-null while a slide is animating (step 4b); the pipeline refuses switches meanwhile. */
  readonly transition: WorkspaceTransition | null;
  /**
   * Resize to `count` workspaces (clamped to >= 1). Grows with empty slots; shrinks by dropping
   * slots above the new count (resolved question: orphaned layouts are dropped — domain data is
   * global, so nothing is lost). Clamps `activeIndex` into range atomically so the invariant never
   * breaks — but clamping alone does NOT hydrate; callers must use
   * {@link ./workspaceSwitch.js applyWorkspaceCount}, which hydrates the surviving slot when the
   * active workspace was dropped.
   */
  setCount(count: number): void;
  /** Write a slot's snapshot + last-seen frame (switch-away serialization). Out-of-range: no-op. */
  saveSlot(
    index: number,
    snapshot: WorkspaceSnapshot | null,
    lastFrame: CapturedFrame | null,
  ): void;
  /** Commit the active workspace. Out-of-range: no-op (the pipeline validates first). */
  setActiveIndex(index: number): void;
  /** Start a slide (step 4b). */
  beginTransition(transition: WorkspaceTransition): void;
  /** Clear any in-flight slide (commit, or cancel-on-resize). */
  clearTransition(): void;
}

/** The workspace store handle. Re-exported so callers don't import `zustand/vanilla` directly. */
export type WorkspaceStoreApi = StoreApi<WorkspaceStoreState>;

/** A never-opened slot. */
function emptySlot(): WorkspaceSlot {
  return { snapshot: null, lastFrame: null };
}

/** Create the workspace store. Defaults to a single workspace (feature inert) until the settings
 * bridge (step 2c) pushes `workspace_count`. */
export function createWorkspaceStore(initialCount = 1): WorkspaceStoreApi {
  const count = Math.max(1, Math.floor(initialCount));
  return createStore<WorkspaceStoreState>()((set) => ({
    count,
    activeIndex: 0,
    slots: Array.from({ length: count }, emptySlot),
    transition: null,
    setCount(nextCount) {
      set((state) => {
        const next = Math.max(1, Math.floor(nextCount));
        if (next === state.count) {
          return state;
        }
        const slots =
          next < state.slots.length
            ? state.slots.slice(0, next)
            : [...state.slots, ...Array.from({ length: next - state.slots.length }, emptySlot)];
        return { count: next, slots, activeIndex: Math.min(state.activeIndex, next - 1) };
      });
    },
    saveSlot(index, snapshot, lastFrame) {
      set((state) => {
        if (index < 0 || index >= state.slots.length) {
          return state;
        }
        const slots = state.slots.map((slot, i) => (i === index ? { snapshot, lastFrame } : slot));
        return { slots };
      });
    },
    setActiveIndex(index) {
      set((state) =>
        Number.isInteger(index) && index >= 0 && index < state.count
          ? { activeIndex: index }
          : state,
      );
    },
    beginTransition(transition) {
      set({ transition });
    },
    clearTransition() {
      set({ transition: null });
    },
  }));
}
