import { Box, Text } from 'ink';
// biome-ignore lint/style/useImportType: tsx's runtime transform for this CLI path expects React.
import React from 'react';
import { PANE_BORDER_GLYPHS, paneBorderStyle } from '../../../src/components/glyphs.js';
import { Ledger, type LedgerEntryContext } from '../../../src/components/Ledger.js';
import { Pane } from '../../../src/components/Pane.js';
import { PaneBorderBottom, PaneBorderTop } from '../../../src/components/paneBorder.js';
import {
  type ResourceRowFields,
  renderResourceEntry,
  renderResourceHeader,
} from '../../../src/components/ResourceRow.js';
import { MultiLineText, TextInput } from '../../../src/components/TextInput.js';
import { getTheme } from '../../../src/theme/themeStore.js';
import {
  type BarFixtureData,
  barData,
  type ChatFixtureData,
  type ChatInputFixtureData,
  type CrowFixtureRow,
  chatData,
  chatInputData,
  crowRows,
  type DocFixtureData,
  docData,
  type HistoryFixtureRow,
  historyRows,
  ledgerRows,
  resourceRows,
  type SimpleLedgerRow,
  type TicketFixtureRow,
  type TransitFixtureData,
  ticketRows,
  transitData,
  type UsageFixtureGroup,
  usageGroups,
} from './data/paneFixtureData.js';
import type { FixtureSize } from './renderInkFixture.js';
import type { PaneFixture } from './types.js';

const PANE_SIZES: readonly FixtureSize[] = [
  { id: 'preferred', width: 54, height: 14 },
  { id: 'cramped', width: 30, height: 8 },
  { id: 'minimum', width: 20, height: 5 },
];

const TICKETS_PANEL_SIZES: readonly FixtureSize[] = [
  { id: 'preferred', width: 54, height: 14 },
  { id: 'cramped', width: 30, height: 8 },
  { id: 'minimum', width: 25, height: 5 },
];

const TREE_PANEL_SIZES: readonly FixtureSize[] = [
  { id: 'preferred', width: 54, height: 14 },
  { id: 'cramped', width: 30, height: 8 },
  { id: 'minimum', width: 25, height: 10 },
];

const BAR_SIZES: readonly FixtureSize[] = [
  { id: 'preferred', width: 60, height: 3 },
  { id: 'cramped', width: 30, height: 3 },
  { id: 'minimum', width: 20, height: 4 },
];

function innerWidth(width: number): number {
  return Math.max(1, width - 4);
}

function innerHeight(height: number): number {
  return Math.max(1, height - 2);
}

function SimpleLedger({
  rows,
  focused,
  width,
  height,
}: {
  readonly rows: readonly SimpleLedgerRow[];
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
}): React.JSX.Element {
  return (
    <Ledger
      rows={rows}
      cursor={Math.min(1, Math.max(rows.length - 1, 0))}
      focused={focused}
      linesPerEntry={1}
      minColumns={1}
      maxColumns={2}
      availableWidth={innerWidth(width)}
      availableHeight={innerHeight(height)}
      header={(columns) => (
        <Text dimColor>{columns >= 2 ? 'name               status' : 'name'}</Text>
      )}
      rowKey={(row) => row.id}
      renderEntry={(row, ctx) => (
        <Box flexDirection="row" flexGrow={1}>
          <Text>{ctx.selected ? '▌ ' : '  '}</Text>
          <Text wrap="truncate">{row.left}</Text>
          {ctx.columns >= 2 ? <Text color={getTheme().heading}>{`  ${row.right}`}</Text> : null}
        </Box>
      )}
    />
  );
}

