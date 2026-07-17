import type {
  BlockContent,
  Code,
  List,
  ListItem,
  Nodes,
  PhrasingContent,
  Root,
  RootContent,
  Table,
} from 'mdast';
import remarkGfm from 'remark-gfm';
import remarkParse from 'remark-parse';
import stringWidth from 'string-width';
import { unified } from 'unified';
import { terminalSafeText } from '../utils/terminalSafeText.js';
import { wrapTextToRows } from '../utils/wrapText.js';
import type { CellStyle, TextRun } from './cellSurface.js';

export type DocumentRenderMode = 'plain' | 'markdown';

export interface StyledDocumentRow {
  readonly runs: readonly TextRun[];
  /** One-based source range represented by this physical row. */
  readonly sourceStartLine: number | null;
  readonly sourceEndLine: number | null;
}

export interface DocumentLayout {
  readonly rows: readonly StyledDocumentRow[];
  /** Index by one-based source line; index zero is always zero. */
  readonly sourceLineToRow: readonly number[];
}

export interface DocumentStyles {
  readonly text: CellStyle;
  readonly heading: CellStyle;
  readonly emphasis: CellStyle;
  readonly strong: CellStyle;
  readonly delete: CellStyle;
  readonly code: CellStyle;
  readonly quote: CellStyle;
  readonly link: CellStyle;
  readonly marker: CellStyle;
  readonly muted: CellStyle;
}

export const DEFAULT_DOCUMENT_STYLES: DocumentStyles = {
  text: {},
  heading: { bold: true },
  emphasis: { italic: true },
  strong: { bold: true },
  delete: { strikethrough: true },
  code: { dim: true },
  quote: { italic: true, dim: true },
  link: { underline: true },
  marker: { bold: true },
  muted: { dim: true },
};

interface InlinePiece {
  readonly text: string;
  readonly style: CellStyle;
  readonly hardBreak?: boolean;
}

interface SourceRange {
  readonly start: number;
  readonly end: number;
}

const parser = unified().use(remarkParse).use(remarkGfm);

export function parseMarkdown(source: string): Root {
  return parser.parse(source);
}

function mergeStyles(...styles: readonly CellStyle[]): CellStyle {
  return Object.assign({}, ...styles);
}

function sameStyle(left: CellStyle, right: CellStyle): boolean {
  return (
    left.fg === right.fg &&
    left.bg === right.bg &&
    left.bold === right.bold &&
    left.dim === right.dim &&
    left.italic === right.italic &&
    left.underline === right.underline &&
    left.strikethrough === right.strikethrough
  );
}

function pushRun(runs: TextRun[], text: string, style: CellStyle): void {
  if (text === '') {
    return;
  }
  const last = runs.at(-1);
  if (last !== undefined && sameStyle(last.style, style)) {
    runs[runs.length - 1] = { text: last.text + text, style };
  } else {
    runs.push({ text, style });
  }
}

export function documentRowText(row: StyledDocumentRow): string {
  return row.runs.map((run) => run.text).join('');
}

export function documentRowWidth(row: StyledDocumentRow): number {
  return stringWidth(documentRowText(row));
}

function sourceRange(node: Nodes): SourceRange {
  return {
    start: node.position?.start.line ?? 1,
    end: node.position?.end.line ?? node.position?.start.line ?? 1,
  };
}

function takeColumns(text: string, columns: number): [string, string] {
  if (columns <= 0 || text === '') {
    return ['', text];
  }
  let used = 0;
  let index = 0;
  for (const char of text) {
    const width = stringWidth(char);
    if (used + width > columns) {
      break;
    }
    used += width;
    index += char.length;
  }
  return [text.slice(0, index), text.slice(index)];
}

function clippedRuns(runs: readonly TextRun[], width: number): TextRun[] {
  const result: TextRun[] = [];
  let remaining = width;
  for (const run of runs) {
    if (remaining <= 0) {
      break;
    }
    const [head] = takeColumns(run.text, remaining);
    pushRun(result, head, run.style);
    remaining -= stringWidth(head);
  }
  return result;
}

function prefixWidth(runs: readonly TextRun[]): number {
  return runs.reduce((total, run) => total + stringWidth(run.text), 0);
}

function safePrefix(runs: readonly TextRun[], width: number, reserveContent = true): TextRun[] {
  return clippedRuns(runs, Math.max(0, width - (reserveContent ? 1 : 0)));
}

