/**
 * `commandDispatch` tests (Workstream E) — the chat-input prefix dispatcher. Each case drives the
 * pure {@link dispatchCommand} with a fake {@link CommandCtx} and asserts which capability fired (and,
 * for the negative cases, that nothing leaked to the agent).
 */

import { describe, expect, it, type Mock, vi } from 'vitest';
import { type CommandCtx, dispatchCommand } from '../../src/input/commandDispatch.js';

/** A fake ctx whose capabilities are all `vi.fn()` spies. The mocks are read back in assertions; the
 * object also satisfies {@link CommandCtx} so it passes straight to {@link dispatchCommand}. */
interface FakeCtx {
  sendKey: Mock;
  openHelp: Mock;
  captureNote: Mock;
  pushToast: Mock;
  clearTranscript: Mock;
  saveTemplate: Mock;
  setPaneViewMode: Mock;
  dismiss?: Mock;
}

/** Build a fake ctx; `withDismiss` toggles the optional `:dismiss` target (omitted, not undefined,
 * so it respects `exactOptionalPropertyTypes`). */
function makeCtx(withDismiss = false): FakeCtx {
  const base: FakeCtx = {
    sendKey: vi.fn(),
    openHelp: vi.fn(),
    captureNote: vi.fn(),
    pushToast: vi.fn(),
    clearTranscript: vi.fn(),
    saveTemplate: vi.fn(),
    setPaneViewMode: vi.fn(),
  };
  if (withDismiss) {
    base.dismiss = vi.fn();
  }
  return base;
}

const AGENT = 'crow-1';

describe('dispatchCommand — / passthrough', () => {
  it('sends the buffer literal WITH a real Return (enter:true), no trailing newline', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand('/compact', AGENT, ctx);
    expect(handled).toBe(true);
    // User ask #5: text without a trailing '\n', literal=true, enter=true (a real Return submits the
    // harness slash field — a literal '\n' typed as text never did).
    expect(ctx.sendKey).toHaveBeenCalledWith(AGENT, '/compact', true, true);
  });

  it('keeps the leading slash in the injected text (harness owns interpretation)', () => {
    const ctx = makeCtx();
    dispatchCommand('/some-new-cc-command arg', AGENT, ctx);
    expect(ctx.sendKey).toHaveBeenCalledWith(AGENT, '/some-new-cc-command arg', true, true);
  });

  it('/clear also wipes the local transcript pane (forward + clearTranscript)', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand('/clear', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.sendKey).toHaveBeenCalledWith(AGENT, '/clear', true, true);
    expect(ctx.clearTranscript).toHaveBeenCalledWith(AGENT);
  });

  it('with no active agent: handled (consumed) but no send — surfaces a toast', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand('/compact', null, ctx);
    expect(handled).toBe(true);
    expect(ctx.sendKey).not.toHaveBeenCalled();
    expect(ctx.pushToast).toHaveBeenCalled();
  });
});

