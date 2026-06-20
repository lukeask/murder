/**
 * `csiU` — an incremental, byte-in/token-out parser for the kitty keyboard protocol's CSI-u key
 * encoding (plus the two query replies we have to swallow during protocol negotiation).
 *
 * ## Why a hand-rolled parser
 *
 * Under the kitty protocol the terminal no longer sends the legacy "Alt+x = ESC x" / "Ctrl+a = 0x01"
 * byte soup; it sends `CSI <code> ; <mods> [: <event>] u` for *every* key, so Ctrl+digit / Ctrl+i /
 * Ctrl+space — combos legacy encoding cannot represent at all — finally arrive unambiguously. To use
 * them we have to read that stream ourselves: this parser turns the raw byte chunks coming off stdin
 * into a flat list of {@link CsiToken}s, while passing everything it does not recognise through
 * verbatim (so paste, UTF-8 text, mouse reports, and any other escape sequence reach Ink untouched).
 *
 * ## Design constraints (all load-bearing)
 *
 *  - **Incremental.** A sequence can be split across `data` chunks at any byte boundary (a slow pipe,
 *    a tmux relay). The parser keeps a small state machine + buffer between {@link CsiUParser.feed}
 *    calls; it only emits a token once it has the whole thing.
 *  - **Lone-ESC timeout flush.** A bare `0x1b` is ambiguous: it is either the start of an escape
 *    sequence whose tail has not arrived yet, or the user pressing Esc. We hold a lone ESC briefly
 *    and emit it as a passthrough byte if nothing follows within {@link LONE_ESC_FLUSH_MS}. Without
 *    this, pressing Esc to dismiss a modal would feel laggy or hang until the next keypress — so this
 *    flush is **load-bearing** and tested explicitly. The parser is pure (no timers); the owning
 *    {@link ./StdinShim.js shim} drives the clock and calls {@link CsiUParser.flushPending} on
 *    timeout.
 *  - **No Ink / no React.** Plain bytes → tokens. Fully unit-testable.
 *
 * ## What it recognises
 *
 *  - `CSI <code> ; <mods> [: <event>] u` — a keypress. The trailing `u` (0x75) is the kitty final
 *    byte. `mods` and the `:event` sub-parameter are optional; `code` is the unicode codepoint of the
 *    base key. → {@link CsiKeyToken}.
 *  - `CSI ? <flags> u` — the reply to the `CSI ? u` capability query (kitty "current flags"). Emitted
 *    as a {@link CsiQueryReplyToken} so the driver can resolve detection; it is NOT passthrough (Ink
 *    must never see it).
 *  - `CSI ? <...> c` — a DA1 device-attributes reply (final byte `c` = 0x63). Emitted as a
 *    {@link CsiDaReplyToken}; also swallowed during detection.
 *  - `CSI < <button> ; <x> ; <y> M|m` — an SGR mouse report (the `<` private marker, final `M` press
 *    / `m` release). Emitted as a {@link CsiMouseToken} so the shim can lift wheel notches into scroll
 *    events; it is NOT passthrough (with mouse reporting enabled the bytes are ours to consume, not
 *    Ink's). A malformed report falls back to passthrough rather than being swallowed.
 *
 * Anything else — including ordinary `CSI ... <final>` sequences that are not one of the above —
 * passes through as {@link PassthroughToken} bytes, byte-for-byte, so the rest of the input pipeline
 * is unaffected.
 */

/** How long a lone ESC is held before it is flushed as a literal Escape keypress (ms). ~50ms is the
 * standard modal-dismissal budget: long enough that a real escape sequence's tail (which arrives in
 * the same OS read, microseconds later) is never split off, short enough that a human pressing Esc
 * does not perceive lag. */
export const LONE_ESC_FLUSH_MS = 50;

