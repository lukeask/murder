import { Box, Text } from 'ink';
import { type JSX, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TmuxFrameEvent } from '../bus/protocol.js';
import { META_SEP } from '../components/glyphs.js';
import { ChatPane as ContractChatPane } from '../components/panes/ChatPane.js';
import { CrowsController } from '../components/panes/CrowsController.js';
import { computeDocWindow } from '../components/panes/docWindow.js';
import { HistoryController } from '../components/panes/HistoryController.js';
import { NotesController } from '../components/panes/NotesController.js';
import { PlansController } from '../components/panes/PlansController.js';
import { ReportsController } from '../components/panes/ReportsController.js';
import { StageDocPane as ContractStageDocPane } from '../components/panes/StageDocPane.js';
import { TicketsController } from '../components/panes/TicketsController.js';
import { TreeController } from '../components/panes/TreeController.js';
import { UsageController } from '../components/panes/UsageController.js';
import { useAppStore } from '../hooks/useAppStore.js';
import { useBusClient } from '../hooks/useBusClient.js';
import { type GotoIntent, useGotoLine } from '../hooks/useGotoLine.js';
import {
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
  usePaneScrollBus,
} from '../hooks/useInputStores.js';
import { CHAT_FOCUS, type FocusId } from '../input/focusStore.js';
import type { PanelKeymap } from '../input/keymap.js';
import { PANELS, type PanelId } from '../input/panels.js';
import type { AgentIdentity } from '../selectors/agentIdentity.js';
import { deriveAgentIdentity } from '../selectors/agentIdentity.js';
import {
  selectActiveAgentId,
  selectOpenChatPanes,
  useConversationTurns,
} from '../selectors/conversationsSelectors.js';
import { harnessModelFooter, worktreeLabel } from '../selectors/harnessDisplay.js';
import { selectUsageView } from '../selectors/usageSelectors.js';
import { DOC_DIR } from '../store/docView/docViewSlice.js';
import type { AppStore } from '../store/store.js';
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

function stageChatFocusId(agentId: string): FocusId {
  return `stage:chat:${agentId}`;
}

function stageDocFocusId(name: string): FocusId {
  return `stage:doc:${name}`;
}

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
const DOC_SCROLL_STEP = 1;
const CHAT_SCROLL_STEP = 1;
const CHAT_NEAR_BOTTOM_THRESHOLD = 3;
const TMUX_WAITING_TEXT = '[waiting for tmux frame…]';

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

function chatKindLabel(kind: AgentIdentity['kind']): string {
  switch (kind) {
    case 'collaborator':
      return 'collab';
    case 'planner':
      return 'planner';
    case 'rogue':
      return 'rogue';
    default:
      return 'ticket';
  }
}

type DocIntent = 'close' | 'scrollDown' | 'scrollUp' | 'pageDown' | 'pageUp' | 'spawnPlanner';

const EMPTY_DOC_KEYMAP: PanelKeymap<DocIntent | GotoIntent> = { keymap: [], onIntent() {} };

const StageDocPaneAdapter = memo(function StageDocPaneAdapter({
  presentation,
  open,
}: {
  readonly presentation: PanePresentation;
  readonly open: NonNullable<AppStore['docView']['open']>;
}): JSX.Element {
  const body = useAppStore((state) => state.docView.body);
  const status = useAppStore((state) => state.docView.status);
  const error = useAppStore((state) => state.docView.error);
  const closeAction = useAppStore((state) => state.actions.docView.close);
  const spawnPlanner = useAppStore((state) => state.actions.plans.spawnPlanner);
  const ref = useFocusRef();
  const focusId = stageDocFocusId(open.name);
  useMeasureFocus(focusId, ref);

  const [scroll, setScroll] = useState(0);
  const lines = useMemo(() => (body === null ? [] : body.split('\n')), [body]);
  const effectiveHeight = Math.max(1, presentation.height - PANE_CHROME_HEIGHT);
  const { start: clampedScroll, maxScroll } = computeDocWindow(
    lines.length,
    scroll,
    effectiveHeight,
  );

  const jump = useCallback((line: number) => setScroll(Math.min(line - 1, maxScroll)), [maxScroll]);
  const goto = useGotoLine(jump);

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
        { chord: { key: { pageDown: true } }, intent: 'pageDown', description: 'page down' },
        { chord: { input: 'b' }, intent: 'pageUp', description: 'page up' },
        { chord: { key: { pageUp: true } }, intent: 'pageUp', description: 'page up' },
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
        if (goto.handle(intent)) {
          return;
        }
        goto.clear();
        switch (intent as DocIntent) {
          case 'close':
            closeAction();
            return;
          case 'scrollDown':
            setScroll((current) => Math.min(current + DOC_SCROLL_STEP, maxScroll));
            return;
          case 'scrollUp':
            setScroll((current) => Math.max(current - DOC_SCROLL_STEP, 0));
            return;
          case 'pageDown':
            setScroll((current) => Math.min(current + effectiveHeight, maxScroll));
            return;
          case 'pageUp':
            setScroll((current) => Math.max(current - effectiveHeight, 0));
            return;
          case 'spawnPlanner':
            void spawnPlanner(open.name);
            return;
        }
      },
    }),
    [closeAction, effectiveHeight, goto, maxScroll, open.kind, open.name, spawnPlanner],
  );
  usePanelKeymap(focusId, presentation.focused ? keymap : EMPTY_DOC_KEYMAP);

  const paneScroll = usePaneScrollBus();
  const maxScrollRef = useRef(maxScroll);
  maxScrollRef.current = maxScroll;
  useEffect(
    () =>
      paneScroll.subscribe(focusId, (direction, amount) => {
        setScroll((current) =>
          direction === 'up'
            ? Math.max(current - amount, 0)
            : Math.min(current + amount, maxScrollRef.current),
        );
      }),
    [focusId, paneScroll],
  );

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractStageDocPane
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        title={`.murder/${DOC_DIR[open.kind]}/${open.name}.md`}
        lines={lines}
        scroll={clampedScroll}
        status={status === 'idle' ? 'ready' : status}
        error={error}
      />
    </Box>
  );
});

