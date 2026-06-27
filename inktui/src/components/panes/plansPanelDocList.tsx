/**
 * PlansPanel doc-list row layout — pane-local renderer implementing the doc-list content
 * priority (star column, name ≥6 rule, char count before date, date compression, header legend).
 */

import { Box, Text } from 'ink';
import type { LedgerEntryContext } from '../Ledger.js';
import type { ResourceRowFields } from '../ResourceRow.js';
import { formatDocTreeName } from './docTreeIndent.js';

/** Star column width — always reserved (`★ ` or `  `). */
export const STAR_COL_WIDTH = 2;

export type PlansDateLevel = 'full' | 'abbrev' | 'numeric' | 'hidden';

export interface PlansRowLayout {
  readonly linesPerEntry: 1 | 2;
  readonly showCharCount: boolean;
  readonly dateLevel: PlansDateLevel;
  readonly showHeader: boolean;
  readonly headerCharCount: boolean;
}

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const;

export function starPrefix(starred: boolean): string {
  return starred ? '★ ' : '  ';
}

/** Strip a leading star glyph from fixture/selector names when the row is starred. */
export function displayName(row: ResourceRowFields): string {
  const raw = row.name;
  if (!row.starred) {
    return raw;
  }
  return raw.replace(/^★\s*/, '');
}

/** Truncate with scaled tree indent and ≥6 leading title chars when clipped. */
export function truncateName(name: string, maxLen: number, innerWidth?: number): string {
  return formatDocTreeName(name, innerWidth ?? maxLen, { maxLen });
}

/** Parse fixture/selector `Mon. dd HH:MM` into compression stages. */
export function compressDate(updatedAt: string, level: PlansDateLevel): string {
  if (level === 'hidden') {
    return '';
  }
  const match = /^(\w+)\.\s+(\d+)\s+\d+:\d+$/.exec(updatedAt);
  if (match === null) {
    return updatedAt;
  }
  const month = match[1] ?? '???';
  const day = match[2] ?? '??';
  const monthNum = MONTHS.indexOf(month as (typeof MONTHS)[number]) + 1;
  switch (level) {
    case 'full':
      return updatedAt;
    case 'abbrev':
      return `${month} ${day}`;
    case 'numeric':
      return monthNum > 0 ? `${monthNum}/${Number(day)}` : `${month} ${day}`;
    default:
      return level satisfies never;
  }
}

function truncateTail(text: string, maxLen: number): string {
  if (text.length <= maxLen) {
    return text;
  }
  if (maxLen <= 1) {
    return text.slice(0, maxLen);
  }
  return `${text.slice(0, maxLen - 1)}…`;
}

function fitMetadata(
  charCount: string,
  date: string,
  budget: number,
  showCharCount: boolean,
): string {
  if (budget <= 0) {
    return '';
  }
  const sep = ' · ';
  if (showCharCount && date.length > 0) {
    const full = `${charCount}${sep}${date}`;
    if (full.length <= budget) {
      return full;
    }
    if (charCount.length <= budget) {
      return truncateTail(charCount, budget);
    }
    return truncateTail(charCount, budget);
  }
  if (showCharCount) {
    return truncateTail(charCount, budget);
  }
  if (date.length > 0) {
    return truncateTail(date, budget);
  }
  return '';
}

/** Merge width-based row layout with height constraints (Phase 2). */
export function rowLayoutForDimensions(innerWidth: number, innerHeight: number): PlansRowLayout {
  const base = rowLayoutFor(innerWidth);
  if (innerHeight < 4) {
    return {
      linesPerEntry: 1,
      showCharCount: false,
      dateLevel: 'hidden',
      showHeader: false,
      headerCharCount: false,
    };
  }
  if (innerHeight < 6) {
    return {
      ...base,
      linesPerEntry: 1,
      showHeader: false,
      headerCharCount: false,
    };
  }
  if (base.linesPerEntry === 2 && innerHeight < 8) {
    return {
      ...base,
      showHeader: false,
    };
  }
  return base;
}

