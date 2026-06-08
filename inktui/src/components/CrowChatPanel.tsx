/**
 * CrowChatPanel — chat panes for favorited crows (panel 0, C10 extension).
 *
 * Shows one or more chat panes, one per favorited crow. Favorited = history panel shown for it.
 * Per spec (Approach › Crows panel (0)):
 *  - collaborator favorited by default
 *  - rogue crows favorited on creation
 *  - ctrl+s while chatting a crow keeps that pane active (C11 seam — see below)
 *
 * Rule 2: ALL selection of which panes to show, ordering, and turn formatting lives in selectors
 * (`conversationsSelectors.ts` + `agentIdentity.ts`). This component receives pre-built view-models
 * and paints them.
 *
 * Rule 1: no bus knowledge, no role/ticketId inspection, no identity derivation here.
 * Rule 3: the send action (`actions.conversations.send`) is the only bus path.
 * Rule 5: keyboard input routes through the existing dispatcher (chat short-circuit). This
 * component does NOT add a `useInput` — that would violate the "one root useInput" constraint.
 * Text input is deferred to the ChatInput slot (where the root dispatcher routes raw chars when
 * chat is focused). The CrowChatPanel renders the history display; message sending uses
 * `actions.conversations.send` dispatched from the ChatInput-level or a send action.
 *
 * --- C11 seam note (for the manager) ---
 * ctrl+s "keep pane active" while chatting a crow:
 *  - C10 provides: `conversationsState.activePaneAgentId` slot + `setActivePaneAgentId(agentId)`
 *    action. The `send` action already sets `activePaneAgentId` to the agent after each send,
 *    so a sent-to pane stays pinned.
 *  - C11 is responsible for: the full starring/favorites prefs system
 *    (`tui.save_favorites`/`tui.load_favorites`), user-toggled persistent favorites, and wiring
 *    ctrl+s in the global dispatcher to call `setActivePaneAgentId` for the currently focused pane.
 *  - C11 can replace `isDefaultFavorited` with a prefs-backed lookup without changing C10's types.
 *
 * --- Exploratory tile layout ---
 * The plan marks the big/small chat-tile layout (`ctrl+h/l` promotes one to big) as exploratory
 * and says it "may split into a follow-up". C10 SKIPS it. The `activePaneAgentId` slot in the
 * conversations slice is the clean seam for a later tile-promotion feature — whoever implements it
 * can read/write that field to track which pane is "promoted to big".
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import type { AgentIdentity } from '../selectors/agentIdentity.js';
import {
  type ChatTurn,
  useConversationTurns,
  useFavoritesChatPanes,
} from '../selectors/conversationsSelectors.js';
import type { ConversationsState } from '../store/conversations/conversationsSlice.js';
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { RosterState } from '../store/roster/rosterSlice.js';

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** One chat turn line. Speaker determines the color. */
const TurnLine = memo(function TurnLine({ turn }: { readonly turn: ChatTurn }): React.JSX.Element {
  const color =
    turn.speaker === 'user'
      ? 'green'
      : turn.speaker === 'assistant'
        ? 'white'
        : turn.speaker === 'tool'
          ? 'yellow'
          : turn.speaker === 'plan'
            ? 'cyan'
            : turn.speaker === 'notice'
              ? 'red'
              : 'gray';

  // Multiline turns (tool_call, plan_update) — split on newline and render each line.
  const lines = turn.text.split('\n');
  return (
    <Box flexDirection="column">
      {lines.map((line, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: lines within one block have no stable id
        <Text key={i} color={color} wrap="truncate">
          {i === 0 ? `${turn.speaker === 'user' ? '›' : '·'} ${line}` : `  ${line}`}
        </Text>
      ))}
    </Box>
  );
});

/** One crow's chat pane: a bordered box with its label and turn history. */
const CrowChatPane = memo(function CrowChatPane({
  identity,
  conversations,
  isActive,
}: {
  readonly identity: AgentIdentity;
  readonly conversations: ConversationsState;
  readonly isActive: boolean;
}): React.JSX.Element {
  const turns = useConversationTurns(identity.agentId, conversations);

  // Show the last N turns to fit the pane.
  const MAX_TURNS = 20;
  const visibleTurns = turns.length > MAX_TURNS ? turns.slice(turns.length - MAX_TURNS) : turns;

  const kindLabel =
    identity.kind === 'collaborator'
      ? 'collab'
      : identity.kind === 'planner'
        ? 'planner'
        : identity.kind === 'rogue'
          ? 'rogue'
          : 'ticket';

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={isActive ? 'green' : 'gray'}
      paddingX={1}
      flexGrow={1}
    >
      <Text bold color={isActive ? 'green' : 'white'} wrap="truncate">
        {`${identity.label} `}
        <Text dimColor>{`[${kindLabel}]`}</Text>
      </Text>
      {visibleTurns.length === 0 ? (
        <Text dimColor>no history</Text>
      ) : (
        visibleTurns.map((turn, i) => <TurnLine key={turn.blockId ?? i} turn={turn} />)
      )}
    </Box>
  );
});

// ---------------------------------------------------------------------------
// CrowChatPanel
// ---------------------------------------------------------------------------

/**
 * The crow chat panels area — renders one pane per favorited crow.
 * Placed below the CrowsPanel in the right region, or rendered as a sibling.
 * Not a toggleable panel itself (no PanelId, no panel-store integration) — it appears whenever
 * there are favorited crows with history. The wrapping CrowsPanel or the App decides visibility.
 *
 * Rule 1: `React.memo` + narrow selector (`shallow` on roster + conversations).
 * Rule 2: `useFavoritesChatPanes` in the selector determines what to show.
 * Rule 3: send is dispatched through `actions.conversations.send`.
 * Rule 5: no `useInput` here.
 */
export const CrowChatPanel = memo(function CrowChatPanel(): React.JSX.Element | null {
  const roster: RosterState = useAppStore((s) => s.roster, shallow);
  const conversations: ConversationsState = useAppStore((s) => s.conversations, shallow);
  const favorites: FavoritesState = useAppStore((s) => s.favorites, shallow);
  const activePaneAgentId = useAppStore((s) => s.conversations.activePaneAgentId);

  // Rule 2: selector derives favorited panes in spec order (default-favorited + explicitly starred).
  const { panes } = useFavoritesChatPanes(roster, favorites);

  // send action ref — the only bus path for chat (rule 3). Not wired to a key here (rule 5:
  // no stray useInput); consumed by an external caller (ChatInput / C11 send seam) via
  // `useAppStore((s) => s.actions.conversations.send)`.

  if (panes.length === 0) {
    return null;
  }

  return (
    <Box flexDirection="column" flexGrow={1}>
      {panes.map((identity) => (
        <CrowChatPane
          key={identity.agentId}
          identity={identity}
          conversations={conversations}
          isActive={
            activePaneAgentId !== null
              ? activePaneAgentId === identity.agentId
              : identity.kind === 'collaborator' // default active = collaborator
          }
        />
      ))}
    </Box>
  );
});
