import { Text } from 'ink';
import { type JSX, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { META_SEP } from '../../components/glyphs.js';
import { useApplicationClient } from '../../hooks/useApplicationClient.js';
import { useAppStore } from '../../hooks/useAppStore.js';
import { type GotoIntent, useGotoLine } from '../../hooks/useGotoLine.js';
import {
  useEffectiveFocus,
  useInputStores,
  usePanelKeymap,
  usePaneScrollBus,
} from '../../hooks/useInputStores.js';
import { stageTranscriptFocusId } from '../../input/focusIds.js';
import { CHAT_FOCUS, selectResolvedFocus } from '../../input/focusStore.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import type { AgentIdentity } from '../../selectors/agentIdentity.js';
import { useConversationTurns } from '../../selectors/conversationsSelectors.js';
import { harnessModelFooter, worktreeLabel } from '../../selectors/harnessDisplay.js';
import type { AppStore } from '../../store/store.js';
import { matchReservedPaneNavigation, TerminalInputWriter } from '../../terminal/rawEditorInput.js';
import { adaptTerminalUpdate } from '../../terminalSurface/protocolAdapter.js';
import {
  HARNESS_TERMINAL_SIZING,
  type TerminalSurfaceUpdate,
  type TerminalViewportCommand,
  type TerminalViewportMetrics,
} from '../../terminalSurface/types.js';
import { useTheme } from '../../theme/themeStore.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import { usePaneGotoLineState } from './shared/usePaneGotoLineState.js';
import { usePaneScrollState } from './shared/usePaneScrollState.js';
import { TranscriptPane } from './TranscriptPane.js';

const TRANSCRIPT_SCROLL_STEP = 1;
const CHAT_NEAR_BOTTOM_THRESHOLD = 3;
const TMUX_WAITING_TEXT = '[waiting for tmux frame…]';
const TERMINAL_LEASE_RENEW_MS = 5_000;

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
  const { focus, modes } = useInputStores();
  const highlighted =
    presentation.focused || (activeRecipientTarget && effectiveFocus === CHAT_FOCUS);

  const defaultChatViewMode = useAppStore((current) => current.settings.defaultChatViewMode);
  const viewMode = state.conversations.paneViewModes[identity.agentId] ?? defaultChatViewMode;
  const turns = useConversationTurns(identity.agentId, state.conversations, viewMode);
  const [scrollUp, setScrollUp] = usePaneScrollState(focusId);
  const [gotoLine, setGotoLine] = usePaneGotoLineState(focusId);
  const [chatMetrics, setChatMetrics] = useState({ lineCount: 0, maxScrollUp: 0 });
  const [terminalViewportCommand, setTerminalViewportCommand] =
    useState<TerminalViewportCommand | null>(null);
  const [terminalViewportMetrics, setTerminalViewportMetrics] =
    useState<TerminalViewportMetrics | null>(null);
  const maxScrollUp = chatMetrics.maxScrollUp;
  const issueTerminalViewportCommand = useCallback(
    (
      command:
        | { readonly kind: 'pan'; readonly deltaColumns: number; readonly deltaRows: number }
        | { readonly kind: 'follow_cursor' },
    ) => {
      setTerminalViewportCommand((current) => ({
        ...command,
        sequence: (current?.sequence ?? 0) + 1,
      }));
    },
    [],
  );

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
        if (viewMode === 'tmux') {
          issueTerminalViewportCommand({
            kind: 'pan',
            deltaColumns: 0,
            deltaRows: direction === 'up' ? -amount : amount,
          });
          return;
        }
        setScrollUp((current) =>
          direction === 'up'
            ? Math.min(current + amount, maxScrollUpRef.current)
            : Math.max(current - amount, 0),
        );
      }),
    [focusId, issueTerminalViewportCommand, paneScroll, setScrollUp, viewMode],
  );
  useEffect(
    () =>
      paneScroll.subscribeTerminalViewport(focusId, (action) => {
        issueTerminalViewportCommand(action);
      }),
    [focusId, issueTerminalViewportCommand, paneScroll],
  );

  const bus = useApplicationClient();
  const [tmuxUpdate, setTmuxUpdate] = useState<TerminalSurfaceUpdate | null>(null);
  const [terminalInputError, setTerminalInputError] = useState<string | null>(null);
  const [terminalInputStatus, setTerminalInputStatus] = useState<
    'inactive' | 'acquiring' | 'interactive' | 'read_only'
  >('inactive');
  const terminalInputWriterRef = useRef<TerminalInputWriter | null>(null);
  const terminalInputGeneration = useRef(0);
  const terminalModeId = `harness-terminal:${focusId}`;
  useEffect(() => {
    if (viewMode !== 'tmux') {
      setTmuxUpdate(null);
      return;
    }
    const unsubscribe = bus.attachTerminal(
      identity.sessionId ?? identity.agentId,
      (terminalFrame) => {
        setTmuxUpdate(adaptTerminalUpdate(terminalFrame));
      },
    );
    return unsubscribe;
  }, [bus, identity.agentId, identity.sessionId, viewMode]);

  useEffect(() => {
    if (viewMode !== 'tmux' || !presentation.focused || identity.sessionId === undefined) {
      setTerminalInputStatus('inactive');
      return;
    }
    const sessionId = identity.sessionId;
    const generation = terminalInputGeneration.current + 1;
    terminalInputGeneration.current = generation;
    let lease: Awaited<ReturnType<typeof bus.openTerminalInput>> | null = null;
    let renewTimer: ReturnType<typeof setInterval> | undefined;
    let disposed = false;
    let acquiring = false;
    let reacquireAfterConnect = false;

    const relinquish = (showError?: string): void => {
      if (renewTimer !== undefined) {
        clearInterval(renewTimer);
        renewTimer = undefined;
      }
      const writer = terminalInputWriterRef.current;
      terminalInputWriterRef.current = null;
      writer?.close();
      modes.getState().exit(terminalModeId);
      if (lease !== null) {
        const releasing = lease;
        lease = null;
        void bus.closeTerminalInput(releasing).catch(() => {});
      }
      if (!disposed && showError !== undefined) {
        setTerminalInputError(showError);
        setTerminalInputStatus('read_only');
      }
    };

    const acquire = (): void => {
      if (disposed || acquiring || terminalInputWriterRef.current !== null) return;
      acquiring = true;
      setTerminalInputError(null);
      setTerminalInputStatus('acquiring');
      void bus
        .openTerminalInput(sessionId)
        .then((opened) => {
          acquiring = false;
          if (disposed || terminalInputGeneration.current !== generation) {
            void bus.closeTerminalInput(opened).catch(() => {});
            return;
          }
          lease = opened;
          let writer: TerminalInputWriter;
          writer = new TerminalInputWriter(
            bus,
            opened.streamId,
            opened.sessionId,
            opened.leaseId,
            opened.fence,
            () => {
              if (terminalInputWriterRef.current !== writer) return;
              relinquish('terminal input unavailable; pane is read-only');
            },
          );
          terminalInputWriterRef.current = writer;
          setTerminalInputStatus('interactive');
          modes.getState().enter({
            id: terminalModeId,
            presentation: 'inlayout',
            passThrough: true,
            captureCtrlC: true,
            restoreFocus: false,
            stdinRoute: {
              kind: 'terminal',
              isActive: () =>
                terminalInputWriterRef.current === writer &&
                selectResolvedFocus(focus).id === focusId,
              consumeReservedChord(buffer) {
                const match = matchReservedPaneNavigation(buffer);
                if (match.direction !== undefined && match.result.kind === 'matched') {
                  focus.getState().navigate(match.direction);
                }
                return match.result;
              },
              write(bytes) {
                if (terminalInputWriterRef.current === writer) writer.enqueue(bytes);
              },
            },
            render: () => null,
            keymap: [],
            onIntent() {},
            onUncaptured() {
              return selectResolvedFocus(focus).id === focusId;
            },
          });
          renewTimer = setInterval(() => {
            const current = lease;
            if (current === null) return;
            void bus
              .renewTerminalInput(current)
              .then((renewed) => {
                if (!disposed && lease === current) lease = renewed;
              })
              .catch(() => relinquish('terminal input lease expired; pane is read-only'));
          }, TERMINAL_LEASE_RENEW_MS);
        })
        .catch((cause: unknown) => {
          acquiring = false;
          if (disposed || terminalInputGeneration.current !== generation) return;
          setTerminalInputError(
            cause instanceof Error
              ? cause.message
              : 'terminal input unavailable; pane is read-only',
          );
          setTerminalInputStatus('read_only');
        });
    };

    const reconnectable = bus as typeof bus & {
      onConnect?: (listener: () => void) => () => void;
      onDisconnect?: (listener: () => void) => () => void;
    };
    const unhookDisconnect = reconnectable.onDisconnect?.(() => {
      reacquireAfterConnect = true;
      relinquish('terminal disconnected; reconnecting read-only');
    });
    const unhookConnect = reconnectable.onConnect?.(() => {
      if (!reacquireAfterConnect) return;
      reacquireAfterConnect = false;
      acquire();
    });
    acquire();

    return () => {
      disposed = true;
      terminalInputGeneration.current += 1;
      unhookConnect?.();
      unhookDisconnect?.();
      relinquish();
    };
  }, [
    bus,
    focus,
    focusId,
    identity.sessionId,
    modes,
    presentation.focused,
    terminalModeId,
    viewMode,
  ]);

  const terminalStatus =
    viewMode !== 'tmux'
      ? null
      : identity.sessionId === undefined || terminalInputStatus === 'read_only'
        ? 'read-only'
        : terminalInputStatus === 'interactive'
          ? 'interactive'
          : terminalInputStatus === 'acquiring'
            ? 'acquiring input'
            : 'not interactive';
  const terminalViewportLabel =
    viewMode === 'tmux' && terminalViewportMetrics?.cropped === true
      ? `viewport ${terminalViewportMetrics.viewportColumns}×${terminalViewportMetrics.viewportRows}/${terminalViewportMetrics.terminalColumns}×${terminalViewportMetrics.terminalRows} @${terminalViewportMetrics.offsetColumn},${terminalViewportMetrics.offsetRow} ${terminalViewportMetrics.followingCursor ? 'follow' : 'manual'}`
      : null;
  const terminalGeometryWarning =
    viewMode === 'tmux' && terminalViewportMetrics?.geometryMatchesPolicy === false
      ? `terminal geometry ${terminalViewportMetrics.terminalColumns}×${terminalViewportMetrics.terminalRows}; expected 220×50`
      : null;

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
            <Text color={theme.muted}>{` [${transcriptKindLabel(identity.kind)}]`}</Text>
            {goto.pending !== null && <Text color={theme.warning}>{` g${goto.pending}`}</Text>}
            {terminalInputError !== null && (
              <Text color={theme.warning}>{` [read-only: ${terminalInputError}]`}</Text>
            )}
            {terminalInputError === null && terminalStatus !== null && (
              <Text color={terminalStatus === 'interactive' ? theme.success : theme.muted}>
                {` [${terminalStatus}]`}
              </Text>
            )}
            {terminalViewportLabel !== null && (
              <Text color={theme.muted}>{` [${terminalViewportLabel}]`}</Text>
            )}
            {terminalGeometryWarning !== null && (
              <Text color={theme.warning}>{` [${terminalGeometryWarning}]`}</Text>
            )}
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
        tmuxUpdate={tmuxUpdate}
        tmuxWaitingText={TMUX_WAITING_TEXT}
        terminalSizingPolicy={HARNESS_TERMINAL_SIZING}
        terminalViewportCommand={terminalViewportCommand}
        onTerminalViewportChange={setTerminalViewportMetrics}
      />
    </AllocatedPaneFrame>
  );
});
