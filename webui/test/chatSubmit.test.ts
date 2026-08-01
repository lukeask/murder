/**
 * processChatSubmit — unit coverage for the WebUI chat pipeline order:
 * expandSpans → workflow fire → template expansion → `/`|`:` commands → plain send.
 */

import type { CommandCtx } from '@murder/ui-core/input/commandDispatch.js';
import { makeSpan } from '@murder/ui-core/input/chat/chatSpans.js';
import { describe, expect, it, vi } from 'vitest';
import { processChatSubmit } from '../src/components/stage/chatSubmit.js';

function fakeCtx(over: Partial<CommandCtx> = {}): CommandCtx {
  return {
    sendKey: vi.fn(),
    clearTranscript: vi.fn(),
    openHelp: vi.fn(),
    captureNote: vi.fn(),
    saveTemplate: vi.fn(),
    resolveRenameTarget: () => null,
    renameRogue: vi.fn(),
    renamePlan: vi.fn(),
    setPaneViewMode: vi.fn(),
    pushToast: vi.fn(() => 0),
    clearToasts: vi.fn(),
    ...over,
  };
}

describe('processChatSubmit', () => {
  it('returns empty for a blank buffer and does not send', () => {
    const send = vi.fn();
    const runWorkflow = vi.fn();
    const result = processChatSubmit({
      message: '',
      agentId: 'a1',
      workflowNames: new Set(),
      templateRegistry: new Map(),
      commandCtx: fakeCtx(),
      runWorkflow,
      send,
    });
    expect(result).toEqual({ kind: 'empty' });
    expect(send).not.toHaveBeenCalled();
    expect(runWorkflow).not.toHaveBeenCalled();
  });

  it('fires a saved workflow and skips send/command', () => {
    const send = vi.fn();
    const runWorkflow = vi.fn();
    const ctx = fakeCtx();
    const result = processChatSubmit({
      message: ':deploy fix login',
      agentId: 'a1',
      workflowNames: new Set(['deploy']),
      templateRegistry: new Map([['deploy', 'template body']]),
      commandCtx: ctx,
      runWorkflow,
      send,
    });
    expect(result).toEqual({ kind: 'workflow', name: 'deploy', spanIds: [] });
    expect(runWorkflow).toHaveBeenCalledWith('deploy', { input: 'fix login' });
    expect(send).not.toHaveBeenCalled();
    expect(ctx.openHelp).not.toHaveBeenCalled();
  });

  it('dispatches a builtin :command without sending', () => {
    const send = vi.fn();
    const runWorkflow = vi.fn();
    const ctx = fakeCtx();
    const result = processChatSubmit({
      message: ':help',
      agentId: 'a1',
      workflowNames: new Set(),
      templateRegistry: new Map(),
      commandCtx: ctx,
      runWorkflow,
      send,
    });
    expect(result).toEqual({ kind: 'command', spanIds: [] });
    expect(ctx.openHelp).toHaveBeenCalledOnce();
    expect(send).not.toHaveBeenCalled();
    expect(runWorkflow).not.toHaveBeenCalled();
  });

  it('passthrough /command via sendKey without conversations.send', () => {
    const send = vi.fn();
    const runWorkflow = vi.fn();
    const ctx = fakeCtx();
    const result = processChatSubmit({
      message: '/compact',
      agentId: 'a1',
      workflowNames: new Set(),
      templateRegistry: new Map(),
      commandCtx: ctx,
      runWorkflow,
      send,
    });
    expect(result).toEqual({ kind: 'command', spanIds: [] });
    expect(ctx.sendKey).toHaveBeenCalledWith('a1', '/compact', true, true);
    expect(send).not.toHaveBeenCalled();
  });

  it('expands a leading template then plain-sends the body', () => {
    const send = vi.fn();
    const runWorkflow = vi.fn();
    const result = processChatSubmit({
      message: ':greet Alice',
      agentId: 'a1',
      workflowNames: new Set(),
      templateRegistry: new Map([['greet', 'hello {name}']]),
      commandCtx: fakeCtx(),
      runWorkflow,
      send,
    });
    expect(result).toEqual({ kind: 'send', message: 'hello Alice', spanIds: [] });
    expect(send).toHaveBeenCalledWith('a1', 'hello Alice');
    expect(runWorkflow).not.toHaveBeenCalled();
  });

  it('plain-sends ordinary text', () => {
    const send = vi.fn();
    const result = processChatSubmit({
      message: 'hello there',
      agentId: 'a1',
      workflowNames: new Set(['deploy']),
      templateRegistry: new Map(),
      commandCtx: fakeCtx(),
      runWorkflow: vi.fn(),
      send,
    });
    expect(result).toEqual({ kind: 'send', message: 'hello there', spanIds: [] });
    expect(send).toHaveBeenCalledWith('a1', 'hello there');
  });

  it('builtin beats a same-named workflow (falls through to command)', () => {
    const send = vi.fn();
    const runWorkflow = vi.fn();
    const ctx = fakeCtx();
    const result = processChatSubmit({
      message: ':help',
      agentId: 'a1',
      workflowNames: new Set(['help']),
      templateRegistry: new Map(),
      commandCtx: ctx,
      runWorkflow,
      send,
    });
    expect(result).toEqual({ kind: 'command', spanIds: [] });
    expect(runWorkflow).not.toHaveBeenCalled();
    expect(ctx.openHelp).toHaveBeenCalledOnce();
  });

  it('strips failed image spans and still sends remaining text', () => {
    const send = vi.fn();
    const id = 'img-failed';
    const result = processChatSubmit({
      message: `${makeSpan(id)}hi`,
      agentId: 'a1',
      workflowNames: new Set(),
      templateRegistry: new Map(),
      commandCtx: fakeCtx(),
      runWorkflow: vi.fn(),
      send,
      imageDraft: {
        drafts: { [id]: { status: 'failed' } },
        pathsById: () => new Map(),
      },
    });
    expect(result).toEqual({ kind: 'send', message: 'hi', spanIds: [id] });
    expect(send).toHaveBeenCalledWith('a1', 'hi');
  });
});
