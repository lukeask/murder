/** ChatTranscript — chat bubbles via useConversationTurns; stick-to-bottom + goto-line + j/k scroll. */

import {
  useConversationTurns,
  type ChatTurn,
  type TurnSpeaker,
} from '@murder/ui-core/selectors/conversationsSelectors.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { stageTranscriptFocusId } from '@murder/ui-core/input/focusIds.js';
import { shallow } from 'zustand/shallow';
import { useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import { Avatar, StatusDot } from '../ds/index.js';
import { usePaneScrollState } from '../../composer/usePaneScrollState.js';
import { panelFocusStore } from '../../panelFocus.js';
import { GotoLineOverlay } from './GotoLineOverlay.js';
import { useStickToBottom } from './useStickToBottom.js';
import { useWebGotoLine } from './useWebGotoLine.js';

const CHAT_INPUT_ID = 'chat-composer-input';
const META_SPEAKERS = new Set<TurnSpeaker>(['notice', 'agent', 'unknown']);
const STRUCT_SPEAKERS = new Set<TurnSpeaker>(['tool', 'plan']);

const DELIVERY_LABEL: Partial<Record<NonNullable<ChatTurn['delivery']>, string>> = {
  sending: 'sending…',
  accepted: 'accepted',
  queued: 'queued',
  failed: 'failed',
  unknown: 'unconfirmed',
};

const DELIVERY_CLASS: Partial<Record<NonNullable<ChatTurn['delivery']>, string>> = {
  failed: 'mds-bubble--delivery-failed',
  unknown: 'mds-bubble--delivery-unknown',
};

function isComposerTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.id === CHAT_INPUT_ID) return true;
  return target.closest(`#${CHAT_INPUT_ID}`) !== null;
}

function isTypingField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.closest('[data-terminal-input="true"]') !== null) return true;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return target.isContentEditable;
}