const ESC = 0x1b;
const CSI_OPEN = 0x5b; // '['
const QUESTION = 0x3f; // '?'
const LESS_THAN = 0x3c; // '<'  — the SGR mouse private marker (CSI < b;x;y M|m)
const FINAL_U = 0x75; // 'u'  — kitty key / query-reply final
const FINAL_C = 0x63; // 'c'  — DA1 reply final
const FINAL_M_PRESS = 0x4d; // 'M' — SGR mouse press final
const FINAL_M_RELEASE = 0x6d; // 'm' — SGR mouse release final

/** A run of bytes the parser did not interpret — forwarded downstream verbatim (text, paste, mouse,
 * unrecognised escape sequences, and a timed-out lone ESC). */
export interface PassthroughToken {
  readonly type: 'passthrough';
  readonly bytes: Uint8Array;
}

/** A decoded kitty keypress: `CSI <code> ; <mods> [: <event>] u`. `code` is the base-key unicode
 * codepoint; `mods` is the raw kitty modifier param (1-based; `undefined` when absent — i.e. no
 * modifiers); `event` is the optional event-type sub-parameter (1=press, 2=repeat, 3=release). */
export interface CsiKeyToken {
  readonly type: 'key';
  readonly code: number;
  readonly mods?: number;
  readonly event?: number;
}

/** The `CSI ? <flags> u` capability-query reply (the terminal's current kitty flags). */
export interface CsiQueryReplyToken {
  readonly type: 'queryReply';
  readonly flags: number;
}

/** A `CSI ? ... c` DA1 device-attributes reply (swallowed during detection's query race). */
export interface CsiDaReplyToken {
  readonly type: 'daReply';
}

/** A decoded SGR mouse report: `CSI < <button> ; <x> ; <y> M|m`. `button` is the raw SGR button code
 * (low bits = button/wheel, plus the +4/+8/+16 shift/meta/ctrl and +32 motion bits — the consumer
 * masks what it needs); `x`/`y` are 1-based cell coordinates; `pressed` is `true` for the `M` final
 * (press / wheel notch) and `false` for `m` (release). Emitted only when the parser is in active mode
 * (mouse reporting enabled), NOT passthrough — Ink never sees mouse bytes, the shim consumes them. */
export interface CsiMouseToken {
  readonly type: 'mouse';
  readonly button: number;
  readonly x: number;
  readonly y: number;
  readonly pressed: boolean;
}

/** Everything the parser can emit. */
export type CsiToken =
  | PassthroughToken
  | CsiKeyToken
  | CsiQueryReplyToken
  | CsiDaReplyToken
  | CsiMouseToken;

/** Internal state-machine phases. */
type Phase =
  | 'ground' // not inside a sequence
  | 'esc' // saw ESC, awaiting '['
  | 'csi'; // inside CSI params, awaiting a final byte

/** Decode the kitty modifier param into the canonical (base-1) value the translator reads. The wire
 * value is `actual + 1`, but we hand the raw param straight through and let the translator subtract;
 * keeping it raw here means the parser has no semantic knowledge of modifier bits. */
function parseParams(bytes: readonly number[]): { code: number; mods?: number; event?: number } {
  // Params look like `<code>;<mods>:<event>` — ';' separates params, ':' separates sub-params of the
  // second (modifier) param. Missing fields default per the protocol (code 1 if absent, no mods).
  const text = String.fromCharCode(...bytes);
  const [codePart = '', modPart = ''] = text.split(';');
  const code = codePart.length > 0 ? Number.parseInt(codePart, 10) : 1;
  if (modPart.length === 0) {
    return { code };
  }
  const [modsStr = '', eventStr = ''] = modPart.split(':');
  const mods = modsStr.length > 0 ? Number.parseInt(modsStr, 10) : undefined;
  const event = eventStr.length > 0 ? Number.parseInt(eventStr, 10) : undefined;
  // exactOptionalPropertyTypes: only attach the optional fields when present.
  return {
    code,
    ...(mods !== undefined && !Number.isNaN(mods) ? { mods } : {}),
    ...(event !== undefined && !Number.isNaN(event) ? { event } : {}),
  };
}

