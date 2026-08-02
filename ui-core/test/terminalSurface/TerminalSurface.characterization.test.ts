import { describe, expect, it } from 'vitest';
import type { TerminalUpdate } from '@murder/ui-core/application/ApplicationClient.js';
import type {
  TerminalCell,
  TerminalKeyframe,
  TerminalRendition,
} from '@murder/ui-core/generated/applicationProtocol.js';
import { adaptTerminalUpdate } from '@murder/ui-core/terminalSurface/protocolAdapter.js';
import { TerminalSurfaceStore } from '@murder/ui-core/terminalSurface/TerminalSurfaceStore.js';
import type { TerminalKeyframeInput } from '@murder/ui-core/terminalSurface/types.js';

const DEFAULT_RENDITION: TerminalRendition = {
  foreground: { kind: 'default' },
  background: { kind: 'default' },
  bold: false,
  faint: false,
  italic: false,
  underline: false,
  blink: false,
  inverse: false,
  invisible: false,
  strikethrough: false,
};

function rawChunk(sequence: number, bytes: Uint8Array): TerminalUpdate {
  return {
    type: 'terminal.chunk',
    sequence,
    encoding: 'base64',
    data: Buffer.from(bytes).toString('base64'),
  };
}

function rowText(cells: readonly { readonly text: string; readonly width: number }[]): string {
  return cells
    .filter((cell) => cell.width !== 0)
    .map((cell) => cell.text)
    .join('');
}

function wireKeyframe(sequence: number, firstRow: string): TerminalKeyframe {
  const columns = 4;
  const rows = 2;
  const cells = Array.from(
    { length: columns * rows },
    (_, index): TerminalCell => ({
      text: index < firstRow.length ? (firstRow[index] ?? ' ') : ' ',
      width: 1,
      rendition: DEFAULT_RENDITION,
    }),
  );
  const cursor = { column: 0, row: 1, visible: true, shape: 'block' as const };
  const buffer = {
    cells,
    cursor,
    saved_cursor: cursor,
    rendition: DEFAULT_RENDITION,
    saved_rendition: DEFAULT_RENDITION,
    scroll_top: 0,
    scroll_bottom: rows - 1,
    wrap_pending: false,
  };
  return {
    type: 'terminal.keyframe',
    sequence,
    captured_at: '2026-07-27T00:00:00Z',
    columns,
    rows,
    primary: buffer,
    alternate: {
      ...buffer,
      cells: cells.map(() => ({ text: ' ', width: 1, rendition: DEFAULT_RENDITION })),
    },
    active_buffer: 'primary',
    rendition: DEFAULT_RENDITION,
    modes: {
      application_cursor: false,
      application_keypad: false,
      bracketed_paste: false,
      insert: false,
      origin: false,
      wraparound: true,
      synchronized_updates: false,
    },
  };
}

function legacyFlatKeyframe(
  sequence: number,
  rows: number,
  columns: number,
  cellRows: string[],
): TerminalKeyframeInput {
  return {
    type: 'terminal.keyframe',
    sequence,
    columns,
    rows,
    active_buffer: 'primary',
    cells: cellRows.map((text) =>
      Array.from({ length: columns }, (_, x) => ({
        text: text[x] ?? ' ',
        width: 1,
      })),
    ),
    cursor: { x: 0, y: 0, visible: true, shape: 'block' },
  };
}

function ingestVt(store: TerminalSurfaceStore, sequence: number, text: string): void {
  store.ingest(adaptTerminalUpdate(rawChunk(sequence, new TextEncoder().encode(text))));
}

