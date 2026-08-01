/**
 * Bounded serial batcher for raw terminal stdin over the application protocol.
 * Browser port of inktui `TerminalInputWriter` — Uint8Array instead of Node Buffer.
 */

import type { TerminalInputBatch } from '@murder/ui-core/application/ApplicationClient.js';

export interface TerminalInputEnvelope extends TerminalInputBatch {}

export interface TerminalInputTransport {
  sendTerminalInput(envelope: TerminalInputEnvelope): boolean;
  watchTerminalInput?(streamId: string, listener: (error: Error) => void): () => void;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]!);
  }
  return btoa(binary);
}

function concatBytes(chunks: readonly Uint8Array[]): Uint8Array {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

/**
 * A bounded, serial event-loop batcher. `sendTerminalInput` returns before a server ack;
 * a lost lease/disconnect closes the writer and discards unsent bytes rather than replaying
 * under a stale fence.
 */
export class TerminalInputWriter {
  private pending: Uint8Array[] = [];
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

  enqueue(bytes: Uint8Array): void {
    if (this.closed || bytes.length === 0) return;
    if (this.pendingBytes + bytes.length > TerminalInputWriter.MAX_PENDING_BYTES) {
      this.close('overflow');
      return;
    }
    this.pending.push(bytes.slice());
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
        data: bytesToBase64(batch),
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

  private takeBatch(maxBytes: number): Uint8Array {
    const chunks: Uint8Array[] = [];
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
    return concatBytes(chunks);
  }
}