/** Parse the params of an SGR mouse sequence (`<button> ; <x> ; <y>`) given the final byte (`M`/`m`).
 * Returns `null` on a malformed report (wrong field count / non-numeric) so the caller can fall back
 * to forwarding the bytes verbatim rather than swallowing a sequence it could not decode. */
function parseMouse(bytes: readonly number[], final: number): CsiMouseToken | null {
  const [buttonPart = '', xPart = '', yPart = '', ...rest] = String.fromCharCode(...bytes).split(
    ';',
  );
  if (rest.length > 0 || buttonPart === '' || xPart === '' || yPart === '') {
    return null;
  }
  const button = Number.parseInt(buttonPart, 10);
  const x = Number.parseInt(xPart, 10);
  const y = Number.parseInt(yPart, 10);
  if (Number.isNaN(button) || Number.isNaN(x) || Number.isNaN(y)) {
    return null;
  }
  return { type: 'mouse', button, x, y, pressed: final === FINAL_M_PRESS };
}

/**
 * The incremental parser. Hold one instance per stdin stream; call {@link feed} with each chunk and
 * {@link flushPending} when the owning shim's lone-ESC timer fires. Both return the tokens produced
 * (possibly empty). The parser keeps no timers of its own — it is pure over the byte stream + an
 * explicit flush signal, so a test drives the timeout deterministically.
 */
export class CsiUParser {
  private phase: Phase = 'ground';
  /** Raw bytes accumulated for the in-progress sequence (everything from ESC onward, exclusive of
   * the ESC itself once we leave the `esc` phase — kept so an aborted sequence can be replayed). */
  private seq: number[] = [];
  /** True once the CSI params start with '?', marking a query/DA reply rather than a keypress. */
  private privateMarker = false;
  /** True once the CSI params start with '<', marking an SGR mouse report (`CSI < b;x;y M|m`). */
  private sgrMouse = false;
  /** Pending passthrough bytes, batched so a run of plain text is one token, not one-per-byte. */
  private passthrough: number[] = [];

  /** Feed one raw chunk; returns every token completed by it. A lone trailing ESC (or partial
   * sequence) stays buffered and is reported on the next {@link feed}/{@link flushPending}. */
  feed(chunk: Uint8Array): CsiToken[] {
    const out: CsiToken[] = [];
    for (const byte of chunk) {
      this.step(byte, out);
    }
    this.drainPassthrough(out);
    return out;
  }

  /**
   * Called by the shim when the lone-ESC timer elapses with the parser still holding a bare ESC (or
   * an ESC-prefixed fragment that never completed). Emits the buffered bytes as passthrough — for a
   * lone ESC that is the literal Escape keypress; for a stalled partial sequence it is the safest
   * recovery (forward what we have rather than swallow it). Resets to ground.
   */
  flushPending(): CsiToken[] {
    const out: CsiToken[] = [];
    if (this.phase === 'esc') {
      // Held a bare ESC (or ESC + nothing yet) — the literal Escape keypress.
      this.passthrough.push(ESC);
      this.reset();
    } else if (this.phase === 'csi') {
      // Stalled mid-CSI: replay everything consumed so far verbatim (ESC '[' [?] params) — recovery,
      // not swallow. The '[' and any private marker were consumed on entry, so reconstruct them.
      this.passthrough.push(ESC, CSI_OPEN);
      if (this.privateMarker) {
        this.passthrough.push(QUESTION);
      }
      if (this.sgrMouse) {
        this.passthrough.push(LESS_THAN);
      }
      this.passthrough.push(...this.seq);
      this.reset();
    }
    this.drainPassthrough(out);
    return out;
  }

  /** True iff the parser is currently holding an incomplete escape sequence (so the shim should arm
   * its lone-ESC flush timer). Ground state with no buffered partial → false. */
  hasPending(): boolean {
    return this.phase !== 'ground';
  }