export function ChatTranscript({
  agentId,
  focused = false,
}: {
  readonly agentId: string;
  /** True when this pane is the active chat target (stage focus for j/k). */
  readonly focused?: boolean;
}): React.JSX.Element {
  const conversations = useAppStore((s) => s.conversations, shallow);
  const defaultChatViewMode = useAppStore((s) => s.settings.defaultChatViewMode);
  const stored = conversations.paneViewModes[agentId] ?? defaultChatViewMode;
  // Stage routes tmux to TmuxFrameView; if we still render, treat as verbose chat.
  const viewMode = stored === 'tmux' ? 'verbose' : stored;
  const turns = useConversationTurns(agentId, conversations, viewMode);
  const threadRef = useRef<HTMLDivElement>(null);
  const turnRefs = useRef<(HTMLDivElement | null)[]>([]);
  const focusLineRef = useRef(0);
  const scrollPaneId = stageTranscriptFocusId(agentId);
  const [storedScroll, setStoredScroll] = usePaneScrollState(scrollPaneId);

  const { onScroll: onStickScroll, restoreScrollTop } = useStickToBottom(threadRef, turns.length);

  const onScroll = useCallback((): void => {
    onStickScroll();
    const el = threadRef.current;
    if (el !== null) setStoredScroll(el.scrollTop);
  }, [onStickScroll, setStoredScroll]);

  // Re-apply snapshotted scrollTop after remount / workspace hydrate.
  useLayoutEffect(() => {
    if (turns.length === 0) return;
    const el = threadRef.current;
    if (el === null) return;
    if (Math.abs(el.scrollTop - storedScroll) <= 1) return;
    restoreScrollTop(storedScroll);
  }, [agentId, restoreScrollTop, storedScroll, turns.length]);

  const jump = useCallback(
    (line: number) => {
      const count = turnRefs.current.length;
      if (count === 0) return;
      const index = Math.min(Math.max(line, 1), count) - 1;
      focusLineRef.current = index;
      turnRefs.current[index]?.scrollIntoView({ block: 'start' });
      const el = threadRef.current;
      if (el !== null) setStoredScroll(el.scrollTop);
    },
    [setStoredScroll],
  );
  const goto = useWebGotoLine(jump, focused);

  // While composing, route wheel to the active transcript (TUI focus-based wheel parity).
  useEffect(() => {
    if (!focused) return;
    const onWheel = (e: WheelEvent): void => {
      if (!isComposerTarget(e.target)) return;
      const el = threadRef.current;
      if (el === null) return;
      e.preventDefault();
      el.scrollTop += e.deltaY;
    };
    window.addEventListener('wheel', onWheel, { passive: false });
    return () => window.removeEventListener('wheel', onWheel);
  }, [focused]);

  // j/k step turns when this pane is focused, no rail panel focus, not typing in composer.
  useEffect(() => {
    if (!focused || turns.length === 0) return;
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (goto.pending !== null) return;
      if (panelFocusStore.getState().focusedId !== null) return;
      if (isComposerTarget(e.target) || isTypingField(e.target)) return;
      if (e.key !== 'j' && e.key !== 'k' && e.key !== 'ArrowDown' && e.key !== 'ArrowUp') {
        return;
      }
      e.preventDefault();
      const count = turnRefs.current.length;
      if (count === 0) return;
      const delta = e.key === 'j' || e.key === 'ArrowDown' ? 1 : -1;
      const next = Math.min(Math.max(focusLineRef.current + delta, 0), count - 1);
      focusLineRef.current = next;
      turnRefs.current[next]?.scrollIntoView({ block: 'nearest' });
      const el = threadRef.current;
      if (el !== null) setStoredScroll(el.scrollTop);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [focused, turns.length, goto.pending, setStoredScroll]);

  if (turns.length === 0) {
    return <div className="mds-stage__empty">No messages yet.</div>;
  }

  return (
    <div className="mds-thread-wrap">
      <div className="mds-thread" ref={threadRef} onScroll={onScroll}>
        {turns.map((turn, i) => (
          <div
            key={turn.blockId ?? `t${i}`}
            ref={(el) => {
              turnRefs.current[i] = el;
            }}
            data-turn-line={i + 1}
          >
            <Turn turn={turn} agentId={agentId} />
          </div>
        ))}
      </div>
      <GotoLineOverlay pending={goto.pending} />
    </div>
  );
}

function Turn({ turn, agentId }: { readonly turn: ChatTurn; readonly agentId: string }): React.JSX.Element {
  if (turn.isLivePrompt === true) {
    return (
      <div className="mds-work">
        <StatusDot status="running" pulse label="running" />
      </div>
    );
  }

  if (META_SPEAKERS.has(turn.speaker)) {
    return (
      <div className="mds-msg mds-msg--meta">
        <span className="mds-meta-chip">{turn.text}</span>
      </div>
    );
  }

  const isUser = turn.speaker === 'user';
  const struct = STRUCT_SPEAKERS.has(turn.speaker);
  const delivery = turn.delivery;
  const deliveryClass =
    delivery === undefined
      ? ''
      : ` ${DELIVERY_CLASS[delivery] ?? 'mds-bubble--delivery-pending'}`;
  const label = delivery === undefined ? null : (DELIVERY_LABEL[delivery] ?? null);

  return (
    <div className={`mds-msg ${isUser ? 'mds-msg--user' : 'mds-msg--crow'}`}>
      {isUser ? null : <Avatar name={agentId} size="md" />}
      <div className="mds-msg__col">
        {isUser ? null : <span className="mds-msg__role">{agentId}</span>}
        <div
          className={`mds-bubble ${isUser ? 'mds-bubble--user' : 'mds-bubble--crow'}${
            struct ? ' mds-bubble--struct' : ''
          }${deliveryClass}`}
        >
          {turn.text}
          {label === null ? null : <span className="mds-bubble__delivery">{label}</span>}
        </div>
      </div>
    </div>
  );
}