type ChatScrollIntent = 'scrollUp' | 'scrollDown';

const EMPTY_CHAT_KEYMAP: PanelKeymap<ChatScrollIntent | GotoIntent> = {
  keymap: [],
  onIntent() {},
};

const StageChatPaneAdapter = memo(function StageChatPaneAdapter({
  presentation,
  identity,
  state,
  chatTarget,
}: {
  readonly presentation: PanePresentation;
  readonly identity: AgentIdentity;
  readonly state: AppStore;
  readonly chatTarget: boolean;
}): JSX.Element {
  const ref = useFocusRef();
  const theme = useTheme();
  const focusId = stageChatFocusId(identity.agentId);
  const effectiveFocus = useEffectiveFocus();
  const highlighted = presentation.focused || (chatTarget && effectiveFocus === CHAT_FOCUS);
  useMeasureFocus(focusId, ref);

  const defaultChatViewMode = useAppStore((current) => current.settings.defaultChatViewMode);
  const viewMode = state.conversations.paneViewModes[identity.agentId] ?? defaultChatViewMode;
  const turns = useConversationTurns(identity.agentId, state.conversations, viewMode);
  const [scrollUp, setScrollUp] = useState(0);
  const [gotoLine, setGotoLine] = useState<number | null>(null);
  const [chatMetrics, setChatMetrics] = useState({ lineCount: 0, maxScrollUp: 0 });
  const maxScrollUp = chatMetrics.maxScrollUp;

  const prevLenRef = useRef<number | null>(null);
  const wasNearBottomRef = useRef(true);
  if (prevLenRef.current === null || chatMetrics.lineCount <= prevLenRef.current) {
    wasNearBottomRef.current = scrollUp <= CHAT_NEAR_BOTTOM_THRESHOLD;
  }
  useEffect(() => {
    const prevLen = prevLenRef.current;
    prevLenRef.current = chatMetrics.lineCount;
    if (prevLen === null) {
      return;
    }
    const delta = chatMetrics.lineCount - prevLen;
    if (delta <= 0) {
      setScrollUp((current) => Math.min(current, maxScrollUp));
      return;
    }
    if (wasNearBottomRef.current) {
      setScrollUp(0);
    } else {
      setScrollUp((current) => Math.min(current + delta, maxScrollUp));
    }
  }, [chatMetrics.lineCount, maxScrollUp]);

  const jump = useCallback((line: number) => setGotoLine(line), []);
  const goto = useGotoLine(jump);
  const keymap: PanelKeymap<ChatScrollIntent | GotoIntent> = useMemo(
    () => ({
      keymap: [
        ...goto.entries,
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'scrollUp',
          description: 'older',
        },
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'scrollDown',
          description: 'newer',
        },
      ],
      onIntent(intent) {
        if (goto.handle(intent)) {
          return;
        }
        goto.clear();
        if (intent === 'scrollUp') {
          setScrollUp((current) => Math.min(current + CHAT_SCROLL_STEP, maxScrollUp));
        } else {
          setScrollUp((current) => Math.max(current - CHAT_SCROLL_STEP, 0));
        }
      },
    }),
    [goto, maxScrollUp],
  );
  usePanelKeymap(focusId, presentation.focused ? keymap : EMPTY_CHAT_KEYMAP);

  const paneScroll = usePaneScrollBus();
  const maxScrollUpRef = useRef(maxScrollUp);
  maxScrollUpRef.current = maxScrollUp;
  useEffect(
    () =>
      paneScroll.subscribe(focusId, (direction, amount) => {
        setScrollUp((current) =>
          direction === 'up'
            ? Math.min(current + amount, maxScrollUpRef.current)
            : Math.max(current - amount, 0),
        );
      }),
    [focusId, paneScroll],
  );

  const bus = useBusClient();
  const [tmuxFrame, setTmuxFrame] = useState('');
  useEffect(() => {
    if (viewMode !== 'tmux') {
      setTmuxFrame('');
      return;
    }
    const unsubscribe = bus.subscribe(
      (event) => {
        if (event.type !== 'tmux.frame') {
          return;
        }
        const tmuxEvent: TmuxFrameEvent = event;
        setTmuxFrame(tmuxEvent.frame);
      },
      { type: 'tmux.frame', agent_id: identity.agentId },
    );
    return unsubscribe;
  }, [bus, identity.agentId, viewMode]);

  const handleScrollUpChange = useCallback((nextScrollUp: number) => {
    setScrollUp(nextScrollUp);
    setGotoLine(null);
  }, []);

  const handleWindowMetricsChange = useCallback(
    (metrics: { readonly lineCount: number; readonly maxScrollUp: number }) => {
      setChatMetrics((current) =>
        current.lineCount === metrics.lineCount && current.maxScrollUp === metrics.maxScrollUp
          ? current
          : metrics,
      );
    },
    [],
  );

  return (
    <Box
      ref={ref}
      width={presentation.width}
      height={presentation.height}
      flexDirection="column"
      overflow="hidden"
    >
      <ContractChatPane
        width={presentation.width}
        height={presentation.height}
        focused={highlighted}
        title={identity.label}
        titleExtra={
          <>
            <Text dimColor>{` [${chatKindLabel(identity.kind)}]`}</Text>
            {goto.pending !== null && <Text color={theme.warning}>{` g${goto.pending}`}</Text>}
          </>
        }
        footerLeft={footerFor(state, identity.agentId) ?? ''}
        footerRight={worktreeFor(state, identity.agentId) ?? ''}
        turns={turns}
        viewMode={viewMode}
        scrollUp={scrollUp}
        gotoLine={gotoLine}
        onScrollUpChange={handleScrollUpChange}
        onWindowMetricsChange={handleWindowMetricsChange}
        tmuxFrame={tmuxFrame}
        tmuxWaitingText={TMUX_WAITING_TEXT}
      />
    </Box>
  );
});