function row(
  runs: readonly TextRun[],
  range: SourceRange | null,
  width: number,
): StyledDocumentRow {
  return {
    runs: clippedRuns(runs, width),
    sourceStartLine: range?.start ?? null,
    sourceEndLine: range?.end ?? null,
  };
}

function splitInlinePiece(piece: InlinePiece): InlinePiece[] {
  if (piece.hardBreak === true) {
    return [piece];
  }
  const parts = piece.text.split(/(\s+)/u).filter((part) => part !== '');
  return parts.map((text) => ({ text, style: piece.style }));
}

function wrapInline(
  pieces: readonly InlinePiece[],
  width: number,
  firstPrefix: readonly TextRun[],
  continuationPrefix: readonly TextRun[],
  range: SourceRange,
): StyledDocumentRow[] {
  const result: StyledDocumentRow[] = [];
  const columns = Math.max(1, width);
  let prefix = safePrefix(firstPrefix, columns);
  let runs = [...prefix];
  let used = prefixWidth(prefix);
  let hasContent = false;

  const trimTrailingSpace = (): void => {
    if (!hasContent) {
      return;
    }
    const last = runs.at(-1);
    if (last === undefined) {
      return;
    }
    const text = last.text.replace(/\s+$/u, '');
    if (text === '') {
      runs.pop();
    } else if (text !== last.text) {
      runs[runs.length - 1] = { ...last, text };
    }
  };

  const flush = (): void => {
    trimTrailingSpace();
    result.push(row(runs, range, columns));
    prefix = safePrefix(continuationPrefix, columns);
    runs = [...prefix];
    used = prefixWidth(prefix);
    hasContent = false;
  };

  for (const original of pieces.flatMap(splitInlinePiece)) {
    if (original.hardBreak === true) {
      flush();
      continue;
    }
    let text = terminalSafeText(original.text);
    const whitespace = /^\s+$/u.test(text);
    if (whitespace) {
      text = ' ';
      if (
        !hasContent ||
        used >= columns ||
        documentRowText(row(runs, range, columns)).endsWith(' ')
      ) {
        continue;
      }
    }
    while (text !== '') {
      const available = columns - used;
      if (available <= 0) {
        flush();
        if (whitespace) {
          break;
        }
        continue;
      }
      const textColumns = stringWidth(text);
      if (
        !whitespace &&
        hasContent &&
        textColumns > available &&
        textColumns <= columns - prefixWidth(safePrefix(continuationPrefix, columns))
      ) {
        flush();
        continue;
      }
      const [head, tail] = takeColumns(text, available);
      if (head === '') {
        flush();
        continue;
      }
      pushRun(runs, head, original.style);
      used += stringWidth(head);
      hasContent = true;
      text = tail;
      if (text !== '') {
        flush();
      }
    }
  }

  if (hasContent || result.length === 0 || runs.length > 0) {
    trimTrailingSpace();
    result.push(row(runs, range, columns));
  }
  return result;
}

function inlinePieces(
  nodes: readonly PhrasingContent[],
  styles: DocumentStyles,
  inherited: CellStyle = styles.text,
): InlinePiece[] {
  const pieces: InlinePiece[] = [];
  for (const node of nodes) {
    switch (node.type) {
      case 'text':
        pieces.push({ text: node.value.replace(/\n+/gu, ' '), style: inherited });
        break;
      case 'break':
        pieces.push({ text: '', style: inherited, hardBreak: true });
        break;
      case 'inlineCode':
        pieces.push({ text: node.value, style: mergeStyles(inherited, styles.code) });
        break;
      case 'emphasis':
        pieces.push(
          ...inlinePieces(node.children, styles, mergeStyles(inherited, styles.emphasis)),
        );
        break;
      case 'strong':
        pieces.push(...inlinePieces(node.children, styles, mergeStyles(inherited, styles.strong)));
        break;
      case 'delete':
        pieces.push(...inlinePieces(node.children, styles, mergeStyles(inherited, styles.delete)));
        break;
      case 'link': {
        const label = inlinePlainText(node.children);
        pieces.push(...inlinePieces(node.children, styles, mergeStyles(inherited, styles.link)));
        if (label !== node.url) {
          pieces.push({
            text: ` (${node.url})`,
            style: mergeStyles(inherited, styles.muted, styles.link),
          });
        }
        break;
      }
      case 'linkReference':
        pieces.push(...inlinePieces(node.children, styles, mergeStyles(inherited, styles.link)));
        pieces.push({
          text: ` [${node.label ?? node.identifier}]`,
          style: mergeStyles(inherited, styles.muted),
        });
        break;
      case 'image':
        pieces.push({ text: node.alt ?? node.url, style: mergeStyles(inherited, styles.muted) });
        if (node.alt !== node.url) {
          pieces.push({ text: ` (${node.url})`, style: mergeStyles(inherited, styles.link) });
        }
        break;
      case 'imageReference':
        pieces.push({
          text: node.alt ?? node.label ?? node.identifier,
          style: mergeStyles(inherited, styles.muted),
        });
        break;
      case 'html':
        pieces.push({ text: node.value, style: mergeStyles(inherited, styles.muted) });
        break;
      case 'footnoteReference':
        pieces.push({ text: `[^${node.label ?? node.identifier}]`, style: styles.link });
        break;
    }
  }
  return pieces;
}