describe('dispatchCommand — : commands', () => {
  it(':help opens the help overlay', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':help', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.openHelp).toHaveBeenCalledOnce();
  });

  it(':note <text> captures a note with the body', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':note remember the milk', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.captureNote).toHaveBeenCalledWith('remember the milk');
  });

  it(':note with no body shows a usage toast and does not capture', () => {
    const ctx = makeCtx();
    dispatchCommand(':note', AGENT, ctx);
    dispatchCommand(':note   ', AGENT, ctx);
    expect(ctx.captureNote).not.toHaveBeenCalled();
    expect(ctx.pushToast).toHaveBeenCalledTimes(2);
  });

  it(':compact sets the pane to condensed (TUIchat-3, no send)', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':compact', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.setPaneViewMode).toHaveBeenCalledWith(AGENT, 'condensed');
    expect(ctx.sendKey).not.toHaveBeenCalled();
  });

  it(':verbose and :tmux set the pane view mode (TUIchat-3)', () => {
    const verboseCtx = makeCtx();
    expect(dispatchCommand(':verbose', AGENT, verboseCtx)).toBe(true);
    expect(verboseCtx.setPaneViewMode).toHaveBeenCalledWith(AGENT, 'verbose');

    const tmuxCtx = makeCtx();
    expect(dispatchCommand(':tmux', AGENT, tmuxCtx)).toBe(true);
    expect(tmuxCtx.setPaneViewMode).toHaveBeenCalledWith(AGENT, 'tmux');
  });

  it(':compact with no active agent toasts and sets nothing (TUIchat-3)', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':compact', null, ctx);
    expect(handled).toBe(true);
    expect(ctx.setPaneViewMode).not.toHaveBeenCalled();
    expect(ctx.pushToast).toHaveBeenCalled();
  });

  it(':resume points at the history panel r keybind', () => {
    const ctx = makeCtx();
    dispatchCommand(':resume', AGENT, ctx);
    expect(ctx.pushToast).toHaveBeenCalledWith(
      ':resume — use r in the history panel',
      expect.anything(),
    );
  });

  it(':dismiss fires the dismiss target when one exists', () => {
    const ctx = makeCtx(true);
    dispatchCommand(':dismiss', AGENT, ctx);
    expect(ctx.dismiss).toHaveBeenCalledOnce();
  });

  it(':dismiss with no target no-ops with a toast', () => {
    const ctx = makeCtx(false);
    const handled = dispatchCommand(':dismiss', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.pushToast).toHaveBeenCalled();
  });

  it('is case-insensitive on the command word', () => {
    const ctx = makeCtx();
    dispatchCommand(':HELP', AGENT, ctx);
    expect(ctx.openHelp).toHaveBeenCalledOnce();
  });

  it('unknown command: literal fallthrough — returns false, no toast, nothing handled', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':unknowncmd', AGENT, ctx);
    // Locked decision: an unknown `:foo` is NOT consumed — it's ordinary text sent verbatim by the
    // caller. No toast, no near-miss hint.
    expect(handled).toBe(false);
    expect(ctx.pushToast).not.toHaveBeenCalled();
    expect(ctx.sendKey).not.toHaveBeenCalled();
    expect(ctx.openHelp).not.toHaveBeenCalled();
    expect(ctx.captureNote).not.toHaveBeenCalled();
  });

  it(':save <name> <body> persists the template and toasts', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':save foo some body', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.saveTemplate).toHaveBeenCalledWith('foo', 'some body');
    expect(ctx.pushToast).toHaveBeenCalledWith('saved :foo:');
  });

  it(':save preserves internal whitespace in the body', () => {
    const ctx = makeCtx();
    dispatchCommand(':save foo line one  and   more', AGENT, ctx);
    expect(ctx.saveTemplate).toHaveBeenCalledWith('foo', 'line one  and   more');
  });

  it(':save with an empty body toasts usage error and does not save', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':save foo', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.saveTemplate).not.toHaveBeenCalled();
    expect(ctx.pushToast).toHaveBeenCalledWith(
      'usage: :save <name> <body>',
      expect.objectContaining({ severity: 'error' }),
    );
  });

  it(':save with an invalid name toasts usage error and does not save', () => {
    const ctx = makeCtx();
    dispatchCommand(':save bad!name a body', AGENT, ctx);
    expect(ctx.saveTemplate).not.toHaveBeenCalled();
    expect(ctx.pushToast).toHaveBeenCalledWith(
      'usage: :save <name> <body>',
      expect.objectContaining({ severity: 'error' }),
    );
  });
});

describe('dispatchCommand — plain text', () => {
  it('returns false so the caller runs its normal send', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand('hello there', AGENT, ctx);
    expect(handled).toBe(false);
    expect(ctx.sendKey).not.toHaveBeenCalled();
    expect(ctx.openHelp).not.toHaveBeenCalled();
    expect(ctx.captureNote).not.toHaveBeenCalled();
    expect(ctx.pushToast).not.toHaveBeenCalled();
  });

  it('text that merely contains : or / later is not a command', () => {
    const ctx = makeCtx();
    expect(dispatchCommand('see https://example.com', AGENT, ctx)).toBe(false);
    expect(dispatchCommand('ratio 3:4', AGENT, ctx)).toBe(false);
  });

  it('a leading-whitespace prefix is plain text, not a command (anchored at index 0)', () => {
    // The contract is "the FIRST character selects routing": `startsWith`, no trim. Leading
    // whitespace before `:`/`/` must NOT route as a command, and crucially the prefix handlers must
    // not fire. These cases fail under a `.includes(':')`-style check, unlike the cases above.
    const ctx = makeCtx();
    expect(dispatchCommand('  :help', AGENT, ctx)).toBe(false);
    expect(dispatchCommand(' /compact', AGENT, ctx)).toBe(false);
    expect(ctx.openHelp).not.toHaveBeenCalled();
    expect(ctx.sendKey).not.toHaveBeenCalled();
    expect(ctx.pushToast).not.toHaveBeenCalled();
  });
});
