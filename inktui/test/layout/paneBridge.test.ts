import { isValidElement, type ReactElement } from 'react';
import { describe, expect, it } from 'vitest';
import { crowsSurfaceRowsFromView } from '../../src/components/panes/CrowsController.js';
import { DocumentController } from '../../src/components/panes/DocumentController.js';
import { historySurfaceRowsFromView } from '../../src/components/panes/HistoryController.js';
import { ticketsSurfaceRowsFromView } from '../../src/components/panes/TicketsController.js';
import { TranscriptController } from '../../src/components/panes/TranscriptController.js';
import { treeSurfaceDataFromView } from '../../src/components/panes/TreeController.js';
import { usageSurfaceGroupsFromState } from '../../src/components/panes/UsageController.js';
import { CHAT_FOCUS } from '../../src/input/focusStore.js';
import {
  buildPaneRequests,
  renderPaneAllocation,
  renderPaneLayoutPlan,
  usagePaneSizing,
} from '../../src/layout/paneBridge.js';
import { computePaneLayout } from '../../src/layout/paneLayout.js';
import type { PaneAllocation, PaneRequest } from '../../src/layout/paneLayoutTypes.js';
import type { AgentIdentity } from '../../src/selectors/agentIdentity.js';
import type { CrowsView } from '../../src/selectors/crowsSelectors.js';
import type { HistoryRowView } from '../../src/selectors/historySelectors.js';
import type { TicketRowView } from '../../src/selectors/ticketsSelectors.js';
import type { TransitCursor, TransitView } from '../../src/selectors/transitSelectors.js';
import type { RosterRow } from '../../src/store/roster/rosterSlice.js';
import { type AppStore, initialAppState } from '../../src/store/store.js';
import type { UsageRow, UsageState } from '../../src/store/usage/usageSlice.js';
import { buildTheme } from '../../src/theme/buildTheme.js';
import { DEFAULT_THEME_ID, getPalette, getThemeMeta } from '../../src/theme/palettes.js';

const defaultPalette = getPalette(DEFAULT_THEME_ID);
if (defaultPalette === undefined) {
  throw new Error('missing default palette');
}
const theme = buildTheme(defaultPalette, getThemeMeta(DEFAULT_THEME_ID)?.variant ?? 'dark');

function row(harness: string, windowKey: string): UsageRow {
  return {
    harness,
    windowKey,
    pct: 50,
    tUntilResetMinutes: 60,
    tPeriodMinutes: 300,
    steering: 'auto',
  };
}

function usage(rows: readonly UsageRow[]): { readonly usage: UsageState } {
  return {
    usage: {
      rows,
      status: 'ready',
      error: null,
    },
  };
}

function rosterRow(overrides: Partial<RosterRow>): RosterRow {
  return {
    agentId: 'a-1',
    role: 'crow',
    ticketId: null,
    ticketTitle: null,
    harness: null,
    model: null,
    status: 'idle',
    session: null,
    ...overrides,
  };
}

function appState(overrides: Partial<AppStore> = {}): AppStore {
  return {
    ...initialAppState,
    actions: {} as AppStore['actions'],
    ...overrides,
  };
}

function isStageTranscriptRequest(request: PaneRequest): request is PaneRequest & {
  readonly source: { readonly type: 'stageTranscript'; readonly agentId: string };
} {
  return request.source.type === 'stageTranscript';
}

function isStageRequest(request: PaneRequest): request is PaneRequest & {
  readonly source:
    | { readonly type: 'stageDoc'; readonly name: string }
    | { readonly type: 'stageTranscript'; readonly agentId: string };
} {
  return request.source.type === 'stageDoc' || request.source.type === 'stageTranscript';
}

function paneRequest(overrides: Partial<PaneRequest> = {}): PaneRequest {
  return {
    id: 'plans',
    kind: 'listPane',
    region: 'leftAligned',
    sizing: {
      min: { width: 25, height: 5 },
      preferred: { width: 25, height: 5 },
    },
    reapPriority: 40,
    orderKey: 0,
    source: { type: 'panel', panelId: 'plans' },
    ...overrides,
  };
}

function paneAllocation(request: PaneRequest): PaneAllocation {
  return {
    request,
    region: request.region,
    rect: { x: 0, y: 0, width: 40, height: 8 },
    presentation: {
      width: 40,
      height: 8,
      focused: true,
    },
  };
}

