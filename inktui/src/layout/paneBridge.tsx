import { Box, Text } from 'ink';
import { type JSX, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { StageDocPane, useDocView } from '../components/DocPane.js';
import { META_SEP } from '../components/glyphs.js';
import { paneContentWidthForWidth } from '../components/Pane.js';
import {
  CrowsPanel as ContractCrowsPanel,
  type CrowsPanelRow,
  type CrowsPanelStatus,
} from '../components/panes/CrowsPanel.js';
import {
  HistoryPanel as ContractHistoryPanel,
  type HistoryPanelMode,
  type HistoryPanelRow,
  type HistoryPanelStatus,
} from '../components/panes/HistoryPanel.js';
import { NotesPanel as ContractNotesPanel } from '../components/panes/NotesPanel.js';
import {
  TreePanel as ContractTreePanel,
  type TreePanelData,
  type TreePanelLane,
} from '../components/panes/TreePanel.js';
import { PlansPanel as ContractPlansPanel } from '../components/panes/PlansPanel.js';
import { ReportsPanel as ContractReportsPanel } from '../components/panes/ReportsPanel.js';
import {
  TicketsPanel as ContractTicketsPanel,
  type TicketsPanelRow,
} from '../components/panes/TicketsPanel.js';
import {
  UsagePanel as ContractUsagePanel,
  type UsagePanelGroup,
} from '../components/panes/UsagePanel.js';
import { ChatPane } from '../components/Stage.js';
import { useTicketEditor } from '../components/TicketEditorMode.js';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useBindings,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import type { FocusId } from '../input/focusStore.js';
import type { KeymapEntry, PanelKeymap } from '../input/keymap.js';
import { PANELS, type PanelId } from '../input/panels.js';
import type { AgentIdentity } from '../selectors/agentIdentity.js';
import { deriveAgentIdentity } from '../selectors/agentIdentity.js';
import {
  isChatPaneOpen,
  selectActiveAgentId,
  selectOpenChatPanes,
} from '../selectors/conversationsSelectors.js';
import { type CrowsView, useCrowsView } from '../selectors/crowsSelectors.js';
import { harnessModelFooter, worktreeLabel } from '../selectors/harnessDisplay.js';
import {
  type HistoryMode,
  type HistoryRowView,
  useHistoryView,
} from '../selectors/historySelectors.js';
import { useNotesView } from '../selectors/notesSelectors.js';
import {
  parseDuration,
  resolveDurationJump,
  type TransitCursor,
  type TransitView,
  useTransitView,
} from '../selectors/transitSelectors.js';
import { selectUsageView } from '../selectors/usageSelectors.js';
import { usePlansView } from '../selectors/plansSelectors.js';
import { useReportsView } from '../selectors/reportsSelectors.js';
import { type TicketRowView, useTicketsView } from '../selectors/ticketsSelectors.js';
import { murderConfirmStore, resetConfirmStore } from '../store/murder/murderConfirmStore.js';
import type { AppStore } from '../store/store.js';
import { toastStore } from '../store/toast/toastStore.js';
import type { UsageState } from '../store/usage/usageSlice.js';
import type { Theme } from '../theme/buildTheme.js';
import { useTheme } from '../theme/themeStore.js';
import type {
  PaneAllocation,
  PaneId,
  PaneKind,
  PaneLayoutPlan,
  PanePresentation,
  PaneRegion,
  PaneRequest,
  PaneSizing,
} from './paneLayout.js';

const PANEL_SIZING: Record<PanelId, PaneSizing> = {
  plans: { min: { width: 25, height: 5 }, preferred: { width: 34, height: 14 } },
  notes: { min: { width: 25, height: 5 }, preferred: { width: 34, height: 14 } },
  reports: { min: { width: 25, height: 5 }, preferred: { width: 34, height: 14 } },
  tickets: { min: { width: 25, height: 5 }, preferred: { width: 42, height: 14 } },
  history: { min: { width: 25, height: 5 }, preferred: { width: 42, height: 14 } },
  tree: { min: { width: 25, height: 10 }, preferred: { width: 40, height: 13 } },
  usage: { min: { width: 20, height: 5 }, preferred: { width: 34, height: 13 } },
  crows: { min: { width: 18, height: 7 }, preferred: { width: 34, height: 13 } },
};

const STAGE_CHAT_SIZING: PaneSizing = {
  min: { width: 30, height: 5 },
  preferred: { width: 56, height: 18 },
};

const STAGE_DOC_SIZING: PaneSizing = {
  min: { width: 30, height: 5 },
  preferred: { width: 72, height: 22 },
};

const PANE_CHROME_HEIGHT = 2;

export interface BuildPaneRequestsInput {
  readonly state: AppStore;
  readonly visiblePanels: ReadonlySet<PanelId>;
  readonly focusedId: FocusId;
}

export interface RenderPaneAllocationContext {
  readonly state: AppStore;
  readonly chatIdentities: ReadonlyMap<string, AgentIdentity>;
}

function panelRegion(panelId: PanelId): PaneRegion {
  const placement = PANELS.find((panel) => panel.id === panelId);
  return placement?.region === 'right' ? 'rightAligned' : 'leftAligned';
}

function panelKind(panelId: PanelId): PaneKind {
  if (panelId === 'usage') {
    return 'usage';
  }
  if (panelId === 'tree') {
    return 'tree';
  }
  return 'listPane';
}

function panelOrder(panelId: PanelId): number {
  const index = PANELS.findIndex((panel) => panel.id === panelId);
  return index < 0 ? 100 : index;
}

function requestPriority(id: PaneId, focusedId: FocusId, base: number): number {
  return id === focusedId ? 1 : base;
}

export function usagePaneSizing(state: Pick<AppStore, 'usage'>): PaneSizing {
  const view = selectUsageView(state.usage);
  const stackedHeight = PANE_CHROME_HEIGHT + view.groups.length + state.usage.rows.length;
  return {
    min: PANEL_SIZING.usage.min,
    preferred: {
      width: PANEL_SIZING.usage.preferred.width,
      height: Math.max(PANEL_SIZING.usage.preferred.height, stackedHeight),
    },
  };
}

function panelSizing(panelId: PanelId, state: AppStore): PaneSizing {
  if (panelId === 'usage') {
    return usagePaneSizing(state);
  }
  return PANEL_SIZING[panelId];
}

type PanelStatus = 'ready' | 'loading' | 'error';

function listPanelStatus(status: 'idle' | 'loading' | 'ready' | 'error'): PanelStatus {
  return status === 'loading' || status === 'error' ? status : 'ready';
}

function historyPanelStatus(status: 'idle' | 'loading' | 'ready' | 'error'): HistoryPanelStatus {
  return status === 'loading' || status === 'error' ? status : 'idle';
}

type PlansIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open' | 'spawnPlanner';

const PlansPanelAdapter = memo(function PlansPanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const plans = useAppStore((state) => state.plans, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const refresh = useAppStore((state) => state.actions.plans.refresh);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const spawnPlanner = useAppStore((state) => state.actions.plans.spawnPlanner);
  const bindings = useBindings();
  const toggleDoc = useDocView('plan');
  const view = usePlansView(plans, favorites);
  const [cursor, setCursor] = useState(0);
  const rowCount = view.rows.length;
  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowIdAtCursor = useCallback(
    (): string | null => view.rows[clampedCursor]?.id ?? null,
    [clampedCursor, view.rows],
  );

  const keymap: PanelKeymap<PlansIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next plan',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev plan',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        { chord: { key: { return: true } }, intent: 'open', description: 'view doc' },
        { chord: { input: 'p' }, intent: 'spawnPlanner', description: 'spawn planner' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((current) => (rowCount === 0 ? 0 : Math.min(current + 1, rowCount - 1)));
            return;
          case 'cursorUp':
            setCursor((current) => Math.max(current - 1, 0));
            return;
          case 'refresh':
            void refresh();
            return;
          case 'star': {
            const id = rowIdAtCursor();
            if (id !== null) {
              void toggleFavorite(id);
            }
            return;
          }
          case 'open': {
            const id = rowIdAtCursor();
            if (id !== null) {
              toggleDoc(id);
            }
            return;
          }
          case 'spawnPlanner': {
            const id = rowIdAtCursor();
            if (id !== null) {
              void spawnPlanner(id);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [bindings, refresh, rowCount, rowIdAtCursor, spawnPlanner, toggleDoc, toggleFavorite],
  );
  usePanelKeymap('plans', keymap);

  const ref = useFocusRef();
  useMeasureFocus('plans', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractPlansPanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        rows={view.rows}
        cursor={clampedCursor}
        status={listPanelStatus(view.status)}
        error={view.error}
      />
    </Box>
  );
});

type NotesIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

const NotesPanelAdapter = memo(function NotesPanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const notes = useAppStore((state) => state.notes, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const refresh = useAppStore((state) => state.actions.notes.refresh);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const bindings = useBindings();
  const toggleDoc = useDocView('note');
  const view = useNotesView(notes, favorites);
  const [cursor, setCursor] = useState(0);
  const rowCount = view.rows.length;
  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowNameAtCursor = useCallback(
    (): string | null => view.rows[clampedCursor]?.name ?? null,
    [clampedCursor, view.rows],
  );

  const keymap: PanelKeymap<NotesIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next note',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev note',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        { chord: { key: { return: true } }, intent: 'open', description: 'view doc' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((current) => (rowCount === 0 ? 0 : Math.min(current + 1, rowCount - 1)));
            return;
          case 'cursorUp':
            setCursor((current) => Math.max(current - 1, 0));
            return;
          case 'refresh':
            void refresh();
            return;
          case 'star': {
            const name = rowNameAtCursor();
            if (name !== null) {
              void toggleFavorite(name);
            }
            return;
          }
          case 'open': {
            const name = rowNameAtCursor();
            if (name !== null) {
              toggleDoc(name);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [bindings, refresh, rowCount, rowNameAtCursor, toggleDoc, toggleFavorite],
  );
  usePanelKeymap('notes', keymap);

  const ref = useFocusRef();
  useMeasureFocus('notes', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractNotesPanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        rows={view.rows}
        cursor={clampedCursor}
        status={listPanelStatus(view.status)}
        error={view.error}
      />
    </Box>
  );
});

type ReportsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

const ReportsPanelAdapter = memo(function ReportsPanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const reports = useAppStore((state) => state.reports, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const refresh = useAppStore((state) => state.actions.reports.refresh);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const bindings = useBindings();
  const toggleDoc = useDocView('report');
  const view = useReportsView(reports, favorites);
  const [cursor, setCursor] = useState(0);
  const rowCount = view.rows.length;
  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowNameAtCursor = useCallback(
    (): string | null => view.rows[clampedCursor]?.name ?? null,
    [clampedCursor, view.rows],
  );

  const keymap: PanelKeymap<ReportsIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next report',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev report',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        { chord: { key: { return: true } }, intent: 'open', description: 'view doc' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((current) => (rowCount === 0 ? 0 : Math.min(current + 1, rowCount - 1)));
            return;
          case 'cursorUp':
            setCursor((current) => Math.max(current - 1, 0));
            return;
          case 'refresh':
            void refresh();
            return;
          case 'star': {
            const name = rowNameAtCursor();
            if (name !== null) {
              void toggleFavorite(name);
            }
            return;
          }
          case 'open': {
            const name = rowNameAtCursor();
            if (name !== null) {
              toggleDoc(name);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [bindings, refresh, rowCount, rowNameAtCursor, toggleDoc, toggleFavorite],
  );
  usePanelKeymap('reports', keymap);

  const ref = useFocusRef();
  useMeasureFocus('reports', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractReportsPanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        rows={view.rows}
        cursor={clampedCursor}
        status={listPanelStatus(view.status)}
        error={view.error}
      />
    </Box>
  );
});

type TicketsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'open';

export function ticketsPanelRowsFromView(
  rows: readonly TicketRowView[],
): readonly TicketsPanelRow[] {
  return rows.map((row) => ({
    id: row.id,
    idCell: row.idCell,
    titleCell: row.titleCell,
    statusCell: row.statusCell,
    statusTone: row.statusTone,
    lastUpdateCell: row.lastUpdateCell,
    depsCell: row.depsCell,
    depsSatisfied: row.depsSatisfied,
    scheduleCell: row.scheduleCell,
    harnessCell: row.harnessCell,
    modelCell: row.modelCell,
    planCell: row.planCell,
    worktreeCell: row.worktreeCell,
  }));
}

const TicketsPanelAdapter = memo(function TicketsPanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const tickets = useAppStore((state) => state.tickets, shallow);
  const refresh = useAppStore((state) => state.actions.tickets.refresh);
  const view = useTicketsView(tickets);
  const rows = useMemo(() => ticketsPanelRowsFromView(view.rows), [view.rows]);
  const openEditor = useTicketEditor();
  const [cursor, setCursor] = useState(0);
  const rowCount = rows.length;
  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));
  const cursorRef = useRef(clampedCursor);
  const rowsRef = useRef(rows);
  cursorRef.current = clampedCursor;
  rowsRef.current = rows;

  const keymap: PanelKeymap<TicketsIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next ticket',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev ticket',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { key: { return: true } }, intent: 'open', description: 'open ticket' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((current) => (rowCount === 0 ? 0 : Math.min(current + 1, rowCount - 1)));
            return;
          case 'cursorUp':
            setCursor((current) => Math.max(current - 1, 0));
            return;
          case 'refresh':
            void refresh();
            return;
          case 'open': {
            const row = rowsRef.current[cursorRef.current];
            if (row !== undefined) {
              openEditor(row.id);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [openEditor, refresh, rowCount],
  );
  usePanelKeymap('tickets', keymap);

  const ref = useFocusRef();
  useMeasureFocus('tickets', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractTicketsPanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        rows={rows}
        cursor={clampedCursor}
        status={listPanelStatus(view.status)}
        error={view.error}
      />
    </Box>
  );
});

type HistoryIntent = 'cursorDown' | 'cursorUp' | 'resumeOrRefresh' | 'toggleMode' | 'dismiss';

export function historyPanelRowsFromView(rows: readonly HistoryRowView[]): readonly HistoryPanelRow[] {
  return rows.map((row) => ({
    id: row.itemId,
    age: row.age,
    target: row.target,
    status: row.status,
    text: row.text,
  }));
}

const HistoryPanelAdapter = memo(function HistoryPanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const history = useAppStore((state) => state.history, shallow);
  const refresh = useAppStore((state) => state.actions.history.refresh);
  const dismiss = useAppStore((state) => state.actions.history.dismiss);
  const resumeConversation = useAppStore((state) => state.actions.history.resumeConversation);
  const [mode, setMode] = useState<HistoryMode>('loose');
  const [cursor, setCursor] = useState(0);
  const view = useHistoryView(history, mode);
  const rows = useMemo(() => historyPanelRowsFromView(view.rows), [view.rows]);
  const rowCount = rows.length;
  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));
  const panelMode: HistoryPanelMode = mode;
  const cursorRef = useRef(clampedCursor);
  const rowsRef = useRef(view.rows);
  cursorRef.current = clampedCursor;
  rowsRef.current = view.rows;

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const keymap: PanelKeymap<HistoryIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next item',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev item',
        },
        { chord: { input: 'r' }, intent: 'resumeOrRefresh', description: 'resume / refresh' },
        { chord: { input: 'a' }, intent: 'toggleMode', description: 'loose ↔ all' },
        { chord: { input: 'x' }, intent: 'dismiss', description: 'dismiss' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((current) => (rowCount === 0 ? 0 : Math.min(current + 1, rowCount - 1)));
            return;
          case 'cursorUp':
            setCursor((current) => Math.max(current - 1, 0));
            return;
          case 'resumeOrRefresh': {
            const row = rowsRef.current[cursorRef.current];
            if (row?.resumable) {
              void resumeConversation(row.conversationId);
              return;
            }
            void refresh();
            return;
          }
          case 'toggleMode':
            setMode((current) => (current === 'loose' ? 'all' : 'loose'));
            return;
          case 'dismiss': {
            const row = rowsRef.current[cursorRef.current];
            if (row !== undefined) {
              void dismiss(row.itemId);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [dismiss, refresh, resumeConversation, rowCount],
  );
  usePanelKeymap('history', keymap);

  const ref = useFocusRef();
  useMeasureFocus('history', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractHistoryPanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        rows={rows}
        mode={panelMode}
        cursor={clampedCursor}
        status={historyPanelStatus(view.status)}
        error={view.error}
      />
    </Box>
  );
});

type CrowsIntent =
  | 'cursorDown'
  | 'cursorUp'
  | 'refresh'
  | 'toggleExpanded'
  | 'star'
  | 'openChat'
  | 'murder'
  | 'reset';

export function crowsPanelRowsFromView(view: CrowsView): readonly CrowsPanelRow[] {
  return view.sections.flatMap((section) =>
    section.rows.map((row) => ({
      id: row.agentId,
      group: section.label,
      name: row.name,
      meta: `${row.harness} · ${row.model}`,
      working: row.working,
      starred: row.favorited,
      health: row.health,
    })),
  );
}

function crowsPanelStatus(status: CrowsView['status']): CrowsPanelStatus {
  return status === 'loading' || status === 'error' ? status : 'idle';
}

const CrowsPanelAdapter = memo(function CrowsPanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const roster = useAppStore((state) => state.roster, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const conversations = useAppStore((state) => state.conversations, shallow);
  const refresh = useAppStore((state) => state.actions.roster.refresh);
  const resetCrow = useAppStore((state) => state.actions.roster.resetCrow);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const setActivePane = useAppStore((state) => state.actions.conversations.setActivePaneAgentId);
  const toggleChatPane = useAppStore((state) => state.actions.conversations.toggleChatPane);
  const bindings = useBindings();
  const view = useCrowsView(roster, favorites);
  const rows = useMemo(() => crowsPanelRowsFromView(view), [view]);
  const [cursor, setCursor] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const clampedCursor = Math.min(cursor, Math.max(rows.length - 1, 0));

  const agentIdAtCursor = useCallback((): string | null => {
    return rows[clampedCursor]?.id ?? null;
  }, [clampedCursor, rows]);

  const nameAtCursor = useCallback(
    (agentId: string): string => rows.find((row) => row.id === agentId)?.name ?? agentId,
    [rows],
  );

  const openChatAtCursor = useCallback(() => {
    const agentId = agentIdAtCursor();
    if (agentId === null) {
      return;
    }
    const rosterRow = roster.rows.find((row) => row.agentId === agentId);
    const identity = rosterRow === undefined ? null : deriveAgentIdentity(rosterRow);
    if (identity === null) {
      return;
    }
    const currentlyOpen = isChatPaneOpen(identity, favorites, conversations.paneOverrides);
    toggleChatPane(agentId, currentlyOpen);
    if (!currentlyOpen) {
      setActivePane(agentId);
    }
  }, [agentIdAtCursor, conversations, favorites, roster, setActivePane, toggleChatPane]);

  const keymap: PanelKeymap<CrowsIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next crow',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev crow',
        },
        { chord: bindings.chordsFor('global.murder'), intent: 'murder', description: 'murder' },
        { chord: { key: { return: true } }, intent: 'openChat', description: 'toggle chat pane' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { input: 'm' }, intent: 'toggleExpanded', description: 'toggle maximized' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        {
          chord: bindings.chordsFor('panel.resetCrow'),
          intent: 'reset',
          description: 'reset crow',
        },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((current) => (rows.length === 0 ? 0 : Math.min(current + 1, rows.length - 1)));
            return;
          case 'cursorUp':
            setCursor((current) => Math.max(current - 1, 0));
            return;
          case 'refresh':
            void refresh();
            return;
          case 'toggleExpanded':
            setExpanded((current) => !current);
            return;
          case 'openChat':
            openChatAtCursor();
            return;
          case 'star': {
            const agentId = agentIdAtCursor();
            if (agentId !== null) {
              void toggleFavorite(agentId);
              setActivePane(agentId);
            }
            return;
          }
          case 'murder': {
            const agentId = agentIdAtCursor();
            if (agentId !== null) {
              murderConfirmStore.getState().arm({ agentId, name: nameAtCursor(agentId) });
            }
            return;
          }
          case 'reset': {
            const agentId = agentIdAtCursor();
            if (agentId === null) {
              return;
            }
            const ticketId = roster.rows.find((row) => row.agentId === agentId)?.ticketId ?? null;
            if (ticketId === null) {
              toastStore.getState().push('no ticket to reset for this row', { ttlMs: 4000 });
              return;
            }
            const name = nameAtCursor(agentId);
            const pending = resetConfirmStore.getState().pending;
            if (pending !== null && pending.ticketId === ticketId) {
              resetConfirmStore.getState().clear();
              void resetCrow(ticketId)
                .then(() => {
                  toastStore.getState().push(`reset ${pending.name} → ready`, { ttlMs: 6000 });
                })
                .catch((error: unknown) => {
                  const message = error instanceof Error ? error.message : String(error);
                  toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
                });
              return;
            }
            resetConfirmStore.getState().arm({ ticketId, name });
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [
      agentIdAtCursor,
      bindings,
      nameAtCursor,
      openChatAtCursor,
      refresh,
      resetCrow,
      roster,
      rows.length,
      setActivePane,
      toggleFavorite,
    ],
  );
  usePanelKeymap('crows', keymap);

  const ref = useFocusRef();
  useMeasureFocus('crows', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractCrowsPanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        rows={rows}
        cursor={clampedCursor}
        expanded={expanded}
        status={crowsPanelStatus(view.status)}
        error={view.error}
      />
    </Box>
  );
});

type TreeIntent =
  | 'older'
  | 'newer'
  | 'laneDown'
  | 'laneUp'
  | 'startG'
  | 'gEnter'
  | 'gEsc'
  | `char:${string}`;

const TREE_ALPHA = 'abcdefghijklmnopqrstuvwxyz';
const TREE_DIGITS = '0123456789';
const TREE_UNIT_LETTERS = new Set(['m', 'h', 'd', 'w']);

function treeLaneColor(index: number, mainIndex: number, theme: Theme): string {
  if (index === mainIndex) {
    return theme.active;
  }
  return theme.laneColors[index % theme.laneColors.length] ?? theme.text;
}

function transitRailText(lane: TransitView['lanes'][number]): string {
  return lane.segments.map((segment) => segment.text).join('');
}

export function treePanelDataFromView(
  view: TransitView,
  cursor: TransitCursor,
  gPending: boolean,
  gBuffer: string,
  theme: Theme,
): TreePanelData {
  const lanes: TreePanelLane[] = view.lanes.map((lane, index) => ({
    branch: lane.branch,
    rail: transitRailText(lane),
    color: treeLaneColor(lane.colorIndex, view.mainIndex, theme),
    selected: index === cursor.laneIndex,
  }));
  const info = gPending
    ? [
        view.lanes.map((lane) => `[${lane.hint}] ${lane.branch}`).join('  '),
        `type 5d/20m +⏎  ${gBuffer.length > 0 ? `· ${gBuffer}` : ''}`,
      ]
    : view.selected === null
      ? ['no commit selected']
      : [`${view.selected.short} · ${view.selected.branch} · ${view.selected.age}`, ...view.infoLines];
  return {
    ruler: view.ruler,
    lanes,
    info,
    pending: gPending,
    status: view.status,
    error: view.error,
  };
}

function nearestShaByTime(
  lane: { readonly commits: readonly { readonly sha: string; readonly tsEpoch: number }[] },
  tsEpoch: number | null,
): string | null {
  if (lane.commits.length === 0) {
    return null;
  }
  if (tsEpoch === null) {
    return lane.commits[0]?.sha ?? null;
  }
  let best = lane.commits[0];
  let bestDelta = Number.POSITIVE_INFINITY;
  for (const commit of lane.commits) {
    const delta = Math.abs(commit.tsEpoch - tsEpoch);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = commit;
    }
  }
  return best?.sha ?? null;
}

const TreePanelAdapter = memo(function TreePanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const transit = useAppStore((state) => state.transit, shallow);
  const refresh = useAppStore((state) => state.actions.transit.refresh);
  const theme = useTheme();
  const innerWidth = paneContentWidthForWidth(presentation.width);
  const [cursor, setCursor] = useState<TransitCursor>({ laneIndex: 0, sha: null });
  const [gBuffer, setGBuffer] = useState<string | null>(null);
  const gPending = gBuffer !== null;
  const view = useTransitView(transit, cursor, innerWidth);
  const data = useMemo(
    () => treePanelDataFromView(view, cursor, gPending, gBuffer ?? '', theme),
    [cursor, gBuffer, gPending, theme, view],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (cursor.sha === null && transit.lanes.length > 0) {
      const head = transit.lanes[0]?.headSha ?? null;
      if (head !== null) {
        setCursor({ laneIndex: 0, sha: head });
      }
    }
  }, [cursor.sha, transit.lanes]);

  const selectedLane = transit.lanes[cursor.laneIndex] ?? null;

  const moveWithinLane = useCallback(
    (delta: number) => {
      const lane = transit.lanes[cursor.laneIndex];
      if (lane === undefined || lane.commits.length === 0) {
        return;
      }
      const index = lane.commits.findIndex((commit) => commit.sha === cursor.sha);
      const current = index >= 0 ? index : 0;
      const next = Math.min(Math.max(current + delta, 0), lane.commits.length - 1);
      const sha = lane.commits[next]?.sha ?? null;
      setCursor((currentCursor) => ({ ...currentCursor, sha }));
    },
    [cursor.laneIndex, cursor.sha, transit.lanes],
  );

  const switchLane = useCallback(
    (delta: number) => {
      if (transit.lanes.length === 0) {
        return;
      }
      const nextIndex = Math.min(Math.max(cursor.laneIndex + delta, 0), transit.lanes.length - 1);
      if (nextIndex === cursor.laneIndex) {
        return;
      }
      const currentLane = transit.lanes[cursor.laneIndex];
      const currentTs =
        currentLane?.commits.find((commit) => commit.sha === cursor.sha)?.tsEpoch ?? null;
      const nextLane = transit.lanes[nextIndex];
      if (nextLane === undefined) {
        return;
      }
      setCursor({ laneIndex: nextIndex, sha: nearestShaByTime(nextLane, currentTs) });
    },
    [cursor.laneIndex, cursor.sha, transit.lanes],
  );

  const jumpToSha = useCallback(
    (sha: string) => {
      const mainIndex = transit.lanes.findIndex((lane) => lane.isMain);
      const mainLane = mainIndex >= 0 ? transit.lanes[mainIndex] : undefined;
      if (mainLane?.commits.some((commit) => commit.sha === sha)) {
        setCursor({ laneIndex: mainIndex, sha });
        return;
      }
      setCursor((current) => ({ ...current, sha }));
    },
    [transit.lanes],
  );

  const handleGChar = useCallback(
    (ch: string) => {
      const buffer = gBuffer ?? '';
      if (buffer.length === 0) {
        const laneByHint = view.lanes.find((lane) => lane.hint === ch);
        if (laneByHint !== undefined) {
          const lane = transit.lanes.find((candidate) => candidate.branch === laneByHint.branch);
          if (lane !== undefined) {
            const laneIndex = transit.lanes.indexOf(lane);
            setCursor({ laneIndex, sha: lane.headSha });
          }
          setGBuffer(null);
          return;
        }
      }
      if (ch >= '0' && ch <= '9') {
        setGBuffer((current) => (current ?? '') + ch);
        return;
      }
      if (TREE_UNIT_LETTERS.has(ch)) {
        setGBuffer((current) => (current ?? '') + ch);
      }
    },
    [gBuffer, transit.lanes, view.lanes],
  );

  const resolveG = useCallback(() => {
    const buffer = gBuffer ?? '';
    const ms = parseDuration(buffer);
    if (ms !== null && selectedLane !== null) {
      const resolved = resolveDurationJump(selectedLane, ms, Date.now());
      if (resolved !== null) {
        jumpToSha(resolved.sha);
      }
    }
    setGBuffer(null);
  }, [gBuffer, jumpToSha, selectedLane]);

  const keymap: PanelKeymap<TreeIntent> = useMemo(() => {
    const charEntries: KeymapEntry<TreeIntent>[] = [];
    for (const ch of TREE_ALPHA + TREE_DIGITS) {
      charEntries.push({
        chord: { input: ch },
        intent: `char:${ch}`,
        description: '',
        hidden: true,
      });
    }
    return {
      keymap: [
        {
          chord: [{ input: 'l' }, { key: { rightArrow: true } }],
          intent: 'newer',
          description: 'newer',
        },
        {
          chord: [{ input: 'h' }, { key: { leftArrow: true } }],
          intent: 'older',
          description: 'older',
        },
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'laneDown',
          description: 'next lane',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'laneUp',
          description: 'prev lane',
        },
        { chord: { input: 'g' }, intent: 'startG', description: 'jump (g)' },
        { chord: { key: { return: true } }, intent: 'gEnter', description: 'resolve' },
        { chord: { key: { escape: true } }, intent: 'gEsc', description: 'cancel' },
        ...charEntries,
      ],
      onIntent(intent) {
        if (intent.startsWith('char:')) {
          if (gPending) {
            handleGChar(intent.slice('char:'.length));
          }
          return;
        }
        switch (intent) {
          case 'older':
            if (gPending) {
              handleGChar('h');
            } else {
              moveWithinLane(1);
            }
            return;
          case 'newer':
            if (!gPending) {
              moveWithinLane(-1);
            }
            return;
          case 'laneDown':
            if (!gPending) {
              switchLane(1);
            }
            return;
          case 'laneUp':
            if (!gPending) {
              switchLane(-1);
            }
            return;
          case 'startG':
            setGBuffer('');
            return;
          case 'gEnter':
            if (gPending) {
              resolveG();
            }
            return;
          case 'gEsc':
            if (gPending) {
              setGBuffer(null);
            }
            return;
          default:
            return;
        }
      },
    };
  }, [gPending, handleGChar, moveWithinLane, resolveG, switchLane]);
  usePanelKeymap('tree', keymap);

  const ref = useFocusRef();
  useMeasureFocus('tree', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractTreePanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        data={data}
      />
    </Box>
  );
});

type UsageIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'cycleSteering';

const USAGE_STEERING_CYCLE: Record<string, string> = {
  auto: 'prefer',
  prefer: 'pause',
  pause: 'auto',
};

function nextUsageSteering(current: string): string {
  return USAGE_STEERING_CYCLE[current] ?? 'prefer';
}

function pctFromLabel(label: string): number {
  const pct = Number.parseInt(label.replace(/%$/, ''), 10);
  return Number.isFinite(pct) ? pct : 0;
}

export function usagePanelGroupsFromState(state: UsageState): readonly UsagePanelGroup[] {
  return selectUsageView(state).groups.map((group) => ({
    harness: group.harness,
    steering: group.steering,
    gauges: group.gauges.map((gauge) => ({
      label: gauge.windowLabel,
      pct: pctFromLabel(gauge.pctLabel),
      reset: gauge.resetLabel,
    })),
  }));
}

function usageGaugeCount(groups: readonly UsagePanelGroup[]): number {
  return groups.reduce((count, group) => count + group.gauges.length, 0);
}

function usagePanelStatus(status: UsageState['status']): 'ready' | 'loading' | 'error' {
  return status === 'idle' ? 'ready' : status;
}

const UsagePanelAdapter = memo(function UsagePanelAdapter({
  presentation,
}: {
  readonly presentation: PanePresentation;
}): JSX.Element {
  const usage = useAppStore((state) => state.usage, shallow);
  const sample = useAppStore((state) => state.actions.usage.sample);
  const setSteering = useAppStore((state) => state.actions.usage.setSteering);
  const bindings = useBindings();
  const groups = useMemo(() => usagePanelGroupsFromState(usage), [usage]);
  const gaugeCount = usageGaugeCount(groups);
  const [cursor, setCursor] = useState(0);
  const clampedCursor = Math.min(cursor, Math.max(gaugeCount - 1, 0));
  const keymap: PanelKeymap<UsageIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next gauge',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev gauge',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'sample' },
        {
          chord: bindings.chordsFor('panel.usageSteering'),
          intent: 'cycleSteering',
          description: 'steering',
        },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((current) => (gaugeCount === 0 ? 0 : Math.min(current + 1, gaugeCount - 1)));
            return;
          case 'cursorUp':
            setCursor((current) => Math.max(current - 1, 0));
            return;
          case 'refresh':
            void sample();
            return;
          case 'cycleSteering': {
            if (gaugeCount === 0) {
              return;
            }
            let index = clampedCursor;
            for (const group of groups) {
              if (index < group.gauges.length) {
                void setSteering(group.harness, nextUsageSteering(group.steering));
                return;
              }
              index -= group.gauges.length;
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [bindings, clampedCursor, gaugeCount, groups, sample, setSteering],
  );
  usePanelKeymap('usage', keymap);

  const ref = useFocusRef();
  useMeasureFocus('usage', ref);

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractUsagePanel
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        groups={groups}
        cursor={clampedCursor}
        status={usagePanelStatus(usage.status)}
        error={usage.error}
      />
    </Box>
  );
});

