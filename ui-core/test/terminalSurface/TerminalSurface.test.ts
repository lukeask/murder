import { describe, expect, it } from 'vitest';
import type { TerminalUpdate } from '@murder/ui-core/application/ApplicationClient.js';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import type {
  TerminalCell,
  TerminalKeyframe,
  TerminalRendition,
} from '@murder/ui-core/generated/applicationProtocol.js';
import { adaptTerminalUpdate } from '@murder/ui-core/terminalSurface/protocolAdapter.js';
import { TerminalSurfaceStore } from '@murder/ui-core/terminalSurface/TerminalSurfaceStore.js';

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

function keyframe(sequence: number, firstRow: string): TerminalKeyframe {
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

describe('TerminalSurfaceStore native VT stream', () => {
  it('retains VT parser state across adversarial chunks and preserves both screen buffers', () => {
    const store = new TerminalSurfaceStore();
    store.resize(10, 4);
    const bytes = new TextEncoder().encode(
      '\u001b[31mR\u001b[0m\u001b[2;3H界e\u0301' +
        '\u001b[?1049h\u001b[32mALT\u001b[0m\u001b[3;5H界\u001b[?1049l',
    );

    let sequence = 0;
    for (const byte of bytes) {
      store.ingest(adaptTerminalUpdate(rawChunk(++sequence, Uint8Array.of(byte))));
    }

    const state = store.exportState();
    expect(state.activeBuffer).toBe('primary');
    expect(state.primary.cells[0]?.[0]).toMatchObject({ text: 'R', width: 1, fg: 1 });
    expect(state.primary.cells[1]?.[2]).toMatchObject({ text: '界', width: 2 });
    expect(state.primary.cells[1]?.[3]).toMatchObject({ text: '', width: 0, continuation: true });
    expect(state.primary.cells[1]?.[4]).toMatchObject({ text: 'e\u0301', width: 1 });
    expect(state.primary.cursor).toMatchObject({ x: 5, y: 1, visible: true });

    expect(rowText(state.alternate.cells[0] ?? [])).toBe('ALT       ');
    expect(state.alternate.cells[2]?.[4]).toMatchObject({ text: '界', width: 2 });
    expect(state.alternate.cells[2]?.[5]).toMatchObject({
      text: '',
      width: 0,
      continuation: true,
    });
    expect(state.alternate.cursor).toMatchObject({ x: 6, y: 2 });
  });

  it('suspends deltas at a subscription gap and exactly adopts the resync keyframe', () => {
    const sessionId = '0198b156-2dd3-70a9-bc79-fca001dc8801';
    const client = new FakeApplicationClient();
    const store = new TerminalSurfaceStore();
    const detach = client.attachTerminal(sessionId, (update) => {
      store.ingest(adaptTerminalUpdate(update));
    });

    client.emitTerminalUpdate(sessionId, keyframe(1, 'A'));
    client.emitTerminalUpdate(sessionId, rawChunk(2, new TextEncoder().encode('B')));
    expect(rowText(store.exportState().primary.cells[1] ?? [])).toBe('B   ');

    client.emitTerminalUpdate(sessionId, rawChunk(4, new TextEncoder().encode('X')));
    client.emitTerminalUpdate(sessionId, rawChunk(5, new TextEncoder().encode('Y')));
    expect(store.exportState().keyframeRequired).toBe(true);
    expect(rowText(store.exportState().primary.cells[1] ?? [])).toBe('B   ');

    const replacement = keyframe(6, 'SYNC');
    client.emitTerminalUpdate(sessionId, { type: 'terminal.resynced', keyframe: replacement });
    const resynced = store.exportState();
    expect(resynced.keyframeRequired).toBe(false);
    expect(resynced.activeBuffer).toBe('primary');
    expect(rowText(resynced.primary.cells[0] ?? [])).toBe('SYNC');
    expect(rowText(resynced.primary.cells[1] ?? [])).toBe('    ');
    expect(resynced.primary.cursor).toEqual({ x: 0, y: 1, visible: true, shape: 'block' });
    detach();
  });
});
