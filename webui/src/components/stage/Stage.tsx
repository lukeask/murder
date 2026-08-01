/**
 * Stage — center column: multi-pane transcript grid (+ optional doc/ticket column),
 * shared composer. Open panes come from favorites + paneOverrides (TUI parity); roster click
 * opens/focuses; each pane can close (button / Ctrl+W). Tickets default to a side column
 * (like docs); expand to full-stage takeover when requested.
 */

import { selectActiveAgentId } from '@murder/ui-core/selectors/conversationsSelectors.js';
import type { DefaultChatViewMode } from '@murder/ui-core/store/settings/settingsSlice.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { Icon, IconButton, Tabs } from '../ds/index.js';
import { ChatTranscript } from './ChatTranscript.js';
import { ChatInput } from './ChatInput.js';
import { TmuxFrameView } from './TmuxFrameView.js';
import { DocViewer } from './DocViewer.js';
import { TicketDetail } from './TicketDetail.js';
import { computeStageLayout, type StageOrientation } from './stageTiling.js';
import {
  MAX_VISIBLE_TRANSCRIPT_PANES,
  partitionStagePanes,
  selectStageTranscriptPanes,
  type StageTranscriptPane,
} from './stagePanes.js';
import { useMediaQuery, MOBILE_QUERY } from '../../useMediaQuery.js';

type StageTab = 'chat' | 'terminal';

function paneViewMode(
  agentId: string,
  paneViewModes: Readonly<Record<string, string>>,
  defaultChatViewMode: DefaultChatViewMode,
): DefaultChatViewMode {
  const stored = paneViewModes[agentId] ?? defaultChatViewMode;
  return stored === 'tmux' ? 'verbose' : (stored as DefaultChatViewMode);
}

function TranscriptPaneChrome({
  pane,
  focused,
  chatViewMode,
  onFocus,
  onClose,
  onViewMode,
  children,
}: {
  readonly pane: StageTranscriptPane;
  readonly focused: boolean;
  readonly chatViewMode: DefaultChatViewMode;
  readonly onFocus: () => void;
  readonly onClose: () => void;
  readonly onViewMode: (mode: DefaultChatViewMode) => void;
  readonly children: ReactNode;
}): React.JSX.Element {
  return (
    <div
      className={`mds-stage-pane${focused ? ' mds-stage-pane--focused' : ''}`}
      data-agent-id={pane.identity.agentId}
      data-focused={focused ? 'true' : 'false'}
      onClick={onFocus}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onFocus();
        }
      }}
      role="group"
      aria-label={`Transcript ${pane.identity.agentId}`}
      tabIndex={0}
    >
      <div className="mds-stage-pane__chrome">
        <span className="mds-stage-pane__title">
          {focused ? <span className="star">★</span> : null}
          {pane.identity.agentId}
        </span>
        <Tabs
          className="mds-stage__view-mode"
          variant="pill"
          tabs={[
            { id: 'verbose', label: 'Verbose' },
            { id: 'condensed', label: 'Condensed' },
          ]}
          value={chatViewMode}
          onChange={(id) => onViewMode(id as DefaultChatViewMode)}
          aria-label={`Chat view mode for ${pane.identity.agentId}`}
        />
        <IconButton
          size="sm"
          label={`Close pane ${pane.identity.agentId}`}
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
        >
          <Icon name="x" size={14} />
        </IconButton>
      </div>
      <div className="mds-stage-pane__body">{children}</div>
    </div>
  );
}

