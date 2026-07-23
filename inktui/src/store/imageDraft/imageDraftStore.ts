/**
 * `imageDraftStore` — the client-side ledger of pasted-image drafts (F9 image-paste/upload UX).
 *
 * The keystone idea (plan TODO-F): **the client mints identity at paste time.** On paste we
 * synchronously generate a `uuid+timestamp` stem — that stem *is* the filename — and record
 * `id → {stem, ext, status, path}` here. Because the client owns the filename, the label→file binding
 * is known the instant you paste; the async upload resolving later changes **no text**, it only flips
 * `status: 'uploading' → 'done' | 'failed'` (and fills `path` on done). This dissolves Textual's
 * fragile rewrite-the-string-after-await dance entirely.
 *
 * The visible `[Image N]` label is NOT stored here — it is derived at render by counting marked spans
 * in the chat buffer (see {@link ../../input/chatInputStore.js}). This store is keyed purely by the
 * stable `id`, so deletion renumbers for free and the map never drifts.
 *
 * ## Why this store owns the bus call (rule 3 nuance)
 *
 * `chatInputStore`'s doc says the *send* bus call lives in the conversations action, not the input
 * store — because send mutates a conversation entity. `image.upload` is different: it writes a *file*
 * and returns a path; it does NOT mutate a conversation entity. So this store legitimately owns the
 * `image.upload` call (the plan calls this out explicitly).
 *
 * ## Serialized FIFO upload queue
 *
 * Uploads run one at a time through a FIFO queue: at most one `image.upload` in flight. This bounds
 * the multi-MB base64 payload on the wire to one, and preserves paste order. When an upload resolves
 * we re-check the id still exists in the map — a span deleted mid-flight (see the span-aware
 * backspace) cancels/ignores its upload by dropping the result and suppressing the toast (we can't
 * abort the in-flight RPC, so we discard it on resolve).
 *
 * ## Toast wiring (this slice owns it; new vs Textual)
 *
 * On `done`/`failed` we push an ambient toast (info on done, error on failed) to the app-level
 * {@link ../toast/toastStore.js toastStore} singleton. Textual left upload feedback silent (only the
 * in-text `[Image upload failed]` marker); the toast is *additive* — we keep the in-text marker too.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React, no Ink.
 */

import { randomUUID } from 'node:crypto';
import { createStore, type StoreApi } from 'zustand/vanilla';
import { asCommandResult } from '../../application/resultCast.js';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { type ToastStoreApi, toastStore } from '../toast/toastStore.js';



/** The lifecycle of one pasted image. `uploading` → `done` (path filled) | `failed`. */
export type ImageDraftStatus = 'uploading' | 'done' | 'failed';

/** One pasted-image draft, keyed by its stable `id` in the store map. */
export interface ImageDraft {
  /** The stable identity. Also the PUA-span payload in the chat buffer. */
  readonly id: string;
  /** The client-minted filename stem (`uuid+timestamp`), passed to the server as `name`. */
  readonly stem: string;
  /** The file extension (e.g. `'png'`). */
  readonly ext: string;
  /** Upload lifecycle state. */
  readonly status: ImageDraftStatus;
  /** The server-returned on-disk path — present only once `status === 'done'`. */
  readonly path?: string;
}

/** Internal draft shape: the public {@link ImageDraft} plus the base64 bytes held until upload (kept
 * off the public type so consumers can't accidentally read multi-MB payloads in render selectors). */
interface ImageDraftInternal extends ImageDraft {
  readonly bytesB64: string;
}

/** The image-draft store state: the id→draft ledger plus the verbs. The map holds the internal shape
 * (with the held base64 bytes); the public {@link ImageDraft} is the read view consumers should use. */