  private step(byte: number, out: CsiToken[]): void {
    switch (this.phase) {
      case 'ground':
        if (byte === ESC) {
          this.drainPassthrough(out);
          this.phase = 'esc';
          return;
        }
        this.passthrough.push(byte);
        return;
      case 'esc':
        if (byte === CSI_OPEN) {
          this.phase = 'csi';
          this.seq = [];
          this.privateMarker = false;
          return;
        }
        // ESC followed by something other than '[' — not a CSI sequence we parse. Forward the ESC and
        // this byte verbatim, then reprocess this byte from ground in case it starts a fresh ESC.
        this.passthrough.push(ESC);
        this.phase = 'ground';
        this.step(byte, out);
        return;
      case 'csi':
        this.stepCsi(byte, out);
        return;
    }
  }

  private stepCsi(byte: number, out: CsiToken[]): void {
    // Leading '?' (private marker) on an otherwise-empty param run → a query/DA reply.
    if (byte === QUESTION && this.seq.length === 0) {
      this.privateMarker = true;
      return;
    }
    // Leading '<' on an otherwise-empty param run → an SGR mouse report (the private marker for
    // `CSI < b;x;y M|m`). Consumed like '?', re-synthesised by the passthrough/flush recovery paths.
    if (byte === LESS_THAN && this.seq.length === 0 && !this.privateMarker) {
      this.sgrMouse = true;
      return;
    }
    // A final byte (0x40–0x7e) terminates the CSI sequence.
    if (byte >= 0x40 && byte <= 0x7e) {
      this.emitCsi(byte, out);
      this.reset();
      return;
    }
    // Otherwise it is a param/intermediate byte; accumulate it.
    this.seq.push(byte);
  }

  private emitCsi(final: number, out: CsiToken[]): void {
    if (this.sgrMouse) {
      // `CSI < b;x;y M|m`. Decode to a mouse token; a malformed report (bad field count / NaN) is
      // forwarded verbatim rather than swallowed, so the byte stream is never silently lost.
      if (final === FINAL_M_PRESS || final === FINAL_M_RELEASE) {
        const mouse = parseMouse(this.seq, final);
        if (mouse !== null) {
          out.push(mouse);
          return;
        }
      }
      this.emitPassthroughSeq(final);
      return;
    }
    if (this.privateMarker) {
      if (final === FINAL_U) {
        // CSI ? <flags> u — capability query reply.
        const text = String.fromCharCode(...this.seq);
        const flags = text.length > 0 ? Number.parseInt(text, 10) : 0;
        out.push({ type: 'queryReply', flags: Number.isNaN(flags) ? 0 : flags });
        return;
      }
      if (final === FINAL_C) {
        // CSI ? ... c — DA1 reply.
        out.push({ type: 'daReply' });
        return;
      }
      // Some other private CSI sequence we don't model — pass it through verbatim.
      this.emitPassthroughSeq(final);
      return;
    }
    if (final === FINAL_U) {
      // CSI <code> ; <mods> [: <event>] u — a keypress.
      out.push({ type: 'key', ...parseParams(this.seq) });
      return;
    }
    // A non-kitty CSI sequence (cursor reports, mouse, SGR, …) — forward verbatim.
    this.emitPassthroughSeq(final);
  }

  /** Forward an unrecognised CSI sequence downstream byte-for-byte (ESC '[' [?] params final). */
  private emitPassthroughSeq(final: number): void {
    this.passthrough.push(ESC, CSI_OPEN);
    if (this.privateMarker) {
      this.passthrough.push(QUESTION);
    }
    if (this.sgrMouse) {
      this.passthrough.push(LESS_THAN);
    }
    this.passthrough.push(...this.seq, final);
  }

  private drainPassthrough(out: CsiToken[]): void {
    if (this.passthrough.length > 0) {
      out.push({ type: 'passthrough', bytes: Uint8Array.from(this.passthrough) });
      this.passthrough = [];
    }
  }

  private reset(): void {
    this.phase = 'ground';
    this.seq = [];
    this.privateMarker = false;
    this.sgrMouse = false;
  }
}
