import type { ResourceRowFields } from '../../src/components/ResourceRow.js';

export interface SimpleLedgerRow {
  readonly id: string;
  readonly left: string;
  readonly right: string;
}

export interface TicketFixtureRow {
  readonly id: string;
  readonly title: string;
  readonly status: string;
  readonly statusTone: 'error' | 'success' | 'warning' | 'blocked' | 'neutral';
  readonly deps: string;
  readonly depsOk: boolean;
  readonly harness: string;
  readonly model: string;
}

export interface CrowFixtureRow {
  readonly id: string;
  readonly group: string;
  readonly name: string;
  readonly status: string;
  readonly meta: string;
  readonly working: boolean;
  readonly starred: boolean;
}

export interface HistoryFixtureRow {
  readonly id: string;
  readonly age: string;
  readonly target: string;
  readonly status: 'open' | 'stale' | 'dismissed';
  readonly text: string;
}

export interface UsageFixtureGroup {
  readonly harness: string;
  readonly steering: string;
  readonly gauges: readonly {
    readonly label: string;
    readonly pct: number;
    readonly reset: string;
  }[];
}

export interface TransitFixtureData {
  readonly ruler: string;
  readonly lanes: readonly {
    readonly branch: string;
    readonly rail: string;
    readonly color: string;
    readonly selected?: boolean;
  }[];
  readonly info: readonly string[];
  readonly pending?: boolean;
}

export interface DocFixtureData {
  readonly title: string;
  readonly lines: readonly string[];
  readonly scroll: number;
}

export interface ChatFixtureData {
  readonly title: string;
  readonly footerLeft: string;
  readonly footerRight: string;
  readonly turns: readonly {
    readonly speaker: 'user' | 'assistant' | 'tool';
    readonly lines: readonly string[];
  }[];
}

export interface ChatInputFixtureData {
  readonly target: string;
  readonly queued?: string;
  readonly value: string;
  readonly placeholder: string;
  readonly footer?: string;
}

export interface BarFixtureData {
  readonly project?: string;
  readonly labels?: readonly { readonly text: string; readonly active: boolean }[];
  readonly hints?: readonly { readonly key: string; readonly description: string }[];
}

export const resourceRows: Record<string, readonly ResourceRowFields[]> = {
  mixed: [
    {
      name: 'Root investigation plan with a deliberately long title',
      charCount: '12.4k',
      updatedAt: 'Jun. 21 09:32',
      starred: true,
    },
    {
      name: '    Child plan: collect transcript boundaries',
      charCount: '4.1k',
      updatedAt: 'Jun. 20 18:04',
      starred: false,
    },
    {
      name: 'Short note',
      charCount: '880',
      updatedAt: 'Jun. 19 07:11',
      starred: false,
    },
  ],
  overflow: [
    {
      name: '★ Starred report with text that should truncate before it escapes the pane',
      charCount: '44.0k',
      updatedAt: 'Jun. 18 23:59',
      starred: true,
    },
    {
      name: 'Autopsy of a narrow rail rendering failure',
      charCount: '9.5k',
      updatedAt: 'Jun. 18 12:00',
      starred: false,
    },
    {
      name: 'Follow-up: footer and scrollbar checks',
      charCount: '3.2k',
      updatedAt: 'Jun. 17 16:44',
      starred: false,
    },
    {
      name: 'Regression notes from fixture review',
      charCount: '1.1k',
      updatedAt: 'Jun. 16 10:05',
      starred: false,
    },
    {
      name: 'Archive candidate',
      charCount: '600',
      updatedAt: 'Jun. 15 08:30',
      starred: false,
    },
  ],
};

export const ledgerRows: Record<string, readonly SimpleLedgerRow[]> = {
  compact: [
    { id: 'a', left: 'alpha', right: 'ready' },
    { id: 'b', left: 'beta with long text', right: 'running' },
    { id: 'c', left: 'gamma', right: 'blocked' },
  ],
  scroll: [
    { id: 'a', left: 'row one', right: 'ok' },
    { id: 'b', left: 'row two', right: 'queued' },
    { id: 'c', left: 'row three', right: 'working' },
    { id: 'd', left: 'row four', right: 'waiting' },
    { id: 'e', left: 'row five', right: 'done' },
    { id: 'f', left: 'row six', right: 'archived' },
  ],
};

export const ticketRows: Record<string, readonly TicketFixtureRow[]> = {
  mixed: [
    {
      id: 'T-104',
      title: 'Pane fixtures for exact border-box sizing',
      status: '●',
      statusTone: 'warning',
      deps: 'ok',
      depsOk: true,
      harness: 'codex',
      model: 'gpt-5-codex',
    },
    {
      id: 'T-099',
      title: 'Long ticket title that must clip cleanly',
      status: '⊘',
      statusTone: 'blocked',
      deps: 'T-088',
      depsOk: false,
      harness: 'claude',
      model: 'opus',
    },
  ],
  empty: [],
};

export const crowRows: Record<string, readonly CrowFixtureRow[]> = {
  mixed: [
    {
      id: 'collab-1',
      group: 'Collaborators',
      name: '★ Ada',
      status: 'idle',
      meta: 'claude · opus',
      working: false,
      starred: true,
    },
    {
      id: 'planner-1',
      group: 'Planners',
      name: 'Plan runner with long display name',
      status: 'working',
      meta: 'codex · gpt-5-codex',
      working: true,
      starred: false,
    },
    {
      id: 'ticket-17',
      group: 'Tickets',
      name: 'T-017 implementation',
      status: 'awaiting input',
      meta: 'cursor · sonnet',
      working: false,
      starred: false,
    },
  ],
  loading: [],
};

