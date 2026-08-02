import { terminalSafeText } from '../utils/terminalSafeText.js';
import type {
  TerminalBufferInput,
  TerminalCell,
  TerminalCellInput,
  TerminalColor,
  TerminalCursor,
  TerminalGridSnapshot,
  TerminalKeyframeInput,
  TerminalModes,
  TerminalRenditionInput,
  TerminalSurfaceState,
  TerminalSurfaceUpdate,
} from './types.js';

const DEFAULT_FG: TerminalColor = undefined;
const DEFAULT_BG: TerminalColor = undefined;
const DEFAULT_CURSOR: TerminalCursor = { x: 0, y: 0, visible: true, shape: 'block' };
const DEFAULT_MODES: TerminalModes = {
  applicationCursor: false,
  applicationKeypad: false,
  bracketedPaste: false,
  autoWrap: true,
  origin: false,
  cursorVisible: true,
  insert: false,
  alternate: false,
  synchronizedUpdates: false,
};

type ParserState = 'text' | 'escape' | 'csi' | 'osc' | 'oscEscape';
interface SavedState {
  readonly cursor: TerminalCursor;
  readonly attrs: Attributes;
}
interface Attributes {
  fg: TerminalColor;
  bg: TerminalColor;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  inverse: boolean;
  hidden: boolean;
  strikethrough: boolean;
}
interface Buffer {
  cells: TerminalCell[][];
  cursor: TerminalCursor;
  rendition: Attributes;
  saved: SavedState;
  scrollTop: number;
  scrollBottom: number;
  wrapPending: boolean;
}

function blankCell(): TerminalCell {
  return {
    text: ' ',
    width: 1,
    continuation: false,
    fg: DEFAULT_FG,
    bg: DEFAULT_BG,
    bold: false,
    dim: false,
    italic: false,
    underline: false,
    inverse: false,
    hidden: false,
    strikethrough: false,
  };
}
function blankRow(columns: number): TerminalCell[] {
  return Array.from({ length: columns }, blankCell);
}
function cloneCell(cell: TerminalCell): TerminalCell {
  return { ...cell };
}
function defaultAttrs(): Attributes {
  return {
    fg: DEFAULT_FG,
    bg: DEFAULT_BG,
    bold: false,
    dim: false,
    italic: false,
    underline: false,
    inverse: false,
    hidden: false,
    strikethrough: false,
  };
}
function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}
function charWidth(text: string): 0 | 1 | 2 {
  if (/^[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]$/u.test(text)) return 0;
  const point = text.codePointAt(0) ?? 0;
  return point >= 0x1100 &&
    (point <= 0x115f ||
      point === 0x2329 ||
      point === 0x232a ||
      (point >= 0x2e80 && point <= 0xa4cf) ||
      (point >= 0xac00 && point <= 0xd7a3) ||
      (point >= 0xf900 && point <= 0xfaff) ||
      (point >= 0xfe10 && point <= 0xfe19) ||
      (point >= 0xfe30 && point <= 0xfe6f) ||
      (point >= 0xff00 && point <= 0xff60) ||
      (point >= 0xffe0 && point <= 0xffe6) ||
      (point >= 0x1f300 && point <= 0x1faff))
    ? 2
    : 1;
}
function color(index: number): TerminalColor {
  return index < 16 ? index : `ansi256:${index}`;
}
function decodeBase64(value: string): Uint8Array {
  return Uint8Array.from(Buffer.from(value, 'base64'));
}

