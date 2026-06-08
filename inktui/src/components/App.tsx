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

import { Box } from 'ink';
import { type JSX, useEffect } from 'react';
import type { BusClient } from '../bus/BusClient.js';
import { AppStoreProvider, useAppStore, useAppStoreApi } from '../hooks/useAppStore.js';
import { BusClientProvider, useBusClient } from '../hooks/useBusClient.js';
import {
  type InputStores,
  InputStoresProvider,
  useInputStores,
  useModeStore,
  usePanelStore,
} from '../hooks/useInputStores.js';
import { useRootInput } from '../hooks/useRootInput.js';
import type { ChatInputHandler } from '../input/dispatcher.js';
import { selectActiveMode } from '../input/modeStore.js';
import type { PanelId } from '../input/panels.js';
import { selectActiveAgentId } from '../selectors/conversationsSelectors.js';
import { createSpawnActions } from '../store/dialogs/spawnActions.js';
import { DOC_DIR } from '../store/docView/docViewSlice.js';
import type { AppStoreApi } from '../store/store.js';
import { BottomBar } from './BottomBar.js';
import { ChatInput } from './ChatInput.js';
import { CrowChatPanel } from './CrowChatPanel.js';
import { CrowsPanel } from './CrowsPanel.js';
import { NotesPanel } from './NotesPanel.js';
import { Overlay, presentationHidesLayout } from './Overlay.js';
import { PlansPanel } from './PlansPanel.js';
import { ReportsPanel } from './ReportsPanel.js';
import type { SpawnContext } from './SpawnWizardModal.js';
import { spawnWizardMode } from './SpawnWizardModal.js';
import { TicketsPanel } from './TicketsPanel.js';
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
 */
function renderPanel(id: PanelId): JSX.Element {
  switch (id) {
    case 'crows':
      // C9: CrowsPanel replaces the RosterPanel reference implementation here. The original
      // RosterPanel remains as the copy-reference; only this `case` changes.
      // C10: CrowChatPanel is stacked below CrowsPanel — favorited crows get a history pane here.
      // Visibility is tied to the crows toggle (panel 0); CrowChatPanel returns null when no
      // favorited crows exist, so it's layout-safe.
      return (
        <Box flexDirection="column" flexGrow={1}>
          <CrowsPanel />
          <CrowChatPanel />
        </Box>
      );
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
      return <UsagePanel />;
    default:
      return id satisfies never;
  }
}

/**
 * One region (left or right): renders, in order, each of its panels that is currently visible, or
 * nothing when none are — so the region's box collapses out of the layout when empty (the plan's
 * "left panel visible if any of 1–4 active", "right panel visible if 0 or 9 active"). Pure over the
 * visible set + the region's panel order.
 */
function PanelRegion({ panels }: { readonly panels: readonly PanelId[] }): JSX.Element | null {
  const visible = usePanelStore((s) => s.visible);
  const shown = panels.filter((id) => visible.has(id));
  if (shown.length === 0) {
    return null;
  }
  return (
    <Box flexDirection="row" columnGap={1} flexGrow={1}>
      {shown.map((id) => (
        <Box key={id} flexGrow={1}>
          {renderPanel(id)}
        </Box>
      ))}
    </Box>
  );
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
 * "focused doc" is therefore whatever doc the user last *opened* in the in-layout doc view
 * ({@link ./DocViewMode.js}), held in the `docView` slice. Reading it replaces C13's first-row proxy
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
): ChatInputHandler {
  return {
    handleKey(input, key): boolean {
      // Enter → send the buffer to the active agent, then clear. Always consumed (Enter is chat's).
      if (key.return === true) {
        const message = chatInput.getState().text;
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
        chatInput.getState().clear();
        return true;
      }
      // Backspace/Delete → delete the last char. Consumed.
      if (key.backspace === true || key.delete === true) {
        chatInput.getState().backspace();
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
 * loop, then lays out the always-visible chrome (top bar, chat input, bottom bar) around the two
 * toggleable panel regions. The middle row holds left + right regions side by side; each collapses
 * when it has no visible panels.
 *
 * C13: wires the `spawn` deferred handler so `ctrl+s` opens the spawn wizard. The handler reads the
 * app store at invocation time (not during render) so it always sees current state.
 *
 * C11: `ctrl+s` is dual-purpose (spawn from chat; star from a panel) — the dispatcher routes it, so
 * this `spawn` handler still only fires when chat is focused (see dispatcher.ts). The spawn context
 * is the OPEN doc-view (focused-doc), via {@link deriveSpawnContext}. C11 also loads the persisted
 * favorites once on mount via the favorites action.
 */
function Shell(): JSX.Element {
  const { modes, chatInput } = useInputStores();
  const appStore = useAppStoreApi();
  const bus = useBusClient();
  const loadFavorites = useAppStore((s) => s.actions.favorites.load);

  // Load persisted favorites once on mount (rule 3: via the action). Fire-and-forget; a rejection
  // (e.g. the modeled-not-live prefs RPC against a real bus) lands in the slice's error and leaves
  // favorites at their defaults — never crashes the shell.
  useEffect(() => {
    void loadFavorites();
  }, [loadFavorites]);

  // `ctrl+s` → open the spawn wizard (only fires when chat is focused; see dispatcher.ts). Reads the
  // store imperatively at call time (getState()) so no stale closure; stores are stable references.
  const spawnHandler = (): void => {
    // The focused doc is the open doc-view (C11 — replaces C13's first-row proxy).
    const spawnContext = deriveSpawnContext(appStore);
    const actions = createSpawnActions(bus);
    modes.getState().enter(spawnWizardMode(modes, actions, { spawnContext }));
  };

  // The single root input loop for the whole app (rule 5) — installed exactly once, here.
  // C13: `spawn` wired to the spawn wizard handler. C11: `chatInput` wired to the persistent
  // chat-input handler (buffers chars, sends on Enter to the active agent). Global ctrl-chords still
  // preempt it (layer 1 < layer 2), so the user can summon panels mid-message.
  useRootInput({ spawn: spawnHandler, chatInput: makeChatInputHandler(chatInput, appStore) });

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
  return (
    <Box flexDirection="column" width="100%">
      <TopBar />
      <Box flexDirection="row" columnGap={1} flexGrow={1}>
        <PanelRegion panels={LEFT_PANELS} />
        <PanelRegion panels={RIGHT_PANELS} />
      </Box>
      <ChatInput />
      <BottomBar />
      <Overlay />
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
}: {
  readonly store: AppStoreApi;
  readonly inputStores: InputStores;
  readonly bus: BusClient;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <BusClientProvider value={bus}>
          <Shell />
        </BusClientProvider>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}
