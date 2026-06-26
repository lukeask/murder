import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { CrowsPanel } from '../components/CrowsPanel.js';
import { StageDocPane } from '../components/DocPane.js';
import { META_SEP } from '../components/glyphs.js';
import { HistoryPanel } from '../components/HistoryPanel.js';
import { NotesPanel } from '../components/NotesPanel.js';
import { PlansPanel } from '../components/PlansPanel.js';
import { ReportsPanel } from '../components/ReportsPanel.js';
import { ChatPane } from '../components/Stage.js';
import { TicketsPanel } from '../components/TicketsPanel.js';
import { TransitPanel } from '../components/TransitPanel.js';
import { UsagePanel } from '../components/UsagePanel.js';
import type { FocusId } from '../input/focusStore.js';
import { PANELS, type PanelId } from '../input/panels.js';
import type { AgentIdentity } from '../selectors/agentIdentity.js';
import { deriveAgentIdentity } from '../selectors/agentIdentity.js';
import { selectActiveAgentId, selectOpenChatPanes } from '../selectors/conversationsSelectors.js';
import { harnessModelFooter, worktreeLabel } from '../selectors/harnessDisplay.js';
import type { AppStore } from '../store/store.js';
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
  plans: { min: { width: 18, height: 6 }, preferred: { width: 34, height: 14 } },
  notes: { min: { width: 18, height: 6 }, preferred: { width: 34, height: 14 } },
  reports: { min: { width: 18, height: 6 }, preferred: { width: 34, height: 14 } },
  tickets: { min: { width: 22, height: 7 }, preferred: { width: 42, height: 14 } },
  history: { min: { width: 22, height: 7 }, preferred: { width: 42, height: 14 } },
  transit: { min: { width: 18, height: 7 }, preferred: { width: 40, height: 13 } },
  usage: { min: { width: 16, height: 7 }, preferred: { width: 34, height: 13 } },
  crows: { min: { width: 18, height: 7 }, preferred: { width: 34, height: 13 } },
};

const STAGE_CHAT_SIZING: PaneSizing = {
  min: { width: 24, height: 8 },
  preferred: { width: 56, height: 18 },
};

const STAGE_DOC_SIZING: PaneSizing = {
  min: { width: 28, height: 8 },
  preferred: { width: 72, height: 22 },
};

const PANE_CHROME_WIDTH = 4;
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
  if (panelId === 'transit') {
    return 'transit';
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
      sizing: PANEL_SIZING[panel.id],
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
      return <CrowsPanel />;
    case 'plans':
      return <PlansPanel />;
    case 'notes':
      return <NotesPanel />;
    case 'reports':
      return <ReportsPanel />;
    case 'tickets':
      return <TicketsPanel />;
    case 'history':
      return <HistoryPanel />;
    case 'transit':
      return <TransitPanel innerWidth={Math.max(0, presentation.width - PANE_CHROME_WIDTH)} />;
    case 'usage':
      return <UsagePanel innerWidth={Math.max(0, presentation.width - PANE_CHROME_WIDTH)} />;
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
    return (
      <Box
        key={region}
        width={landscape ? rect.width : plan.body.width}
        height={landscape ? plan.body.height : rect.height}
        flexShrink={0}
        minWidth={0}
        minHeight={0}
        overflow="hidden"
        flexDirection={landscape ? 'column' : 'row'}
        rowGap={landscape ? plan.gap : 0}
        columnGap={landscape ? 0 : plan.gap}
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
      const doc = docs[0];
      if (doc === undefined) {
        return null;
      }
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
          {renderPaneAllocation(doc, context)}
          <Box
            width={chatRect.width}
            height={plan.body.height}
            flexDirection="column"
            rowGap={plan.gap}
            minWidth={0}
            minHeight={0}
            overflow="hidden"
          >
            {chats.map((allocation) => renderPaneAllocation(allocation, context))}
          </Box>
        </Box>
      );
    }
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
        {center.map((allocation) => renderPaneAllocation(allocation, context))}
      </Box>
    );
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