export function buildPaneRequests(input: BuildPaneRequestsInput): readonly PaneRequest[] {
  const { state, visiblePanels, focusedId } = input;
  const requests: PaneRequest[] = [];

  for (const panel of PANELS) {
    if (!visiblePanels.has(panel.id)) {
      continue;
    }
    requests.push({
      id: panel.id,
      kind: panelKind(panel.id),
      region: panelRegion(panel.id),
      sizing: panelSizing(panel.id, state),
      reapPriority: requestPriority(panel.id, focusedId, 40 + panelOrder(panel.id)),
      orderKey: panelOrder(panel.id),
      source: { type: 'panel', panelId: panel.id },
    });
  }

  const currentAgentId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
  const lockedPanes = selectOpenChatPanes(
    state.roster,
    state.favorites,
    state.conversations.paneOverrides,
  ).panes;
  const chatPanesByAgentId = new Map<string, { identity: AgentIdentity; locked: boolean }>();
  for (const identity of lockedPanes) {
    chatPanesByAgentId.set(identity.agentId, { identity, locked: true });
  }
  if (currentAgentId !== null && !chatPanesByAgentId.has(currentAgentId)) {
    const row = state.roster.rows.find((candidate) => candidate.agentId === currentAgentId);
    const identity = row === undefined ? null : deriveAgentIdentity(row);
    if (identity !== null) {
      chatPanesByAgentId.set(currentAgentId, { identity, locked: false });
    }
  }

  let stageOrder = 1000;
  if (state.docView.open !== null) {
    const id: PaneId = `stage:doc:${state.docView.open.name}`;
    requests.push({
      id,
      kind: 'stageDoc',
      region: 'centerStage',
      sizing: STAGE_DOC_SIZING,
      reapPriority: requestPriority(id, focusedId, 12),
      orderKey: stageOrder++,
      source: { type: 'stageDoc', name: state.docView.open.name },
    });
  }

  for (const { identity, locked } of chatPanesByAgentId.values()) {
    const current = identity.agentId === currentAgentId;
    const id: PaneId = `stage:chat:${identity.agentId}`;
    requests.push({
      id,
      kind: 'stageChat',
      region: 'centerStage',
      sizing: STAGE_CHAT_SIZING,
      reapPriority: requestPriority(id, focusedId, current ? 1 : locked ? 24 : 10),
      orderKey: stageOrder++,
      source: {
        type: 'stageChat',
        agentId: identity.agentId,
        locked,
        ephemeral: !locked,
        current,
      },
    });
  }

  return requests;
}

