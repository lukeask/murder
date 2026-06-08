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
 * is in the visible set. The real panels today are `crows` ({@link CrowsPanel}, crows-by-type) and
 * `usage` ({@link UsagePanel}) — both landed by C9; the flat {@link RosterPanel} is kept aside as
 * the copyable reference implementation. The remaining ids are {@link PlaceholderPanel}s tagged with
 * the chunk that fills them. A later chunk swaps its placeholder for a real panel copied from
 * `RosterPanel`/`CrowsPanel` — *this file changes only at its `renderPanel` case for that id*. That
 * is the skeleton's contract: stable composition, panels filled in independently.
 *
 * C13: `Shell` now wires the `spawn` deferred handler so `ctrl+s` opens the spawn wizard. The
 * spawn handler reads the focus + app store at invocation time to derive the spawn context
 * (focused doc → reference-by-path). See {@link deriveSpawnContext} for the C11 seam note.
 */

import { Box } from 'ink';
import type { JSX } from 'react';
import type { BusClient } from '../bus/BusClient.js';
import { AppStoreProvider, useAppStoreApi } from '../hooks/useAppStore.js';
import { BusClientProvider, useBusClient } from '../hooks/useBusClient.js';
import {
  type InputStores,
  InputStoresProvider,
  useInputStores,
  useModeStore,
  usePanelStore,
} from '../hooks/useInputStores.js';
import { useRootInput } from '../hooks/useRootInput.js';
import { resolveFocus } from '../input/focusStore.js';
import { selectActiveMode } from '../input/modeStore.js';
import type { PanelId } from '../input/panels.js';
import { createSpawnActions } from '../store/dialogs/spawnActions.js';
import type { AppStoreApi } from '../store/store.js';
import { BottomBar } from './BottomBar.js';
import { ChatInput } from './ChatInput.js';
import { CrowsPanel } from './CrowsPanel.js';
import { NotesPanel } from './NotesPanel.js';
import { Overlay, presentationHidesLayout } from './Overlay.js';
import { PlaceholderPanel } from './PlaceholderPanel.js';
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
 * Render one panel by id — the single dispatch from a {@link PanelId} to its component. Most ids are
 * now real panels (`crows`, `usage`, `notes`, `reports`, `tickets`); `plans` is still a labelled
 * {@link PlaceholderPanel}. A later chunk replaces a placeholder `case` with the real panel it copies
 * from `RosterPanel`/`CrowsPanel`; nothing else in the shell changes. Defined as a function (not
 * inline) so the swap is one localised edit.
 */
function renderPanel(id: PanelId): JSX.Element {
  switch (id) {
    case 'crows':
      // C9: CrowsPanel replaces the RosterPanel reference implementation here. The original
      // RosterPanel remains as the copy-reference; only this `case` changes.
      return <CrowsPanel />;
    case 'plans':
      return <PlaceholderPanel id={id} title="Plans" filledBy="C6/plans-TBD" />;
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
 * Derive the spawn context from the focus and app stores at `ctrl+s` invocation time.
 * Returns a {@link SpawnContext} when the focused panel is `notes` or `reports` AND at least one
 * row is available; otherwise `null` (no context step shown in the wizard).
 *
 * ## C11 seam — cursor-in-store
 * Each panel's cursor is currently local `useState`, inaccessible here. This function uses the
 * **first available row** as a best-effort proxy for the "selected" doc. When C11 lands
 * doc-toggle with cursor-in-store, update this function to use the real cursor index — the
 * {@link SpawnContext} shape and the wizard factory interface are already seam-ready; no changes
 * to `SpawnWizardModal` are needed.
 *
 * ## Reference-by-path (locked mechanism)
 * The returned `path` is `.murder/<dir>/<name>.md`. The wizard builds:
 *   `"Please read ${path} before starting."`
 * which the rogue receives as its kickoff message — it reads the file, not an inlined body.
 *
 * ## Plans panel
 * Plans (panel 1) is a placeholder (C6 TBD) — its slice does not exist yet. Once C6 lands plans
 * rows, add a `'plans'` branch here mirroring the `'notes'` branch. No wizard changes needed.
 */
export function deriveSpawnContext(
  focus: ReturnType<typeof import('../input/focusStore.js').createFocusStore>,
  appStore: AppStoreApi,
): SpawnContext | null {
  const focused = resolveFocus(focus.getState().intendedId, focus.panels.getState().visible);
  const state = appStore.getState();

  if (focused === 'notes') {
    const first = state.notes.rows[0];
    if (first === undefined) return null;
    return { title: first.name, path: `.murder/notes/${first.name}.md` };
  }
  if (focused === 'reports') {
    const first = state.reports.rows[0];
    if (first === undefined) return null;
    return { title: first.name, path: `.murder/reports/${first.name}.md` };
  }
  // 'plans': placeholder — no rows yet (C6 TBD). See doc above.
  // 'tickets', 'usage', 'crows', 'chat': not doc panels — no context.
  return null;
}

/**
 * The shell body — runs inside both providers so it can read the stores. Installs the one root input
 * loop, then lays out the always-visible chrome (top bar, chat input, bottom bar) around the two
 * toggleable panel regions. The middle row holds left + right regions side by side; each collapses
 * when it has no visible panels.
 *
 * C13: wires the `spawn` deferred handler so `ctrl+s` opens the spawn wizard. The handler reads
 * the focus + app store at invocation time (not during render) so it always sees current state.
 */
function Shell(): JSX.Element {
  const { modes, focus } = useInputStores();
  const appStore = useAppStoreApi();
  const bus = useBusClient();

  // `ctrl+s` → open the spawn wizard. Reads stores imperatively at call time (getState()) so no
  // stale closure; does NOT need useMemo/useCallback — stores are stable references.
  const spawnHandler = (): void => {
    // Snapshot the spawn context at invocation time (C11 seam: first-row proxy).
    const spawnContext = deriveSpawnContext(focus, appStore);
    const actions = createSpawnActions(bus);
    modes.getState().enter(spawnWizardMode(modes, actions, { spawnContext }));
  };

  // The single root input loop for the whole app (rule 5) — installed exactly once, here.
  // C13: `spawn` is now wired to the real spawn wizard handler.
  useRootInput({ spawn: spawnHandler });

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
