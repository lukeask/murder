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
 * ## Per-repository bags (single-daemon Phase 8)
 *
 * Slot state is keyed `(repository_id, slot_index)`. The live `slots` / `count` / `activeIndex`
 * always describe the **current** repository; inactive repos park a {@link RepoWorkspaceBag} under
 * `repoBags[repository_id]`. Repo switch ({@link ./workspaceSwitch.js switchRepositoryWorkspace})
 * serializes the live bag under the old key and hydrates the new key — same snapshot machinery as
 * workspace switch. `workspace_count` is per-repo client state (see
 * {@link workspaceCountStorageKey}); the settings wire field is dual-written for TUI cold start
 * only and is not the live source of truth while a session is open.
 *
 * ## What a snapshot holds (and what it never holds)
 *
 * {@link WorkspaceSnapshot} is per-workspace **layout/UI intent** only: the visible panel set, the
 * stage pane configuration (conversation pane overrides / active pane / view modes, open doc),
 * intended focus, every pane's hoisted scroll/cursor state ({@link @murder/ui-core/input/paneUiStore.js}), and the chat
 * input draft with its history-nav state. Domain data (roster, conversations, settings, docs
 * bodies), the murder-wide `chatHistory.entries` corpus, the mode stack, toasts, bindings, and
 * terminal caps are GLOBAL — never snapshotted, shared by every workspace.
 *
 * Every snapshot field is plain JSON-serializable data (resolved question: persisting slots to user
 * config later must be cheap), which the round-trip test pins.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink — the same idiom as
 * {@link @murder/ui-core/input/paneUiStore.js}/{@link ./chatInputStore.js}.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import type { ChatViewMode } from '@murder/ui-core/store/conversations/conversationsSlice.js';
import type { DocKind } from '@murder/ui-core/store/docView/docViewSlice.js';
import type { BufferState } from '@murder/ui-core/input/chatBuffer.js';
import type { FocusId } from '@murder/ui-core/input/focusIds.js';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import type { PaneUiState } from '@murder/ui-core/input/paneUiStore.js';

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
  /** All of {@link @murder/ui-core/input/paneUiStore.js}'s keyed state — scroll offsets, list cursors, per-panel flags. */
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

/**
 * Parked workspace layout for one repository: count + active index + slots keyed by slot_index.
 * Written on repo switch-away; restored on switch-to.
 */
export interface RepoWorkspaceBag {
  readonly count: number;
  readonly activeIndex: number;
  readonly slots: readonly WorkspaceSlot[];
}

/** Minimal storage surface for per-repo `workspace_count` (browser `localStorage` or an in-memory map). */
export interface WorkspaceCountStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** localStorage / storage key for one repository's workspace count. */
export function workspaceCountStorageKey(repositoryId: string): string {
  return `murder.${repositoryId}.workspace_count`;
}

/** Read a persisted per-repo count (clamped 1–9), or `null` when absent/invalid. */
export function loadWorkspaceCount(
  repositoryId: string,
  storage?: WorkspaceCountStorage | null,
): number | null {
  if (storage === undefined || storage === null) {
    return null;
  }
  try {
    const raw = storage.getItem(workspaceCountStorageKey(repositoryId));
    if (raw === null || raw === '') {
      return null;
    }
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1 || n > 9) {
      return null;
    }
    return n;
  } catch {
    return null;
  }
}

/** Persist a per-repo workspace count (clamped 1–9). No-op without storage. */
export function persistWorkspaceCount(
  repositoryId: string,
  count: number,
  storage?: WorkspaceCountStorage | null,
): void {
  if (storage === undefined || storage === null) {
    return;
  }
  const next = Math.min(9, Math.max(1, Math.floor(count)));
  try {
    storage.setItem(workspaceCountStorageKey(repositoryId), String(next));
  } catch {
    // Quota / private mode — count still lives in the in-memory bag.
  }
}

/** Resolve browser localStorage when available (web); otherwise `null` (TUI / tests without inject). */
export function defaultWorkspaceCountStorage(): WorkspaceCountStorage | null {
  try {
    const storage = (globalThis as { localStorage?: WorkspaceCountStorage }).localStorage;
    return storage ?? null;
  } catch {
    return null;
  }
}