function inlinePlainText(nodes: readonly PhrasingContent[]): string {
  return nodes
    .map((node) => {
      if ('value' in node && typeof node.value === 'string') {
        return node.value;
      }
      if ('children' in node && Array.isArray(node.children)) {
        return inlinePlainText(node.children as PhrasingContent[]);
      }
      if (node.type === 'image' || node.type === 'imageReference') {
        return node.alt ?? '';
      }
      return '';
    })
    .join('');
}

function plainRuns(text: string, style: CellStyle): TextRun[] {
  return text === '' ? [] : [{ text, style }];
}

class MarkdownLayouter {
  readonly rows: StyledDocumentRow[] = [];

  constructor(
    readonly width: number,
    readonly styles: DocumentStyles,
  ) {}

  render(root: Root): readonly StyledDocumentRow[] {
    this.renderChildren(root.children, []);
    while (this.rows.length > 0 && documentRowText(this.rows.at(-1) as StyledDocumentRow) === '') {
      this.rows.pop();
    }
    return this.rows;
  }

  private blank(prefix: readonly TextRun[] = []): void {
    if (this.rows.length === 0 || documentRowText(this.rows.at(-1) as StyledDocumentRow) === '') {
      return;
    }
    this.rows.push(row(prefix, null, this.width));
  }

  private renderChildren(children: readonly RootContent[], prefix: readonly TextRun[]): void {
    for (const node of children) {
      this.renderBlock(node, prefix);
    }
  }

  private renderBlock(node: RootContent | BlockContent, prefix: readonly TextRun[]): void {
    const range = sourceRange(node);
    switch (node.type) {
      case 'paragraph':
        this.rows.push(
          ...wrapInline(
            inlinePieces(node.children, this.styles),
            this.width,
            prefix,
            prefix,
            range,
          ),
        );
        this.blank(prefix);
        break;
      case 'heading': {
        const marker = `${'#'.repeat(node.depth)} `;
        const first = [...prefix, { text: marker, style: this.styles.marker }];
        const continuation = [
          ...prefix,
          { text: ' '.repeat(stringWidth(marker)), style: this.styles.marker },
        ];
        this.rows.push(
          ...wrapInline(
            inlinePieces(node.children, this.styles, this.styles.heading),
            this.width,
            first,
            continuation,
            range,
          ),
        );
        this.blank(prefix);
        break;
      }
      case 'thematicBreak': {
        const available = Math.max(
          1,
          this.width - prefixWidth(safePrefix(prefix, this.width, false)),
        );
        this.rows.push(
          row(
            [
              ...safePrefix(prefix, this.width, false),
              { text: '─'.repeat(available), style: this.styles.muted },
            ],
            range,
            this.width,
          ),
        );
        this.blank(prefix);
        break;
      }
      case 'blockquote': {
        const quotePrefix = [
          ...prefix,
          { text: '│ ', style: mergeStyles(this.styles.marker, this.styles.quote) },
        ];
        for (const child of node.children) {
          this.renderBlock(child, quotePrefix);
        }
        this.blank(prefix);
        break;
      }
      case 'list':
        this.renderList(node, prefix);
        this.blank(prefix);
        break;
      case 'code':
        this.renderCode(node, prefix);
        this.blank(prefix);
        break;
      case 'table':
        this.renderTable(node, prefix);
        this.blank(prefix);
        break;
      case 'html':
        this.rows.push(
          ...wrapInline(
            [{ text: node.value, style: this.styles.muted }],
            this.width,
            prefix,
            prefix,
            range,
          ),
        );
        this.blank(prefix);
        break;
      case 'definition':
        break;
      case 'yaml':
        this.renderCode({ ...node, type: 'code', lang: 'yaml', meta: null }, prefix);
        this.blank(prefix);
        break;
      case 'footnoteDefinition': {
        const footnotePrefix = [
          ...prefix,
          { text: `[^${node.label ?? node.identifier}]: `, style: this.styles.marker },
        ];
        for (const child of node.children) {
          this.renderBlock(child, footnotePrefix);
        }
        this.blank(prefix);
        break;
      }
    }
  }