function ResourcePane({
  title,
  rows,
  focused,
  width,
  height,
  emptyText,
}: {
  readonly title: string;
  readonly rows: readonly ResourceRowFields[];
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
  readonly emptyText: string;
}): React.JSX.Element {
  return (
    <Pane
      title={title}
      focused={focused}
      overflowBelow={rows.length > 3 ? Math.max(1, rows.length - 2) : 0}
    >
      {rows.length === 0 ? (
        <Text dimColor>{emptyText}</Text>
      ) : (
        <Ledger
          rows={rows}
          cursor={Math.min(1, rows.length - 1)}
          focused={focused}
          linesPerEntry={2}
          minColumns={1}
          maxColumns={1}
          availableWidth={innerWidth(width)}
          availableHeight={innerHeight(height)}
          renderEntry={renderResourceEntry}
          header={renderResourceHeader}
          rowKey={(row) => row.name}
        />
      )}
    </Pane>
  );
}

function statusToneColor(tone: TicketFixtureRow['statusTone']): string {
  const theme = getTheme();
  switch (tone) {
    case 'error':
      return theme.error;
    case 'success':
      return theme.success;
    case 'warning':
      return theme.warning;
    case 'blocked':
      return theme.accent;
    default:
      return theme.heading;
  }
}

function renderTicketEntry(row: TicketFixtureRow, ctx: LedgerEntryContext): React.ReactNode {
  return (
    <Box flexDirection="row" flexGrow={1} flexShrink={0}>
      <Text>{ctx.selected ? '▌ ' : '  '}</Text>
      <Box flexDirection="column" marginRight={2}>
        <Text bold={ctx.selected}>{row.id}</Text>
        <Text dimColor={!ctx.selected} wrap="truncate">
          {row.title}
        </Text>
      </Box>
      {ctx.columns >= 2 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={statusToneColor(row.statusTone)}>{row.status}</Text>
          <Text dimColor>Jun. 21</Text>
        </Box>
      ) : null}
      {ctx.columns >= 3 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={row.depsOk ? getTheme().success : getTheme().warning}>{row.deps}</Text>
          <Text dimColor>queued</Text>
        </Box>
      ) : null}
      {ctx.columns >= 4 ? (
        <Box flexDirection="column">
          <Text>{row.harness}</Text>
          <Text dimColor>{row.model}</Text>
        </Box>
      ) : null}
    </Box>
  );
}

function TicketsFixture({
  rows,
  focused,
  width,
  height,
}: {
  readonly rows: readonly TicketFixtureRow[];
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
}): React.JSX.Element {
  return (
    <Pane title="Tickets" focused={focused} overflowBelow={rows.length > 2 ? 1 : 0}>
      {rows.length === 0 ? (
        <Text dimColor>no tickets</Text>
      ) : (
        <Ledger
          rows={rows}
          cursor={0}
          focused={focused}
          linesPerEntry={2}
          minColumns={1}
          maxColumns={4}
          availableWidth={innerWidth(width)}
          availableHeight={innerHeight(height)}
          renderEntry={renderTicketEntry}
          header={(columns) => (
            <Text dimColor>{columns >= 4 ? 'id/title  status  deps  harness' : 'id/title'}</Text>
          )}
          rowKey={(row) => row.id}
        />
      )}
    </Pane>
  );
}

type CrowLedgerRow =
  | { readonly kind: 'header'; readonly label: string }
  | { readonly kind: 'crow'; readonly row: CrowFixtureRow };

function flattenCrows(rows: readonly CrowFixtureRow[]): CrowLedgerRow[] {
  const out: CrowLedgerRow[] = [];
  let group = '';
  for (const row of rows) {
    if (row.group !== group) {
      group = row.group;
      out.push({ kind: 'header', label: group });
    }
    out.push({ kind: 'crow', row });
  }
  return out;
}

function renderCrowEntry(row: CrowLedgerRow, ctx: LedgerEntryContext): React.ReactNode {
  if (row.kind === 'header') {
    return (
      <Text dimColor bold>
        {row.label}
      </Text>
    );
  }
  const theme = getTheme();
  return (
    <Box flexDirection="column" flexGrow={1}>
      <Text wrap="truncate">
        <Text color={row.row.working ? theme.success : theme.warning}>
          {row.row.working ? '●' : '○'}
        </Text>
        {` ${row.row.name} `}
        <Text color={theme.heading}>{row.row.status}</Text>
      </Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {`  ${row.row.meta}`}
      </Text>
    </Box>
  );
}

