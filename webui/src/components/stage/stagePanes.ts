/**
 * Stage open-pane derivation for WebUI — mirrors TUI paneBridge open set (favorites + overrides +
 * ephemeral active) with a visible cap and overflow tabs.
 */

import { deriveAgentIdentity, type AgentIdentity } from '@murder/ui-core/selectors/agentIdentity.js';
import {
  isTranscriptPaneOpen,
  selectActiveAgentId,
  selectOpenTranscriptPanes,
  selectRecipientTargets,
} from '@murder/ui-core/selectors/conversationsSelectors.js';
import type { ConversationsState } from '@murder/ui-core/store/conversations/conversationsSlice.js';
import type { FavoritesState } from '@murder/ui-core/store/favorites/favoritesSlice.js';
import type { RosterState } from '@murder/ui-core/store/roster/rosterSlice.js';
import type { AppStoreApi } from '@murder/ui-core/store/store.js';

/** Max transcript columns rendered at once; the rest become overflow tabs. */
export const MAX_VISIBLE_TRANSCRIPT_PANES = 3;

export interface StageTranscriptPane {
  readonly identity: AgentIdentity;
  /** Locked open via favorites/overrides (not merely the ephemeral active). */
  readonly locked: boolean;
  /** True when this pane is the current send/focus target. */
  readonly current: boolean;
}

export interface StagePanePartition {
  /** Ordered open panes (locked + optional ephemeral active). */
  readonly open: readonly StageTranscriptPane[];
  /** Subset rendered in the grid (≤ {@link MAX_VISIBLE_TRANSCRIPT_PANES}). */
  readonly visible: readonly StageTranscriptPane[];
  /** Open panes not in `visible` — shown as overflow tabs. */
  readonly overflow: readonly StageTranscriptPane[];
}

function orderedTranscriptAgentIds(
  conversations: ConversationsState,
  roster: RosterState,
  favorites: FavoritesState,
  currentAgentId: string | null,
): readonly string[] {
  const ids = selectRecipientTargets(conversations, roster, favorites).map((i) => i.agentId);
  if (currentAgentId === null || ids.includes(currentAgentId)) {
    return ids;
  }
  return [...ids, currentAgentId];
}

/**
 * Derive the Stage's open transcript panes the same way TUI paneBridge does:
 * locked panes from {@link selectOpenTranscriptPanes}, plus the active agent as an ephemeral pane
 * unless explicitly closed (`paneOverrides.get(id) === false`).
 */
export function selectStageTranscriptPanes(
  conversations: ConversationsState,
  roster: RosterState,
  favorites: FavoritesState,
): readonly StageTranscriptPane[] {
  const currentAgentId = selectActiveAgentId(conversations, roster, favorites);
  const transcriptOrder = new Map(
    orderedTranscriptAgentIds(conversations, roster, favorites, currentAgentId).map(
      (agentId, index) => [agentId, index],
    ),
  );

  const lockedPanes = selectOpenTranscriptPanes(
    roster,
    favorites,
    conversations.paneOverrides,
  ).panes;
  const byAgent = new Map<string, StageTranscriptPane>();
  for (const identity of lockedPanes) {
    byAgent.set(identity.agentId, {
      identity,
      locked: true,
      current: identity.agentId === currentAgentId,
    });
  }

  if (
    currentAgentId !== null &&
    conversations.paneOverrides.get(currentAgentId) !== false &&
    !byAgent.has(currentAgentId)
  ) {
    const row = roster.rows.find((candidate) => candidate.agentId === currentAgentId);
    const identity = row === undefined ? null : deriveAgentIdentity(row);
    if (identity !== null) {
      byAgent.set(currentAgentId, {
        identity,
        locked: false,
        current: true,
      });
    }
  }

  return [...byAgent.values()].sort(
    (a, b) =>
      (transcriptOrder.get(a.identity.agentId) ?? Number.MAX_SAFE_INTEGER) -
        (transcriptOrder.get(b.identity.agentId) ?? Number.MAX_SAFE_INTEGER) ||
      a.identity.agentId.localeCompare(b.identity.agentId),
  );
}

/**
 * Cap open panes to {@link MAX_VISIBLE_TRANSCRIPT_PANES} for the grid, keeping the current target
 * visible when possible. Remaining open panes become overflow tabs.
 */
export function partitionStagePanes(
  open: readonly StageTranscriptPane[],
  maxVisible: number = MAX_VISIBLE_TRANSCRIPT_PANES,
): StagePanePartition {
  if (open.length <= maxVisible) {
    return { open, visible: open, overflow: [] };
  }

  const current = open.find((p) => p.current);
  const rest = open.filter((p) => !p.current);
  const visible: StageTranscriptPane[] = [];
  if (current !== undefined) {
    visible.push(current);
  }
  for (const pane of rest) {
    if (visible.length >= maxVisible) break;
    visible.push(pane);
  }
  // Preserve relative order from `open` among the visible set.
  const visibleIds = new Set(visible.map((p) => p.identity.agentId));
  const orderedVisible = open.filter((p) => visibleIds.has(p.identity.agentId));
  const overflow = open.filter((p) => !visibleIds.has(p.identity.agentId));
  return { open, visible: orderedVisible, overflow };
}

/**
 * Imperative toggle for the desktop keybind (modifier+w) — TUI `global.toggleTargetPane`.
 * Closes the active transcript when open; opens it when closed. With no active target and a doc
 * open, closes the doc.
 */
export function closeOrToggleActiveTranscriptPane(store: AppStoreApi): void {
  const state = store.getState();
  const agentId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
  if (agentId === null) {
    if (state.docView.open !== null) {
      state.actions.docView.close();
    }
    return;
  }
  const row = state.roster.rows.find((r) => r.agentId === agentId);
  const identity = row === undefined ? null : deriveAgentIdentity(row);
  const currentlyOpen =
    identity === null
      ? state.conversations.paneOverrides.get(agentId) === true
      : isTranscriptPaneOpen(identity, state.favorites, state.conversations.paneOverrides);
  state.actions.conversations.toggleTranscriptPane(agentId, currentlyOpen);
  if (currentlyOpen && state.conversations.activePaneAgentId === agentId) {
    const nextOverrides = new Map(state.conversations.paneOverrides);
    nextOverrides.set(agentId, false);
    const remaining = selectStageTranscriptPanes(
      { ...state.conversations, paneOverrides: nextOverrides },
      state.roster,
      state.favorites,
    );
    state.actions.conversations.setActivePaneAgentId(remaining[0]?.identity.agentId ?? null);
  } else if (!currentlyOpen) {
    state.actions.conversations.setActivePaneAgentId(agentId);
  }
}