function childrenOf(element: ReactElement): readonly unknown[] {
  const props = element.props as { readonly children?: unknown };
  const children = props.children;
  return Array.isArray(children) ? children : [children];
}

interface ElementProps {
  readonly children?: unknown;
  readonly flexDirection?: unknown;
  readonly width?: unknown;
  readonly height?: unknown;
  readonly open?: unknown;
  readonly presentation?: unknown;
  readonly identity?: unknown;
  readonly activeRecipientTarget?: unknown;
}

function propsOf(element: ReactElement): ElementProps {
  return element.props as ElementProps;
}

describe('usagePaneSizing', () => {
  it('uses 20 columns as the usage minimum width', () => {
    expect(usagePaneSizing(usage([])).min.width).toBe(20);
  });

  it('uses 5 rows as the usage minimum height so wide short layouts can mount', () => {
    const sizing = usagePaneSizing(
      usage([
        row('codex', '5h'),
        row('codex', 'weekly'),
        row('claude_code', '5h'),
        row('claude_code', 'weekly'),
        row('cursor', '5h'),
        row('cursor', 'weekly'),
      ]),
    );

    expect(sizing.min.height).toBe(5);
  });

  it('keeps preferred height at least as tall as the stacked usage form', () => {
    const rows = Array.from({ length: 18 }, (_, index) => row(`harness-${index}`, 'daily'));
    const sizing = usagePaneSizing(usage(rows));

    expect(sizing.preferred.height).toBe(2 + rows.length + rows.length);
  });

  it('maps live usage state into the new explicit usage pane props', () => {
    const groups = usageSurfaceGroupsFromState({
      rows: [
        {
          harness: 'codex',
          windowKey: 'current_session',
          pct: 42.4,
          tUntilResetMinutes: 72,
          tPeriodMinutes: 300,
          steering: 'prefer',
        },
        {
          harness: 'codex',
          windowKey: 'weekly',
          pct: 83.9,
          tUntilResetMinutes: 49 * 60,
          tPeriodMinutes: 7 * 24 * 60,
          steering: 'prefer',
        },
      ],
      status: 'ready',
      error: null,
    });

    expect(groups).toEqual([
      {
        harness: 'codex',
        steering: 'prefer',
        gauges: [
          { label: 'session', pct: 42, reset: '1h12m' },
          { label: 'weekly', pct: 84, reset: '2d1h' },
        ],
      },
    ]);
  });
});

describe('buildPaneRequests', () => {
  it('does not resurrect an explicitly closed active transcript as an ephemeral pane', () => {
    const state = appState({
      roster: {
        ...initialAppState.roster,
        status: 'ready',
        rows: [
          rosterRow({ role: 'collaborator', agentId: 'collab', session: 'collab' }),
          rosterRow({ role: 'planner', agentId: 'p1', session: 'murder_murder_planner_alpha' }),
          rosterRow({
            role: 'crow',
            ticketId: null,
            agentId: 'r1',
            session: 'murder_murder_crow_claude_rogue_tony',
          }),
        ],
      },
      favorites: {
        ...initialAppState.favorites,
        status: 'ready',
        ids: new Set(['p1']),
      },
      conversations: {
        ...initialAppState.conversations,
        activePaneAgentId: 'p1',
        paneOverrides: new Map([['p1', false]]),
      },
    });

    const requests = buildPaneRequests({
      state,
      visiblePanels: new Set(),
      focusedId: CHAT_FOCUS,
    }).filter(isStageTranscriptRequest);

    expect(requests.map((request) => request.source.agentId)).toEqual(['collab', 'r1']);
    expect(requests.map((request) => request.orderKey)).toEqual([1000, 1001]);
  });

  it('keeps stage documents before transcript requests while ordering transcripts in the bridge', () => {
    const state = appState({
      roster: {
        ...initialAppState.roster,
        status: 'ready',
        rows: [
          rosterRow({ role: 'collaborator', agentId: 'collab', session: 'collab' }),
          rosterRow({
            role: 'crow',
            ticketId: null,
            agentId: 'r1',
            session: 'murder_murder_crow_claude_rogue_tony',
          }),
        ],
      },
      docView: { ...initialAppState.docView, open: { kind: 'note', name: 'brief' } },
    });

    const requests = buildPaneRequests({
      state,
      visiblePanels: new Set(),
      focusedId: CHAT_FOCUS,
    }).filter(isStageRequest);

    expect(
      requests.map((request) =>
        request.source.type === 'stageTranscript' ? request.source.agentId : request.source.name,
      ),
    ).toEqual(['brief', 'collab', 'r1']);
    expect(requests.map((request) => request.orderKey)).toEqual([1000, 1001, 1002]);
  });
});