export function Stage(): React.JSX.Element {
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const defaultChatViewMode = useAppStore((s) => s.settings.defaultChatViewMode);
  const setPaneViewMode = useAppStore((s) => s.actions.conversations.setPaneViewMode);
  const setActivePane = useAppStore((s) => s.actions.conversations.setActivePaneAgentId);
  const setTranscriptPaneOpen = useAppStore((s) => s.actions.conversations.setTranscriptPaneOpen);
  const docOpen = useAppStore((s) => s.docView.open !== null);
  const ticketOpen = useAppStore((s) => s.ticketDetail.ticketId !== null);
  const isNarrow = useMediaQuery(MOBILE_QUERY);
  const [tab, setTab] = useState<StageTab>('chat');

  const activeAgentId = selectActiveAgentId(conversations, roster, favorites);
  const openPanes = selectStageTranscriptPanes(conversations, roster, favorites);
  const { visible, overflow } = partitionStagePanes(openPanes, MAX_VISIBLE_TRANSCRIPT_PANES);

  const orientation: StageOrientation = isNarrow ? 'portrait' : 'landscape';
  const layout = computeStageLayout(
    visible.map((p) => p.identity.agentId),
    docOpen && tab === 'chat',
    orientation,
  );

  const terminalSessionId =
    activeAgentId === null
      ? null
      : (roster.rows.find((row) => row.agentId === activeAgentId)?.sessionId ?? null);

  const closePane = useCallback(
    (agentId: string): void => {
      setTranscriptPaneOpen(agentId, false);
      if (conversations.activePaneAgentId === agentId) {
        const remaining = openPanes
          .map((p) => p.identity.agentId)
          .filter((id) => id !== agentId);
        setActivePane(remaining[0] ?? null);
      }
    },
    [conversations.activePaneAgentId, openPanes, setActivePane, setTranscriptPaneOpen],
  );

  if (ticketOpen) {
    return (
      <div className="stage mds-stage mds-stage--overlay">
        <TicketDetail />
      </div>
    );
  }

  return (
    <div className="stage mds-stage">
      <div className="mds-stage__tabs">
        <Tabs
          tabs={[
            { id: 'chat', label: 'Chat' },
            { id: 'terminal', label: 'Terminal' },
          ]}
          value={tab}
          onChange={(id) => setTab(id as StageTab)}
        />
        {overflow.length > 0 ? (
          <div className="mds-stage__overflow" role="tablist" aria-label="Overflow panes">
            {overflow.map((pane) => (
              <button
                key={pane.identity.agentId}
                type="button"
                className={`mds-stage__overflow-tab${
                  pane.current ? ' mds-stage__overflow-tab--active' : ''
                }`}
                onClick={() => {
                  setActivePane(pane.identity.agentId);
                  setTranscriptPaneOpen(pane.identity.agentId, true);
                }}
              >
                {pane.identity.agentId}
              </button>
            ))}
          </div>
        ) : null}
        {activeAgentId !== null ? (
          <span className="mds-stage__target">
            <span className="star">★</span>
            {activeAgentId}
          </span>
        ) : null}
      </div>

      <div className="mds-stage__body">
        {tab === 'terminal' ? (
          activeAgentId === null ? (
            <div className="mds-stage__empty">
              Select a crow from the roster to watch its terminal.
            </div>
          ) : (
            <TmuxFrameView sessionId={terminalSessionId} />
          )
        ) : visible.length === 0 && !docOpen ? (
          <div className="mds-stage__empty">Select a crow from the roster to start chatting.</div>
        ) : (
          <div
            className={`mds-stage__grid mds-stage__grid--${orientation}`}
            style={
              {
                '--stage-doc-weight': String(layout.docWeight),
                '--stage-transcript-weight': String(layout.transcriptWeight),
              } as CSSProperties
            }
            data-columns={layout.columns}
            data-pane-count={visible.length}
          >
            {docOpen ? (
              <div className="mds-stage__doc-col">
                <DocViewer />
              </div>
            ) : null}
            {visible.length > 0 ? (
              <div
                className="mds-stage__transcripts"
                style={{ '--stage-cols': String(layout.columns) } as CSSProperties}
              >
                {visible.map((pane) => {
                  const agentId = pane.identity.agentId;
                  const focused = agentId === activeAgentId;
                  const mode = paneViewMode(
                    agentId,
                    conversations.paneViewModes,
                    defaultChatViewMode,
                  );
                  return (
                    <TranscriptPaneChrome
                      key={agentId}
                      pane={pane}
                      focused={focused}
                      chatViewMode={mode}
                      onFocus={() => setActivePane(agentId)}
                      onClose={() => closePane(agentId)}
                      onViewMode={(m) => setPaneViewMode(agentId, m)}
                    >
                      <ChatTranscript agentId={agentId} />
                    </TranscriptPaneChrome>
                  );
                })}
              </div>
            ) : null}
          </div>
        )}
      </div>
      <ChatInput />
    </div>
  );
}