  private renderList(node: List, prefix: readonly TextRun[]): void {
    let ordinal = node.start ?? 1;
    for (const item of node.children) {
      const baseMarker = node.ordered === true ? `${ordinal}. ` : '• ';
      const taskMarker = item.checked === true ? '[x] ' : item.checked === false ? '[ ] ' : '';
      const marker = `${baseMarker}${taskMarker}`;
      this.renderListItem(item, prefix, marker);
      if (node.spread === true || item.spread === true) {
        this.blank(prefix);
      }
      ordinal += 1;
    }
  }

  private renderListItem(item: ListItem, prefix: readonly TextRun[], marker: string): void {
    const firstPrefix = [...prefix, { text: marker, style: this.styles.marker }];
    const childPrefix = [
      ...prefix,
      { text: ' '.repeat(stringWidth(marker)), style: this.styles.marker },
    ];
    let first = true;
    for (const child of item.children) {
      if (first && child.type === 'paragraph') {
        this.rows.push(
          ...wrapInline(
            inlinePieces(child.children, this.styles),
            this.width,
            firstPrefix,
            childPrefix,
            sourceRange(child),
          ),
        );
      } else if (child.type === 'list') {
        this.renderList(child, childPrefix);
      } else {
        this.renderBlock(child, first ? firstPrefix : childPrefix);
      }
      first = false;
    }
    if (item.children.length === 0) {
      this.rows.push(row(firstPrefix, sourceRange(item), this.width));
    }
  }

  private renderCode(node: Code, prefix: readonly TextRun[]): void {
    const range = sourceRange(node);
    const codePrefix = [
      ...prefix,
      { text: '│ ', style: mergeStyles(this.styles.marker, this.styles.code) },
    ];
    if (node.lang !== null && node.lang !== undefined && node.lang !== '') {
      this.rows.push(
        row(
          [
            ...prefix,
            {
              text: `┌ ${node.lang}${node.meta === null ? '' : ` ${node.meta}`}`,
              style: this.styles.muted,
            },
          ],
          { start: range.start, end: range.start },
          this.width,
        ),
      );
    }
    const values = node.value === '' ? [''] : node.value.split('\n');
    let sourceLine = node.position?.start.line ?? 1;
    if (node.lang !== null && node.lang !== undefined) {
      sourceLine += 1;
    }
    for (const value of values) {
      const available = Math.max(1, this.width - prefixWidth(safePrefix(codePrefix, this.width)));
      const chunks = wrapVerbatim(value, available);
      for (const chunk of chunks) {
        this.rows.push(
          row(
            [...safePrefix(codePrefix, this.width), { text: chunk, style: this.styles.code }],
            { start: sourceLine, end: sourceLine },
            this.width,
          ),
        );
      }
      sourceLine += 1;
    }
    if (node.lang !== null && node.lang !== undefined && node.lang !== '') {
      this.rows.push(
        row(
          [...prefix, { text: '└', style: this.styles.muted }],
          { start: range.end, end: range.end },
          this.width,
        ),
      );
    }
  }

