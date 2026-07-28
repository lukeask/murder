/** Raw embedded-terminal stdin helpers. This module intentionally deals in Buffers, never Ink keys. */

import type { ReservedChordResult } from './StdinShim.js';

export type PaneNavigationDirection = 'left' | 'down' | 'up' | 'right';

const CONTROL_NAVIGATION: Readonly<Record<number, PaneNavigationDirection>> = {
  0x08: 'left',
  0x0a: 'down',
  0x0b: 'up',
  0x0c: 'right',
};
const ALT_NAVIGATION: Readonly<Record<number, PaneNavigationDirection>> = {
  0x68: 'left',
  0x6a: 'down',
  0x6b: 'up',
  0x6c: 'right',
};

export interface ReservedPaneNavigationMatch {
  readonly result: ReservedChordResult;
  readonly direction?: PaneNavigationDirection;
}

/**
 * Match only the pane-navigation bytes Murder reserves while raw terminal mode is active. `pending`
 * is returned solely for a prefix that could still become a reserved sequence, so ordinary Esc,
 * arrows, paste delimiters, and UTF-8 bytes retain their exact representation and order.
 *
 * The raw route runs with kitty/CSI-u reporting disabled.  CSI-u is nevertheless understood here so
 * a route transition cannot leak a reserved chord if a terminal had already queued enhanced bytes.
 */
