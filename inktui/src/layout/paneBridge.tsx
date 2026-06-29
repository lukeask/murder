import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { CrowsController } from '../components/panes/CrowsController.js';
import { DocumentController } from '../components/panes/DocumentController.js';
import { HistoryController } from '../components/panes/HistoryController.js';
import { NotesController } from '../components/panes/NotesController.js';
import { PlansController } from '../components/panes/PlansController.js';
import { ReportsController } from '../components/panes/ReportsController.js';
import { TicketsController } from '../components/panes/TicketsController.js';
import { TranscriptController } from '../components/panes/TranscriptController.js';
import { TreeController } from '../components/panes/TreeController.js';
import { UsageController } from '../components/panes/UsageController.js';
import { stageDocFocusId, stageTranscriptFocusId } from '../input/focusIds.js';
import type { FocusId } from '../input/focusStore.js';
import { PANELS, type PanelId } from '../input/panels.js';
import type { AgentIdentity } from '../selectors/agentIdentity.js';
import { deriveAgentIdentity } from '../selectors/agentIdentity.js';
import {
  selectActiveAgentId,
  selectOpenTranscriptPanes,
  selectRecipientTargets,
} from '../selectors/conversationsSelectors.js';
import { selectUsageView } from '../selectors/usageSelectors.js';
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
} from './paneLayoutTypes.js';

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

const STAGE_TRANSCRIPT_SIZING: PaneSizing = {
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

function orderedTranscriptAgentIds(
  state: AppStore,
  currentAgentId: string | null,
): readonly string[] {
  const ids = selectRecipientTargets(state.conversations, state.roster, state.favorites).map(
    (identity) => identity.agentId,
  );
  if (currentAgentId === null || ids.includes(currentAgentId)) {
    return ids;
  }
  return [...ids, currentAgentId];
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
  const transcriptOrder = new Map<string, number>(
    orderedTranscriptAgentIds(state, currentAgentId).map((agentId, index) => [agentId, index]),
  );
  const lockedPanes = selectOpenTranscriptPanes(
    state.roster,
    state.favorites,
    state.conversations.paneOverrides,
  ).panes;
  const chatPanesByAgentId = new Map<string, { identity: AgentIdentity; locked: boolean }>();
  for (const identity of lockedPanes) {
    chatPanesByAgentId.set(identity.agentId, { identity, locked: true });
  }
  if (
    currentAgentId !== null &&
    state.conversations.paneOverrides.get(currentAgentId) !== false &&
    !chatPanesByAgentId.has(currentAgentId)
  ) {
    const row = state.roster.rows.find((candidate) => candidate.agentId === currentAgentId);
    const identity = row === undefined ? null : deriveAgentIdentity(row);
    if (identity !== null) {
      chatPanesByAgentId.set(currentAgentId, { identity, locked: false });
    }
  }

  let stageOrder = 1000;
  if (state.docView.open !== null) {
    const id: PaneId = stageDocFocusId(state.docView.open.name);
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

  const chatPanes = [...chatPanesByAgentId.values()].sort(
    (a, b) =>
      (transcriptOrder.get(a.identity.agentId) ?? Number.MAX_SAFE_INTEGER) -
        (transcriptOrder.get(b.identity.agentId) ?? Number.MAX_SAFE_INTEGER) ||
      a.identity.agentId.localeCompare(b.identity.agentId),
  );
  for (const { identity, locked } of chatPanes) {
    const current = identity.agentId === currentAgentId;
    const id: PaneId = stageTranscriptFocusId(identity.agentId);
    const chatReapPriority = current ? 1 : locked ? 24 : 10;
    requests.push({
      id,
      kind: 'stageTranscript',
      region: 'centerStage',
      sizing: STAGE_TRANSCRIPT_SIZING,
      reapPriority: requestPriority(id, focusedId, agedPriority(id, chatReapPriority)),
      orderKey: stageOrder++,
      source: {
        type: 'stageTranscript',
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
        <DocumentController open={context.state.docView.open} presentation={presentation} />
      )
    ) : (
      (() => {
        const identity = context.chatIdentities.get(request.source.agentId);
        if (identity === undefined) {
          return null;
        }
        return (
          <TranscriptController
            presentation={presentation}
            identity={identity}
            state={context.state}
            activeRecipientTarget={request.source.current}
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
    const transcripts = center.filter(
      (allocation) => allocation.request.kind === 'stageTranscript',
    );
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
    if (docs.length > 0 && transcripts.length > 0) {
      const docRect = regionRect(docs);
      const transcriptRect = regionRect(transcripts);
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
          {renderAllocationGrid(
            'center:transcripts',
            transcripts,
            transcriptRect.width,
            transcriptRect.height,
          )}
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
