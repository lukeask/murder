/**
 * `commandDispatch` — the single chat-input prefix interceptor (Workstream E).
 *
 * Semantics (locked by the plan): the first character of the chat buffer selects a routing mode.
 *
 *  - **`/` prefix** → raw passthrough. The whole buffer (the `/` included) is injected verbatim into
 *    the active agent's harness pane via `sendKey(..., literal=true)` followed by a newline, so a
 *    brand-new harness-native slash command (`/compact`, a CC command we've never heard of) works
 *    inside murder before any integration exists. murder does not interpret the text.
 *  - **`:` prefix** → murder command. Routed to the {@link COMMANDS} table by the word after the `:`.
 *    Unknown commands surface a toast and send NOTHING (so a typo never leaks to the agent).
 *  - **anything else** → not a command: `dispatchCommand` returns `false` and the caller proceeds
 *    with its normal send path. This is the only signal the caller needs.
 *
 * ## Why a pure function, not a store
 *
 * Dispatch is a one-shot decision over the buffer text plus a bag of action references — there is no
 * state to keep between calls. Keeping it a pure function (mirroring `matchKeymap`, `selectLiveToasts`)
 * makes every branch unit-testable with a fake {@link CommandCtx} and keeps the routing in ONE place
 * instead of smeared across stores. The intercept site (App's chat-input handler) builds the `ctx`
 * from what it already has in scope and calls this before `conversations.send`.
 */

import type { PushOptions } from '../store/toast/toastStore.js';

/**
 * The action references `dispatchCommand` needs, shaped from what the chat-input intercept site has
 * in scope. No store handles leak in — each is a narrow capability so the dispatcher (and its tests)
 * stay decoupled from store internals.
 */
export interface CommandCtx {
  /** Forward raw text to the active agent's harness pane (the `/` passthrough primitive). `literal`
   * sends the text verbatim (printable chars) rather than as a tmux key name. Mirrors the
   * `conversations.sendKey` action signature. */
  readonly sendKey: (agentId: string, key: string, literal: boolean) => void;
  /** Open (or focus) the keybinding help overlay — the same action `?` triggers. */
  readonly openHelp: () => void;
  /** Capture a quick note (the `ctrl+n` quick-note submit path), with the text as the note body. */
  readonly captureNote: (text: string) => void;
  /** Dismiss/close the focused panel's current overlay or selection, if the panel architecture has a
   * uniform dismiss concept. `undefined` when no dismiss target is available — `:dismiss` then no-ops
   * with a toast rather than throwing. */
  readonly dismiss?: () => void;
  /** Push a transient toast (unknown-command feedback, stub "coming soon" messages, passthrough hint). */
  readonly pushToast: (text: string, options?: PushOptions) => void;
}

/** One murder `:command` handler. Receives the argument string (everything after the command word,
 * trimmed of the single leading space) and the active agent id (may be `null` if no agent is active).
 * Pure side-effects through the ctx; returns nothing — handling is unconditional once routed here. */
type CommandHandler = (args: string, agentId: string | null, ctx: CommandCtx) => void;

/**
 * The murder `:command` table. The keys are the bare command words (no `:`). Each entry is a named
 * handler — no inline closures in the dispatch loop, so the table reads as documentation of the v0
 * command surface. Adding a command = adding one entry here (and one Help "Commands" row).
 */
const COMMANDS: Readonly<Record<string, CommandHandler>> = {
  /** `:help` — open the help overlay (same as `?`). */
  help(_args, _agentId, ctx) {
    ctx.openHelp();
  },

  /** `:note <text>` — quick-capture a note with the given body. Empty body → a usage toast (the
   * capture surface needs text; sending an empty note is never what the user meant). */
  note(args, _agentId, ctx) {
    const body = args.trim();
    if (body === '') {
      ctx.pushToast('usage: :note <text>', { ttlMs: 3000 });
      return;
    }
    ctx.captureNote(body);
  },

  /** `:dismiss` — close the focused panel's overlay/selection, if a dismiss target exists. */
  dismiss(_args, _agentId, ctx) {
    if (ctx.dismiss === undefined) {
      ctx.pushToast('nothing to dismiss', { ttlMs: 2000 });
      return;
    }
    ctx.dismiss();
  },

  /** `:compact` — stub. `transcript_summarize.py` is unwired (Workstream D deferred); surface a
   * discoverability toast rather than silently doing nothing. */
  compact(_args, _agentId, ctx) {
    ctx.pushToast(':compact is not yet available', { ttlMs: 3000 });
  },

  /** `:resume` — stub. The v0 surface for resuming is the history panel's `r` keybind; point there. */
  resume(_args, _agentId, ctx) {
    ctx.pushToast(':resume — use r in the history panel', { ttlMs: 4000 });
  },
};

/**
 * Route the chat buffer through the prefix dispatcher.
 *
 * Returns `true` when the text was handled as a command or passthrough (the caller must NOT also run
 * its normal send), and `false` for ordinary text (the caller proceeds as before). The buffer-clearing
 * is the caller's job in both cases — this function only routes.
 *
 * @param text     the raw chat buffer (already image-span-expanded by the caller; we only look at the
 *                 leading char and, for `:`, the first word).
 * @param agentId  the active agent id, or `null` if none is active. `/` passthrough requires an agent;
 *                 with none, it falls through to a toast (nothing to send to).
 * @param ctx      the action capabilities (see {@link CommandCtx}).
 */
export function dispatchCommand(text: string, agentId: string | null, ctx: CommandCtx): boolean {
  // `/` passthrough — inject the buffer verbatim (the `/` included) into the agent's harness pane.
  if (text.startsWith('/')) {
    if (agentId === null) {
      ctx.pushToast('no active agent for passthrough', { ttlMs: 2000 });
      return true; // still handled: a `/`-prefixed buffer is never an ordinary send.
    }
    ctx.sendKey(agentId, `${text}\n`, true);
    ctx.pushToast('[→ passthrough]', { ttlMs: 1500 });
    return true;
  }

  // `:` murder command — split the first word (the command) from the rest (its args).
  if (text.startsWith(':')) {
    const rest = text.slice(1);
    const spaceIdx = rest.indexOf(' ');
    const name = (spaceIdx === -1 ? rest : rest.slice(0, spaceIdx)).toLowerCase();
    const args = spaceIdx === -1 ? '' : rest.slice(spaceIdx + 1);
    const handler = COMMANDS[name];
    if (handler === undefined) {
      ctx.pushToast(`Unknown command: :${name}`, { ttlMs: 3000 });
      return true; // handled (consumed): an unknown `:command` must not leak to the agent.
    }
    handler(args, agentId, ctx);
    return true;
  }

  // Ordinary text — the caller runs its normal send.
  return false;
}

/** The command words known to the dispatcher, for callers that want to introspect the surface (e.g.
 * the Help overlay's "Commands" section keeps its own descriptions, but this keeps the two honest). */
export const COMMAND_NAMES: readonly string[] = Object.keys(COMMANDS);