export function matchReservedPaneNavigation(buffer: Buffer): ReservedPaneNavigationMatch {
  const first = buffer[0];
  if (first === undefined) return { result: { kind: 'pending' } };
  const control = CONTROL_NAVIGATION[first];
  if (control !== undefined) return { result: { kind: 'matched', bytes: 1 }, direction: control };
  if (first !== 0x1b) return { result: { kind: 'passthrough', bytes: 1 } };
  if (buffer.length === 1) return { result: { kind: 'pending' } };

  const second = buffer[1];
  const alt = second === undefined ? undefined : ALT_NAVIGATION[second];
  if (alt !== undefined) return { result: { kind: 'matched', bytes: 2 }, direction: alt };
  if (second !== 0x5b) return { result: { kind: 'passthrough', bytes: 1 } };

  // A CSI key sequence can be split at any boundary.  Hold it only while it still has the exact
  // numeric prefix of a reserved kitty CSI-u chord.  A nonmatching CSI is released immediately.
  let final = -1;
  for (let index = 2; index < buffer.length; index += 1) {
    const byte = buffer[index]!;
    if (byte >= 0x40 && byte <= 0x7e) {
      final = index;
      break;
    }
  }
  if (final < 0) {
    const prefix = buffer.subarray(0).toString('ascii');
    return isReservedCsiPrefix(prefix)
      ? { result: { kind: 'pending' } }
      : { result: { kind: 'passthrough', bytes: 1 } };
  }
  const text = buffer.subarray(0, final + 1).toString('ascii');
  const match = /^\x1b\[(104|106|107|108);([35])(?::1|:2)?u$/.exec(text);
  if (match !== null) {
    const code = Number.parseInt(match[1]!, 10);
    const modifier = Number.parseInt(match[2]!, 10);
    const direction = code === 104 ? 'left' : code === 106 ? 'down' : code === 107 ? 'up' : 'right';
    // Kitty modifiers are one-based: Alt is 3 and Ctrl is 5. (The exact-only check rejects
    // Ctrl+Alt and Shift combinations, which are not reserved by Murder.)
    if (modifier === 3 || modifier === 5) {
      return { result: { kind: 'matched', bytes: final + 1 }, direction };
    }
  }
  return { result: { kind: 'passthrough', bytes: final + 1 } };
}

function isReservedCsiPrefix(prefix: string): boolean {
  return [
    '\x1b[104;3u',
    '\x1b[104;5u',
    '\x1b[106;3u',
    '\x1b[106;5u',
    '\x1b[107;3u',
    '\x1b[107;5u',
    '\x1b[108;3u',
    '\x1b[108;5u',
  ].some((candidate) => candidate.startsWith(prefix));
}

export interface TerminalInputEnvelope {
  readonly streamId: string;
  readonly sessionId: string;
  readonly leaseId: string;
  readonly fence: number;
  readonly inputSequence: number;
  readonly data: string;
}

export interface TerminalInputTransport {
  sendTerminalInput(envelope: TerminalInputEnvelope): boolean;
  watchTerminalInput?(streamId: string, listener: (error: Error) => void): () => void;
}

/**
 * A bounded, serial event-loop batcher. `sendTerminalInput` sends directly to the already-open
 * WebSocket and returns before a server acknowledgement; therefore batches cannot be reordered by
 * competing async request/reply paths.  A lost lease/disconnect closes the writer and discards its
 * unsent bytes rather than replaying them under a stale fence.
 */
export class TerminalInputWriter {
  private pending: Buffer[] = [];
  private pendingBytes = 0;
  private nextSequence = 1;
  private flushScheduled = false;
  private closed = false;
  private readonly unwatch: (() => void) | undefined;

  static readonly MAX_PENDING_BYTES = 256 * 1024;
  static readonly MAX_BATCH_BYTES = 32 * 1024;

  constructor(
    private readonly transport: TerminalInputTransport,
    private readonly streamId: string,
    private readonly sessionId: string,
    private readonly leaseId: string,
    private readonly fence: number,
    private readonly onUnavailable?: (reason: 'overflow' | 'disconnected' | 'rejected') => void,
  ) {
    this.unwatch = transport.watchTerminalInput?.(streamId, () => this.close('rejected'));
  }

  enqueue(bytes: Buffer): void {
    if (this.closed || bytes.length === 0) return;
    // Retain the newest bounded prefix only when the transport is stalled; normal paste is split
    // into batches below, never rejected for being larger than a typing burst.
    if (this.pendingBytes + bytes.length > TerminalInputWriter.MAX_PENDING_BYTES) {
      this.close('overflow');
      return;
    }
    this.pending.push(Buffer.from(bytes));
    this.pendingBytes += bytes.length;
    if (this.pendingBytes >= TerminalInputWriter.MAX_BATCH_BYTES) {
      this.flush();
    } else if (!this.flushScheduled) {
      this.flushScheduled = true;
      queueMicrotask(() => this.flush());
    }
  }

  flush(): void {
    this.flushScheduled = false;
    if (this.closed) return;
    while (this.pendingBytes > 0) {
      const batch = this.takeBatch(TerminalInputWriter.MAX_BATCH_BYTES);
      const accepted = this.transport.sendTerminalInput({
        streamId: this.streamId,
        sessionId: this.sessionId,
        leaseId: this.leaseId,
        fence: this.fence,
        inputSequence: this.nextSequence,
        data: batch.toString('base64'),
      });
      if (!accepted) {
        this.close('disconnected');
        return;
      }
      this.nextSequence += 1;
    }
  }

  close(reason?: 'overflow' | 'disconnected' | 'rejected'): void {
    const wasOpen = !this.closed;
    this.closed = true;
    this.pending = [];
    this.pendingBytes = 0;
    this.flushScheduled = false;
    this.unwatch?.();
    if (wasOpen && reason !== undefined) this.onUnavailable?.(reason);
  }

  private takeBatch(maxBytes: number): Buffer {
    const chunks: Buffer[] = [];
    let remaining = maxBytes;
    while (remaining > 0 && this.pending.length > 0) {
      const chunk = this.pending[0]!;
      if (chunk.length <= remaining) {
        chunks.push(chunk);
        this.pending.shift();
        this.pendingBytes -= chunk.length;
        remaining -= chunk.length;
      } else {
        chunks.push(chunk.subarray(0, remaining));
        this.pending[0] = chunk.subarray(remaining);
        this.pendingBytes -= remaining;
        remaining = 0;
      }
    }
    return Buffer.concat(chunks);
  }
}
