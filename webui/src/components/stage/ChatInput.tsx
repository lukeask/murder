/** ChatInput — composer for the active chat target; TUI-parity submit + history/vim/choice chrome. */

import {
  isFreeformChoiceLabel,
  isFreeformChoiceSelected,
  selectActiveAgentId,
  selectConversationMeta,
  selectLiveChoicePrompt,
  selectUserHistory,
  type LiveChoicePromptView,
} from '@murder/ui-core/selectors/conversationsSelectors.js';
import { useAppStore, useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { visualDown, visualUp } from '@murder/ui-core/input/chatBuffer.js';
import { SPAN_RE, spanLabels } from '@murder/ui-core/input/chat/chatSpans.js';
import { reduceVimNormal } from '@murder/ui-core/input/chatVimReducer.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { shallow } from 'zustand/shallow';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent as ReactClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { Input, IconButton, KeyHint, Icon } from '../ds/index.js';
import { CHAT_INPUT_ID } from '../../useDesktopKeybinds.js';
import { buildWebCommandCtx } from '../../chatCommandCtx.js';
import { useCreationDialogs } from '../../creationDialogs.js';
import {
  useChatInputStore,
  useChatVimStore,
  useComposerStores,
} from '../../composer/ComposerStoresProvider.js';
import { applyVimEffect } from '../../composer/applyVimEffect.js';
import { estimateContentWidth, keyFromDomEvent } from '../../composer/domKey.js';
import { processChatSubmit } from './chatSubmit.js';
import {
  createImageDraftStore,
  type ImageDraftStoreApi,
} from '../../store/imageDraft/imageDraftStore.js';

const CHOICE_KEY_MAP: Record<string, string> = {
  ArrowUp: 'Up',
  ArrowDown: 'Down',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  Enter: 'Enter',
  Escape: 'Escape',
  ' ': 'Space',
  Backspace: 'BSpace',
  Tab: 'Tab',
};

/** Plain-text view of the buffer for the textarea (image spans live as chips, not glyphs). */
function stripSpans(text: string): string {
  return text.replace(SPAN_RE, '');
}

/** Guess extension from a MIME type (`image/png` → `png`). */
function extFromMime(mime: string): string {
  const sub = mime.split('/')[1]?.split('+')[0]?.toLowerCase();
  if (sub === 'jpeg') return 'jpg';
  if (sub === 'svg+xml') return 'svg';
  return sub && /^[a-z0-9]+$/.test(sub) ? sub : 'png';
}

/** Collapse a queued message to one renderable line (first line, whitespace-squashed). */
function queuedPreview(message: string): string {
  return message.replace(/\s+/g, ' ').trim();
}

function ChoiceMenu({
  prompt,
  onPick,
}: {
  readonly prompt: LiveChoicePromptView;
  readonly onPick: (key: string, literal: boolean) => void;
}): React.JSX.Element {
  const composing = isFreeformChoiceSelected(prompt);
  const hint = prompt.multi
    ? '↑/↓ move · space toggle · enter select · esc cancel'
    : '↑/↓ move · 1-9 jump · enter select · esc cancel';
  const submitAfter = prompt.multi
    ? prompt.options.reduce((acc, o, i) => (o.checked !== null ? i : acc), -1)
    : -1;
  const submitCursor = prompt.multi && prompt.selected === null;

  const rows = prompt.options.flatMap((option, index) => {
    const isCursor = prompt.selected !== null && option.number === prompt.selected;
    const isComposeRow = composing && isCursor && isFreeformChoiceLabel(option.label);
    const checkedMark =
      option.checked === null ? null : (
        <span className="mds-composer__opt-check" data-checked={option.checked ? 'true' : 'false'} aria-hidden>
          {option.checked ? '✓' : ''}
        </span>
      );
    const row = (
      <li
        key={option.number}
        data-selected={isCursor ? 'true' : undefined}
        data-compose={isComposeRow ? 'true' : undefined}
        onClick={() => onPick(String(option.number), true)}
      >
        <span className="mds-composer__opt-num">{option.number}.</span>
        {checkedMark}
        <span className="mds-composer__opt-body">
          <span className="mds-composer__opt-label">
            {isComposeRow ? 'type something…' : option.label}
          </span>
          {option.description !== null && !isComposeRow ? (
            <span className="mds-composer__opt-desc">{option.description}</span>
          ) : null}
        </span>
      </li>
    );
    if (index === submitAfter) {
      return [
        row,
        <li
          key="submit"
          className="mds-composer__submit"
          data-selected={submitCursor ? 'true' : undefined}
          onClick={() => onPick('Enter', false)}
        >
          Submit
        </li>,
      ];
    }
    return [row];
  });

  return (
    <div className="mds-composer__prompt">
      <span className="mds-composer__prompt-q">{prompt.question}</span>
      <ol className="mds-composer__options">{rows}</ol>
      {submitAfter === -1 && prompt.multi ? (
        <button
          type="button"
          className="mds-composer__submit"
          data-selected={submitCursor ? 'true' : undefined}
          onClick={() => onPick('Enter', false)}
        >
          Submit
        </button>
      ) : null}
      <span className="mds-composer__prompt-hint">{prompt.footer ?? hint}</span>
    </div>
  );
}

export function ChatInput(): React.JSX.Element {
  const store = useAppStoreApi();
  const bus = useApplicationClient();
  const { chatInput, chatHistory, chatVim } = useComposerStores();
  const { openTicket, openHelp, openWorkflowLibrary } = useCreationDialogs();
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const vimMode = useAppStore((s) => s.settings.vimMode);
  const send = useAppStore((s) => s.actions.conversations.send);
  const sendKey = useAppStore((s) => s.actions.conversations.sendKey);
  const interrupt = useAppStore((s) => s.actions.conversations.interrupt);
  const runWorkflow = useAppStore((s) => s.actions.workflows.run);

  const imageDraft: ImageDraftStoreApi = useMemo(
    () => createImageDraftStore(bus, toastStore),
    [bus],
  );
  const drafts = useStoreWithEqualityFn(imageDraft, (s) => s.drafts, shallow);
  const [thumbUrls, setThumbUrls] = useState<Readonly<Record<string, string>>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const text = useChatInputStore((s) => s.text);
  const cursor = useChatInputStore((s) => s.cursor);
  const vimSubmode = useChatVimStore((s) => s.submode);
  const fieldRef = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null);
  const displayText = stripSpans(text);
  const imageChips = spanLabels(text);

  const agentId = selectActiveAgentId(conversations, roster, favorites);
  const livePrompt = agentId === null ? null : selectLiveChoicePrompt(conversations, agentId);
  const meta = selectConversationMeta(conversations, agentId);
  const isChoice = livePrompt !== null && agentId !== null;
  const isFreeform = isChoice && livePrompt !== null && isFreeformChoiceSelected(livePrompt);
  const canSend = agentId !== null && (text.length > 0 || imageChips.length > 0) && (!isChoice || isFreeform);
  const queued = meta.queuedMessage;
  const working = meta.liveState === 'working';

  // Seed murder-wide send history from transcripts (and reseed on transcript ref-change).
  useEffect(() => {
    chatHistory.getState().seed(selectUserHistory(store.getState().conversations));
    return store.subscribe((state, prev) => {
      if (state.conversations.transcripts !== prev.conversations.transcripts) {
        chatHistory.getState().seed(selectUserHistory(state.conversations));
      }
    });
  }, [store, chatHistory]);

  // Keep the native caret in sync when vim/history mutate the buffer (plain-text display offsets).
  useLayoutEffect(() => {
    const el = fieldRef.current;
    if (el === null || typeof el.setSelectionRange !== 'function') return;
    // Spans live outside the textarea; clamp cursor into the plain region.
    const plain = stripSpans(chatInput.getState().text);
    const sel = Math.min(cursor, plain.length);
    try {
      el.setSelectionRange(sel, sel);
    } catch {
      // Some input types reject setSelectionRange; ignore.
    }
  }, [cursor, text, chatInput]);

  const commandCtx = useMemo(
    () =>
      buildWebCommandCtx({
        store,
        bus,
        openTicket,
        openHelp,
        openWorkflows: (name) => openWorkflowLibrary(name),
      }),
    [store, bus, openTicket, openHelp, openWorkflowLibrary],
  );

  const revokeThumb = useCallback((id: string): void => {
    setThumbUrls((prev) => {
      const url = prev[id];
      if (url !== undefined) URL.revokeObjectURL(url);
      if (url === undefined) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  const attachImageBytes = useCallback(
    (bytes: Uint8Array, ext: string, previewBlob?: Blob): void => {
      const id = imageDraft.getState().paste(bytes, ext);
      // Keep marked spans as a prefix so the textarea only edits plain text.
      const labels = spanLabels(chatInput.getState().text);
      let prefixLen = 0;
      for (const chip of labels) {
        prefixLen += chip.id.length + 2;
      }
      const buf = chatInput.getState().buffer;
      chatInput.getState().setBuffer({ ...buf, cursor: prefixLen, desiredVisualColumn: null });
      chatInput.getState().insertImageSpan(id);
      if (previewBlob !== undefined) {
        const url = URL.createObjectURL(previewBlob);
        setThumbUrls((prev) => ({ ...prev, [id]: url }));
      }
    },
    [imageDraft, chatInput],
  );

  const attachImageFile = useCallback(
    async (file: File): Promise<void> => {
      if (!file.type.startsWith('image/')) return;
      const buf = new Uint8Array(await file.arrayBuffer());
      attachImageBytes(buf, extFromMime(file.type), file);
    },
    [attachImageBytes],
  );

  const removeImageChip = useCallback(
    (id: string): void => {
      imageDraft.getState().drop(id);
      revokeThumb(id);
      const cleaned = chatInput.getState().text.replace(SPAN_RE, (whole, spanId: string) =>
        spanId === id ? '' : whole,
      );
      chatInput.getState().setBuffer({
        text: cleaned,
        cursor: Math.min(chatInput.getState().cursor, stripSpans(cleaned).length),
        desiredVisualColumn: null,
      });
    },
    [imageDraft, chatInput, revokeThumb],
  );

  const syncFromField = (el: HTMLTextAreaElement | HTMLInputElement): void => {
    const plain = el.value;
    const sel = el.selectionStart ?? plain.length;
    // Preserve image spans (chips); textarea only edits the plain-text suffix.
    const labels = spanLabels(chatInput.getState().text);
    let prefix = '';
    for (const { id } of labels) {
      prefix += `\u{E000}${id}\u{E001}`;
    }
    if (chatInput.getState().historyIndex !== null) {
      chatInput.getState().clear();
    }
    chatInput.getState().setBuffer({
      text: prefix + plain,
      cursor: prefix.length + sel,
      desiredVisualColumn: null,
    });
  };

  const submitPipeline = useCallback((): void => {
    const state = store.getState();
    const activeId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
    const message = chatInput.getState().text;
    const result = processChatSubmit({
      message,
      agentId: activeId,
      workflowNames: new Set(state.workflows.items.map((w) => w.name)),
      templateRegistry: new Map(state.templates.items.map((t) => [t.name, t.body])),
      commandCtx,
      runWorkflow: (name, args) => {
        void runWorkflow(name, args);
      },
      send: (id, msg) => {
        void send(id, msg);
      },
      imageDraft: imageDraft.getState(),
      onUploading: () => {
        toastStore.getState().push('image still uploading…', { ttlMs: 4000 });
      },
    });
    if (result.kind === 'uploading') {
      return;
    }
    if (result.kind === 'send') {
      chatHistory.getState().record(result.message);
    }
    if (result.kind !== 'empty') {
      for (const id of result.spanIds) {
        imageDraft.getState().drop(id);
        revokeThumb(id);
      }
      chatInput.getState().clear();
    }
  }, [store, commandCtx, runWorkflow, send, chatInput, chatHistory, imageDraft, revokeThumb]);

  const submit = (): void => {
    if (isFreeform && agentId !== null) {
      void sendKey(agentId, `${chatInput.getState().text}\n`, true);
      chatInput.getState().clear();
      return;
    }
    if (isChoice) return;
    if (chatInput.getState().text.length === 0) {
      if (agentId !== null) void interrupt(agentId);
      return;
    }
    submitPipeline();
  };

  const onPaste = (e: ReactClipboardEvent<HTMLTextAreaElement>): void => {
    const items = e.clipboardData?.items;
    if (items === undefined) return;
    for (const item of items) {
      if (item.kind === 'file' && item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file !== null) {
          e.preventDefault();
          void attachImageFile(file);
        }
        return;
      }
    }
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLInputElement | HTMLTextAreaElement>): void => {
    if (isChoice && agentId !== null) {
      if (isFreeform) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          void sendKey(agentId, `${chatInput.getState().text}\n`, true);
          chatInput.getState().clear();
          return;
        }
        const nav = CHOICE_KEY_MAP[e.key];
        if (nav !== undefined && e.key !== 'Enter' && e.key !== 'Backspace' && e.key !== ' ') {
          e.preventDefault();
          chatInput.getState().clear();
          void sendKey(agentId, nav, false);
        }
        return;
      }
      const named = CHOICE_KEY_MAP[e.key];
      if (named !== undefined) {
        e.preventDefault();
        const key = e.key === 'Tab' && e.shiftKey ? 'BTab' : named;
        void sendKey(agentId, key, false);
        return;
      }
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        void sendKey(agentId, e.key, true);
      }
      return;
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }

    const { input, key } = keyFromDomEvent(e);
    const width = estimateContentWidth(
      fieldRef.current instanceof HTMLTextAreaElement ? fieldRef.current : null,
    );

    if (vimMode) {
      const vimState = chatVim.getState();
      if (vimState.submode === 'normal') {
        e.preventDefault();
        if (vimState.pending === null && (input === 'j' || input === 'k')) {
          const buf = chatInput.getState().buffer;
          const moved = input === 'k' ? visualUp(buf, width) : visualDown(buf, width);
          if (moved !== null) {
            chatInput.getState().setBuffer(moved);
          }
          return;
        }
        const effect = reduceVimNormal(
          chatInput.getState().buffer,
          input,
          key,
          vimState.pending,
          vimState.register,
        );
        applyVimEffect(chatInput, chatVim, effect);
        return;
      }
      if (key.escape) {
        e.preventDefault();
        chatVim.getState().setSubmode('normal');
        return;
      }
    }

    // History recall at visual edges (non-vim + vim insert).
    if (e.key === 'ArrowUp' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      const moved = visualUp(chatInput.getState().buffer, width);
      if (moved !== null) {
        e.preventDefault();
        chatInput.getState().setBuffer(moved);
      } else {
        e.preventDefault();
        chatInput.getState().historyPrev(chatHistory.getState().entries);
      }
      return;
    }
    if (e.key === 'ArrowDown' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      const moved = visualDown(chatInput.getState().buffer, width);
      if (moved !== null) {
        e.preventDefault();
        chatInput.getState().setBuffer(moved);
      } else {
        e.preventDefault();
        chatInput.getState().historyNext(chatHistory.getState().entries);
      }
      return;
    }
  };

  const showComposerField = !isChoice || isFreeform;
  const stateBits: string[] = [];
  if (isChoice) stateBits.push('choice');
  else if (working) stateBits.push('working');
  if (vimMode) stateBits.push(vimSubmode === 'normal' ? 'NORMAL' : 'INSERT');

  return (
    <div className="mds-composer">
      {queued !== null ? (
        <div className="mds-composer__queued">
          <span className="mds-composer__queued-mark">⏸</span>
          <span className="mds-composer__queued-label">queued</span>
          <span className="mds-composer__queued-msg">{queuedPreview(queued)}</span>
          {livePrompt === null ? (
            <span className="mds-composer__queued-hint">⏎ interrupt & send now</span>
          ) : null}
        </div>
      ) : null}
      <div className="mds-composer__meta">
        <span className="mds-composer__to">
          <span className="star">★</span>
          <span className="mds-composer__to-name">{agentId ?? 'no crow'}</span>
          {stateBits.length > 0 ? (
            <span className="mds-composer__state">· {stateBits.join(' · ')}</span>
          ) : null}
        </span>
        <span className="mds-composer__hints">
          {vimMode ? (
            <KeyHint
              chord={vimSubmode === 'normal' ? 'i' : 'Esc'}
              desc={vimSubmode === 'normal' ? 'insert' : 'normal'}
              tone="muted"
            />
          ) : null}
          <KeyHint chord="Enter" desc="send" tone="muted" />
          {showComposerField ? <KeyHint chord="S-Enter" desc="newline" tone="muted" /> : null}
        </span>
      </div>
      {isChoice && livePrompt !== null ? (
        <ChoiceMenu
          prompt={livePrompt}
          onPick={(k, literal) => {
            if (agentId !== null) void sendKey(agentId, k, literal);
          }}
        />
      ) : null}
      {imageChips.length > 0 ? (
        <ul className="mds-composer__drafts" aria-label="attached images">
          {imageChips.map(({ id, label }) => {
            const draft = drafts[id];
            const status = draft?.status ?? 'uploading';
            const thumb = thumbUrls[id];
            return (
              <li key={id} className="mds-composer__draft" data-status={status}>
                {thumb !== undefined ? (
                  <img className="mds-composer__draft-thumb" src={thumb} alt="" />
                ) : (
                  <span className="mds-composer__draft-ph" aria-hidden />
                )}
                <span className="mds-composer__draft-label">{label}</span>
                <span className="mds-composer__draft-status">{status}</span>
                <button
                  type="button"
                  className="mds-composer__draft-x"
                  aria-label={`remove ${label}`}
                  onClick={() => removeImageChip(id)}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
      {showComposerField ? (
        <Input
          multiline
          size="lg"
          id={CHAT_INPUT_ID}
          ref={fieldRef}
          placeholder={
            isFreeform
              ? 'type something…'
              : agentId === null
                ? 'select a crow to chat…'
                : `message ${agentId}…`
          }
          value={displayText}
          disabled={agentId === null}
          readOnly={vimMode && vimSubmode === 'normal'}
          className={vimMode && vimSubmode === 'normal' ? 'mds-input--vim-normal' : undefined}
          autoFocus={isFreeform ? true : undefined}
          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => {
            if (vimMode && vimSubmode === 'normal') return;
            syncFromField(e.target);
          }}
          onPaste={onPaste}
          onSelect={(e: React.SyntheticEvent<HTMLTextAreaElement>) => {
            const el = e.currentTarget;
            const sel = el.selectionStart;
            if (sel === null) return;
            const labels = spanLabels(chatInput.getState().text);
            let prefixLen = 0;
            for (const { id } of labels) {
              prefixLen += id.length + 2; // U+E000 + id + U+E001
            }
            const storeCursor = prefixLen + sel;
            if (storeCursor !== chatInput.getState().cursor) {
              chatInput.getState().setBuffer({
                text: chatInput.getState().text,
                cursor: storeCursor,
                desiredVisualColumn: null,
              });
            }
          }}
          onKeyDown={onKeyDown}
          trailing={
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                className="mds-composer__file"
                tabIndex={-1}
                aria-hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = '';
                  if (file !== undefined) void attachImageFile(file);
                }}
              />
              <IconButton
                label="attach image"
                size="md"
                disabled={agentId === null}
                onClick={() => fileInputRef.current?.click()}
              >
                <Icon name="plus" />
              </IconButton>
              <IconButton
                label="send"
                size="md"
                disabled={!canSend}
                onClick={submit}
                style={
                  canSend
                    ? { background: 'var(--accent)', color: 'var(--text-on-accent)' }
                    : undefined
                }
              >
                <Icon name="send" />
              </IconButton>
            </>
          }
        />
      ) : (
        <Input
          size="lg"
          placeholder="answer (keys forward to the agent)…"
          autoFocus
          onKeyDown={onKeyDown}
        />
      )}
    </div>
  );
}
