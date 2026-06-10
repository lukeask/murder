/**
 * `StdinShim` — a drop-in replacement for `process.stdin` that sits between the real TTY and Ink, so
 * we can parse the kitty CSI-u stream ourselves before Ink's own input parser sees it.
 *
 * ## What it is
 *
 * A `Readable` (an `EventEmitter` stream) that Ink's `render(…, { stdin: shim })` consumes exactly as
 * it would the real stdin. It forwards the TTY control surface Ink needs (`isTTY`, `setRawMode`,
 * `ref`/`unref`, `resume`/`pause`/`setEncoding`) straight to the wrapped real stream, and re-emits
 * the real stream's `data` — but *transformed*:
 *
 *  - **Bypass mode (default).** Pure passthrough: every byte the real stdin emits is forwarded
 *    unchanged. This is the state until the kitty protocol is actually enabled, so with the modifier
 *    defaulting to alt the shim is behavior-neutral — Ink sees the identical byte stream it always
 *    did.
 *  - **Active mode.** Bytes flow through the {@link ./csiU.js CsiUParser}: recognised CSI-u keypresses
 *    are run through {@link ./translate.js translate} — legacy-representable ones are re-emitted as
 *    the synthesised legacy bytes (so Ink decodes them as today), and the unrepresentable command
 *    combos (ctrl+digit/space/i/m/h) are emitted as `chord` events instead of bytes. Passthrough runs
 *    (text, paste, mouse, unknown escapes) are forwarded verbatim. The lone-ESC flush timer fires the
 *    parser's pending ESC as a literal Escape after {@link LONE_ESC_FLUSH_MS} so modal dismissal is
 *    snappy.
 *
 * ## Detection routing
 *
 * The shim also implements {@link TokenSource}: during protocol detection the driver subscribes here
 * and the shim feeds it the query/DA reply tokens (and swallows them from the downstream byte stream
 * — Ink must never see a protocol reply). Detection works in either mode because the parser always
 * runs for *recognition*; what differs by mode is only whether key tokens are translated/forwarded or
 * passed straight through.
 *
 * ## No Ink import
 *
 * Plain Node streams + the pure parser/translator. The `chord` event is the only outward coupling and
 * it carries a plain {@link Chord} record; the dispatcher wiring lives elsewhere.
 */

import { Readable } from 'node:stream';
import type { CsiToken } from './csiU.js';
import { CsiUParser, LONE_ESC_FLUSH_MS } from './csiU.js';
import type { TokenSource } from './kittyDriver.js';
import { type Chord, translate } from './translate.js';

/** The minimal real-stdin surface the shim forwards to. `process.stdin` (a `ReadStream`) satisfies
 * this structurally; a test passes a fake `EventEmitter` with these members. */
export interface RealStdin {
  isTTY?: boolean;
  on(event: 'data', listener: (chunk: Buffer | string) => void): unknown;
  off?(event: 'data', listener: (chunk: Buffer | string) => void): unknown;
  removeListener?(event: 'data', listener: (chunk: Buffer | string) => void): unknown;
  setRawMode?(mode: boolean): unknown;
  setEncoding?(encoding: BufferEncoding): unknown;
  resume?(): unknown;
  pause?(): unknown;
  ref?(): unknown;
  unref?(): unknown;
}

/** Events the shim emits beyond the standard stream `data`/`end`. */
export interface StdinShimEvents {
  chord: (chord: Chord) => void;
}

/**
 * The shim. Construct it around the real stdin, hand it to `render(…, { stdin })`, then drive its
 * mode from the protocol lifecycle: stays in `bypass` until {@link setBypass}(false) (after the
 * driver enables the protocol), back to bypass when the protocol is disabled.
 */
export class StdinShim extends Readable implements TokenSource {
  /** `isTTY` mirrors the real stream so Ink's raw-mode path is taken iff the real terminal is a TTY. */
  public readonly isTTY: boolean;

  private readonly real: RealStdin;
  private readonly parser = new CsiUParser();
  /** Detection-phase token listeners (the driver). When any are present, recognised query/DA reply
   * tokens are routed to them and swallowed downstream. */
  private readonly tokenListeners = new Set<(token: CsiToken) => void>();
  private bypass = true;
  private flushTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(real: RealStdin) {
    // objectMode:false — we push Buffers, exactly like the real stdin.
    super();
    this.real = real;
    this.isTTY = real.isTTY ?? false;
    real.on('data', this.onData);
  }

  /** Required by `Readable`; the shim is push-driven (it pushes on real-stdin `data`), so `_read` is
   * a no-op — there is nothing to pull. */
  override _read(): void {}