function renderPanel(panelId: PanelId, presentation: PanePresentation): JSX.Element {
  switch (panelId) {
    case 'crows':
      return <CrowsPanelAdapter presentation={presentation} />;
    case 'plans':
      return <PlansPanelAdapter presentation={presentation} />;
    case 'notes':
      return <NotesPanelAdapter presentation={presentation} />;
    case 'reports':
      return <ReportsPanelAdapter presentation={presentation} />;
    case 'tickets':
      return <TicketsPanelAdapter presentation={presentation} />;
    case 'history':
      return <HistoryPanelAdapter presentation={presentation} />;
    case 'tree':
      return <TreePanelAdapter presentation={presentation} />;
    case 'usage':
      return <UsagePanelAdapter presentation={presentation} />;
    default:
      return panelId satisfies never;
  }
}

function footerFor(state: AppStore, agentId: string): string | null {
  const row = state.roster.rows.find((candidate) => candidate.agentId === agentId);
  if (row === undefined) {
    return null;
  }
  return harnessModelFooter(row.harness, row.model, META_SEP);
}

function worktreeFor(state: AppStore, agentId: string): string | null {
  const row = state.roster.rows.find((candidate) => candidate.agentId === agentId);
  if (row === undefined) {
    return null;
  }
  return worktreeLabel(row.worktreePath ?? null);
}

