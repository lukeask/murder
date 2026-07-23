import { Text } from 'ink';
import { type JSX, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { META_SEP } from '../../components/glyphs.js';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useApplicationClient } from '../../hooks/useApplicationClient.js';
import { type GotoIntent, useGotoLine } from '../../hooks/useGotoLine.js';
import { useEffectiveFocus, usePanelKeymap, usePaneScrollBus } from '../../hooks/useInputStores.js';
import { stageTranscriptFocusId } from '../../input/focusIds.js';
import { CHAT_FOCUS } from '../../input/focusStore.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import type { AgentIdentity } from '../../selectors/agentIdentity.js';
import { useConversationTurns } from '../../selectors/conversationsSelectors.js';
import { harnessModelFooter, worktreeLabel } from '../../selectors/harnessDisplay.js';
import type { AppStore } from '../../store/store.js';
import { useTheme } from '../../theme/themeStore.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import { usePaneGotoLineState } from './shared/usePaneGotoLineState.js';
import { usePaneScrollState } from './shared/usePaneScrollState.js';
import { TranscriptPane } from './TranscriptPane.js';

const TRANSCRIPT_SCROLL_STEP = 1;
const CHAT_NEAR_BOTTOM_THRESHOLD = 3;
const TMUX_WAITING_TEXT = '[waiting for tmux frame…]';

type TranscriptScrollIntent = 'scrollUp' | 'scrollDown';

const EMPTY_TRANSCRIPT_KEYMAP: PanelKeymap<TranscriptScrollIntent | GotoIntent> = {
  keymap: [],
  onIntent() {},
};

export interface TranscriptControllerProps {
  readonly presentation: PanePresentation;
  readonly identity: AgentIdentity;
  readonly state: AppStore;
  readonly activeRecipientTarget: boolean;
}

function transcriptKindLabel(kind: AgentIdentity['kind']): string {
  switch (kind) {
    case 'collaborator':
      return 'collab';
    case 'planner':
      return 'planner';
    case 'rogue':
      return 'rogue';
    default:
      return 'ticket';
  }
}

function footerFor(state: AppStore, agentId: string): string | null {
  const row = state.roster.rows.find((candidate) => candidate.agentId === agentId);
  if (row === undefined) {
    return null;
  }
  return harnessModelFooter(row.harness, row.model, META_SEP);
}

function worktreeFor(state: AppStore, agentId: string): string | null {
  const row = state.roster.rows.find((candidate) => candidate.agentId === agentId);
  if (row === undefined) {
    return null;
  }
  return worktreeLabel(row.worktreePath ?? null);
}