function CrowsFixture({
  rows,
  focused,
  width,
  height,
}: {
  readonly rows: readonly CrowFixtureRow[];
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
}): React.JSX.Element {
  const flat = flattenCrows(rows);
  return (
    <Pane title="Crows" focused={focused}>
      {rows.length === 0 ? (
        <Text dimColor>loading...</Text>
      ) : (
        <Ledger
          rows={flat}
          cursor={1}
          focused={focused}
          linesPerEntry={2}
          minColumns={1}
          maxColumns={1}
          availableWidth={innerWidth(width)}
          availableHeight={innerHeight(height)}
          header={() => <Text dimColor>{'  ○ ready  ● working'}</Text>}
          renderEntry={renderCrowEntry}
          rowKey={(row, index) => (row.kind === 'header' ? `h:${row.label}` : row.row.id) + index}
        />
      )}
    </Pane>
  );
}

function HistoryFixture({
  rows,
  focused,
  width,
  height,
}: {
  readonly rows: readonly HistoryFixtureRow[];
  readonly focused: boolean;
  readonly width: number;
  readonly height: number;
}): React.JSX.Element {
  return (
    <Pane title={`History · ${rows.length} loose`} focused={focused}>
      {rows.length === 0 ? (
        <Text dimColor>no loose threads</Text>
      ) : (
        <Ledger
          rows={rows}
          cursor={0}
          focused={focused}
          linesPerEntry={3}
          minColumns={1}
          maxColumns={1}
          availableWidth={innerWidth(width)}
          availableHeight={innerHeight(height)}
          header={() => <Text dimColor>{'  age      target  status\n    intention'}</Text>}
          rowKey={(row) => row.id}
          renderEntry={(row, ctx) => (
            <Box flexDirection="column" flexGrow={1} flexShrink={0}>
              <Text wrap="truncate">
                {ctx.selected ? '▌ ' : '  '}
                {row.age.padEnd(8)} {row.target}{' '}
                <Text color={row.status === 'stale' ? getTheme().warning : getTheme().accent}>
                  {row.status}
                </Text>
              </Text>
              <Box height={2} overflow="hidden">
                <Text dimColor={!ctx.selected} wrap="wrap">{`    ${row.text}`}</Text>
              </Box>
            </Box>
          )}
        />
      )}
    </Pane>
  );
}

function UsageFixture({
  groups,
  focused,
}: {
  readonly groups: readonly UsageFixtureGroup[];
  readonly focused: boolean;
}): React.JSX.Element {
  const theme = getTheme();
  return (
    <Pane title="Usage" focused={focused}>
      {groups.length === 0 ? (
        <Text dimColor>no usage data</Text>
      ) : (
        <Box flexDirection="column">
          <Text dimColor>{'  window     usage        reset'}</Text>
          {groups.map((group) => (
            <Box key={group.harness} flexDirection="column">
              <Box backgroundColor={theme.panelHeaderBg}>
                <Text bold>
                  {` ${group.harness}`}
                  {group.steering !== 'auto' ? (
                    <Text color={theme.accent}>{` [${group.steering}]`}</Text>
                  ) : null}
                </Text>
              </Box>
              {group.gauges.map((gauge, index) => {
                const fill = Math.round((gauge.pct / 100) * 10);
                return (
                  <Text
                    key={`${group.harness}:${gauge.label}`}
                    {...(focused && index === 0 ? { backgroundColor: theme.panelSelectedBg } : {})}
                    wrap="truncate"
                  >
                    {focused && index === 0 ? '▌ ' : '  '}
                    <Text dimColor>{gauge.label.padEnd(10)}</Text>
                    <Text color={gauge.pct >= 80 ? theme.gaugeHigh : theme.gaugeNormal}>
                      {'█'.repeat(fill)}
                    </Text>
                    <Text color={theme.gaugeTrack}>{'░'.repeat(10 - fill)}</Text>
                    <Text dimColor>{` ${String(gauge.pct).padStart(3)}% ${gauge.reset}`}</Text>
                  </Text>
                );
              })}
            </Box>
          ))}
        </Box>
      )}
    </Pane>
  );
}

