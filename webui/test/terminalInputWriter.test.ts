/**
 * TerminalInputWriter: batches keystrokes and stops on stream failure.
 */

import { describe, expect, it, vi } from 'vitest';
import { TerminalInputWriter } from '../src/components/stage/terminalInputWriter.js';

function decodeBase64(data: string): string {
  return new TextDecoder().decode(Uint8Array.from(atob(data), (c) => c.charCodeAt(0)));
}

describe('TerminalInputWriter', () => {
  it('batches one event-loop burst with monotonic sequence numbers', async () => {
    const sent: Array<{ inputSequence: number; data: string }> = [];
    const writer = new TerminalInputWriter(
      { sendTerminalInput: (batch) => (sent.push(batch), true) },
      'stream',
      'session',
      'lease',
      3,
    );
    writer.enqueue(new TextEncoder().encode('a'));
    writer.enqueue(new TextEncoder().encode('é'));
    writer.enqueue(new TextEncoder().encode('\x1b[A'));
    await Promise.resolve();
    expect(sent).toHaveLength(1);
    expect(sent[0]?.inputSequence).toBe(1);
    expect(decodeBase64(sent[0]?.data ?? '')).toBe('aé\x1b[A');
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
    writer.enqueue(new TextEncoder().encode('before'));
    await Promise.resolve();
    failure?.(new Error('stale fence'));
    writer.enqueue(new TextEncoder().encode('after'));
    await Promise.resolve();
    expect(sent.map(decodeBase64)).toEqual(['before']);
    expect(unavailable).toHaveBeenCalledWith('rejected');
  });
});