/** Persistent VT emulator. Ingestion mutates a model; consumers render immutable snapshots. */
export class TerminalSurfaceStore {
  private columns = 80;
  private rows = 24;
  private rowVersions = Array.from({ length: this.rows }, () => 0);
  private primary = this.newBuffer();
  private alternate = this.newBuffer();
  private active = this.primary;
  private attrs = defaultAttrs();
  private modes: TerminalModes = { ...DEFAULT_MODES };
  private parser: ParserState = 'text';
  private csi = '';
  private decoder = new TextDecoder('utf-8');
  private dirty = new Set<number>();
  private wholeDirty = true;
  private version = 0;
  private synchronousDepth = 0;
  private deferredNotify = false;
  private keyframeRequired = false;
  /** Rows cloned for mutation since the last published snapshot (copy-on-write batch). */
  private cowRows = new Set<number>();
  private readonly listeners = new Set<() => void>();
  private snapshot: TerminalGridSnapshot = this.makeSnapshot();

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  getSnapshot = (): TerminalGridSnapshot => this.snapshot;
  /** Export both buffers; rendering snapshots intentionally expose only the active one. */
  exportState(): TerminalSurfaceState {
    return {
      columns: this.columns,
      rows: this.rows,
      primary: this.exportBuffer(this.primary),
      alternate: this.exportBuffer(this.alternate),
      activeBuffer: this.active === this.alternate ? 'alternate' : 'primary',
      modes: { ...this.modes },
      keyframeRequired: this.keyframeRequired,
    };
  }
  resize(columns: number, rows: number): void {
    const nextColumns = Math.max(1, Math.floor(columns));
    const nextRows = Math.max(1, Math.floor(rows));
    if (nextColumns === this.columns && nextRows === this.rows) return;
    this.columns = nextColumns;
    this.rows = nextRows;
    this.rowVersions = Array.from({ length: this.rows }, () => this.version + 1);
    this.resizeBuffer(this.primary);
    this.resizeBuffer(this.alternate);
    this.wholeDirty = true;
    this.publish();
  }
  ingest(update: TerminalSurfaceUpdate): void {
    if (update.type === 'terminal.gap') {
      this.keyframeRequired = true;
      this.publish();
      return;
    }
    // Trust boundary: accepted transport updates are in-order. Sequence admission and gap
    // detection live in ApplicationWebSocketClient; the store does not re-check sequences.
    if (update.type === 'terminal.keyframe') {
      this.applyKeyframe(update);
      return;
    }
    if (update.type === 'terminal.frame' && (update.reset ?? true)) {
      this.applyFrame(update);
      return;
    }
    if (update.type === 'terminal.frame') {
      this.applyFrame(update);
      return;
    }
    if (this.keyframeRequired) return;
    this.consume(
      update.encoding === 'base64'
        ? this.decoder.decode(decodeBase64(update.data), { stream: true })
        : update.data,
    );
    this.publish();
  }

