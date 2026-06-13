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
  };
  if (withDismiss) {
    base.dismiss = vi.fn();
  }
  return base;
}

const AGENT = 'crow-1';

describe('dispatchCommand — / passthrough', () => {
  it('sends the buffer verbatim (with newline, literal) to the agent pane', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand('/compact', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.sendKey).toHaveBeenCalledWith(AGENT, '/compact\n', true);
  });

  it('keeps the leading slash in the injected text (harness owns interpretation)', () => {
    const ctx = makeCtx();
    dispatchCommand('/some-new-cc-command arg', AGENT, ctx);
    expect(ctx.sendKey).toHaveBeenCalledWith(AGENT, '/some-new-cc-command arg\n', true);
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

  it(':compact shows the coming-soon stub toast (no send)', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':compact', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.pushToast).toHaveBeenCalledWith(':compact is not yet available', expect.anything());
    expect(ctx.sendKey).not.toHaveBeenCalled();
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

  it('unknown command: consumed, toast shown, nothing sent', () => {
    const ctx = makeCtx();
    const handled = dispatchCommand(':unknowncmd', AGENT, ctx);
    expect(handled).toBe(true);
    expect(ctx.pushToast).toHaveBeenCalledWith('Unknown command: :unknowncmd', expect.anything());
    expect(ctx.sendKey).not.toHaveBeenCalled();
    expect(ctx.openHelp).not.toHaveBeenCalled();
    expect(ctx.captureNote).not.toHaveBeenCalled();
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
});
