/**
 * Stage — center column: multi-pane transcript grid (+ optional doc/ticket column),
 * shared composer. Open panes come from favorites + paneOverrides (TUI parity); roster click
 * opens/focuses; each pane can close (button / Ctrl+W). Tickets default to a side column
 * (like docs); expand to full-stage takeover when requested.
 *
 * Per-pane view mode cycles verbose → condensed → tmux (TUIchat-3 / cyclePaneViewMode).
 * `paneViewModes[agent]=tmux` renders the terminal surface in that pane.
 */

import { selectActiveAgentId } from '@murder/ui-core/selectors/conversationsSelectors.js';
import type { ChatViewMode } from '@murder/ui-core/store/conversations/conversationsSlice.js';
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

/** Landscape + both doc/ticket open → two side columns; narrow → tabbed/stacked side slot. */
export function shouldDualSideColumns(bothSidesOpen: boolean, isNarrow: boolean): boolean {
  return bothSidesOpen && !isNarrow;
}

function paneViewMode(
  agentId: string,
  paneViewModes: Readonly<Record<string, string>>,
  defaultChatViewMode: DefaultChatViewMode,
): ChatViewMode {
  const stored = paneViewModes[agentId] ?? defaultChatViewMode;
  if (stored === 'verbose' || stored === 'condensed' || stored === 'tmux') {
    return stored;
  }
  return defaultChatViewMode;
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
  readonly chatViewMode: ChatViewMode;
  readonly onFocus: () => void;
  readonly onClose: () => void;
  readonly onViewMode: (mode: ChatViewMode) => void;
  readonly children: ReactNode;
}): React.JSX.Element {
  return (
    <div
      className={`mds-stage-pane${focused ? ' mds-stage-pane--focused' : ''}`}
      data-agent-id={pane.identity.agentId}
      data-focused={focused ? 'true' : 'false'}
      data-view-mode={chatViewMode}
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
            { id: 'tmux', label: 'Tmux' },
          ]}
          value={chatViewMode}
          onChange={(id) => onViewMode(id as ChatViewMode)}
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
  const ticketId = useAppStore((s) => s.ticketDetail.ticketId);
  const ticketOpen = ticketId !== null;
  const isNarrow = useMediaQuery(MOBILE_QUERY);
  const [tab, setTab] = useState<StageTab>('chat');
  /** Full-stage ticket takeover; default false = column beside transcripts (doc-column parity). */
  const [ticketExpanded, setTicketExpanded] = useState(false);
  /** Narrow dual-side: which surface fills the stacked side slot. */
  const [sideTab, setSideTab] = useState<'doc' | 'ticket'>('doc');

  useEffect(() => {
    if (!ticketOpen) setTicketExpanded(false);
  }, [ticketOpen]);

  useEffect(() => {
    if (docOpen) setSideTab('doc');
    else if (ticketOpen) setSideTab('ticket');
  }, [docOpen, ticketOpen]);

  const activeAgentId = selectActiveAgentId(conversations, roster, favorites);
  const openPanes = selectStageTranscriptPanes(conversations, roster, favorites);
  const { visible, overflow } = partitionStagePanes(openPanes, MAX_VISIBLE_TRANSCRIPT_PANES);

  const sideColumn = tab === 'chat' && (docOpen || (ticketOpen && !ticketExpanded));
  const orientation: StageOrientation = isNarrow ? 'portrait' : 'landscape';
  const layout = computeStageLayout(
    visible.map((p) => p.identity.agentId),
    sideColumn,
    orientation,
  );

  const sessionIdFor = useCallback(
    (agentId: string): string | null =>
      roster.rows.find((row) => row.agentId === agentId)?.sessionId ?? null,
    [roster.rows],
  );

  const terminalSessionId =
    activeAgentId === null ? null : sessionIdFor(activeAgentId);

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

  if (ticketOpen && ticketExpanded) {
    return (
      <div className="stage mds-stage mds-stage--overlay">
        <TicketDetail
          layoutActions={
            <IconButton
              size="sm"
              label="Pop out beside transcripts"
              title="Beside"
              onClick={() => setTicketExpanded(false)}
            >
              <Icon name="back" size={14} />
            </IconButton>
          }
        />
      </div>
    );
  }

  const emptyStage = visible.length === 0 && !docOpen && !ticketOpen;
  const sideOpen = docOpen || (ticketOpen && !ticketExpanded);
  const bothSides = docOpen && ticketOpen && !ticketExpanded;
  const dualSide = shouldDualSideColumns(bothSides, isNarrow);
  const sideStacked = bothSides && isNarrow;

  const ticketBeside = (
    <div className="mds-stage__doc-col mds-stage__doc-col--ticket">
      <TicketDetail
        layoutActions={
          <IconButton
            size="sm"
            label="Expand detail to full stage"
            title="Expand"
            onClick={() => setTicketExpanded(true)}
          >
            <Icon name="plus" size={14} />
          </IconButton>
        }
      />
    </div>
  );

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
        ) : emptyStage ? (
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
            data-dual-side={dualSide ? 'true' : 'false'}
          >
            {sideOpen ? (
              <div
                className={`mds-stage__side${
                  sideStacked
                    ? ' mds-stage__side--stacked'
                    : dualSide
                      ? ' mds-stage__side--dual'
                      : ''
                }`}
                data-side={sideStacked ? 'stacked' : dualSide ? 'dual' : 'single'}
              >
                {sideStacked ? (
                  <>
                    <div className="mds-stage__side-tabs">
                      <Tabs
                        tabs={[
                          { id: 'doc', label: 'Doc' },
                          { id: 'ticket', label: 'Ticket' },
                        ]}
                        value={sideTab}
                        onChange={(id) => setSideTab(id as 'doc' | 'ticket')}
                        aria-label="Side panel"
                      />
                    </div>
                    {sideTab === 'doc' ? (
                      <div className="mds-stage__doc-col">
                        <DocViewer />
                      </div>
                    ) : (
                      ticketBeside
                    )}
                  </>
                ) : (
                  <>
                    {docOpen ? (
                      <div className="mds-stage__doc-col">
                        <DocViewer />
                      </div>
                    ) : null}
                    {ticketOpen && !ticketExpanded ? ticketBeside : null}
                  </>
                )}
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
                      {mode === 'tmux' ? (
                        <TmuxFrameView sessionId={sessionIdFor(agentId)} />
                      ) : (
                        <ChatTranscript agentId={agentId} focused={focused} />
                      )}
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
