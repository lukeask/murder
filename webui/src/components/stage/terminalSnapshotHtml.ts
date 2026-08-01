/**
 * Render a TerminalSurfaceStore grid snapshot as escaped HTML for `<pre>` (span styles only).
 * Ports enough of the inktui TerminalGridView cell walk for the web Stage terminal tab.
 */

import type {
  TerminalCell,
  TerminalColor,
  TerminalGridSnapshot,
} from '@murder/ui-core/terminalSurface/types.js';

const ANSI16 = [
  '#000000',
  '#cd0000',
  '#00cd00',
  '#cdcd00',
  '#0000ee',
  '#cd00cd',
  '#00cdcd',
  '#e5e5e5',
  '#7f7f7f',
  '#ff0000',
  '#00ff00',
  '#ffff00',
  '#5c5cff',
  '#ff00ff',
  '#00ffff',
  '#ffffff',
] as const;

function escapeXml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function cssColor(color: TerminalColor): string | undefined {
  if (color === undefined) return undefined;
  if (typeof color === 'string') {
    if (color.startsWith('rgb(') || color.startsWith('#')) return color;
    if (color.startsWith('ansi256:')) {
      const index = Number(color.slice('ansi256:'.length));
      return Number.isFinite(index) ? ansi256Css(index) : undefined;
    }
    return undefined;
  }
  if (color >= 0 && color < 16) return ANSI16[color];
  return ansi256Css(color);
}

function ansi256Css(index: number): string | undefined {
  if (index < 0 || index > 255) return undefined;
  if (index < 16) return ANSI16[index];
  if (index < 232) {
    const n = index - 16;
    const r = Math.floor(n / 36);
    const g = Math.floor((n % 36) / 6);
    const b = n % 6;
    const ramp = (v: number): number => (v === 0 ? 0 : 55 + v * 40);
    return `rgb(${ramp(r)},${ramp(g)},${ramp(b)})`;
  }
  const gray = 8 + (index - 232) * 10;
  return `rgb(${gray},${gray},${gray})`;
}

function cellStyle(cell: TerminalCell, cursor: boolean): string {
  let fg = cssColor(cell.fg);
  let bg = cssColor(cell.bg);
  if (cell.inverse !== cursor) {
    const swap = fg;
    fg = bg ?? '#e5e5e5';
    bg = swap ?? '#000000';
  }
  const parts: string[] = [];
  if (fg !== undefined) parts.push(`color:${fg}`);
  if (bg !== undefined) parts.push(`background-color:${bg}`);
  if (cell.bold) parts.push('font-weight:bold');
  if (cell.dim) parts.push('opacity:0.65');
  if (cell.italic) parts.push('font-style:italic');
  if (cell.underline) parts.push('text-decoration:underline');
  if (cell.strikethrough) parts.push('text-decoration:line-through');
  return parts.join(';');
}

function sameStyle(a: TerminalCell, aCursor: boolean, b: TerminalCell, bCursor: boolean): boolean {
  return (
    aCursor === bCursor &&
    a.fg === b.fg &&
    a.bg === b.bg &&
    a.bold === b.bold &&
    a.dim === b.dim &&
    a.italic === b.italic &&
    a.underline === b.underline &&
    a.inverse === b.inverse &&
    a.hidden === b.hidden &&
    a.strikethrough === b.strikethrough
  );
}

/** Convert one snapshot into HTML suitable for dangerouslySetInnerHTML inside a `<pre>`. */
export function terminalSnapshotToHtml(snapshot: TerminalGridSnapshot): string {
  const lines: string[] = [];
  for (let y = 0; y < snapshot.rows; y += 1) {
    const row = snapshot.cells[y] ?? [];
    let html = '';
    let runText = '';
    let runCell: TerminalCell | null = null;
    let runCursor = false;

    const flush = (): void => {
      if (runCell === null || runText === '') return;
      const style = cellStyle(runCell, runCursor);
      const escaped = escapeXml(runText);
      html += style === '' ? escaped : `<span style="${style}">${escaped}</span>`;
      runText = '';
      runCell = null;
    };

    for (let x = 0; x < snapshot.columns; x += 1) {
      const cell = row[x];
      if (cell === undefined || cell.continuation) continue;
      const cursor =
        snapshot.cursor.visible && snapshot.cursor.y === y && snapshot.cursor.x === x;
      const text = cell.hidden ? ' '.repeat(Math.max(1, cell.width)) : cell.text || ' ';
      if (runCell !== null && sameStyle(runCell, runCursor, cell, cursor)) {
        runText += text;
      } else {
        flush();
        runCell = cell;
        runCursor = cursor;
        runText = text;
      }
    }
    flush();
    lines.push(html);
  }
  while (lines.length > 0 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  return lines.join('<br>');
}
