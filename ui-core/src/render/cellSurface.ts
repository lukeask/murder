export interface CellStyle {
  readonly fg?: string;
  readonly bg?: string;
  readonly bold?: boolean;
  readonly dim?: boolean;
  readonly italic?: boolean;
  readonly underline?: boolean;
  readonly strikethrough?: boolean;
}

export interface Cell {
  readonly char: string;
  readonly style: CellStyle;
}

export interface CellSurface {
  readonly width: number;
  readonly height: number;
  readonly cells: Cell[];
}

export interface CellOverlay {
  readonly x: number;
  readonly y: number;
  readonly cells: readonly Cell[];
  readonly z?: number;
}

export interface TextRun {
  readonly text: string;
  readonly style: CellStyle;
}

export interface SurfaceRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

const EMPTY_STYLE: CellStyle = {};
const N = 1;
const E = 2;
const S = 4;
const W = 8;
const CONNECTION_GLYPHS: Readonly<Record<number, string>> = {
  [N | S]: '│',
  [E | W]: '─',
  [E | S]: '┌',
  [W | S]: '┐',
  [E | N]: '└',
  [W | N]: '┘',
  [N | E | S]: '├',
  [N | W | S]: '┤',
  [E | S | W]: '┬',
  [N | E | W]: '┴',
  [N | E | S | W]: '┼',
};
const GLYPH_CONNECTIONS = new Map(
  Object.entries(CONNECTION_GLYPHS).map(([mask, glyph]) => [glyph, Number(mask)]),
);

function styleKey(style: CellStyle): string {
  return `${style.fg ?? ''}\0${style.bg ?? ''}\0${style.bold === true ? '1' : '0'}\0${
    style.dim === true ? '1' : '0'
  }\0${style.italic === true ? '1' : '0'}\0${style.underline === true ? '1' : '0'}\0${
    style.strikethrough === true ? '1' : '0'
  }`;
}

function normalizeChar(char: string): string {
  return Array.from(char)[0] ?? ' ';
}

function indexOf(surface: CellSurface, x: number, y: number): number {
  return y * surface.width + x;
}

export function createSurface(
  width: number,
  height: number,
  fillStyle: CellStyle = {},
): CellSurface {
  const safeWidth = Math.max(0, Math.floor(width));
  const safeHeight = Math.max(0, Math.floor(height));
  const fill: Cell = { char: ' ', style: fillStyle };
  return {
    width: safeWidth,
    height: safeHeight,
    cells: Array.from({ length: safeWidth * safeHeight }, () => fill),
  };
}

export function putText(
  surface: CellSurface,
  x: number,
  y: number,
  text: string,
  style: CellStyle = EMPTY_STYLE,
): CellSurface {
  if (y < 0 || y >= surface.height || surface.width === 0) {
    return surface;
  }
  const chars = Array.from(text);
  for (let i = 0; i < chars.length; i += 1) {
    const cx = x + i;
    if (cx >= 0 && cx < surface.width) {
      surface.cells[indexOf(surface, cx, y)] = { char: normalizeChar(chars[i] ?? ' '), style };
    }
  }
  return surface;
}

/** Put one clipped cell. These mutating drawing helpers share the surface's intentionally mutable backing array. */
export function putCell(
  surface: CellSurface,
  x: number,
  y: number,
  char: string,
  style: CellStyle = EMPTY_STYLE,
): CellSurface {
  if (x >= 0 && x < surface.width && y >= 0 && y < surface.height) {
    surface.cells[indexOf(surface, x, y)] = { char: normalizeChar(char), style };
  }
  return surface;
}

export function putClippedText(
  surface: CellSurface,
  x: number,
  y: number,
  width: number,
  text: string,
  style: CellStyle = EMPTY_STYLE,
): CellSurface {
  return putText(surface, x, y, Array.from(text).slice(0, Math.max(0, width)).join(''), style);
}

export function drawHorizontal(
  surface: CellSurface,
  x1: number,
  x2: number,
  y: number,
  connectionMask: number = 0,
  style: CellStyle = EMPTY_STYLE,
): CellSurface {
  const from = Math.min(x1, x2);
  const to = Math.max(x1, x2);
  for (let x = from; x <= to; x += 1) {
    mergeConnectionCell(surface, x, y, E | W | connectionMask, style);
  }
  return surface;
}