export const TranscriptController = memo(function TranscriptController({
  presentation,
  identity,
  state,
  activeRecipientTarget,
}: TranscriptControllerProps): JSX.Element {
  const theme = useTheme();
  const focusId = stageTranscriptFocusId(identity.agentId);
  const effectiveFocus = useEffectiveFocus();
  const highlighted =
    presentation.focused || (activeRecipientTarget && effectiveFocus === CHAT_FOCUS);

  const defaultChatViewMode = useAppStore((current) => current.settings.defaultChatViewMode);
  const viewMode = state.conversations.paneViewModes[identity.agentId] ?? defaultChatViewMode;
  const turns = useConversationTurns(identity.agentId, state.conversations, viewMode);
  const [scrollUp, setScrollUp] = usePaneScrollState(focusId);
  const [gotoLine, setGotoLine] = usePaneGotoLineState(focusId);
  const [chatMetrics, setChatMetrics] = useState({ lineCount: 0, maxScrollUp: 0 });
  const maxScrollUp = chatMetrics.maxScrollUp;

  const prevLenRef = useRef<number | null>(null);
  const wasNearBottomRef = useRef(true);
  if (prevLenRef.current === null || chatMetrics.lineCount <= prevLenRef.current) {
    wasNearBottomRef.current = scrollUp <= CHAT_NEAR_BOTTOM_THRESHOLD;
  }
  useEffect(() => {
    const prevLen = prevLenRef.current;
    prevLenRef.current = chatMetrics.lineCount;
    if (prevLen === null) {
      return;
    }
    const delta = chatMetrics.lineCount - prevLen;
    if (delta <= 0) {
      setScrollUp((current) => Math.min(current, maxScrollUp));
      return;
    }
    if (wasNearBottomRef.current) {
      setScrollUp(0);
    } else {
      setScrollUp((current) => Math.min(current + delta, maxScrollUp));
    }
  }, [chatMetrics.lineCount, maxScrollUp, setScrollUp]);

  const jump = useCallback((line: number) => setGotoLine(line), [setGotoLine]);
  const goto = useGotoLine(jump);
  const keymap: PanelKeymap<TranscriptScrollIntent | GotoIntent> = useMemo(
    () => ({
      keymap: [
        ...goto.entries,
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'scrollUp',
          description: 'older',
        },
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'scrollDown',
          description: 'newer',
        },
      ],
      onIntent(intent) {
        if (goto.handle(intent)) {
          return;
        }
        goto.clear();
        if (intent === 'scrollUp') {
          setScrollUp((current) => Math.min(current + TRANSCRIPT_SCROLL_STEP, maxScrollUp));
        } else {
          setScrollUp((current) => Math.max(current - TRANSCRIPT_SCROLL_STEP, 0));
        }
      },
    }),
    [goto, maxScrollUp, setScrollUp],
  );
  usePanelKeymap(focusId, presentation.focused ? keymap : EMPTY_TRANSCRIPT_KEYMAP);

  const paneScroll = usePaneScrollBus();
  const maxScrollUpRef = useRef(maxScrollUp);
  maxScrollUpRef.current = maxScrollUp;
  useEffect(
    () =>
      paneScroll.subscribe(focusId, (direction, amount) => {
        setScrollUp((current) =>
          direction === 'up'
            ? Math.min(current + amount, maxScrollUpRef.current)
            : Math.max(current - amount, 0),
        );
      }),
    [focusId, paneScroll, setScrollUp],
  );

  const bus = useApplicationClient();
  const [tmuxFrame, setTmuxFrame] = useState('');
  useEffect(() => {
    if (viewMode !== 'tmux') {
      setTmuxFrame('');
      return;
    }
    const unsubscribe = bus.attachTerminal(identity.sessionId ?? identity.agentId, (terminalFrame) => {
      setTmuxFrame((current) =>
        terminalFrame.type === 'terminal.frame' && terminalFrame.reset
          ? terminalFrame.data
          : `${current}${terminalFrame.data}`,
      );
    });
    return unsubscribe;
  }, [bus, identity.agentId, identity.sessionId, viewMode]);

  const handleScrollUpChange = useCallback(
    (nextScrollUp: number) => {
      setScrollUp(nextScrollUp);
      setGotoLine(null);
    },
    [setGotoLine, setScrollUp],
  );

  const handleWindowMetricsChange = useCallback(
    (metrics: { readonly lineCount: number; readonly maxScrollUp: number }) => {
      setChatMetrics((current) =>
        current.lineCount === metrics.lineCount && current.maxScrollUp === metrics.maxScrollUp
          ? current
          : metrics,
      );
    },
    [],
  );

  return (
    <AllocatedPaneFrame id={focusId} presentation={presentation}>
      <TranscriptPane
        width={presentation.width}
        height={presentation.height}
        focused={highlighted}
        title={identity.label}
        titleExtra={
          <>
            <Text dimColor>{` [${transcriptKindLabel(identity.kind)}]`}</Text>
            {goto.pending !== null && <Text color={theme.warning}>{` g${goto.pending}`}</Text>}
          </>
        }
        footerLeft={footerFor(state, identity.agentId) ?? ''}
        footerRight={worktreeFor(state, identity.agentId) ?? ''}
        turns={turns}
        viewMode={viewMode}
        scrollUp={scrollUp}
        gotoLine={gotoLine}
        onScrollUpChange={handleScrollUpChange}
        onWindowMetricsChange={handleWindowMetricsChange}
        tmuxFrame={tmuxFrame}
        tmuxWaitingText={TMUX_WAITING_TEXT}
      />
    </AllocatedPaneFrame>
  );
});