function TransitFixture({
  data,
  focused,
}: {
  readonly data: TransitFixtureData;
  readonly focused: boolean;
}): React.JSX.Element {
  return (
    <Pane title="Git Tree" focused={focused}>
      <Box flexDirection="column">
        <Text dimColor wrap="truncate">
          {data.ruler}
        </Text>
        {data.lanes.map((lane) => {
          const selected = lane.selected === true;
          return (
            <Text key={lane.branch} wrap="truncate">
              <Text color={selected ? getTheme().focus : lane.color}>{lane.rail}</Text>{' '}
              <Text
                color={lane.color}
                bold={selected}
                {...(selected ? { backgroundColor: getTheme().panelSelectedBg } : {})}
              >
                {`▐ ${lane.branch} ▌`}
              </Text>
            </Text>
          );
        })}
        <Text> </Text>
        {data.info.map((line) => (
          <Text
            key={line}
            dimColor={data.pending !== true}
            {...(data.pending ? { color: getTheme().heading } : {})}
            wrap="truncate"
          >
            {line}
          </Text>
        ))}
      </Box>
    </Pane>
  );
}

function DocFixture({
  data,
  focused,
  height,
}: {
  readonly data: DocFixtureData;
  readonly focused: boolean;
  readonly height: number;
}): React.JSX.Element {
  const visibleHeight = innerHeight(height);
  const visible = data.lines.slice(data.scroll, data.scroll + visibleHeight);
  const thumb =
    data.lines.length > visibleHeight
      ? {
          size: Math.max(1, Math.floor((visibleHeight * visibleHeight) / data.lines.length)),
          offset: 1,
        }
      : null;
  return (
    <Pane
      title={data.title}
      focused={focused}
      paddingRight={0}
      scrollbar={{ height: visibleHeight, thumb }}
    >
      <Box flexDirection="column">
        {visible.map((line, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: doc fixture lines are deterministic position-keyed slices.
          <Text key={`${index}:${line}`} wrap="truncate">
            {line.length > 0 ? line : ' '}
          </Text>
        ))}
      </Box>
    </Pane>
  );
}

function ChatPaneFixture({
  data,
  focused,
  height,
}: {
  readonly data: ChatFixtureData;
  readonly focused: boolean;
  readonly height: number;
}): React.JSX.Element {
  const lines = data.turns.flatMap((turn, turnIndex) => [
    ...(turnIndex === 0 ? [] : [{ speaker: turn.speaker, line: ' ' }]),
    ...turn.lines.map((line) => ({ speaker: turn.speaker, line })),
  ]);
  const theme = getTheme();
  return (
    <Pane
      title={data.title}
      focused={focused}
      footerLeft={<Text dimColor>{data.footerLeft}</Text>}
      footerRight={<Text dimColor>{data.footerRight}</Text>}
      overflowBelow={lines.length > innerHeight(height) ? lines.length - innerHeight(height) : 0}
    >
      <Box flexDirection="column">
        {lines.slice(0, innerHeight(height)).map((item, index) => {
          const color =
            item.speaker === 'user'
              ? theme.success
              : item.speaker === 'tool'
                ? theme.warning
                : theme.text;
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: chat fixture lines are deterministic position-keyed slices.
            <Box key={`${index}:${item.line}`} flexDirection="row">
              <Text color={color}>{item.line === ' ' ? '  ' : index === 0 ? '▌ ' : '▏ '}</Text>
              <Text color={color} wrap="wrap">
                {item.line}
              </Text>
            </Box>
          );
        })}
      </Box>
    </Pane>
  );
}