export function renderPaneAllocation(
  allocation: PaneAllocation,
  context: RenderPaneAllocationContext,
): JSX.Element | null {
  const { presentation, request } = allocation;
  const body =
    request.source.type === 'panel' ? (
      renderPanel(request.source.panelId, presentation)
    ) : request.source.type === 'stageDoc' ? (
      context.state.docView.open === null ? null : (
        <StageDocPane open={context.state.docView.open} presentation={presentation} />
      )
    ) : (
      (() => {
        const identity = context.chatIdentities.get(request.source.agentId);
        if (identity === undefined) {
          return null;
        }
        return (
          <ChatPane
            identity={identity}
            conversations={context.state.conversations}
            chatTarget={request.source.current}
            footer={footerFor(context.state, identity.agentId)}
            worktree={worktreeFor(context.state, identity.agentId)}
            contentHeight={Math.max(0, presentation.height - PANE_CHROME_HEIGHT)}
            presentation={presentation}
          />
        );
      })()
    );
  if (body === null) {
    return null;
  }
  return (
    <Box
      key={request.id}
      width={presentation.width}
      height={presentation.height}
      flexShrink={0}
      minWidth={0}
      minHeight={0}
      overflow="hidden"
      flexDirection="column"
    >
      {body}
    </Box>
  );
}