export const historyRows: Record<string, readonly HistoryFixtureRow[]> = {
  mixed: [
    {
      id: 'h1',
      age: '2m',
      target: 'T-104',
      status: 'open',
      text: 'User asked for fixture snapshots with ANSI preserved and deterministic dimensions.',
    },
    {
      id: 'h2',
      age: '41m',
      target: 'planner',
      status: 'stale',
      text: 'Longer historical intention wraps into the second line and then clips inside the fixed row.',
    },
    {
      id: 'h3',
      age: '1d',
      target: 'chat',
      status: 'dismissed',
      text: 'Dismissed follow-up about a narrow bottom bar.',
    },
  ],
  empty: [],
};

export const usageGroups: Record<string, readonly UsageFixtureGroup[]> = {
  normal: [
    {
      harness: 'codex',
      steering: 'auto',
      gauges: [
        { label: 'session', pct: 42, reset: '1h12m' },
        { label: 'weekly', pct: 71, reset: '4d3h' },
      ],
    },
    {
      harness: 'claude',
      steering: 'prefer',
      gauges: [{ label: '5h', pct: 86, reset: '22m' }],
    },
  ],
  empty: [],
};

export const transitData: Record<string, TransitFixtureData> = {
  railway: {
    ruler: '4d   2d   8h   2h   15m',
    lanes: [
      { branch: 'main', rail: '○━━┳━━○━━◆━━▶', color: '#a7c080', selected: true },
      { branch: 'feature/tui-layout', rail: '   ╰━━○━━○━━▶', color: '#7fbbb3' },
      { branch: 'bugfix/footer-wrap', rail: '      ╰━━○━━▶', color: '#d699b6' },
    ],
    info: ['a1b2c3d · main · 15m', 'Refactor pane fixture tooling', 'Preserve ANSI snapshots'],
  },
  pending: {
    ruler: 'type 5d/20m +enter',
    lanes: [
      { branch: 'main', rail: '○━━○━━▶', color: '#a7c080' },
      { branch: 'wip/transit', rail: '╰━━◆━━▶', color: '#dbbc7f', selected: true },
    ],
    info: ['[m] main  [w] wip/transit', 'buffer · 5d'],
    pending: true,
  },
};

export const docData: Record<string, DocFixtureData> = {
  short: {
    title: '.murder/plans/fixture-plan.md',
    scroll: 0,
    lines: ['# Fixture plan', '', '- render at exact dimensions', '- preserve color'],
  },
  long: {
    title: '.murder/reports/very-long-fixture-report-name.md',
    scroll: 3,
    lines: [
      '# Long report',
      '',
      'The pane should show a scrollbar when there are more lines than fit.',
      'Line 4: deterministic content.',
      'Line 5: deterministic content.',
      'Line 6: deterministic content.',
      'Line 7: deterministic content.',
      'Line 8: deterministic content.',
      'Line 9: deterministic content.',
      'Line 10: deterministic content.',
    ],
  },
};

export const chatData: Record<string, ChatFixtureData> = {
  mixed: {
    title: 'Ada',
    footerLeft: 'claude ◇ opus',
    footerRight: 'main',
    turns: [
      { speaker: 'user', lines: ['Can you isolate this pane?'] },
      {
        speaker: 'assistant',
        lines: ['Yes. Rendering with fixture data only.', '- exact width', '- exact height'],
      },
      { speaker: 'tool', lines: ['render-pane-fixture --fixture chat-pane'] },
    ],
  },
  long: {
    title: 'Plan runner with long title',
    footerLeft: 'codex ◇ gpt-5-codex',
    footerRight: 'TUIlayoutrefactor',
    turns: [
      { speaker: 'user', lines: ['Summarize the latest fixture result.'] },
      {
        speaker: 'assistant',
        lines: [
          'Snapshot output is ANSI text and can be inspected directly in a terminal.',
          'Long assistant prose intentionally wraps inside the pane body.',
        ],
      },
    ],
  },
};

export const chatInputData: Record<string, ChatInputFixtureData> = {
  empty: {
    target: '★ Ada',
    value: '',
    placeholder: 'type a message',
    footer: '◂ planner · ticket ▸',
  },
  wrapped: {
    target: 'Plan runner · working',
    queued: 'previous queued message waiting for interrupt',
    value: 'Please inspect the exact border-box dimensions before optimizing the layout.',
    placeholder: 'type a message',
    footer: '◂ Ada · T-017 ▸',
  },
};

export const barData: Record<string, BarFixtureData> = {
  normal: {
    project: 'murder',
    labels: [
      { text: 'plans_1', active: true },
      { text: 'notes_2', active: false },
      { text: 'tickets_4', active: true },
      { text: 'crows_0', active: true },
    ],
    hints: [
      { key: 'alt+1-0', description: 'panels' },
      { key: 'alt+hjkl', description: 'nav' },
      { key: 'j/k', description: 'move' },
      { key: 'enter', description: 'open' },
    ],
  },
  narrow: {
    project: 'long-project-name',
    labels: [
      { text: 'history_5', active: true },
      { text: 'transit_8', active: true },
      { text: 'usage_9', active: false },
    ],
    hints: [
      { key: 'g', description: 'jump' },
      { key: 'space', description: 'page' },
      { key: 'esc', description: 'close' },
      { key: 'alt+/', description: 'help' },
    ],
  },
};
