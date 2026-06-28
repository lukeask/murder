export interface CellStyle {
  readonly fg?: string;
  readonly bg?: string;
  readonly bold?: boolean;
  readonly dim?: boolean;
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

const EMPTY_STYLE: CellStyle = {};

function styleKey(style: CellStyle): string {
  return `${style.fg ?? ''}\0${style.bg ?? ''}\0${style.bold === true ? '1' : '0'}\0${
    style.dim === true ? '1' : '0'
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
