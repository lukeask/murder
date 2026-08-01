/**
 * useGotoLine — the vim-style `g<digits>` go-to-line gesture, shared by BOTH scrollable Stage panes
 * ({@link ../components/panes/DocumentController.js DocumentController} and
 * transcript pane) so the gesture is one mechanism, not two forks.
 *
 * ## The gesture
 * With a doc/transcript pane focused, `g` starts a line-number capture; each digit typed extends the
 * number and jumps the pane's window IMMEDIATELY (`g39` = press `g`, then `3` — jump to line 3 —
 * then `9` — refine to line 39). There is no commit key: the jump is live per digit. `g`/`esc`/
 * `enter` end the capture (position keeps the last jump); any OTHER matched pane key (e.g. `j`)
 * also ends it via {@link GotoLine.clear} and then acts normally. Lines are 1-based; the pane's
 * `jump` callback clamps to its own scroll range.
 *
 * ## How it plugs into a pane's keymap (rule 5 — declared, not handled)
 * The pane spreads {@link GotoLine.entries} AHEAD of its own entries (so while a capture is live,
 * `enter`/`esc` end the capture instead of firing the pane's own `close`), and routes intents
 * through {@link GotoLine.handle} first:
 *
 *   keymap: [...goto.entries, ...ownEntries],
 *   onIntent(intent) {
 *     if (goto.handle(intent)) return;
 *     goto.clear(); // any other pane key ends a live capture
 *     …own switch…
 *   }
 *
 * The digit/end entries are `hidden: true` (see keymap.ts) so the transient capture keys don't
 * flood the bottom bar — only the `g  go to line` hint shows.
 *
 * The state machine itself is the pure {@link reduceGoto} (the test seam); the hook only owns the
 * `useState` for the pending digits and memoises the derived keymap entries.
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import type { Keymap } from '../input/keymap.js';

const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'] as const;
type Digit = (typeof DIGITS)[number];

/** The gesture's intents: start the capture, extend it by one digit, or end it. */
export type GotoIntent = 'goto.start' | 'goto.end' | `goto.digit.${Digit}`;

/**
 * The keymap entries the gesture declares for a given capture state. Idle (`pending === null`):
 * the visible `g` hint PLUS the ten digits — pre-registered but inert ({@link reduceGoto} ignores a
 * digit with no live capture), because keymap re-registration lands a render AFTER the `g` keypress;
 * a fast `g3` arriving in one stdin chunk would otherwise lose the `3` against the stale registry.
 * Capturing: the digits plus the end chords (`g`/`esc`/`enter`) — the end chords are NOT
 * pre-registered while idle, where `enter`/`esc` must keep their pane meanings (e.g. close the doc).
 * Everything but the `g` starter is `hidden` — gesture sub-steps, not hints.
 */
export function gotoKeymap(pending: string | null): Keymap<GotoIntent> {
  const digits = DIGITS.map((digit) => ({
    chord: { input: digit },
    intent: `goto.digit.${digit}` as const,
    description: 'go-to-line digit',
    hidden: true,
  }));
  if (pending === null) {
    return [{ chord: { input: 'g' }, intent: 'goto.start', description: 'go to line' }, ...digits];
  }
  return [
    ...digits,
    {
      chord: [{ input: 'g' }, { key: { escape: true } }, { key: { return: true } }],
      intent: 'goto.end',
      description: 'end go to line',
      hidden: true,
    },
  ];
}

/** One step of the capture: the next pending digits (`null` = capture ended) and the 1-based line
 * to jump to now (`null` = no jump this step — start/end are positionless). */
export interface GotoStep {
  readonly pending: string | null;
  readonly jumpTo: number | null;
}

/**
 * The pure state machine: fold one intent into the capture state. Returns `null` for a non-goto
 * intent (the caller's own keymap handles it — and should end any live capture via `clear`).
 * A digit extends the pending number and jumps live; `0` alone clamps to line 1. A digit with NO
 * live capture is an inert step (consumed, nothing changes) — digits are pre-registered while idle
 * so a same-chunk `g3` lands, which means a bare digit can arrive without a capture.
 */
export function reduceGoto(pending: string | null, intent: string): GotoStep | null {
  if (!intent.startsWith('goto.')) {
    return null;
  }
  if (intent === 'goto.start') {
    return { pending: '', jumpTo: null };
  }
  if (intent === 'goto.end') {
    return { pending: null, jumpTo: null };
  }
  if (pending === null) {
    return { pending: null, jumpTo: null };
  }
  const next = pending + intent.slice('goto.digit.'.length);
  return { pending: next, jumpTo: Math.max(Number.parseInt(next, 10), 1) };
}

/** What a pane gets back: the live capture (for a `g39` title indicator), the entries to spread
 * into its keymap, the intent router, and the any-other-key terminator. */
export interface GotoLine {
  /** The digits captured so far (`''` right after `g`), or `null` when no capture is live. */
  readonly pending: string | null;
  readonly entries: Keymap<GotoIntent>;
  /** Route one intent: `true` = it was a goto intent (consumed); `false` = the pane's own. */
  readonly handle: (intent: string) => boolean;
  /** End a live capture (called when any non-goto pane intent fires). */
  readonly clear: () => void;
}

/**
 * The React wiring over {@link reduceGoto}. `jump` receives the 1-based target line on every digit;
 * the pane clamps it to its own scroll range (the hook knows nothing about windows — rule 1: the
 * scroll offset stays the pane's `useState`).
 *
 * The capture state lives in BOTH a ref and state: `handle` reads/writes the ref synchronously, so
 * a `g` and its digits arriving in one stdin chunk (dispatched before React re-renders) still fold
 * into one capture; the state mirror only drives rendering (the title indicator + which entries
 * are declared).
 */
export function useGotoLine(jump: (line: number) => void): GotoLine {
  const [pending, setPending] = useState<string | null>(null);
  const pendingRef = useRef<string | null>(null);
  const entries = useMemo(() => gotoKeymap(pending), [pending]);
  const handle = useCallback(
    (intent: string): boolean => {
      const step = reduceGoto(pendingRef.current, intent);
      if (step === null) {
        return false;
      }
      pendingRef.current = step.pending;
      setPending(step.pending);
      if (step.jumpTo !== null) {
        jump(step.jumpTo);
      }
      return true;
    },
    [jump],
  );
  const clear = useCallback(() => {
    pendingRef.current = null;
    setPending(null);
  }, []);
  return useMemo(() => ({ pending, entries, handle, clear }), [pending, entries, handle, clear]);
}