describe('TerminalSurfaceStore characterization', () => {
  describe('keyframe loading', () => {
    it('loads structured primary and alternate buffers from an adapted wire keyframe', () => {
      const store = new TerminalSurfaceStore();
      store.ingest(adaptTerminalUpdate(wireKeyframe(1, 'ABCD')));

      const state = store.exportState();
      expect(state.columns).toBe(4);
      expect(state.rows).toBe(2);
      expect(state.activeBuffer).toBe('primary');
      expect(rowText(state.primary.cells[0] ?? [])).toBe('ABCD');
      expect(rowText(state.alternate.cells[0] ?? [])).toBe('    ');
      expect(state.primary.cursor).toEqual({ x: 0, y: 1, visible: true, shape: 'block' });
      expect(state.keyframeRequired).toBe(false);
    });

    it('loads the legacy flat cells form into the active primary buffer', () => {
      const store = new TerminalSurfaceStore();
      store.ingest(
        legacyFlatKeyframe(1, 2, 4, ['LEG1', 'LEG2']),
      );

      const state = store.exportState();
      expect(rowText(state.primary.cells[0] ?? [])).toBe('LEG1');
      expect(rowText(state.primary.cells[1] ?? [])).toBe('LEG2');
      expect(state.primary.cursor).toEqual({ x: 0, y: 0, visible: true, shape: 'block' });
      expect(rowText(state.alternate.cells[0] ?? [])).toBe('    ');
    });

    it('routes legacy flat cells to the alternate buffer when active_buffer is alternate', () => {
      const store = new TerminalSurfaceStore();
      store.ingest({
        type: 'terminal.keyframe',
        sequence: 1,
        columns: 4,
        rows: 2,
        active_buffer: 'alternate',
        cells: [
          [{ text: 'A', width: 1 }, { text: 'L', width: 1 }, { text: 'T', width: 1 }, { text: ' ', width: 1 }],
          [{ text: ' ', width: 1 }, { text: ' ', width: 1 }, { text: ' ', width: 1 }, { text: ' ', width: 1 }],
        ],
        modes: { alternate: true },
      });

      const state = store.exportState();
      expect(state.activeBuffer).toBe('alternate');
      expect(rowText(state.alternate.cells[0] ?? [])).toBe('ALT ');
      expect(rowText(state.primary.cells[0] ?? [])).toBe('    ');
      expect(state.modes.alternate).toBe(true);
    });
  });

  describe('primary and alternate buffer switching', () => {
    it('switches to the alternate buffer with DECSET 1049 and back with DECRESET 1049', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 3);
      ingestVt(store, 1, '\u001b[31mPRI\u001b[0m');
      ingestVt(store, 2, '\u001b[?1049h\u001b[32mALT\u001b[0m');
      ingestVt(store, 3, '\u001b[?1049l');

      const state = store.exportState();
      expect(state.activeBuffer).toBe('primary');
      expect(state.modes.alternate).toBe(false);
      expect(rowText(state.primary.cells[0] ?? [])).toBe('PRI   ');
      expect(rowText(state.alternate.cells[0] ?? [])).toBe('ALT   ');
    });

    it('switches buffers with DECSET 47 and DECRESET 47 without saving primary state', () => {
      const store = new TerminalSurfaceStore();
      store.resize(4, 2);
      ingestVt(store, 1, 'P1');
      ingestVt(store, 2, '\u001b[?47hA2');
      ingestVt(store, 3, '\u001b[?47l');

      const state = store.exportState();
      expect(state.activeBuffer).toBe('primary');
      expect(rowText(state.primary.cells[0] ?? [])).toBe('P1  ');
      expect(rowText(state.alternate.cells[0] ?? [])).toBe('A2  ');
    });
  });

  describe('saved cursor and saved rendition', () => {
    it('restores cursor and SGR via ESC 7 and ESC 8', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      ingestVt(store, 1, '\u001b[1;1H\u001b[31mR\u001b7');
      ingestVt(store, 2, '\u001b[1;4H\u001b[32mX\u001b8');

      const state = store.exportState();
      expect(state.primary.cursor).toMatchObject({ x: 1, y: 0 });
      expect(state.primary.cells[0]?.[3]).toMatchObject({ text: 'X', fg: 2 });
      expect(state.primary.cells[0]?.[0]).toMatchObject({ text: 'R', fg: 1 });
    });

    it('restores cursor and SGR via CSI s and CSI u', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      ingestVt(store, 1, '\u001b[1;1H\u001b[33mS\u001b[s');
      ingestVt(store, 2, '\u001b[1;6H\u001b[34mT\u001b[u');

      const state = store.exportState();
      expect(state.primary.cursor).toMatchObject({ x: 1, y: 0 });
      expect(state.primary.cells[0]?.[5]).toMatchObject({ text: 'T', fg: 4 });
      expect(state.primary.cells[0]?.[0]).toMatchObject({ text: 'S', fg: 3 });
    });

    it('loads saved cursor and saved rendition from a keyframe buffer', () => {
      const store = new TerminalSurfaceStore();
      store.ingest({
        type: 'terminal.keyframe',
        sequence: 1,
        columns: 4,
        rows: 2,
        primary: {
          cells: [
            [{ text: ' ', width: 1 }, { text: ' ', width: 1 }, { text: ' ', width: 1 }, { text: ' ', width: 1 }],
            [{ text: ' ', width: 1 }, { text: ' ', width: 1 }, { text: ' ', width: 1 }, { text: ' ', width: 1 }],
          ],
          cursor: { x: 2, y: 1, visible: true, shape: 'bar' },
          saved_cursor: { x: 0, y: 0, visible: false, shape: 'underline' },
          rendition: { fg: 1 },
          saved_rendition: { fg: 2, bold: true },
        },
      });

      const buf = store.exportState().primary;
      expect(buf.cursor).toEqual({ x: 2, y: 1, visible: true, shape: 'bar' });
      expect(buf.savedCursor).toEqual({ x: 0, y: 0, visible: false, shape: 'underline' });
      expect(buf.rendition).toMatchObject({ fg: 1 });
      expect(buf.savedRendition).toMatchObject({ fg: 2, bold: true });
    });

    it('restores primary cursor and attrs after DECSET 1049 then DECRESET 1049', () => {
      const store = new TerminalSurfaceStore();
      store.resize(5, 2);
      ingestVt(store, 1, '\u001b[1;3H\u001b[35mM');
      ingestVt(store, 2, '\u001b[?1049h\u001b[36mA');
      ingestVt(store, 3, '\u001b[?1049l');

      const state = store.exportState();
      expect(state.activeBuffer).toBe('primary');
      expect(state.primary.cursor).toMatchObject({ x: 3, y: 0 });
      expect(state.primary.cells[0]?.[2]).toMatchObject({ text: 'M', fg: 5 });
    });
  });

  describe('SGR attributes and mode set and reset', () => {
    it('applies foreground, background, and style SGR attributes to written cells', () => {
      const store = new TerminalSurfaceStore();
      store.resize(8, 1);
      ingestVt(store, 1, '\u001b[1;3;4;31;43mB\u001b[0mN');

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({
        text: 'B',
        fg: 1,
        bg: 3,
        bold: true,
        underline: true,
      });
      expect(row[1]).toMatchObject({ text: 'N', bold: false, underline: false });
    });

    it('resets SGR attributes with CSI 0 m', () => {
      const store = new TerminalSurfaceStore();
      store.resize(4, 1);
      ingestVt(store, 1, '\u001b[1;31mR\u001b[0mX');

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({ text: 'R', fg: 1, bold: true });
      expect(row[1]).toMatchObject({ text: 'X', bold: false, fg: undefined });
    });

    it('sets and resets terminal modes including insert, origin, and cursor visibility', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 3);
      ingestVt(store, 1, '\u001b[?25l');
      ingestVt(store, 2, '\u001b[4h\u001b[?6h');
      expect(store.exportState().modes).toMatchObject({
        insert: true,
        origin: true,
        cursorVisible: false,
      });

      ingestVt(store, 3, '\u001b[4l\u001b[?6l\u001b[?25h');
      expect(store.exportState().modes).toMatchObject({
        insert: false,
        origin: false,
        cursorVisible: true,
      });
    });

    it('sets application cursor, keypad, and bracketed paste private modes', () => {
      const store = new TerminalSurfaceStore();
      store.resize(4, 2);
      ingestVt(store, 1, '\u001b[?1h\u001b[?66h\u001b[?2004h');
      expect(store.exportState().modes).toMatchObject({
        applicationCursor: true,
        applicationKeypad: true,
        bracketedPaste: true,
      });

      ingestVt(store, 2, '\u001b[?1l\u001b[?66l\u001b[?2004l');
      expect(store.exportState().modes).toMatchObject({
        applicationCursor: false,
        applicationKeypad: false,
        bracketedPaste: false,
      });
    });
  });

  describe('scrolling regions and origin mode', () => {
    it('limits scroll operations to the configured region via DECSTBM', () => {
      const store = new TerminalSurfaceStore();
      store.resize(4, 4);
      ingestVt(store, 1, 'A\nB\nC\nD');
      ingestVt(store, 2, '\u001b[2;3r\u001b[2;1H\u001b[S');

      const state = store.exportState().primary;
      expect(state.scrollTop).toBe(1);
      expect(state.scrollBottom).toBe(2);
      expect(rowText(state.cells[0] ?? [])).toBe('A   ');
      expect(rowText(state.cells[1] ?? [])).toBe('  C ');
      expect(rowText(state.cells[2] ?? [])).toBe('    ');
      expect(rowText(state.cells[3] ?? [])).toBe('   D');
    });

    it('positions the cursor relative to the scroll region when origin mode is enabled', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 4);
      ingestVt(store, 1, '\u001b[2;4r\u001b[?6h\u001b[1;1H');
      expect(store.exportState().primary.cursor).toMatchObject({ x: 0, y: 1 });
    });

    it('loads scroll region bounds from a keyframe buffer', () => {
      const store = new TerminalSurfaceStore();
      store.ingest({
        type: 'terminal.keyframe',
        sequence: 1,
        columns: 4,
        rows: 4,
        primary: {
          cells: Array.from({ length: 4 }, () =>
            Array.from({ length: 4 }, () => ({ text: ' ', width: 1 })),
          ),
          scroll_top: 1,
          scroll_bottom: 2,
        },
      });

      const buf = store.exportState().primary;
      expect(buf.scrollTop).toBe(1);
      expect(buf.scrollBottom).toBe(2);
    });
  });

  describe('wide-character and continuation-cell repair', () => {
    it('places wide characters with a continuation cell', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      ingestVt(store, 1, '\u001b[1;1H界');

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({ text: '界', width: 2 });
      expect(row[1]).toMatchObject({ text: '', width: 0, continuation: true });
    });

    it('appends combining marks to the prior cell without advancing the cursor', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      ingestVt(store, 1, 'e\u0301');

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({ text: 'e\u0301', width: 1 });
      expect(store.exportState().primary.cursor.x).toBe(1);
    });

    it('clears the full line and repairs continuation cells when EL 0 runs from column zero', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, '界X');
      ingestVt(store, 2, '\u001b[1;1H\u001b[K');

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({ text: ' ', width: 1, continuation: false });
      expect(row[1]).toMatchObject({ text: ' ', width: 1, continuation: false });
      expect(rowText(row)).toBe('      ');
    });

    it('repairs a wide cell truncated at the right edge during keyframe load', () => {
      const store = new TerminalSurfaceStore();
      store.ingest({
        type: 'terminal.keyframe',
        sequence: 1,
        columns: 2,
        rows: 1,
        primary: {
          cells: [[{ text: '界', width: 2 }, { text: '', width: 0, continuation: true }]],
        },
      });

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({ text: '界', width: 2 });
      expect(row[1]).toMatchObject({ text: '', width: 0, continuation: true });
    });
  });

  describe('insert, delete, and erase families', () => {
    it('inserts blank characters at the cursor with ICH', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, 'ABCDE');
      ingestVt(store, 2, '\u001b[1;3H\u001b[2@');

      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('AB  CD');
    });

    it('deletes characters at the cursor with DCH', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, 'ABCDE');
      ingestVt(store, 2, '\u001b[1;2H\u001b[2P');

      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('ADE   ');
    });

    it('erases characters at the cursor with ECH', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, 'ABCDE');
      ingestVt(store, 2, '\u001b[1;2H\u001b[2X');

      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('A  DE ');
    });

    it('inserts and deletes lines within the scroll region', () => {
      const store = new TerminalSurfaceStore();
      store.resize(4, 4);
      ingestVt(store, 1, 'A\nB\nC\nD');
      ingestVt(store, 2, '\u001b[2;3r\u001b[2;1H\u001b[1L');
      expect(rowText(store.exportState().primary.cells[1] ?? [])).toBe('    ');
      expect(rowText(store.exportState().primary.cells[2] ?? [])).toBe(' B  ');

      ingestVt(store, 3, '\u001b[2;1H\u001b[1M');
      expect(rowText(store.exportState().primary.cells[1] ?? [])).toBe(' B  ');
    });

    it('erases from cursor to end of display with ED 0', () => {
      const store = new TerminalSurfaceStore();
      store.resize(4, 3);
      ingestVt(store, 1, 'A\nB\nC');
      ingestVt(store, 2, '\u001b[2;1H\u001b[0J');

      const cells = store.exportState().primary.cells;
      expect(rowText(cells[0] ?? [])).toBe('A   ');
      expect(rowText(cells[1] ?? [])).toBe('    ');
      expect(rowText(cells[2] ?? [])).toBe('    ');
    });

    it('erases from cursor to start of line with EL 1', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, 'ABCDE');
      ingestVt(store, 2, '\u001b[1;4H\u001b[1K');

      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('    E ');
    });

    it('shifts in insert mode so new characters push existing cells right', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, '\u001b[4hABCD');
      ingestVt(store, 2, '\u001b[1;2HXX');

      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('AXXBCD');
    });
  });

  describe('synchronized-update deferral and release', () => {
    it('defers snapshot publication while DEC mode 2026 is active', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      const versionBefore = store.getSnapshot().version;
      const versions: number[] = [];
      store.subscribe(() => versions.push(store.getSnapshot().version));

      ingestVt(store, 1, '\u001b[?2026hA');
      const duringSync = store.getSnapshot();
      expect(duringSync.version).toBe(versionBefore);
      expect(duringSync.modes.synchronizedUpdates).toBe(false);
      // Published snapshot stays frozen while DEC 2026 defers; live buffer already has 'A'.
      expect(rowText(duringSync.cells[0] ?? [])).toBe('      ');
      expect(store.exportState().modes.synchronizedUpdates).toBe(true);
      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('A     ');

      ingestVt(store, 2, 'B\u001b[?2026l');
      const afterRelease = store.getSnapshot();
      expect(afterRelease.modes.synchronizedUpdates).toBe(false);
      expect(rowText(afterRelease.cells[0] ?? [])).toBe('AB    ');
      expect(afterRelease.version).toBeGreaterThan(versionBefore);
      expect(versions.length).toBe(2);
    });

    it('coalesces multiple updates into one publication after synchronized bracket ends', () => {
      const store = new TerminalSurfaceStore();
      store.resize(4, 1);
      let notifyCount = 0;
      store.subscribe(() => {
        notifyCount += 1;
      });

      ingestVt(store, 1, '\u001b[?2026h');
      const versionAtStart = store.getSnapshot().version;
      ingestVt(store, 2, '1');
      ingestVt(store, 3, '2');
      ingestVt(store, 4, '3\u001b[?2026l');

      expect(store.getSnapshot().version).toBeGreaterThan(versionAtStart);
      expect(rowText(store.getSnapshot().cells[0] ?? [])).toBe('123 ');
      expect(notifyCount).toBe(2);
    });
  });

  describe('legacy reset frames through applyFrame', () => {
    it('replaces the grid from a reset frame and hides the cursor', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      ingestVt(store, 1, 'OLD');
      store.ingest({
        type: 'terminal.frame',
        sequence: 2,
        columns: 6,
        rows: 2,
        data: 'NEW\nLINE',
        reset: true,
      });

      const state = store.exportState();
      expect(rowText(state.primary.cells[0] ?? [])).toBe('NEW   ');
      expect(rowText(state.primary.cells[1] ?? [])).toBe('LINE  ');
      expect(state.primary.cursor.visible).toBe(false);
      expect(state.modes.cursorVisible).toBe(false);
    });

    it('preserves SGR sequences in reset frames via the terminalSafeText stripAnsi:false path', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      store.ingest({
        type: 'terminal.frame',
        sequence: 1,
        columns: 6,
        rows: 1,
        data: '\u001b[31mR\u001b[0mG',
        reset: true,
      });

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({ text: 'R', fg: 1 });
      expect(row[1]).toMatchObject({ text: 'G', fg: undefined });
    });

    it('applies incremental frame data without reset when reset is false', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, 'AB');
      store.ingest({
        type: 'terminal.frame',
        sequence: 2,
        data: 'CD',
        reset: false,
      });

      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('ABCD  ');
    });
  });

  describe('decoder reset after keyframe across split multi-byte sequence', () => {
    it('discards partial UTF-8 decoder state when a keyframe is adopted', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      const full = new TextEncoder().encode('é');
      const partial = full.subarray(0, 1);

      store.ingest(adaptTerminalUpdate(rawChunk(1, partial)));
      store.ingest(
        legacyFlatKeyframe(2, 2, 6, ['SYNC  ', '      ']),
      );
      store.ingest(adaptTerminalUpdate(rawChunk(3, full.subarray(1))));

      const state = store.exportState();
      expect(rowText(state.primary.cells[0] ?? [])).toBe('\uFFFDYNC  ');
      expect(rowText(state.primary.cells[1] ?? [])).not.toContain('é');
    });

    it('decodes a complete multi-byte character when bytes arrive after keyframe reset', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      const bytes = new TextEncoder().encode('界');

      store.ingest(adaptTerminalUpdate(rawChunk(1, bytes.subarray(0, 1))));
      store.ingest({
        type: 'terminal.keyframe',
        sequence: 2,
        columns: 6,
        rows: 1,
        primary: {
          cells: [Array.from({ length: 6 }, () => ({ text: ' ', width: 1 }))],
        },
      });
      store.ingest(adaptTerminalUpdate(rawChunk(3, bytes)));

      const row = store.exportState().primary.cells[0] ?? [];
      expect(row[0]).toMatchObject({ text: '界', width: 2 });
      expect(row[1]).toMatchObject({ text: '', width: 0, continuation: true });
    });
  });

  describe('snapshot stability (copy-on-write rows)', () => {
    it('keeps an old snapshot unchanged after further cell updates', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 2);
      ingestVt(store, 1, 'HELLO');
      const old = store.getSnapshot();
      const oldRow0 = old.cells[0];
      const oldText = rowText(old.cells[0] ?? []);
      const oldVersions = [...old.rowVersions];
      const oldDirty = old.dirtyRows;

      ingestVt(store, 2, '\rWORLD!');
      const next = store.getSnapshot();

      expect(rowText(old.cells[0] ?? [])).toBe(oldText);
      expect(old.cells[0]).toBe(oldRow0);
      expect(old.rowVersions).toEqual(oldVersions);
      expect(old.dirtyRows).toEqual(oldDirty);
      expect(rowText(next.cells[0] ?? [])).toBe('WORLD!');
      expect(next.cells[0]).not.toBe(old.cells[0]);
      expect(next.rowVersions).toBeDefined();
      expect(next.dirtyRows === null || Array.isArray(next.dirtyRows)).toBe(true);
    });

    it('keeps an old snapshot unchanged inside a synchronized-update bracket', () => {
      const store = new TerminalSurfaceStore();
      store.resize(6, 1);
      ingestVt(store, 1, 'BASE');
      const old = store.getSnapshot();
      const oldText = rowText(old.cells[0] ?? []);

      ingestVt(store, 2, '\u001b[?2026h\rXXXX');
      expect(rowText(old.cells[0] ?? [])).toBe(oldText);
      expect(store.getSnapshot()).toBe(old);
      expect(rowText(store.exportState().primary.cells[0] ?? [])).toBe('XXXX  ');

      ingestVt(store, 3, '\u001b[?2026l');
      expect(rowText(old.cells[0] ?? [])).toBe(oldText);
      expect(rowText(store.getSnapshot().cells[0] ?? [])).toBe('XXXX  ');
    });
  });
});