describe('renderPaneLayoutPlan', () => {
  it('keeps wrapped portrait side-region allocations in separate rendered rows', () => {
    const plan = computePaneLayout({
      terminal: { width: 25, height: 17 },
      chrome: { topBar: 0, bottomBar: 0, chatInput: 0 },
      body: { width: 25, height: 17 },
      orientation: 'portrait',
      gap: 1,
      requests: ['notes', 'plans', 'reports'].map((id, index) =>
        paneRequest({
          id,
          orderKey: index,
          source: { type: 'panel', panelId: id as 'notes' | 'plans' | 'reports' },
        }),
      ),
    });
    const root = renderPaneLayoutPlan(plan, {
      state: {} as AppStore,
      chatIdentities: new Map(),
    });
    const [left] = childrenOf(root);

    expect(isValidElement(left)).toBe(true);
    if (!isValidElement(left)) {
      return;
    }

    expect(propsOf(left).flexDirection).toBe('column');
    const rows = childrenOf(left);
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(isValidElement(row)).toBe(true);
      if (isValidElement(row)) {
        const rowProps = propsOf(row);
        expect(rowProps.flexDirection).toBe('row');
        expect(rowProps.width).toBe(25);
        expect(rowProps.height).toBe(5);
      }
    }
  });
});

describe('renderPaneAllocation — stage controller routing', () => {
  it('routes stage document allocations to DocumentController', () => {
    const open = { kind: 'note' as const, name: 'field-notes' };
    const allocation = paneAllocation(
      paneRequest({
        id: 'stage:doc:field-notes',
        kind: 'stageDoc',
        region: 'centerStage',
        source: { type: 'stageDoc', name: 'field-notes' },
      }),
    );
    const root = renderPaneAllocation(allocation, {
      state: { docView: { open } } as AppStore,
      chatIdentities: new Map(),
    });

    expect(isValidElement(root)).toBe(true);
    if (!isValidElement(root)) {
      return;
    }
    const [child] = childrenOf(root);
    expect(isValidElement(child)).toBe(true);
    if (!isValidElement(child)) {
      return;
    }
    expect(child.type).toBe(DocumentController);
    expect(propsOf(child).open).toEqual(open);
    expect(propsOf(child).presentation).toBe(allocation.presentation);
  });

  it('routes stage transcript allocations to TranscriptController', () => {
    const identity: AgentIdentity = {
      kind: 'collaborator',
      agentId: 'collab-1',
      label: 'collab',
    };
    const root = renderPaneAllocation(
      paneAllocation(
        paneRequest({
          id: 'stage:transcript:collab-1',
          kind: 'stageTranscript',
          region: 'centerStage',
          source: {
            type: 'stageTranscript',
            agentId: 'collab-1',
            locked: true,
            ephemeral: false,
            current: true,
          },
        }),
      ),
      {
        state: {} as AppStore,
        chatIdentities: new Map([['collab-1', identity]]),
      },
    );

    expect(isValidElement(root)).toBe(true);
    if (!isValidElement(root)) {
      return;
    }
    const [child] = childrenOf(root);
    expect(isValidElement(child)).toBe(true);
    if (!isValidElement(child)) {
      return;
    }
    expect(child.type).toBe(TranscriptController);
    expect(propsOf(child).identity).toBe(identity);
    expect(propsOf(child).activeRecipientTarget).toBe(true);
  });
});

describe('crowsSurfaceRowsFromView', () => {
  it('maps selector-owned CrowsView rows into the explicit pane contract', () => {
    const view: CrowsView = {
      status: 'ready',
      error: null,
      isEmpty: false,
      sections: [
        {
          group: 'collaborator',
          label: 'Collaborator',
          rows: [
            {
              agentId: 'collab-1',
              name: 'collab',
              favorited: true,
              status: 'running',
              working: true,
              harness: 'claude',
              model: 'opus',
              health: 'green',
            },
          ],
        },
        {
          group: 'ticket',
          label: 'Ticket Crows',
          rows: [
            {
              agentId: 'ticket-1',
              name: 'ticket-crow',
              favorited: false,
              status: 'blocked',
              working: false,
              harness: 'codex',
              model: 'gpt-5',
              health: 'red',
            },
          ],
        },
      ],
    };

    expect(crowsSurfaceRowsFromView(view)).toEqual([
      {
        id: 'collab-1',
        group: 'Collaborator',
        name: 'collab',
        meta: 'claude · opus',
        working: true,
        starred: true,
        health: 'green',
      },
      {
        id: 'ticket-1',
        group: 'Ticket Crows',
        name: 'ticket-crow',
        meta: 'codex · gpt-5',
        working: false,
        starred: false,
        health: 'red',
      },
    ]);
  });
});