function ChatInputFixture({
  data,
  focused,
}: {
  readonly data: ChatInputFixtureData;
  readonly focused: boolean;
}): React.JSX.Element {
  const theme = getTheme();
  const style = paneBorderStyle(focused);
  const glyphs = PANE_BORDER_GLYPHS[style];
  const borderColor = focused ? theme.active : theme.inactive;
  return (
    <Box flexDirection="column">
      {data.queued !== undefined ? (
        <Box paddingX={1}>
          <Text color={theme.warning} wrap="truncate">
            {`queued · ${data.queued}`}
          </Text>
        </Box>
      ) : null}
      <PaneBorderTop
        title={`▶ ${data.target}`}
        borderColor={borderColor}
        titleColor={borderColor}
        glyphs={glyphs}
        bold={focused}
      />
      <Box borderStyle={style} borderTop={false} borderColor={borderColor} paddingX={1}>
        <MultiLineText value={data.value} placeholder={data.placeholder} focused={focused} />
      </Box>
      <PaneBorderBottom
        borderColor={borderColor}
        glyphs={glyphs}
        rightExtra={data.footer === undefined ? undefined : <Text dimColor>{data.footer}</Text>}
      />
    </Box>
  );
}

function TopBarFixture({ data }: { readonly data: BarFixtureData }): React.JSX.Element {
  const theme = getTheme();
  return (
    <Box flexDirection="row" paddingX={1} justifyContent="space-between">
      <Box flexDirection="row">
        <Text bold color={theme.brand}>
          murder
        </Text>
        {data.project !== undefined ? (
          <Text color={theme.muted}>{` · ${data.project}   `}</Text>
        ) : null}
        {data.labels?.map((label) => (
          <Text
            key={label.text}
            bold={label.active}
            color={label.active ? theme.active : theme.inactive}
          >
            {`${label.text} `}
          </Text>
        ))}
      </Box>
      <Text color={theme.warning}>connected</Text>
    </Box>
  );
}

function BottomBarFixture({ data }: { readonly data: BarFixtureData }): React.JSX.Element {
  const theme = getTheme();
  return (
    <Box flexDirection="column" paddingX={1}>
      <Box flexDirection="row" columnGap={1} flexWrap="wrap">
        {data.hints?.map((hint) => (
          <Text key={`${hint.key}:${hint.description}`} dimColor>
            <Text color={theme.warning}>{hint.key}</Text>
            {hint.description.length > 0 ? ` ${hint.description}` : ''}
          </Text>
        ))}
      </Box>
    </Box>
  );
}