function allocationsForRegion(plan: PaneLayoutPlan, region: PaneRegion): readonly PaneAllocation[] {
  return plan.allocations.filter((allocation) => allocation.request.region === region);
}

function regionRect(allocations: readonly PaneAllocation[]): { width: number; height: number } {
  if (allocations.length === 0) {
    return { width: 0, height: 0 };
  }
  const minX = Math.min(...allocations.map((allocation) => allocation.rect.x));
  const maxX = Math.max(
    ...allocations.map((allocation) => allocation.rect.x + allocation.rect.width),
  );
  const minY = Math.min(...allocations.map((allocation) => allocation.rect.y));
  const maxY = Math.max(
    ...allocations.map((allocation) => allocation.rect.y + allocation.rect.height),
  );
  return { width: maxX - minX, height: maxY - minY };
}

function allocationRows(
  allocations: readonly PaneAllocation[],
): readonly (readonly PaneAllocation[])[] {
  const sorted = [...allocations].sort(
    (a, b) =>
      a.rect.y - b.rect.y || a.rect.x - b.rect.x || a.request.id.localeCompare(b.request.id),
  );
  const rows: PaneAllocation[][] = [];
  for (const allocation of sorted) {
    const row = rows.find((candidate) => candidate[0]?.rect.y === allocation.rect.y);
    if (row === undefined) {
      rows.push([allocation]);
    } else {
      row.push(allocation);
    }
  }
  return rows;
}