export function rowLayoutFor(innerWidth: number): PlansRowLayout {
  if (innerWidth < 10) {
    return {
      linesPerEntry: 1,
      showCharCount: false,
      dateLevel: 'hidden',
      showHeader: false,
      headerCharCount: false,
    };
  }
  if (innerWidth < 14) {
    return {
      linesPerEntry: 1,
      showCharCount: false,
      dateLevel: 'hidden',
      showHeader: false,
      headerCharCount: false,
    };
  }
  if (innerWidth < 20) {
    return {
      linesPerEntry: 1,
      showCharCount: innerWidth >= 18,
      dateLevel: innerWidth >= 16 ? 'numeric' : 'hidden',
      showHeader: false,
      headerCharCount: false,
    };
  }
  if (innerWidth < 26) {
    return {
      linesPerEntry: 2,
      showCharCount: true,
      dateLevel: 'abbrev',
      showHeader: true,
      headerCharCount: true,
    };
  }
  return {
    linesPerEntry: 2,
    showCharCount: true,
    dateLevel: 'full',
    showHeader: true,
    headerCharCount: true,
  };
}

function formatSecondLine(
  row: ResourceRowFields,
  layout: PlansRowLayout,
  innerWidth: number,
): string {
  const indent = ' '.repeat(STAR_COL_WIDTH);
  const budget = innerWidth - indent.length;
  if (budget <= 0) {
    return indent;
  }
  let dateLevel = layout.dateLevel;
  let showCharCount = layout.showCharCount;
  let date = compressDate(row.updatedAt, dateLevel);
  let meta = fitMetadata(row.charCount, date, budget, showCharCount);
  if (meta.length > 0) {
    return `${indent}${meta}`;
  }
  if (showCharCount) {
    meta = fitMetadata(row.charCount, '', budget, true);
    if (meta.length > 0) {
      return `${indent}${meta}`;
    }
  }
  for (const level of ['numeric', 'abbrev', 'hidden'] as const) {
    date = compressDate(row.updatedAt, level);
    meta = fitMetadata('', date, budget, false);
    if (meta.length > 0) {
      return `${indent}${meta}`;
    }
  }
  return indent;
}

export function renderPlansEntry(
  row: ResourceRowFields,
  ctx: LedgerEntryContext,
  innerWidth: number,
  layout: PlansRowLayout,
): React.ReactNode {
  const star = starPrefix(row.starred);
  if (layout.linesPerEntry === 1) {
    let suffix = '';
    if (layout.showCharCount || layout.dateLevel !== 'hidden') {
      const date = compressDate(row.updatedAt, layout.dateLevel);
      suffix = fitMetadata(
        row.charCount,
        date,
        Math.max(0, innerWidth - STAR_COL_WIDTH - 7),
        layout.showCharCount,
      );
      if (suffix.length > 0) {
        suffix = ` ${suffix}`;
      }
    }
    const nameMax = Math.max(0, innerWidth - STAR_COL_WIDTH - suffix.length);
    const name = formatDocTreeName(displayName(row), innerWidth, { maxLen: nameMax });
    return (
      <Text wrap="truncate" dimColor={!ctx.selected && suffix.length > 0}>
        {star}
        <Text dimColor={false}>{name}</Text>
        {suffix}
      </Text>
    );
  }
  const name = formatDocTreeName(displayName(row), innerWidth, {
    maxLen: innerWidth - STAR_COL_WIDTH,
  });
  const line2 = formatSecondLine(row, layout, innerWidth);
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">{`${star}${name}`}</Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {line2}
      </Text>
    </Box>
  );
}

export function renderPlansHeader(layout: PlansRowLayout): React.ReactNode {
  if (!layout.showHeader) {
    return null;
  }
  const secondLine = layout.headerCharCount ? '  size · updated' : '  updated';
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Text dimColor>{`${starPrefix(false)}name`}</Text>
      <Text dimColor>{secondLine}</Text>
    </Box>
  );
}