  /** Enter (`true`) or leave (`false`) pure-passthrough mode. Active mode (`false`) runs the
   * parser/translator and emits `chord` events; bypass forwards bytes verbatim. */
  setBypass(bypass: boolean): void {
    this.bypass = bypass;
    if (bypass) {
      this.clearFlush();
    }
  }

  /** Whether the shim is currently in pure-passthrough mode. */
  isBypass(): boolean {
    return this.bypass;
  }

  /** {@link TokenSource}. Subscribe a detection listener; returns an unsubscribe fn. While at least
   * one listener is subscribed the parser runs even in bypass so reply tokens are caught. */
  subscribe(listener: (token: CsiToken) => void): () => void {
    this.tokenListeners.add(listener);
    return () => {
      this.tokenListeners.delete(listener);
    };
  }

  // --- TTY surface forwarding (Ink needs these on its stdin) -------------------------------------

  override setEncoding(encoding: BufferEncoding): this {
    this.real.setEncoding?.(encoding);
    return this;
  }
  setRawMode(mode: boolean): this {
    this.real.setRawMode?.(mode);
    return this;
  }
  override resume(): this {
    this.real.resume?.();
    return this;
  }
  override pause(): this {
    this.real.pause?.();
    return this;
  }
  ref(): this {
    this.real.ref?.();
    return this;
  }
  unref(): this {
    this.real.unref?.();
    return this;
  }

  /** Detach from the real stream and cancel any pending flush. */
  dispose(): void {
    const off = this.real.off ?? this.real.removeListener;
    off?.call(this.real, 'data', this.onData);
    this.clearFlush();
  }

  // --- internals ---------------------------------------------------------------------------------

  private readonly onData = (chunk: Buffer | string): void => {
    const bytes = typeof chunk === 'string' ? Buffer.from(chunk, 'utf8') : chunk;
    // Fast path: pure bypass with no detection in flight → forward verbatim, parser untouched. This
    // is the behavior-neutral default (modifier=alt), so we never pay parser cost when not needed.
    if (this.bypass && this.tokenListeners.size === 0) {
      this.forward(bytes);
      return;
    }
    const tokens = this.parser.feed(new Uint8Array(bytes));
    this.emitTokens(tokens);
    this.armFlush();
  };

  private emitTokens(tokens: readonly CsiToken[]): void {
    for (const token of tokens) {
      switch (token.type) {
        case 'queryReply':
        case 'daReply':
          // Protocol replies: hand to detection listeners and swallow (never reaches Ink).
          for (const listener of this.tokenListeners) {
            listener(token);
          }
          break;
        case 'passthrough':
          this.forward(Buffer.from(token.bytes));
          break;
        case 'key':
          this.emitKey(token);
          break;
      }
    }
  }

  private emitKey(token: Extract<CsiToken, { type: 'key' }>): void {
    // In bypass-with-detection (e.g. detection running before enable), a stray real keypress is not a
    // CSI-u key under legacy encoding — but if one arrives, forward nothing surprising: re-emit as
    // its translated legacy bytes too, so behavior is consistent. (Normally no `key` token arrives in
    // bypass because legacy keystrokes are passthrough.)
    const result = translate(token);
    if (result.kind === 'chord') {
      this.emit('chord', result.chord);
      return;
    }
    if (result.bytes.length > 0) {
      this.forward(Buffer.from(result.bytes));
    }
  }

  /**
   * Push a chunk downstream to the consumer (Ink, via the normal `Readable` pull model) and also
   * emit a synchronous `forward` event carrying the same bytes. Ink reads through `read()`/`readable`
   * (async by stream design); the synchronous `forward` event is the deterministic observation seam a
   * test taps without fighting the stream's flow timing. Production consumers ignore `forward`.
   */
  private forward(bytes: Buffer): void {
    this.push(bytes);
    this.emit('forward', bytes);
  }

  /** Arm (or re-arm) the lone-ESC flush timer iff the parser is holding an incomplete sequence. */
  private armFlush(): void {
    this.clearFlush();
    if (!this.parser.hasPending()) {
      return;
    }
    this.flushTimer = setTimeout(() => {
      this.flushTimer = undefined;
      const tokens = this.parser.flushPending();
      this.emitTokens(tokens);
    }, LONE_ESC_FLUSH_MS);
    // Don't let the flush timer keep the process alive on its own.
    this.flushTimer.unref?.();
  }

  private clearFlush(): void {
    if (this.flushTimer !== undefined) {
      clearTimeout(this.flushTimer);
      this.flushTimer = undefined;
    }
  }
}
