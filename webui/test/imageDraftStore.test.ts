/**
 * imageDraftStore smoke — browser port of the F9 paste/upload ledger.
 */

import { describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { createToastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { makeSpan } from '@murder/ui-core/input/chat/chatSpans.js';
import { expandSpans } from '@murder/ui-core/input/chatInputStore.js';
import {
  bytesToBase64,
  createImageDraftStore,
} from '../src/store/imageDraft/imageDraftStore.js';
import { processChatSubmit } from '../src/components/stage/chatSubmit.js';
import type { CommandCtx } from '@murder/ui-core/input/commandDispatch.js';
import { vi } from 'vitest';

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function fakeCtx(): CommandCtx {
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
  };
}

describe('imageDraftStore (web)', () => {
  it('mints a stem synchronously and uploads FIFO to done', async () => {
    const bus = new FakeApplicationClient();
    bus.stubCommand('image.upload', (params) => ({
      ok: true,
      path: `/img/${params['name']}.png`,
    }));
    const store = createImageDraftStore(bus, createToastStore());

    const id = store.getState().paste(new Uint8Array([1, 2, 3]), 'png');
    expect(store.getState().drafts[id]?.status).toBe('uploading');
    expect(bytesToBase64(new Uint8Array([1, 2, 3]))).toBe(btoa('\x01\x02\x03'));

    await flush();
    expect(store.getState().drafts[id]?.status).toBe('done');
    expect(store.getState().drafts[id]?.path).toBe(`/img/${id}.png`);
  });

  it('expandSpans on submit includes done drafts as markdown images', async () => {
    const bus = new FakeApplicationClient();
    bus.stubCommand('image.upload', { ok: true, path: '/p.png' });
    const imageDraft = createImageDraftStore(bus, createToastStore());
    const id = imageDraft.getState().paste(new Uint8Array([9]), 'png');
    await flush();

    const send = vi.fn();
    const buffer = `${makeSpan(id)}hello`;
    const result = processChatSubmit({
      message: buffer,
      agentId: 'a1',
      workflowNames: new Set(),
      templateRegistry: new Map(),
      commandCtx: fakeCtx(),
      runWorkflow: vi.fn(),
      send,
      imageDraft: imageDraft.getState(),
    });
    expect(result).toEqual({
      kind: 'send',
      message: '![image](/p.png)hello',
      spanIds: [id],
    });
    expect(send).toHaveBeenCalledWith('a1', '![image](/p.png)hello');
    expect(expandSpans(buffer, imageDraft.getState().pathsById())).toBe('![image](/p.png)hello');
  });

  it('blocks submit while a draft is still uploading', () => {
    const bus = new FakeApplicationClient();
    // Never resolve — leave uploading.
    bus.stubCommand(
      'image.upload',
      () => new Promise(() => {}) as unknown as { ok: boolean; path: string },
    );
    const imageDraft = createImageDraftStore(bus, createToastStore());
    const id = imageDraft.getState().paste(new Uint8Array([1]), 'png');
    const onUploading = vi.fn();
    const send = vi.fn();
    const result = processChatSubmit({
      message: makeSpan(id),
      agentId: 'a1',
      workflowNames: new Set(),
      templateRegistry: new Map(),
      commandCtx: fakeCtx(),
      runWorkflow: vi.fn(),
      send,
      imageDraft: imageDraft.getState(),
      onUploading,
    });
    expect(result).toEqual({ kind: 'uploading' });
    expect(onUploading).toHaveBeenCalledOnce();
    expect(send).not.toHaveBeenCalled();
  });
});