/** Build an empty bag with `count` never-opened slots. */
export function emptyRepoWorkspaceBag(count = 1): RepoWorkspaceBag {
  const n = Math.max(1, Math.floor(count));
  return {
    count: n,
    activeIndex: 0,
    slots: Array.from({ length: n }, emptySlot),
  };
}

/** The workspace store's state + verbs. Mutation only via the verbs; the switch *pipeline* (which
 * verb calls happen in what order around serialize/hydrate) lives in {@link ./workspaceSwitch.js}. */
export interface WorkspaceStoreState {
  /** How many workspaces exist for the bound repo. Per-repo client state; 1 = feature inert. */
  readonly count: number;
  /** The active workspace, 0-based. Invariant: `0 <= activeIndex < count` (setCount clamps). */
  readonly activeIndex: number;
  /** `length == count`. The active slot's contents are stale while that workspace is live. */
  readonly slots: readonly WorkspaceSlot[];
  /** Non-null while a slide is animating (step 4b); the pipeline refuses switches meanwhile. */
  readonly transition: WorkspaceTransition | null;
  /**
   * Repository partition whose live bag is in `slots` / `count` / `activeIndex`.
   * `null` until the first {@link ./workspaceSwitch.js switchRepositoryWorkspace} / bind.
   */
  readonly repositoryId: string | null;
  /**
   * Parked bags for other repositories — keyed by `repository_id`. The live repo's bag is only
   * rewritten into this map on switch-away (same staleness rule as an active slot).
   */
  readonly repoBags: Readonly<Record<string, RepoWorkspaceBag>>;
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
  /** Bind the live bag to a repository id (does not swap slots). */
  setRepositoryId(repositoryId: string | null): void;
  /** Park a full bag under `repositoryId` (repo switch-away). */
  saveRepoBag(repositoryId: string, bag: RepoWorkspaceBag): void;
  /** Read a parked bag, or `null` when this process has never opened that repo. */
  getRepoBag(repositoryId: string): RepoWorkspaceBag | null;
  /**
   * Replace the live count / activeIndex / slots from a bag (repo switch-to). Does not touch
   * `repoBags` or hydrate UI stores — the switch pipeline owns that.
   */
  replaceLiveBag(bag: RepoWorkspaceBag): void;
}

/** The workspace store handle. Re-exported so callers don't import `zustand/vanilla` directly. */
export type WorkspaceStoreApi = StoreApi<WorkspaceStoreState>;

/** A never-opened slot. */
function emptySlot(): WorkspaceSlot {
  return { snapshot: null, lastFrame: null };
}

/** Create the workspace store. Defaults to a single workspace (feature inert) until a per-repo
 * count is applied (localStorage / {@link ./workspaceSwitch.js applyWorkspaceCount}). */
export function createWorkspaceStore(initialCount = 1): WorkspaceStoreApi {
  const count = Math.max(1, Math.floor(initialCount));
  return createStore<WorkspaceStoreState>()((set, get) => ({
    count,
    activeIndex: 0,
    slots: Array.from({ length: count }, emptySlot),
    transition: null,
    repositoryId: null,
    repoBags: {},
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
    setRepositoryId(repositoryId) {
      set({ repositoryId });
    },
    saveRepoBag(repositoryId, bag) {
      set((state) => ({
        repoBags: { ...state.repoBags, [repositoryId]: bag },
      }));
    },
    getRepoBag(repositoryId) {
      return get().repoBags[repositoryId] ?? null;
    },
    replaceLiveBag(bag) {
      const next = Math.max(1, Math.floor(bag.count));
      const slots =
        bag.slots.length === next
          ? [...bag.slots]
          : bag.slots.length > next
            ? bag.slots.slice(0, next)
            : [
                ...bag.slots,
                ...Array.from({ length: next - bag.slots.length }, emptySlot),
              ];
      const activeIndex = Math.min(Math.max(0, Math.floor(bag.activeIndex)), next - 1);
      set({ count: next, slots, activeIndex, transition: null });
    },
  }));
}
