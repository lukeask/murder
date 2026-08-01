/**
 * Browser port of inktui `imageDraftStore` — client-minted image paste ledger + FIFO `image.upload`.
 * Same contract as the TUI store; uses `crypto.randomUUID` + base64 (no Node `Buffer`/`node:crypto`).
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import { asCommandResult } from '@murder/ui-core/application/resultCast.js';
import type { ApplicationClient } from '@murder/ui-core/application/ApplicationClient.js';
import { type ToastStoreApi, toastStore } from '@murder/ui-core/store/toast/toastStore.js';

export type ImageDraftStatus = 'uploading' | 'done' | 'failed';

export interface ImageDraft {
  readonly id: string;
  readonly stem: string;
  readonly ext: string;
  readonly status: ImageDraftStatus;
  readonly path?: string;
}

interface ImageDraftInternal extends ImageDraft {
  readonly bytesB64: string;
}

export interface ImageDraftState {
  readonly drafts: Readonly<Record<string, ImageDraftInternal>>;
  /** Mint id synchronously, enqueue FIFO upload; returns id for an immediate chat-buffer span. */
  paste(bytes: Uint8Array, ext: string): string;
  drop(id: string): void;
  pathsById(): ReadonlyMap<string, string>;
  clear(): void;
}

export type ImageDraftStoreApi = StoreApi<ImageDraftState>;

function mintStem(): string {
  return `img-${Date.now()}-${crypto.randomUUID()}`;
}

/** Encode raw bytes as standard base64 (browser-safe; no Buffer). */
export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function createImageDraftStore(
  bus: ApplicationClient,
  toasts: ToastStoreApi = toastStore,
): ImageDraftStoreApi {
  const queue: string[] = [];
  let draining = false;

  const store = createStore<ImageDraftState>()((set, get) => {
    async function drain(): Promise<void> {
      if (draining) return;
      draining = true;
      try {
        while (queue.length > 0) {
          const id = queue.shift();
          if (id === undefined) continue;
          const draft = get().drafts[id];
          if (draft === undefined) continue;
          await uploadOne(draft);
        }
      } finally {
        draining = false;
      }
    }

    async function uploadOne(draft: ImageDraftInternal): Promise<void> {
      try {
        const reply = await bus.command('image.upload', {
          name: draft.stem,
          ext: draft.ext,
          bytes: draft.bytesB64,
        });
        if (get().drafts[draft.id] === undefined) return;
        const result = asCommandResult<'image.upload', { ok?: boolean; path?: string; error?: string }>(
          reply,
        );
        if (result.ok === true && typeof result.path === 'string') {
          const path: string = result.path;
          set((state) => ({
            drafts: {
              ...state.drafts,
              [draft.id]: { ...draft, status: 'done', path },
            },
          }));
          toasts.getState().push('image uploaded', { severity: 'info' });
        } else {
          markFailed(draft);
          toasts
            .getState()
            .push(String(result.error ?? 'image upload failed'), { severity: 'error' });
        }
      } catch {
        if (get().drafts[draft.id] === undefined) return;
        markFailed(draft);
        toasts.getState().push('image upload failed', { severity: 'error' });
      }
    }

    function markFailed(draft: ImageDraftInternal): void {
      set((state) => ({
        drafts: { ...state.drafts, [draft.id]: { ...draft, status: 'failed' } },
      }));
    }

    return {
      drafts: {},
      paste(bytes, ext) {
        const stem = mintStem();
        const id = stem;
        const draft: ImageDraftInternal = {
          id,
          stem,
          ext,
          status: 'uploading',
          bytesB64: bytesToBase64(bytes),
        };
        set((state) => ({ drafts: { ...state.drafts, [id]: draft } }));
        queue.push(id);
        void drain();
        return id;
      },
      drop(id) {
        set((state) => {
          if (state.drafts[id] === undefined) return state;
          const next = { ...state.drafts };
          delete next[id];
          return { drafts: next };
        });
      },
      pathsById() {
        const map = new Map<string, string>();
        for (const draft of Object.values(get().drafts)) {
          if (draft.status === 'done' && draft.path !== undefined) {
            map.set(draft.id, draft.path);
          }
        }
        return map;
      },
      clear() {
        set((state) => (Object.keys(state.drafts).length === 0 ? state : { drafts: {} }));
      },
    };
  });
  return store;
}
