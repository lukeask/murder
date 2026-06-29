import { Box, Text } from 'ink';
// biome-ignore lint/style/useImportType: tsx's runtime transform for this CLI path expects React.
import React from 'react';
import { PANE_BORDER_GLYPHS, paneBorderStyle } from '../src/components/glyphs.js';
import { Ledger, type LedgerEntryContext } from '../src/components/Ledger.js';
import { Pane } from '../src/components/Pane.js';
import { PaneBorderBottom, PaneBorderTop } from '../src/components/paneBorder.js';
import { CrowsSurface } from '../src/components/panes/CrowsSurface.js';
import { DocumentSurface } from '../src/components/panes/DocumentSurface.js';
import { HistorySurface } from '../src/components/panes/HistorySurface.js';
import { NotesSurface } from '../src/components/panes/NotesSurface.js';
import { PlansSurface } from '../src/components/panes/PlansSurface.js';
import { ReportsSurface } from '../src/components/panes/ReportsSurface.js';
import { TicketsSurface } from '../src/components/panes/TicketsSurface.js';
import { TranscriptPane } from '../src/components/panes/TranscriptPane.js';
import { TreeSurface } from '../src/components/panes/TreeSurface.js';
import { UsageSurface } from '../src/components/panes/UsageSurface.js';
import {
  type ResourceRowFields,
  renderResourceEntry,
  renderResourceHeader,
} from '../src/components/ResourceRow.js';
import { MultiLineText, TextInput } from '../src/components/TextInput.js';
import { getTheme } from '../src/theme/themeStore.js';
import { crowsSurfaceRowsFromFixture } from './crowsPanelFixture.js';
import {
  type BarFixtureData,
  barData,
  type ChatInputFixtureData,
  type CrowFixtureRow,
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
  type TranscriptFixtureData,
  type TransitFixtureData,
  ticketRows,
  transcriptData,
  transitData,
  type UsageFixtureGroup,
  usageGroups,
} from './data/paneFixtureData.js';
import type { FixtureSize } from './renderInkFixture.js';
import { ticketFixtureToSurfaceRows } from './ticketsPanelFixture.js';
import type { PaneFixture } from './types.js';

const PANE_SIZES: readonly FixtureSize[] = [
  { id: 'preferred', width: 54, height: 14 },
  { id: 'cramped', width: 30, height: 8 },
  { id: 'minimum', width: 20, height: 5 },
];

const TICKETS_SURFACE_SIZES: readonly FixtureSize[] = [
  { id: 'preferred', width: 54, height: 14 },
  { id: 'cramped', width: 30, height: 8 },
  { id: 'minimum', width: 25, height: 5 },
];

const TREE_SURFACE_SIZES: readonly FixtureSize[] = [
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
    description: 'Store-free PlansSurface with explicit width/height contract.',
    sizes: PANE_SIZES,
    data: resourceRows,
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <PlansSurface
          rows={data as readonly ResourceRowFields[]}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
  },
  {
    id: 'notes-panel',
    description: 'Store-free NotesSurface with explicit width/height contract.',
    sizes: PANE_SIZES,
    // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
    data: { mixed: resourceRows['mixed'] ?? [], empty: [] },
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <NotesSurface
          rows={data as readonly ResourceRowFields[]}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
  },
  {
    id: 'reports-panel',
    description: 'Store-free ReportsSurface with explicit width/height contract.',
    sizes: PANE_SIZES,
    // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
    data: { mixed: resourceRows['overflow'] ?? [], empty: [] },
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <ReportsSurface
          rows={data as readonly ResourceRowFields[]}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
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
    description: 'Store-free TicketsSurface with explicit width/height contract.',
    sizes: TICKETS_SURFACE_SIZES,
    data: ticketRows,
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <TicketsSurface
          rows={ticketFixtureToSurfaceRows(data as readonly TicketFixtureRow[])}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
  },
  {
    id: 'crows-panel',
    description: 'Store-free CrowsSurface body wrapper with grouped rows.',
    sizes: PANE_SIZES,
    // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
    data: { mixed: crowRows['mixed'] ?? [], loading: crowRows['loading'] ?? [] },
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <CrowsSurface
          rows={crowsSurfaceRowsFromFixture(data as readonly CrowFixtureRow[])}
          // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature requires bracket access.
          status={data === (crowRows['loading'] ?? []) ? 'loading' : 'idle'}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
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
    description: 'Store-free HistorySurface body wrapper with fixed-height intention rows.',
    sizes: PANE_SIZES,
    data: historyRows,
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <HistorySurface
          rows={data as readonly HistoryFixtureRow[]}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
  },
  {
    id: 'usage-panel',
    description: 'Store-free UsageSurface body wrapper with colored gauges.',
    sizes: PANE_SIZES,
    data: usageGroups,
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <UsageSurface
          groups={data as readonly UsageFixtureGroup[]}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
  },
  {
    id: 'tree-panel',
    description: 'Store-free TreeSurface body wrapper with deterministic railway rows.',
    sizes: TREE_SURFACE_SIZES,
    data: transitData,
    render: ({ data, focused, width, height }) => {
      const theme = getTheme();
      return (
        <TreeSurface
          data={data as TransitFixtureData}
          focused={focused}
          theme={theme}
          width={width}
          height={height}
        />
      );
    },
  },
  {
    id: 'doc-pane',
    description: 'Store-free DocumentSurface wrapper with document lines and scrollbar chrome.',
    sizes: PANE_SIZES,
    data: docData,
    render: ({ data, focused, width, height }) => {
      const doc = data as DocFixtureData;
      return (
        <DocumentSurface
          title={doc.title}
          lines={doc.lines}
          scroll={doc.scroll}
          focused={focused}
          width={width}
          height={height}
        />
      );
    },
  },
  {
    id: 'transcript-pane',
    description: 'Store-free TranscriptPane with explicit width/height contract.',
    sizes: PANE_SIZES,
    data: transcriptData,
    render: ({ data, focused, width, height }) => {
      const transcript = data as TranscriptFixtureData;
      return (
        <TranscriptPane
          title={transcript.title}
          footerLeft={transcript.footerLeft}
          footerRight={transcript.footerRight}
          turns={transcript.turns.map((turn, index) => ({
            speaker: turn.speaker,
            text: turn.lines.join('\n'),
            blockId: `fixture-${index}`,
          }))}
          viewMode="verbose"
          scrollUp={0}
          gotoLine={null}
          focused={focused}
          width={width}
          height={height}
        />
      );
    },
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
