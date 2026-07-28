import { describe, expect, it, vi } from 'vitest';
import { StdinShim, type RealStdin } from '../../src/terminal/StdinShim.js';
import {
  matchReservedPaneNavigation,
  TerminalInputWriter,
} from '../../src/terminal/rawEditorInput.js';
import { EventEmitter } from 'node:events';

class FakeStdin extends EventEmitter implements RealStdin {
  isTTY = true;
  push(bytes: Buffer): void {
    this.emit('data', bytes);
  }
}

describe('raw editor stdin routing', () => {
  it('forwards ordinary, UTF-8, escape, bracketed paste, and nonreserved controls unchanged', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    const received: Buffer[] = [];
    shim.setRoute({
      kind: 'terminal',
      consumeReservedChord: (buffer) => matchReservedPaneNavigation(buffer).result,
      write: (bytes) => received.push(Buffer.from(bytes)),
    });
    const bytes = Buffer.from(
      'aé\x1b\x17\x1bq\x1b[200~paste🙂\x1b[201~\x1b[A\x1b[15~',
      'utf8',
    );
    real.push(bytes);
    expect(Buffer.concat(received)).toEqual(bytes);
  });

  it('recognizes a reserved Alt navigation chord split across chunks and never writes it', () => {
    const real = new FakeStdin();
    const shim = new StdinShim(real);
    const received: Buffer[] = [];
    const directions: string[] = [];
    shim.setRoute({
      kind: 'terminal',
      consumeReservedChord(buffer) {
        const match = matchReservedPaneNavigation(buffer);
        if (match.direction !== undefined && match.result.kind === 'matched') {
          directions.push(match.direction);
        }
        return match.result;
      },
      write: (bytes) => received.push(Buffer.from(bytes)),
    });
    real.push(Buffer.from('\x1b', 'latin1'));
    real.push(Buffer.from('h', 'latin1'));
    expect(directions).toEqual(['left']);
    expect(Buffer.concat(received)).toEqual(Buffer.alloc(0));
  });

  it('consumes Ctrl-H/J/K/L but preserves other Ctrl bytes for the editor', () => {
    const reserved = [0x08, 0x0a, 0x0b, 0x0c].map((byte) =>
      matchReservedPaneNavigation(Buffer.from([byte])),
    );
    expect(reserved).toEqual([
      { result: { kind: 'matched', bytes: 1 }, direction: 'left' },
      { result: { kind: 'matched', bytes: 1 }, direction: 'down' },
      { result: { kind: 'matched', bytes: 1 }, direction: 'up' },
      { result: { kind: 'matched', bytes: 1 }, direction: 'right' },
    ]);
    expect(
      ['h', 'j', 'k', 'l'].map((letter) =>
        matchReservedPaneNavigation(Buffer.from(`\x1b${letter}`, 'latin1')),
      ),
    ).toEqual([
      { result: { kind: 'matched', bytes: 2 }, direction: 'left' },
      { result: { kind: 'matched', bytes: 2 }, direction: 'down' },
      { result: { kind: 'matched', bytes: 2 }, direction: 'up' },
      { result: { kind: 'matched', bytes: 2 }, direction: 'right' },
    ]);
    expect(matchReservedPaneNavigation(Buffer.from([0x17])).result).toEqual({
      kind: 'passthrough',
      bytes: 1,
    });
  });

  it('flushes a lone Escape raw to the editor after the ambiguity timeout', () => {
    vi.useFakeTimers();
    try {
      const real = new FakeStdin();
      const shim = new StdinShim(real);
      const received: Buffer[] = [];
      shim.setRoute({
        kind: 'terminal',
        consumeReservedChord: (buffer) => matchReservedPaneNavigation(buffer).result,
        write: (bytes) => received.push(Buffer.from(bytes)),
      });
      real.push(Buffer.from('\x1b', 'latin1'));
      expect(received).toEqual([]);
      vi.advanceTimersByTime(50);
      expect(Buffer.concat(received)).toEqual(Buffer.from('\x1b', 'latin1'));
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('TerminalInputWriter', () => {
  it('batches one event-loop burst in order with monotonic sequence numbers', async () => {
    const sent: Array<{ inputSequence: number; data: string }> = [];
    const writer = new TerminalInputWriter(
      { sendTerminalInput: (batch) => (sent.push(batch), true) },
      'stream',
      'session',
      'lease',
      3,
    );
    writer.enqueue(Buffer.from('a'));
    writer.enqueue(Buffer.from('é'));
    writer.enqueue(Buffer.from('\x1b[200~paste\x1b[201~', 'utf8'));
    await Promise.resolve();
    expect(sent).toHaveLength(1);
    expect(sent[0]?.inputSequence).toBe(1);
    expect(Buffer.from(sent[0]?.data ?? '', 'base64').toString('utf8')).toBe('aé\x1b[200~paste\x1b[201~');
  });

  it('stops sending after a stream-scoped server failure', async () => {
    let failure: ((error: Error) => void) | undefined;
    const sent: string[] = [];
    const unavailable = vi.fn();
    const writer = new TerminalInputWriter(
      {
        sendTerminalInput: (batch) => (sent.push(batch.data), true),
        watchTerminalInput: (_streamId, listener) => {
          failure = listener;
          return () => {
            failure = undefined;
          };
        },
      },
      'stream',
      'session',
      'lease',
      3,
      unavailable,
    );
    writer.enqueue(Buffer.from('before'));
    await Promise.resolve();
    failure?.(new Error('stale fence'));
    writer.enqueue(Buffer.from('after'));
    await Promise.resolve();
    expect(sent.map((data) => Buffer.from(data, 'base64').toString('utf8'))).toEqual(['before']);
    expect(unavailable).toHaveBeenCalledWith('rejected');
  });
});