export function drawVertical(
  surface: CellSurface,
  x: number,
  y1: number,
  y2: number,
  connectionMask: number = 0,
  style: CellStyle = EMPTY_STYLE,
): CellSurface {
  const from = Math.min(y1, y2);
  const to = Math.max(y1, y2);
  for (let y = from; y <= to; y += 1) {
    mergeConnectionCell(surface, x, y, N | S | connectionMask, style);
  }
  return surface;
}

function mergeConnectionCell(
  surface: CellSurface,
  x: number,
  y: number,
  mask: number,
  style: CellStyle,
): void {
  if (x < 0 || x >= surface.width || y < 0 || y >= surface.height) return;
  const current = surface.cells[indexOf(surface, x, y)] as Cell;
  const merged = (GLYPH_CONNECTIONS.get(current.char) ?? 0) | mask;
  putCell(surface, x, y, CONNECTION_GLYPHS[merged] ?? (merged & (N | S) ? '│' : '─'), style);
}

export function drawBox(
  surface: CellSurface,
  rect: SurfaceRect,
  style: CellStyle = EMPTY_STYLE,
): CellSurface {
  if (rect.width <= 0 || rect.height <= 0) return surface;
  if (rect.width === 1 || rect.height === 1) {
    for (let y = rect.y; y < rect.y + rect.height; y += 1)
      for (let x = rect.x; x < rect.x + rect.width; x += 1) putCell(surface, x, y, '─', style);
    return surface;
  }
  drawHorizontal(surface, rect.x + 1, rect.x + rect.width - 2, rect.y, 0, style);
  drawHorizontal(surface, rect.x + 1, rect.x + rect.width - 2, rect.y + rect.height - 1, 0, style);
  drawVertical(surface, rect.x, rect.y + 1, rect.y + rect.height - 2, 0, style);
  drawVertical(surface, rect.x + rect.width - 1, rect.y + 1, rect.y + rect.height - 2, 0, style);
  putCell(surface, rect.x, rect.y, '┌', style);
  putCell(surface, rect.x + rect.width - 1, rect.y, '┐', style);
  putCell(surface, rect.x, rect.y + rect.height - 1, '└', style);
  putCell(surface, rect.x + rect.width - 1, rect.y + rect.height - 1, '┘', style);
  return surface;
}

export function cellsFromText(text: string, style: CellStyle = EMPTY_STYLE): Cell[] {
  return Array.from(text, (char) => ({ char: normalizeChar(char), style }));
}

export function applyOverlay(surface: CellSurface, overlay: CellOverlay): CellSurface {
  if (overlay.y < 0 || overlay.y >= surface.height || surface.width === 0) {
    return surface;
  }
  for (let i = 0; i < overlay.cells.length; i += 1) {
    const cx = overlay.x + i;
    if (cx >= 0 && cx < surface.width) {
      surface.cells[indexOf(surface, cx, overlay.y)] = overlay.cells[i] as Cell;
    }
  }
  return surface;
}

export function applyOverlays(surface: CellSurface, overlays: readonly CellOverlay[]): CellSurface {
  for (const overlay of [...overlays].sort((a, b) => (a.z ?? 0) - (b.z ?? 0))) {
    applyOverlay(surface, overlay);
  }
  return surface;
}

export function renderSurface(surface: CellSurface, y = 0): TextRun[] {
  if (y < 0 || y >= surface.height || surface.width === 0) {
    return [];
  }
  const runs: TextRun[] = [];
  for (let x = 0; x < surface.width; x += 1) {
    const cell = surface.cells[indexOf(surface, x, y)] as Cell;
    const last = runs.at(-1);
    if (last !== undefined && styleKey(last.style) === styleKey(cell.style)) {
      runs[runs.length - 1] = { ...last, text: last.text + cell.char };
    } else {
      runs.push({ text: cell.char, style: cell.style });
    }
  }
  return runs;
}