  private newBuffer(): Buffer {
    const cells = Array.from({ length: this.rows }, () => blankRow(this.columns));
    return {
      cells,
      cursor: { ...DEFAULT_CURSOR },
      rendition: defaultAttrs(),
      saved: { cursor: { ...DEFAULT_CURSOR }, attrs: defaultAttrs() },
      scrollTop: 0,
      scrollBottom: this.rows - 1,
      wrapPending: false,
    };
  }
  private resizeBuffer(buffer: Buffer): void {
    buffer.cells = Array.from({ length: this.rows }, (_, y) => {
      const old = buffer.cells[y] ?? [];
      return Array.from({ length: this.columns }, (_, x) =>
        old[x] === undefined ? blankCell() : cloneCell(old[x]),
      );
    });
    buffer.cursor = {
      ...buffer.cursor,
      x: clamp(buffer.cursor.x, 0, this.columns - 1),
      y: clamp(buffer.cursor.y, 0, this.rows - 1),
    };
    buffer.scrollTop = clamp(buffer.scrollTop, 0, this.rows - 1);
    buffer.scrollBottom = clamp(Math.max(buffer.scrollTop, buffer.scrollBottom), 0, this.rows - 1);
  }
  private applyKeyframe(frame: TerminalKeyframeInput): void {
    this.columns = Math.max(1, frame.columns);
    this.rows = Math.max(1, frame.rows);
    this.rowVersions = Array.from({ length: this.rows }, () => this.version + 1);
    this.primary = this.newBuffer();
    this.alternate = this.newBuffer();
    this.loadBuffer(
      this.primary,
      frame.primary ?? (frame.active_buffer !== 'alternate' ? this.legacyBuffer(frame) : undefined),
    );
    this.loadBuffer(
      this.alternate,
      frame.alternate ??
        (frame.active_buffer === 'alternate' ? this.legacyBuffer(frame) : undefined),
    );
    const target =
      frame.active_buffer === 'alternate' ||
      (frame.active_buffer === undefined && frame.modes?.alternate)
        ? this.alternate
        : this.primary;
    this.active = target;
    this.modes = { ...DEFAULT_MODES, ...frame.modes, alternate: target === this.alternate };
    this.attrs = this.attrsFromInput(frame.rendition ?? target.rendition);
    target.rendition = { ...this.attrs };
    this.parser = 'text';
    this.csi = '';
    this.decoder = new TextDecoder('utf-8');
    this.keyframeRequired = false;
    this.wholeDirty = true;
    this.synchronousDepth = this.modes.synchronizedUpdates ? 1 : 0;
    this.deferredNotify = false;
    this.publish();
  }
  private applyFrame(frame: Extract<TerminalSurfaceUpdate, { type: 'terminal.frame' }>): void {
    if (frame.columns !== undefined && frame.rows !== undefined) {
      this.columns = Math.max(1, frame.columns);
      this.rows = Math.max(1, frame.rows);
    }
    if (frame.reset ?? true) {
      this.primary = this.newBuffer();
      this.alternate = this.newBuffer();
      this.active = this.primary;
      this.modes = { ...DEFAULT_MODES };
      this.attrs = defaultAttrs();
      // Keep SGR so the VT consume path can paint colors; strip only C0/C1 that aren't ESC.
      const lines = terminalSafeText(frame.data, { stripAnsi: false }).split('\n');
      for (let y = 0; y < Math.min(lines.length, this.rows); y += 1) {
        this.active.cursor = { ...this.active.cursor, x: 0, y };
        this.consume(lines[y] ?? '');
      }
      this.active.cursor = { ...this.active.cursor, visible: false };
      this.modes.cursorVisible = false;
    } else if (!this.keyframeRequired) {
      this.consume(frame.data);
    }
    this.wholeDirty = true;
    this.publish();
  }
  private fromInput(input: TerminalCellInput | undefined): TerminalCell {
    if (input === undefined) return blankCell();
    const rendition = input.rendition;
    return {
      text: input.text ?? ' ',
      width: input.continuation ? 0 : (input.width ?? 1),
      continuation: input.continuation ?? input.width === 0,
      fg: input.fg ?? rendition?.fg,
      bg: input.bg ?? rendition?.bg,
      bold: input.bold ?? rendition?.bold ?? false,
      dim: input.dim ?? rendition?.dim ?? false,
      italic: input.italic ?? rendition?.italic ?? false,
      underline: input.underline ?? rendition?.underline ?? false,
      inverse: input.inverse ?? rendition?.inverse ?? false,
      hidden: input.hidden ?? rendition?.hidden ?? false,
      strikethrough: input.strikethrough ?? rendition?.strikethrough ?? false,
    };
  }
  private legacyBuffer(frame: TerminalKeyframeInput): TerminalBufferInput {
    return frame.cursor === undefined
      ? { cells: frame.cells ?? [] }
      : { cells: frame.cells ?? [], cursor: frame.cursor };
  }
  private loadBuffer(buffer: Buffer, input: TerminalBufferInput | undefined): void {
    if (input === undefined) return;
    buffer.cells = Array.from({ length: this.rows }, (_, y) =>
      Array.from({ length: this.columns }, (_, x) => this.fromInput(input.cells[y]?.[x])),
    );
    buffer.cursor = this.normalCursor(input.cursor);
    buffer.rendition = this.attrsFromInput(input.rendition);
    buffer.saved = {
      cursor: this.normalCursor(input.saved_cursor ?? input.savedCursor),
      attrs: this.attrsFromInput(input.saved_rendition ?? input.savedRendition),
    };
    buffer.scrollTop = clamp(input.scroll_top ?? input.scrollTop ?? 0, 0, this.rows - 1);
    buffer.scrollBottom = clamp(
      input.scroll_bottom ?? input.scrollBottom ?? this.rows - 1,
      buffer.scrollTop,
      this.rows - 1,
    );
    buffer.wrapPending = input.wrap_pending ?? input.wrapPending ?? false;
  }
  private exportBuffer(buffer: Buffer): TerminalSurfaceState['primary'] {
    return {
      cells: buffer.cells.map((row) => row.map(cloneCell)),
      cursor: { ...buffer.cursor },
      savedCursor: { ...buffer.saved.cursor },
      rendition: { ...buffer.rendition },
      savedRendition: { ...buffer.saved.attrs },
      scrollTop: buffer.scrollTop,
      scrollBottom: buffer.scrollBottom,
      wrapPending: buffer.wrapPending,
    };
  }
  private attrsFromInput(input: TerminalRenditionInput | undefined): Attributes {
    if (input === undefined) return defaultAttrs();
    return {
      fg: input.fg,
      bg: input.bg,
      bold: input.bold ?? false,
      dim: input.dim ?? false,
      italic: input.italic ?? false,
      underline: input.underline ?? false,
      inverse: input.inverse ?? false,
      hidden: input.hidden ?? false,
      strikethrough: input.strikethrough ?? false,
    };
  }
  private normalCursor(cursor: TerminalKeyframeInput['cursor']): TerminalCursor {
    return {
      x: clamp(cursor?.x ?? 0, 0, this.columns - 1),
      y: clamp(cursor?.y ?? 0, 0, this.rows - 1),
      visible: cursor?.visible ?? true,
      shape: cursor?.shape ?? 'block',
    };
  }
  private consume(text: string): void {
    for (const char of text) this.consumeChar(char);
  }
  private consumeChar(char: string): void {
    if (this.parser === 'text') {
      if (char === '\u001b') {
        this.parser = 'escape';
        return;
      }
      if (char === '\n' || char === '\u000b' || char === '\f') {
        this.lineFeed();
        return;
      }
      if (char === '\r') {
        this.active.cursor = { ...this.active.cursor, x: 0 };
        this.active.wrapPending = false;
        return;
      }
      if (char === '\b') {
        this.active.cursor = { ...this.active.cursor, x: Math.max(0, this.active.cursor.x - 1) };
        this.active.wrapPending = false;
        return;
      }
      if (char === '\t') {
        this.active.cursor = {
          ...this.active.cursor,
          x: Math.min(this.columns - 1, ((this.active.cursor.x >> 3) + 1) * 8),
        };
        return;
      }
      if (char >= ' ') this.put(char);
      return;
    }
    if (this.parser === 'escape') {
      this.escape(char);
      return;
    }
    if (this.parser === 'csi') {
      if (char >= '@' && char <= '~') {
        this.csiDispatch(char);
        this.parser = 'text';
        this.csi = '';
      } else if (char >= ' ' && char <= '?') this.csi += char;
      else this.parser = 'text';
      return;
    }
    if (this.parser === 'osc') {
      if (char === '\u0007') {
        this.parser = 'text';
      } else if (char === '\u001b') this.parser = 'oscEscape';
      return;
    }
    if (char === '\\') {
      this.parser = 'text';
    } else {
      this.parser = 'osc';
    }
  }
  private escape(char: string): void {
    this.parser = 'text';
    if (char === '[') {
      this.parser = 'csi';
      return;
    }
    if (char === ']') {
      this.parser = 'osc';
      return;
    }
    if (char === '7') {
      this.active.saved = { cursor: { ...this.active.cursor }, attrs: { ...this.attrs } };
      return;
    }
    if (char === '8') {
      this.active.cursor = { ...this.active.saved.cursor };
      this.attrs = { ...this.active.saved.attrs };
      this.active.rendition = { ...this.attrs };
      return;
    }
    if (char === 'D') {
      this.lineFeed();
      return;
    }
    if (char === 'M') {
      this.reverseIndex();
      return;
    }
    if (char === 'E') {
      this.lineFeed();
      this.active.cursor = { ...this.active.cursor, x: 0 };
      return;
    }
    if (char === 'c') {
      this.primary = this.newBuffer();
      this.alternate = this.newBuffer();
      this.active = this.primary;
      this.attrs = defaultAttrs();
      this.modes = { ...DEFAULT_MODES };
      this.wholeDirty = true;
    }
  }
  private csiDispatch(final: string): void {
    const raw = this.csi;
    const privateMode = raw.startsWith('?');
    const params = (privateMode ? raw.slice(1) : raw)
      .split(';')
      .map((item) => (item === '' ? 0 : Number(item)));
    const n = (index = 0, fallback = 1) => Math.max(1, params[index] || fallback);
    const cursor = this.active.cursor;
    switch (final) {
      case 'A':
        cursor.y = Math.max(this.modes.origin ? this.active.scrollTop : 0, cursor.y - n());
        break;
      case 'B':
        cursor.y = Math.min(
          this.modes.origin ? this.active.scrollBottom : this.rows - 1,
          cursor.y + n(),
        );
        break;
      case 'C':
        cursor.x = Math.min(this.columns - 1, cursor.x + n());
        break;
      case 'D':
        cursor.x = Math.max(0, cursor.x - n());
        break;
      case 'E':
        cursor.y = Math.min(this.rows - 1, cursor.y + n());
        cursor.x = 0;
        break;
      case 'F':
        cursor.y = Math.max(0, cursor.y - n());
        cursor.x = 0;
        break;
      case 'G':
        cursor.x = clamp(n() - 1, 0, this.columns - 1);
        break;
      case 'H':
      case 'f':
        cursor.y = clamp(
          (params[0] || 1) - 1 + (this.modes.origin ? this.active.scrollTop : 0),
          0,
          this.rows - 1,
        );
        cursor.x = clamp((params[1] || 1) - 1, 0, this.columns - 1);
        break;
      case 'J':
        this.eraseDisplay(params[0] || 0);
        break;
      case 'K':
        this.eraseLine(params[0] || 0);
        break;
      case 'm':
        this.sgr(params);
        break;
      case 'r':
        this.active.scrollTop = clamp((params[0] || 1) - 1, 0, this.rows - 1);
        this.active.scrollBottom = clamp(
          (params[1] || this.rows) - 1,
          this.active.scrollTop,
          this.rows - 1,
        );
        cursor.x = 0;
        cursor.y = this.modes.origin ? this.active.scrollTop : 0;
        break;
      case 's':
        this.active.saved = { cursor: { ...cursor }, attrs: { ...this.attrs } };
        break;
      case 'u':
        this.active.cursor = { ...this.active.saved.cursor };
        this.attrs = { ...this.active.saved.attrs };
        this.active.rendition = { ...this.attrs };
        break;
      case '@':
        this.insertChars(n());
        break;
      case 'P':
        this.deleteChars(n());
        break;
      case 'X':
        this.eraseChars(n());
        break;
      case 'L':
        this.insertLines(n());
        break;
      case 'M':
        this.deleteLines(n());
        break;
      case 'S':
        this.scrollUp(n());
        break;
      case 'T':
        this.scrollDown(n());
        break;
      case 'q': {
        if (raw.endsWith(' ')) {
          const style = params[0] || 0;
          cursor.shape =
            style === 3 || style === 4 ? 'underline' : style === 5 || style === 6 ? 'bar' : 'block';
        }
        break;
      }
      case 'h':
      case 'l':
        this.setMode(params, privateMode, final === 'h');
        break;
      default:
        break;
    }
    this.active.wrapPending = false;
  }
  private put(char: string): void {
    const width = charWidth(char);
    const buffer = this.active;
    if (width === 0) {
      const row = this.mutableRow(buffer.cursor.y);
      let priorX = Math.max(0, buffer.cursor.x - 1);
      while (priorX > 0 && row[priorX]?.continuation) priorX -= 1;
      const prior = row[priorX];
      if (prior !== undefined) {
        prior.text += char;
      }
      return;
    }
    if (buffer.wrapPending && this.modes.autoWrap) {
      this.lineFeed();
      buffer.cursor = { ...buffer.cursor, x: 0 };
      buffer.wrapPending = false;
    }
    if (width === 2 && buffer.cursor.x === this.columns - 1) {
      if (this.modes.autoWrap) {
        this.lineFeed();
        buffer.cursor = { ...buffer.cursor, x: 0 };
      } else return;
    }
    if (this.modes.insert) this.insertChars(width);
    const x = buffer.cursor.x;
    const row = this.mutableRow(buffer.cursor.y);
    this.clearOverlap(row, x);
    if (width === 2) this.clearOverlap(row, x + 1);
    row[x] = { text: char, width, continuation: false, ...this.attrs };
    if (width === 2 && x + 1 < this.columns)
      row[x + 1] = {
        ...blankCell(),
        text: '',
        width: 0,
        continuation: true,
        fg: this.attrs.fg,
        bg: this.attrs.bg,
      };
    const next = x + width;
    buffer.cursor = { ...buffer.cursor, x: Math.min(this.columns - 1, next) };
    buffer.wrapPending = next >= this.columns;
  }
  private clearOverlap(row: TerminalCell[], x: number): void {
    const cell = row[x];
    if (cell?.continuation && x > 0) row[x - 1] = blankCell();
    if (cell?.width === 2 && x + 1 < this.columns) row[x + 1] = blankCell();
  }
  private repairWideCells(row: TerminalCell[]): void {
    for (let x = 0; x < this.columns; x += 1) {
      const cell = row[x];
      if (cell === undefined) continue;
      if (cell.continuation) {
        if (x === 0 || row[x - 1]?.width !== 2) row[x] = blankCell();
      } else if (cell.width === 2) {
        if (x + 1 >= this.columns) {
          row[x] = blankCell();
        } else {
          row[x + 1] = {
            ...blankCell(),
            text: '',
            width: 0,
            continuation: true,
            fg: cell.fg,
            bg: cell.bg,
          };
          x += 1;
        }
      }
    }
  }
  private lineFeed(): void {
    const b = this.active;
    if (b.cursor.y === b.scrollBottom) this.scrollUp(1);
    else b.cursor = { ...b.cursor, y: Math.min(this.rows - 1, b.cursor.y + 1) };
    b.wrapPending = false;
  }
  private reverseIndex(): void {
    const b = this.active;
    if (b.cursor.y === b.scrollTop) this.scrollDown(1);
    else b.cursor = { ...b.cursor, y: Math.max(0, b.cursor.y - 1) };
  }
  private scrollUp(amount: number): void {
    const b = this.active;
    const count = Math.min(amount, b.scrollBottom - b.scrollTop + 1);
    b.cells.splice(b.scrollTop, count);
    b.cells.splice(
      b.scrollBottom - count + 1,
      0,
      ...Array.from({ length: count }, () => blankRow(this.columns)),
    );
    this.markRegion(b.scrollTop, b.scrollBottom);
  }
  private scrollDown(amount: number): void {
    const b = this.active;
    const count = Math.min(amount, b.scrollBottom - b.scrollTop + 1);
    b.cells.splice(b.scrollBottom - count + 1, count);
    b.cells.splice(b.scrollTop, 0, ...Array.from({ length: count }, () => blankRow(this.columns)));
    this.markRegion(b.scrollTop, b.scrollBottom);
  }
  private eraseDisplay(mode: number): void {
    if (mode === 2 || mode === 3) {
      for (let y = 0; y < this.rows; y += 1) this.active.cells[y] = blankRow(this.columns);
      this.wholeDirty = true;
      return;
    }
    if (mode === 0) {
      this.eraseLine(0);
      for (let y = this.active.cursor.y + 1; y < this.rows; y += 1) {
        this.active.cells[y] = blankRow(this.columns);
        this.mark(y);
      }
    } else {
      this.eraseLine(1);
      for (let y = 0; y < this.active.cursor.y; y += 1) {
        this.active.cells[y] = blankRow(this.columns);
        this.mark(y);
      }
    }
  }
  private eraseLine(mode: number): void {
    const row = this.mutableRow(this.active.cursor.y);
    const start = mode === 0 ? this.active.cursor.x : 0;
    const end = mode === 1 ? this.active.cursor.x : this.columns - 1;
    this.clearOverlap(row, start);
    this.clearOverlap(row, end);
    for (let x = start; x <= end; x += 1) row[x] = blankCell();
    this.repairWideCells(row);
  }
  private eraseChars(amount: number): void {
    const row = this.mutableRow(this.active.cursor.y);
    const end = Math.min(this.columns, this.active.cursor.x + amount);
    this.clearOverlap(row, this.active.cursor.x);
    if (end > this.active.cursor.x) this.clearOverlap(row, end - 1);
    for (let x = this.active.cursor.x; x < end; x += 1) row[x] = blankCell();
    this.repairWideCells(row);
  }
  private insertChars(amount: number): void {
    const row = this.mutableRow(this.active.cursor.y);
    this.clearOverlap(row, this.active.cursor.x);
    row.splice(this.active.cursor.x, 0, ...Array.from({ length: amount }, blankCell));
    row.length = this.columns;
    this.repairWideCells(row);
  }
  private deleteChars(amount: number): void {
    const row = this.mutableRow(this.active.cursor.y);
    this.clearOverlap(row, this.active.cursor.x);
    if (this.active.cursor.x + amount < this.columns)
      this.clearOverlap(row, this.active.cursor.x + amount);
    row.splice(this.active.cursor.x, amount);
    while (row.length < this.columns) row.push(blankCell());
    this.repairWideCells(row);
  }
  private insertLines(amount: number): void {
    const b = this.active;
    if (b.cursor.y < b.scrollTop || b.cursor.y > b.scrollBottom) return;
    const count = Math.min(amount, b.scrollBottom - b.cursor.y + 1);
    b.cells.splice(b.cursor.y, 0, ...Array.from({ length: count }, () => blankRow(this.columns)));
    b.cells.splice(b.scrollBottom + 1, count);
    this.markRegion(b.cursor.y, b.scrollBottom);
  }
  private deleteLines(amount: number): void {
    const b = this.active;
    if (b.cursor.y < b.scrollTop || b.cursor.y > b.scrollBottom) return;
    const count = Math.min(amount, b.scrollBottom - b.cursor.y + 1);
    b.cells.splice(b.cursor.y, count);
    b.cells.splice(
      b.scrollBottom - count + 1,
      0,
      ...Array.from({ length: count }, () => blankRow(this.columns)),
    );
    this.markRegion(b.cursor.y, b.scrollBottom);
  }
  private sgr(params: number[]): void {
    if (params.length === 0) params = [0];
    for (let i = 0; i < params.length; i += 1) {
      const p = params[i] ?? 0;
      if (p === 0) this.attrs = defaultAttrs();
      else if (p === 1) this.attrs.bold = true;
      else if (p === 2) this.attrs.dim = true;
      else if (p === 3) this.attrs.italic = true;
      else if (p === 4) this.attrs.underline = true;
      else if (p === 7) this.attrs.inverse = true;
      else if (p === 8) this.attrs.hidden = true;
      else if (p === 9) this.attrs.strikethrough = true;
      else if (p === 22) {
        this.attrs.bold = false;
        this.attrs.dim = false;
      } else if (p === 23) this.attrs.italic = false;
      else if (p === 24) this.attrs.underline = false;
      else if (p === 27) this.attrs.inverse = false;
      else if (p === 28) this.attrs.hidden = false;
      else if (p === 29) this.attrs.strikethrough = false;
      else if (p >= 30 && p <= 37) this.attrs.fg = color(p - 30);
      else if (p >= 40 && p <= 47) this.attrs.bg = color(p - 40);
      else if (p >= 90 && p <= 97) this.attrs.fg = color(p - 90 + 8);
      else if (p >= 100 && p <= 107) this.attrs.bg = color(p - 100 + 8);
      else if (p === 39) this.attrs.fg = DEFAULT_FG;
      else if (p === 49) this.attrs.bg = DEFAULT_BG;
      else if ((p === 38 || p === 48) && params[i + 1] === 5) {
        const value = params[i + 2] ?? 0;
        if (p === 38) this.attrs.fg = color(value);
        else this.attrs.bg = color(value);
        i += 2;
      } else if ((p === 38 || p === 48) && params[i + 1] === 2) {
        const rgb = `${params[i + 2] ?? 0},${params[i + 3] ?? 0},${params[i + 4] ?? 0}`;
        if (p === 38) this.attrs.fg = `rgb(${rgb})`;
        else this.attrs.bg = `rgb(${rgb})`;
        i += 4;
      }
    }
    this.active.rendition = { ...this.attrs };
  }
  private setMode(params: number[], privateMode: boolean, enabled: boolean): void {
    for (const value of params) {
      if (!privateMode && value === 4) this.modes.insert = enabled;
      if (!privateMode) continue;
      if (value === 1) this.modes.applicationCursor = enabled;
      else if (value === 6) this.modes.origin = enabled;
      else if (value === 7) this.modes.autoWrap = enabled;
      else if (value === 25) {
        this.modes.cursorVisible = enabled;
        this.active.cursor = { ...this.active.cursor, visible: enabled };
      } else if (value === 66) this.modes.applicationKeypad = enabled;
      else if (value === 2004) this.modes.bracketedPaste = enabled;
      else if (value === 2026) {
        if (this.modes.synchronizedUpdates === enabled) continue;
        this.modes.synchronizedUpdates = enabled;
        this.synchronousDepth = enabled ? 1 : 0;
        if (this.synchronousDepth <= 0) {
          if (this.deferredNotify) {
            this.deferredNotify = false;
            this.publish();
          }
        }
      } else if (value === 47 || value === 1047 || value === 1049) {
        this.active.rendition = { ...this.attrs };
        if (enabled) {
          if (value === 1049) {
            this.primary.saved = { cursor: { ...this.primary.cursor }, attrs: { ...this.attrs } };
            this.alternate = this.newBuffer();
          }
          this.active = this.alternate;
          this.modes.alternate = true;
        } else {
          this.active = this.primary;
          if (value === 1049) {
            this.primary.cursor = { ...this.primary.saved.cursor };
            this.attrs = { ...this.primary.saved.attrs };
          }
          this.modes.alternate = false;
        }
        this.attrs =
          !enabled && value === 1049
            ? { ...this.primary.saved.attrs }
            : { ...this.active.rendition };
        this.wholeDirty = true;
      }
    }
  }
  /**
   * Copy-on-write row accessor: clone each changed row at most once per publication batch, then
   * mark it dirty. Never clones the whole grid per character. Callers that mutate cells must go
   * through this (put, erase family, insert/delete chars). Scroll paths splice whole row arrays
   * and already replace references.
   */
  private mutableRow(y: number): TerminalCell[] {
    const buffer = this.active;
    const existing = buffer.cells[y];
    if (existing === undefined) {
      const created = blankRow(this.columns);
      buffer.cells[y] = created;
      this.cowRows.add(y);
      this.mark(y);
      return created;
    }
    if (!this.cowRows.has(y)) {
      const cloned = existing.map(cloneCell);
      buffer.cells[y] = cloned;
      this.cowRows.add(y);
      this.mark(y);
      return cloned;
    }
    this.mark(y);
    return existing;
  }
  private mark(y: number): void {
    this.dirty.add(y);
    this.rowVersions[y] = (this.rowVersions[y] ?? 0) + 1;
  }
  private markRegion(top: number, bottom: number): void {
    for (let y = top; y <= bottom; y += 1) this.mark(y);
  }
  private publish(): void {
    if (this.synchronousDepth > 0) {
      this.deferredNotify = true;
      return;
    }
    if (this.wholeDirty) this.rowVersions = this.rowVersions.map((value) => value + 1);
    this.version += 1;
    this.snapshot = this.makeSnapshot();
    for (const listener of this.listeners) listener();
    this.dirty.clear();
    this.wholeDirty = false;
    this.cowRows.clear();
  }
  private makeSnapshot(): TerminalGridSnapshot {
    return {
      columns: this.columns,
      rows: this.rows,
      // Shallow-copy the outer row array so scroll splices on the live buffer cannot reshape
      // a published snapshot. Row contents are protected by mutableRow copy-on-write.
      cells: this.active.cells.slice(),
      cursor: {
        ...this.active.cursor,
        visible: this.active.cursor.visible && this.modes.cursorVisible,
      },
      modes: { ...this.modes },
      version: this.version,
      dirtyRows: this.wholeDirty ? null : [...this.dirty],
      rowVersions: [...this.rowVersions],
      keyframeRequired: this.keyframeRequired,
    };
  }
}