export const paneFixtures: readonly PaneFixture[] = [
  {
    id: 'pane',
    description: 'Bare Pane chrome with footer and overflow indicators.',
    sizes: PANE_SIZES,
    data: {
      basic: ['body line', 'second line'],
      overflow: ['first visible row', 'second visible row', 'third visible row'],
    },
    render: ({ data, focused }) => {
      const lines = data as string[];
      return (
        <Pane
          title="Plans With Long Title"
          focused={focused}
          footerLeft={<Text dimColor>footer left</Text>}
          footerRight={<Text dimColor>main</Text>}
          overflowAbove={lines.length > 2 ? 2 : 0}
          overflowBelow={lines.length > 2 ? 5 : 0}
        >
          <Box flexDirection="column">
            {lines.map((line) => (
              <Text key={line}>{line}</Text>
            ))}
          </Box>
        </Pane>
      );
    },
  },
  {
    id: 'ledger',
    description: 'Ledger rows with cursor highlight, header, and column collapse.',
    sizes: PANE_SIZES,
    data: ledgerRows,
    render: ({ data, focused, width, height }) => (
      <SimpleLedger
        rows={data as readonly SimpleLedgerRow[]}
        focused={focused}
        width={width}
        height={height}
      />
    ),
  },
  {
    id: 'plans-panel',
    description: 'Store-free PlansPanel body wrapper using Pane + ResourceRow + Ledger.',
    sizes: PANE_SIZES,
    data: resourceRows,
    render: ({ data, focused, width, height }) => (
      <ResourcePane
        title="Plans"
        rows={data as readonly ResourceRowFields[]}
        focused={focused}
        width={width}
        height={height}
        emptyText="no plans"
      />
    ),
  },
  {
    id: 'notes-panel',
    description: 'Store-free NotesPanel body wrapper using Pane + ResourceRow + Ledger.',
    sizes: PANE_SIZES,
    // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
    data: { mixed: resourceRows['mixed'] ?? [], empty: [] },
    render: ({ data, focused, width, height }) => (
      <ResourcePane
        title="Notes"
        rows={data as readonly ResourceRowFields[]}
        focused={focused}
        width={width}
        height={height}
        emptyText="no notes"
      />
    ),
  },
  {
    id: 'reports-panel',
    description: 'Store-free ReportsPanel body wrapper using Pane + ResourceRow + Ledger.',
    sizes: PANE_SIZES,
    // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
    data: { mixed: resourceRows['overflow'] ?? [], empty: [] },
    render: ({ data, focused, width, height }) => (
      <ResourcePane
        title="Reports"
        rows={data as readonly ResourceRowFields[]}
        focused={focused}
        width={width}
        height={height}
        emptyText="no reports"
      />
    ),
  },
  {
    id: 'resource-row',
    description: 'Shared two-line ResourceRow renderer inside Ledger.',
    sizes: PANE_SIZES,
    data: resourceRows,
    render: ({ data, focused, width, height }) => (
      <Ledger
        rows={data as readonly ResourceRowFields[]}
        cursor={0}
        focused={focused}
        linesPerEntry={2}
        minColumns={1}
        maxColumns={1}
        availableWidth={width}
        availableHeight={height}
        renderEntry={renderResourceEntry}
        header={renderResourceHeader}
        rowKey={(row) => row.name}
      />
    ),
  },
  {
    id: 'tickets-panel',
    description: 'Store-free TicketsPanel body wrapper with responsive multi-column rows.',
    sizes: TICKETS_PANEL_SIZES,
    data: ticketRows,
    render: ({ data, focused, width, height }) => (
      <TicketsFixture
        rows={data as readonly TicketFixtureRow[]}
        focused={focused}
        width={width}
        height={height}
      />
    ),
  },
  {
    id: 'crows-panel',
    description: 'Store-free CrowsPanel body wrapper with grouped rows.',
    sizes: PANE_SIZES,
    data: crowRows,
    render: ({ data, focused, width, height }) => (
      <CrowsFixture
        rows={data as readonly CrowFixtureRow[]}
        focused={focused}
        width={width}
        height={height}
      />
    ),
  },
  {
    id: 'roster-panel',
    description: 'Roster-style pane wrapper using the new Pane contract.',
    sizes: PANE_SIZES,
    // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
    data: { mixed: crowRows['mixed'] ?? [], loading: crowRows['loading'] ?? [] },
    render: ({ data, focused, width, height }) => (
      <CrowsFixture
        rows={data as readonly CrowFixtureRow[]}
        focused={focused}
        width={width}
        height={height}
      />
    ),
  },
  {
    id: 'history-panel',
    description: 'Store-free HistoryPanel body wrapper with fixed-height intention rows.',
    sizes: PANE_SIZES,
    data: historyRows,
    render: ({ data, focused, width, height }) => (
      <HistoryFixture
        rows={data as readonly HistoryFixtureRow[]}
        focused={focused}
        width={width}
        height={height}
      />
    ),
  },
  {
    id: 'usage-panel',
    description: 'Store-free UsagePanel body wrapper with colored gauges.',
    sizes: PANE_SIZES,
    data: usageGroups,
    render: ({ data, focused }) => (
      <UsageFixture groups={data as readonly UsageFixtureGroup[]} focused={focused} />
    ),
  },
  {
    id: 'tree-panel',
    description: 'Store-free TreePanel body wrapper with deterministic railway rows.',
    sizes: TREE_PANEL_SIZES,
    data: transitData,
    render: ({ data, focused }) => (
      <TransitFixture data={data as TransitFixtureData} focused={focused} />
    ),
  },
  {
    id: 'doc-pane',
    description: 'Store-free StageDocPane wrapper with document lines and scrollbar chrome.',
    sizes: PANE_SIZES,
    data: docData,
    render: ({ data, focused, height }) => (
      <DocFixture data={data as DocFixtureData} focused={focused} height={height} />
    ),
  },
  {
    id: 'chat-pane',
    description: 'Store-free Stage ChatPane wrapper with mixed chat turns.',
    sizes: PANE_SIZES,
    data: chatData,
    render: ({ data, focused, height }) => (
      <ChatPaneFixture data={data as ChatFixtureData} focused={focused} height={height} />
    ),
  },
  {
    id: 'chat-input',
    description: 'Store-free ChatInput chrome wrapper with wrapped draft and queued state.',
    sizes: PANE_SIZES,
    data: chatInputData,
    render: ({ data, focused }) => (
      <ChatInputFixture data={data as ChatInputFixtureData} focused={focused} />
    ),
  },
  {
    id: 'text-input',
    description: 'Controlled TextInput and MultiLineText states.',
    sizes: BAR_SIZES,
    data: {
      empty: { value: '', placeholder: 'Enter name...' },
      filled: { value: 'draft with cursor', placeholder: 'Enter name...' },
    },
    render: ({ data, focused }) => (
      <Box flexDirection="column" paddingX={1}>
        <TextInput
          value={(data as { value: string; placeholder: string }).value}
          placeholder={(data as { value: string; placeholder: string }).placeholder}
          focused={focused}
        />
        <MultiLineText
          value={
            (data as { value: string; placeholder: string }).value.length > 0
              ? `${(data as { value: string; placeholder: string }).value}\nsecond line`
              : ''
          }
          placeholder="Body..."
          focused={focused}
        />
      </Box>
    ),
  },
  {
    id: 'pane-border',
    description: 'Shared paneBorder top and bottom rows in isolation.',
    sizes: BAR_SIZES,
    data: {
      basic: { title: 'Shared Border', left: 'left', right: 'right' },
      overflow: { title: 'Very Long Shared Border Title', left: 'Cursor ◇ model', right: 'branch' },
    },
    render: ({ data, focused }) => {
      const borderData = data as { title: string; left: string; right: string };
      const theme = getTheme();
      const style = paneBorderStyle(focused);
      const glyphs = PANE_BORDER_GLYPHS[style];
      const borderColor = focused ? theme.focus : theme.borderBlurred;
      return (
        <Box flexDirection="column">
          <PaneBorderTop
            title={borderData.title}
            borderColor={borderColor}
            titleColor={focused ? theme.focus : theme.titleBlurred}
            glyphs={glyphs}
            bold={focused}
            overflowAbove={borderData.title.length > 20 ? 3 : 0}
            overflowBelow={borderData.title.length > 20 ? 7 : 0}
          />
          <Text dimColor>body</Text>
          <PaneBorderBottom
            borderColor={borderColor}
            glyphs={glyphs}
            leftExtra={<Text dimColor>{borderData.left}</Text>}
            rightExtra={<Text dimColor>{borderData.right}</Text>}
          />
        </Box>
      );
    },
  },
  {
    id: 'top-bar',
    description: 'Store-free TopBar visual fixture.',
    sizes: BAR_SIZES,
    data: barData,
    focusStates: [true],
    render: ({ data }) => <TopBarFixture data={data as BarFixtureData} />,
  },
  {
    id: 'bottom-bar',
    description: 'Store-free BottomBar visual fixture.',
    sizes: BAR_SIZES,
    data: barData,
    focusStates: [true],
    render: ({ data }) => <BottomBarFixture data={data as BarFixtureData} />,
  },
];