  private renderTable(node: Table, prefix: readonly TextRun[]): void {
    const rows = node.children;
    const header = rows[0];
    if (header === undefined) {
      return;
    }
    const headers = header.children.map((cell) => inlinePlainText(cell.children));
    const body = rows.slice(1);
    const natural = headers.map((heading, index) =>
      Math.max(
        stringWidth(heading),
        ...body.map((tableRow) =>
          stringWidth(inlinePlainText(tableRow.children[index]?.children ?? [])),
        ),
      ),
    );
    const tableWidth = natural.reduce((total, cell) => total + cell + 3, 1);
    const available = Math.max(1, this.width - prefixWidth(safePrefix(prefix, this.width)));
    if (tableWidth <= available) {
      for (const [rowIndex, tableRow] of rows.entries()) {
        const runs: TextRun[] = [...prefix, { text: '|', style: this.styles.marker }];
        tableRow.children.forEach((cell, cellIndex) => {
          const value = inlinePlainText(cell.children);
          const padding = Math.max(0, (natural[cellIndex] ?? 0) - stringWidth(value));
          pushRun(
            runs,
            ` ${value}${' '.repeat(padding)} `,
            rowIndex === 0 ? this.styles.strong : this.styles.text,
          );
          pushRun(runs, '|', this.styles.marker);
        });
        this.rows.push(row(runs, sourceRange(tableRow), this.width));
        if (rowIndex === 0) {
          const separator = natural.map((cell) => '-'.repeat(cell + 2)).join('|');
          this.rows.push(
            row(
              [...prefix, { text: `|${separator}|`, style: this.styles.muted }],
              sourceRange(tableRow),
              this.width,
            ),
          );
        }
      }
      return;
    }

    const dataRows = body.length === 0 ? [header] : body;
    for (const [rowIndex, tableRow] of dataRows.entries()) {
      if (rowIndex > 0) {
        this.blank(prefix);
      }
      tableRow.children.forEach((cell, cellIndex) => {
        const label = headers[cellIndex] ?? `Column ${cellIndex + 1}`;
        const labelPrefix = [...prefix, { text: `${label}: `, style: this.styles.strong }];
        this.rows.push(
          ...wrapInline(
            inlinePieces(cell.children, this.styles),
            this.width,
            labelPrefix,
            [...prefix, { text: '  ', style: this.styles.text }],
            sourceRange(cell),
          ),
        );
      });
    }
  }
}

function wrapVerbatim(text: string, width: number): readonly string[] {
  const safe = terminalSafeText(text).replace(/\t/gu, '    ');
  if (safe === '') {
    return [''];
  }
  const result: string[] = [];
  let rest = safe;
  while (rest !== '') {
    const [head, tail] = takeColumns(rest, Math.max(1, width));
    result.push(head);
    rest = tail;
  }
  return result;
}

function sourceMap(rows: readonly StyledDocumentRow[], sourceLineCount: number): readonly number[] {
  const map = Array.from({ length: sourceLineCount + 1 }, () => -1);
  map[0] = 0;
  rows.forEach((displayRow, rowIndex) => {
    if (displayRow.sourceStartLine === null || displayRow.sourceEndLine === null) {
      return;
    }
    for (
      let line = Math.max(1, displayRow.sourceStartLine);
      line <= Math.min(sourceLineCount, displayRow.sourceEndLine);
      line += 1
    ) {
      if (map[line] === -1) {
        map[line] = rowIndex;
      }
    }
  });
  let nearest = 0;
  for (let line = 1; line < map.length; line += 1) {
    if (map[line] === -1) {
      map[line] = nearest;
    } else {
      nearest = map[line] as number;
    }
  }
  return map;
}

export function rowForSourceLine(layout: DocumentLayout, sourceLine: number): number {
  const safeLine = Math.max(1, Math.floor(sourceLine));
  return layout.sourceLineToRow[Math.min(safeLine, layout.sourceLineToRow.length - 1)] ?? 0;
}

export function layoutPlainText(
  source: string,
  width: number,
  styles: DocumentStyles = DEFAULT_DOCUMENT_STYLES,
): DocumentLayout {
  if (source === '') {
    return { rows: [], sourceLineToRow: [0, 0] };
  }
  const columns = Math.max(1, width);
  const rows: StyledDocumentRow[] = [];
  const lines = source.split('\n');
  lines.forEach((line, index) => {
    const wrapped = wrapTextToRows(line, columns, { hard: true, wordWrap: true });
    for (const displayLine of wrapped) {
      rows.push(
        row(plainRuns(displayLine, styles.text), { start: index + 1, end: index + 1 }, columns),
      );
    }
  });
  return { rows, sourceLineToRow: sourceMap(rows, lines.length) };
}

export function layoutMarkdown(
  root: Root,
  source: string,
  width: number,
  styles: DocumentStyles = DEFAULT_DOCUMENT_STYLES,
): DocumentLayout {
  const columns = Math.max(1, width);
  const rows = new MarkdownLayouter(columns, styles).render(root);
  return {
    rows,
    sourceLineToRow: sourceMap(rows, Math.max(1, source.split('\n').length)),
  };
}

export function layoutDocument(
  source: string,
  mode: DocumentRenderMode,
  width: number,
  styles: DocumentStyles = DEFAULT_DOCUMENT_STYLES,
): DocumentLayout {
  if (mode === 'plain') {
    return layoutPlainText(source, width, styles);
  }
  if (source === '') {
    return { rows: [], sourceLineToRow: [0, 0] };
  }
  return layoutMarkdown(parseMarkdown(source), source, width, styles);
}