export interface ImageDraftState {
  /** The draft ledger, keyed by stable id. */
  readonly drafts: Readonly<Record<string, ImageDraftInternal>>;
  /**
   * Register a freshly-pasted image: mint a stem + id synchronously, record it as `uploading`, and
   * enqueue the upload (FIFO). Returns the new `id` so the caller can wrap it in a chat-buffer span
   * immediately — the label→file binding is known *now*, before the upload resolves.
   */
  paste(bytes: Buffer, ext: string): string;
  /**
   * Drop a draft by id (called when its span is deleted from the chat buffer). Idempotent. The
   * in-flight upload, if any, is cancelled-by-ignore: the FIFO worker re-checks existence on resolve,
   * so a dropped id's result is discarded and its toast suppressed.
   */
  drop(id: string): void;
  /** Resolve the id → on-disk path map for the drafts that finished (used at submit-time expansion). */
  pathsById(): ReadonlyMap<string, string>;
  /** Clear all drafts (called after a successful submit, or to reset). Does not abort in-flight RPCs;
   * their results are discarded on resolve (the id is gone). */
  clear(): void;
}

/** The image-draft store handle. */
export type ImageDraftStoreApi = StoreApi<ImageDraftState>;

/** Mint a filename stem that is unique and roughly time-ordered: `img-<timestamp>-<uuid>`. The client
 * owns this — it becomes the file's name (the server sanitizes it before use, never trusts the wire). */
function mintStem(): string {
  return `img-${Date.now()}-${randomUUID()}`;
}

/**
 * Create an image-draft store bound to a {@link ApplicationClient}. The `toasts` param is the toast store to
 * push done/failed feedback into — defaults to the app singleton, overridable for isolated tests.
 */
export function createImageDraftStore(
  bus: ApplicationClient,
  toasts: ToastStoreApi = toastStore,
): ImageDraftStoreApi {
  // The FIFO upload queue: ids awaiting their turn, plus a flag for the one in flight. Closure-private
  // (not store state) — it's transport plumbing, not rendered UI. One upload at a time bounds the
  // multi-MB base64 payload on the wire and preserves paste order.
  const queue: string[] = [];
  let draining = false;

  const store = createStore<ImageDraftState>()((set, get) => {
    /** Drain the FIFO queue, one upload at a time. Re-entrancy-guarded by `draining`. */
    async function drain(): Promise<void> {
      if (draining) {
        return;
      }
      draining = true;
      try {
        while (queue.length > 0) {
          const id = queue.shift();
          if (id === undefined) {
            continue;
          }
          const draft = get().drafts[id];
          // The span was deleted before its turn — skip (cancelled-by-ignore).
          if (draft === undefined) {
            continue;
          }
          await uploadOne(draft);
        }
      } finally {
        draining = false;
      }
    }

    /** Upload one draft and flip its status on resolve. On resolve we re-check the id still exists —
     * a span deleted mid-flight has its result discarded and its toast suppressed. */
    async function uploadOne(draft: ImageDraftInternal): Promise<void> {
      const bytesB64 = draft.bytesB64;
      try {
        const reply = await bus.command('image.upload', {
          name: draft.stem,
          ext: draft.ext,
          bytes: bytesB64,
        });
        // Deleted mid-flight: discard, no toast.
        if (get().drafts[draft.id] === undefined) {
          return;
        }
        const result = asCommandResult<'image.upload', { ok?: boolean; path?: string; error?: string }>(reply);
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
      } catch (error: unknown) {
        if (get().drafts[draft.id] === undefined) {
          return;
        }
        markFailed(draft);
        toasts.getState().push('image upload failed', { severity: 'error' });
        void error;
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
        const id = stem; // the stem is unique already; use it as the stable id (one less indirection).
        const draft: ImageDraftInternal = {
          id,
          stem,
          ext,
          status: 'uploading',
          bytesB64: bytes.toString('base64'),
        };
        set((state) => ({ drafts: { ...state.drafts, [id]: draft } }));
        queue.push(id);
        void drain();
        return id;
      },
      drop(id) {
        set((state) => {
          if (state.drafts[id] === undefined) {
            return state;
          }
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