export function createChatIdentityMap(state: AppStore): ReadonlyMap<string, AgentIdentity> {
  const identities = new Map<string, AgentIdentity>();
  for (const row of state.roster.rows) {
    const identity = deriveAgentIdentity(row);
    if (identity !== null) {
      identities.set(identity.agentId, identity);
    }
  }
  return identities;
}

export function renderPaneLayoutPlan(
  plan: PaneLayoutPlan,
  context: RenderPaneAllocationContext,
): JSX.Element {
  const left = allocationsForRegion(plan, 'leftAligned');
  const center = allocationsForRegion(plan, 'centerStage');
  const right = allocationsForRegion(plan, 'rightAligned');
  if (plan.allocations.length === 0) {
    return (
      <Box flexGrow={1} alignItems="center" justifyContent="center">
        <Text dimColor>no panes admitted</Text>
      </Box>
    );
  }
  const landscape = plan.orientation === 'landscape';
  const renderSide = (
    allocations: readonly PaneAllocation[],
    region: PaneRegion,
  ): JSX.Element | null => {
    if (allocations.length === 0) {
      return null;
    }
    const rect = regionRect(allocations);
    if (!landscape) {
      const rows = allocationRows(allocations);
      return (
        <Box
          key={region}
          width={plan.body.width}
          height={rect.height}
          flexShrink={0}
          minWidth={0}
          minHeight={0}
          overflow="hidden"
          flexDirection="column"
          rowGap={plan.gap}
        >
          {rows.map((row) => {
            const rowY = row[0]?.rect.y ?? 0;
            const rowHeight = Math.max(...row.map((allocation) => allocation.rect.height));
            return (
              <Box
                key={`${region}:row:${rowY}`}
                width={plan.body.width}
                height={rowHeight}
                flexShrink={0}
                minWidth={0}
                minHeight={0}
                overflow="hidden"
                flexDirection="row"
                columnGap={plan.gap}
              >
                {row.map((allocation) => renderPaneAllocation(allocation, context))}
              </Box>
            );
          })}
        </Box>
      );
    }
    return (
      <Box
        key={region}
        width={rect.width}
        height={plan.body.height}
        flexShrink={0}
        minWidth={0}
        minHeight={0}
        overflow="hidden"
        flexDirection="column"
        rowGap={plan.gap}
      >
        {allocations.map((allocation) => renderPaneAllocation(allocation, context))}
      </Box>
    );
  };
  const renderCenter = (): JSX.Element | null => {
    if (center.length === 0) {
      return null;
    }
    const rect = regionRect(center);
    const docs = center.filter((allocation) => allocation.request.kind === 'stageDoc');
    const chats = center.filter((allocation) => allocation.request.kind === 'stageChat');
    const renderAllocationGrid = (
      key: string,
      allocations: readonly PaneAllocation[],
      width: number,
      height: number,
    ): JSX.Element => {
      const rows = allocationRows(allocations);
      return (
        <Box
          key={key}
          width={width}
          height={height}
          flexShrink={0}
          minWidth={0}
          minHeight={0}
          overflow="hidden"
          flexDirection="column"
          rowGap={plan.gap}
        >
          {rows.map((row) => {
            const rowY = row[0]?.rect.y ?? 0;
            const rowHeight = Math.max(...row.map((allocation) => allocation.rect.height));
            return (
              <Box
                key={`${key}:row:${rowY}`}
                width={width}
                height={rowHeight}
                flexShrink={0}
                minWidth={0}
                minHeight={0}
                overflow="hidden"
                flexDirection="row"
                columnGap={plan.gap}
              >
                {row.map((allocation) => renderPaneAllocation(allocation, context))}
              </Box>
            );
          })}
        </Box>
      );
    };

    if (!landscape) {
      return (
        <Box
          key="center"
          width={plan.body.width}
          height={rect.height}
          flexShrink={0}
          minWidth={0}
          minHeight={0}
          overflow="hidden"
          flexDirection="column"
          rowGap={plan.gap}
        >
          {center.map((allocation) => renderPaneAllocation(allocation, context))}
        </Box>
      );
    }
    if (docs.length > 0 && chats.length > 0) {
      const docRect = regionRect(docs);
      const chatRect = regionRect(chats);
      return (
        <Box
          key="center"
          width={rect.width}
          height={plan.body.height}
          flexShrink={0}
          minWidth={0}
          minHeight={0}
          overflow="hidden"
          flexDirection="row"
          columnGap={plan.gap}
        >
          {renderAllocationGrid('center:docs', docs, docRect.width, docRect.height)}
          {renderAllocationGrid('center:chats', chats, chatRect.width, chatRect.height)}
        </Box>
      );
    }
    return renderAllocationGrid('center', center, rect.width, rect.height);
  };
  return (
    <Box
      flexDirection={landscape ? 'row' : 'column'}
      width={plan.body.width}
      height={plan.body.height}
      minWidth={0}
      minHeight={0}
      overflow="hidden"
      columnGap={landscape ? plan.gap : 0}
      rowGap={landscape ? 0 : plan.gap}
    >
      {renderSide(left, 'leftAligned')}
      {renderCenter()}
      {renderSide(right, 'rightAligned')}
    </Box>
  );
}
