/**
 * `kittyDriver` — the kitty-keyboard-protocol implementation of {@link KeyProtocolDriver}: the seam
 * that knows the *wire sequences* for detecting, enabling, and disabling a terminal key protocol.
 *
 * ## Why an interface, not just a function
 *
 * The plan keeps a graceful upgrade path: xterm's `modifyOtherKeys` (with `formatOtherKeys=1`) also
 * emits CSI-u, so its parser is the SAME {@link ./csiU.js} — only the negotiation handshake differs.
 * Modelling the handshake as a {@link KeyProtocolDriver} means a second driver can be added later
 * without touching the parser, the translator, or the shim: they all speak CSI-u tokens regardless of
 * which protocol produced them. This file is the kitty handshake; a future `modifyOtherKeys` driver
 * is a sibling.
 *
 * ## The detection race (kitty query + DA1)
 *
 * `detect()` writes the kitty capability query `CSI ? u` immediately followed by a DA1 request
 * `CSI c`. A kitty-capable terminal answers the query with `CSI ? <flags> u` *before* the DA1 reply
 * `CSI ? ... c`; every terminal answers DA1. So:
 *
 *  - a `queryReply` token arrives → kitty supported → resolve `true`.
 *  - a `daReply` arrives first (no query reply) → unsupported → resolve `false`.
 *  - neither within `timeoutMs` (a dumb pipe, or tmux<3.3 swallowing both) → resolve `false`.
 *
 * The reply tokens come from the shim's parser (it owns stdin), NOT from a raw stdin listener — so the
 * replies never reach Ink. The driver therefore takes a {@link TokenSource}: a subscribe-for-tokens
 * seam the shim implements. Detection is byte-in/promise-out and tests against a scripted fake source.
 *
 * ## Enable / disable = flag-stack push/pop
 *
 *  - `enable()` pushes our desired flags with `CSI > <flags> u` (push onto the terminal's protocol
 *    stack — survives nested programs and is popped on exit).
 *  - `disable()` pops with `CSI < u`. Idempotent-safe: a redundant pop on a conforming terminal is a
 *    no-op, so the exit/SIGTERM best-effort pop can fire even if `enable` never ran.
 */

import type { CsiToken } from './csiU.js';

/** The protocol-negotiation seam. A driver knows the wire handshake for one key protocol; the parser,
 * translator, and shim are protocol-agnostic (they only ever see CSI-u tokens). */
export interface KeyProtocolDriver {
  /** Probe the terminal. Resolves `true` iff it supports (and will deliver) this protocol's CSI-u
   * key reporting. Never rejects — a non-answering terminal resolves `false` on timeout. */
  detect(timeoutMs?: number): Promise<boolean>;
  /** Turn the protocol on (push our flags). Safe to call once detection succeeded. */
  enable(): void;
  /** Turn the protocol off (pop our flags). Idempotent / best-effort, so it is safe in a teardown or
   * signal handler even if {@link enable} never ran. */
  disable(): void;
}

/** The bytes a driver writes to the terminal (stdout). Narrowed to just `write` so a test passes a
 * trivial sink. */
export interface ProtocolWriter {
  write(data: string): void;
}

/**
 * A subscribe-for-tokens seam. The shim's parser produces {@link CsiToken}s; during detection the
 * driver listens for the query/DA reply tokens here (the shim routes them to the driver instead of
 * downstream). `subscribe` returns an unsubscribe fn. Plain enough that a test feeds scripted tokens.
 */
export interface TokenSource {
  subscribe(listener: (token: CsiToken) => void): () => void;
}

/** Default detection budget. Mirrors Ink's own kitty-query timeout (200ms): long enough for a relay
 * (tmux/ssh) round-trip, short enough that startup is not perceptibly delayed when unsupported. */
export const DEFAULT_DETECT_TIMEOUT_MS = 200;

/** The kitty flags we request on enable. `disambiguateEscapeCodes` (bit 1) is the minimum that makes
 * Ctrl+digit / Ctrl+i/m/h unambiguous — the whole reason we opt in. We deliberately do NOT request
 * event-types or report-all-as-escape-codes: the translator synthesises legacy bytes for everything
 * legacy handled, so the lighter the flag set, the smaller the behavioral surface. */
export const KITTY_ENABLE_FLAGS = 1;

/** Construct the kitty-protocol driver over a stdout writer and the shim's token source. */
export function createKittyDriver(writer: ProtocolWriter, tokens: TokenSource): KeyProtocolDriver {
  return {
    detect(timeoutMs: number = DEFAULT_DETECT_TIMEOUT_MS): Promise<boolean> {
      return new Promise<boolean>((resolve) => {
        let settled = false;
        const finish = (supported: boolean): void => {
          if (settled) {
            return;
          }
          settled = true;
          clearTimeout(timer);
          unsubscribe();
          resolve(supported);
        };
        const unsubscribe = tokens.subscribe((token) => {
          if (token.type === 'queryReply') {
            // Kitty answered the capability query before DA1 → supported.
            finish(true);
          } else if (token.type === 'daReply') {
            // DA1 came back with no preceding query reply → unsupported.
            finish(false);
          }
        });
        const timer = setTimeout(() => finish(false), timeoutMs);
        // Query then DA1, in that order, so a kitty terminal's query reply precedes the DA1 fallback.
        writer.write('[?u');
        writer.write('[c');
      });
    },
    enable(): void {
      writer.write(`[>${KITTY_ENABLE_FLAGS}u`);
    },
    disable(): void {
      writer.write('[<u');
    },
  };
}
