/**
 * Scalable tree indentation for doc-list panes.
 *
 * Selector/fixture data bakes depth as leading spaces (standard 4 per level). Display indent
 * scales in three tiers (2 / 1 / 1 spaces per level) so child titles stay readable at narrow widths.
 */

/** Spaces per tree level in selector/fixture `name` strings. */
export const STANDARD_TAB_LEN = 4;

/** Display spaces per level when the pane is very wide. */
export const WIDE_TAB_LEN = 2;

/** Display spaces per level at medium widths (default). */
export const DEFAULT_TAB_LEN = 1;

/** Display spaces per level at narrow widths. */
export const MIN_TAB_LEN = 1;

/** Default minimum visible title prefix when truncating longer titles. */
export const MIN_TITLE_PREFIX = 6;

/** Inner width at/above which indent uses {@link WIDE_TAB_LEN}. */
export const WIDE_INNER_WIDTH = 26;

/** Inner width at/below which indent uses {@link MIN_TAB_LEN}. */
export const NARROW_INNER_WIDTH = 14;

export interface DocTreeIndentOpts {
  readonly standardTabLen?: number;
  readonly wideTabLen?: number;
  readonly defaultTabLen?: number;
  readonly minTabLen?: number;
  readonly minTitlePrefix?: number;
  readonly wideWidth?: number;
  readonly narrowWidth?: number;
  /** Truncation budget; defaults to `innerWidth` when omitted. */
  readonly maxLen?: number;
}

export interface ParsedTreeName {
  readonly depth: number;
  readonly title: string;
}

/** Map pane inner width to discrete spaces-per-level (2 / 1 / 1). */
export function tabLenForWidth(innerWidth: number, opts?: DocTreeIndentOpts): number {
  const wide = opts?.wideWidth ?? WIDE_INNER_WIDTH;
  const narrow = opts?.narrowWidth ?? NARROW_INNER_WIDTH;
  const wideTab = opts?.wideTabLen ?? WIDE_TAB_LEN;
  const defaultTab = opts?.defaultTabLen ?? DEFAULT_TAB_LEN;
  const min = opts?.minTabLen ?? MIN_TAB_LEN;
  if (innerWidth >= wide) {
    return wideTab;
  }
  if (innerWidth <= narrow) {
    return min;
  }
  return defaultTab;
}

/** Split fixture/selector leading spaces from the title portion. */
export function parseTreeName(
  nameWithLeadingSpaces: string,
  standardTabLen: number = STANDARD_TAB_LEN,
): ParsedTreeName {
  const match = /^(\s*)(.*)$/.exec(nameWithLeadingSpaces);
  const indent = match?.[1] ?? '';
  const title = match?.[2] ?? nameWithLeadingSpaces;
  const depth =
    standardTabLen > 0 ? Math.floor(indent.length / standardTabLen) : indent.length > 0 ? 1 : 0;
  return { depth, title };
}

export function scaleTreeIndent(depth: number, tabLen: number): string {
  if (depth <= 0 || tabLen <= 0) {
    return '';
  }
  return ' '.repeat(depth * tabLen);
}

/** Truncate the title portion; keep ≥minPrefix leading chars when the title is longer. */
export function truncateTreeTitle(
  title: string,
  budget: number,
  minPrefix: number = MIN_TITLE_PREFIX,
): string {
  if (budget <= 0) {
    return '';
  }
  if (title.length <= budget) {
    return title;
  }
  const minVisible = title.length > minPrefix ? minPrefix : title.length;
  if (budget <= minVisible) {
    return title.slice(0, budget);
  }
  return `${title.slice(0, budget - 1)}…`;
}

/**
 * Scale tree indent from leading spaces, then truncate the title for `maxLen`.
 *
 * @param innerWidth Pane content width — drives indent scaling.
 * @param opts.maxLen Display budget (after star/marker columns); defaults to `innerWidth`.
 */
export function formatDocTreeName(
  nameWithLeadingSpaces: string,
  innerWidth: number,
  opts?: DocTreeIndentOpts,
): string {
  const standardTabLen = opts?.standardTabLen ?? STANDARD_TAB_LEN;
  const minTitlePrefix = opts?.minTitlePrefix ?? MIN_TITLE_PREFIX;
  const maxLen = opts?.maxLen ?? innerWidth;
  if (maxLen <= 0) {
    return '';
  }

  const { depth, title } = parseTreeName(nameWithLeadingSpaces, standardTabLen);
  const tabLen = tabLenForWidth(innerWidth, opts);
  const indent = scaleTreeIndent(depth, tabLen);
  const titleBudget = maxLen - indent.length;
  if (titleBudget <= 0) {
    return indent.slice(0, maxLen);
  }
  return `${indent}${truncateTreeTitle(title, titleBudget, minTitlePrefix)}`;
}