describe('ticketsSurfaceRowsFromView', () => {
  it('drops selector-only fields while preserving explicit ticket pane cells', () => {
    const rows: TicketRowView[] = [
      {
        id: 'T-1',
        idCell: 'T-1',
        titleCell: 'Implement pane bridge',
        statusCell: '◕',
        statusTone: 'warning',
        lastUpdateCell: '2026-06-27 now',
        depsCell: 'ok',
        depsSatisfied: true,
        scheduleCell: 'now',
        harnessCell: 'codex',
        modelCell: 'gpt-5.5',
        planCell: 'panes',
        worktreeCell: 'paneswarm',
        rowParity: 1,
        depth: 2,
      },
    ];

    expect(ticketsSurfaceRowsFromView(rows)).toEqual([
      {
        id: 'T-1',
        idCell: 'T-1',
        titleCell: 'Implement pane bridge',
        statusCell: '◕',
        statusTone: 'warning',
        lastUpdateCell: '2026-06-27 now',
        depsCell: 'ok',
        depsSatisfied: true,
        scheduleCell: 'now',
        harnessCell: 'codex',
        modelCell: 'gpt-5.5',
        planCell: 'panes',
        worktreeCell: 'paneswarm',
      },
    ]);
  });
});

describe('historySurfaceRowsFromView', () => {
  it('maps history selector rows to display-only pane rows', () => {
    const rows: HistoryRowView[] = [
      {
        itemId: 'item-1',
        text: 'Continue the layout migration',
        target: 'codex',
        conversationId: 'conv-1',
        age: '4m',
        statusTag: 'OPEN',
        status: 'open',
        resumable: true,
      },
    ];

    expect(historySurfaceRowsFromView(rows)).toEqual([
      {
        id: 'item-1',
        age: '4m',
        target: 'codex',
        status: 'open',
        text: 'Continue the layout migration',
      },
    ]);
  });
});

describe('treeSurfaceDataFromView', () => {
  const transitView: TransitView = {
    lanes: [
      {
        branch: 'main',
        isMain: true,
        hint: 'm',
        headSha: 'm0',
        colorIndex: 0,
        tag: '▐ main ⌂ ▌',
        segments: [
          { text: '○━', color: 0 },
          { text: '◆', color: -2 },
        ],
        stationShas: ['m0'],
      },
      {
        branch: 'feature',
        isMain: false,
        hint: 'f',
        headSha: 'f0',
        colorIndex: 1,
        tag: '▐ feature ⌂ ▌',
        segments: [{ text: '╰━▶', color: 1 }],
        stationShas: ['f0'],
      },
    ],
    ruler: ' 1h  2d',
    railwayWidth: 12,
    tagColWidth: 14,
    mainIndex: 0,
    selected: {
      sha: 'f0',
      short: 'f0aaaa',
      branch: 'feature',
      subject: 'feature work',
      body: 'body',
      age: '1h',
    },
    infoLines: ['feature work', 'body'],
    status: 'ready',
    error: null,
    isEmpty: false,
  };

  const cursor: TransitCursor = { laneIndex: 1, sha: 'f0' };

  it('maps TransitView geometry into explicit TreeSurfaceData', () => {
    expect(treeSurfaceDataFromView(transitView, cursor, false, '', theme)).toMatchObject({
      ruler: ' 1h  2d',
      pending: false,
      status: 'ready',
      error: null,
      lanes: [
        { branch: 'main', rail: '○━◆', selected: false },
        { branch: 'feature', rail: '╰━▶', selected: true },
      ],
      info: ['f0aaaa · feature · 1h', 'feature work', 'body'],
    });
  });

  it('maps g-pending mode to hint overlay lines', () => {
    expect(treeSurfaceDataFromView(transitView, cursor, true, '20d', theme).info).toEqual([
      '[m] main  [f] feature',
      'type 5d/20m +⏎  · 20d',
    ]);
  });
});