export function buildPaneRequests(input: BuildPaneRequestsInput): readonly PaneRequest[] {
  const { state, visiblePanels, focusedId } = input;
  const requests: PaneRequest[] = [];
  const agedPriority = (id: PaneId, priority: number): number => {
    if (priority === 0) {
      return 0;
    }
    return priority + (state.conversations.paneReapAges.get(id) ?? 0);
  };

  for (const panel of PANELS) {
    if (!visiblePanels.has(panel.id)) {
      continue;
    }
    requests.push({
      id: panel.id,
      kind: panelKind(panel.id),
      region: panelRegion(panel.id),
      sizing: panelSizing(panel.id, state),
      reapPriority: requestPriority(
        panel.id,
        focusedId,
        agedPriority(panel.id, 40 + panelOrder(panel.id)),
      ),
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
      reapPriority: requestPriority(id, focusedId, agedPriority(id, 12)),
      orderKey: stageOrder++,
      source: { type: 'stageDoc', name: state.docView.open.name },
    });
  }

  for (const { identity, locked } of chatPanesByAgentId.values()) {
    const current = identity.agentId === currentAgentId;
    const id: PaneId = `stage:chat:${identity.agentId}`;
    const chatReapPriority = current ? 1 : locked ? 24 : 10;
    requests.push({
      id,
      kind: 'stageChat',
      region: 'centerStage',
      sizing: STAGE_CHAT_SIZING,
      reapPriority: requestPriority(id, focusedId, agedPriority(id, chatReapPriority)),
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
      return <CrowsController presentation={presentation} />;
    case 'plans':
      return <PlansController presentation={presentation} />;
    case 'notes':
      return <NotesController presentation={presentation} />;
    case 'reports':
      return <ReportsController presentation={presentation} />;
    case 'tickets':
      return <TicketsController presentation={presentation} />;
    case 'history':
      return <HistoryController presentation={presentation} />;
    case 'tree':
      return <TreeController presentation={presentation} />;
    case 'usage':
      return <UsageController presentation={presentation} />;
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
        <StageDocPaneAdapter open={context.state.docView.open} presentation={presentation} />
      )
    ) : (
      (() => {
        const identity = context.chatIdentities.get(request.source.agentId);
        if (identity === undefined) {
          return null;
        }
        return (
          <StageChatPaneAdapter
            presentation={presentation}
            identity={identity}
            state={context.state}
            chatTarget={request.source.current}
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
