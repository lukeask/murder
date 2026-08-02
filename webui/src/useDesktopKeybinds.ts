/** Desktop keyboard shortcuts for the web cockpit (panels, chat, creation dialogs, stage chords).
 *
 * Out of scope for the ui-core GLOBAL_RULES refactor: this hook duplicates command knowledge as a
 * second if-chain over raw `e.key` literals with no ResolvedBindings or GLOBAL_SCOPE. Sharing with
 * the browser would need a normalized chord type produced before matching — a separate change. */

import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { panelForDigit } from '@murder/ui-core/input/panels.js';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import { deriveAgentIdentity } from '@murder/ui-core/selectors/agentIdentity.js';
import {
  selectActiveAgentId,
  selectCycledRecipientTarget,
  selectFavoriteTranscriptPanes,
  selectOpenTranscriptPanes,
} from '@murder/ui-core/selectors/conversationsSelectors.js';
import type { SettingsModifier } from '@murder/ui-core/store/settings/settingsSlice.js';
import { murderConfirmStore } from '@murder/ui-core/store/murder/murderConfirmStore.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { useEffect } from 'react';
import { useComposerStores } from './composer/ComposerStoresProvider.js';
import { toWorkspaceStores } from './composer/createComposerStores.js';
import {
  workspaceJump,
  workspaceNext,
  workspacePrev,
} from './composer/workspaceActions.js';
import type { CreationDialogsApi } from './creationDialogs.js';
import { closeOrToggleActiveTranscriptPane } from './components/stage/stagePanes.js';
import { togglePanelVisibility } from './panelVisibility.js';
import { directionFromVimKey, hopPanelFocus, panelFocusStore } from './panelFocus.js';

const CHAT_INPUT_ID = 'chat-composer-input';

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.closest('[data-terminal-input="true"]') !== null) {
    return true;
  }
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
    return true;
  }
  return target.isContentEditable;
}

function commandModifierDown(e: KeyboardEvent, modifier: SettingsModifier): boolean {
  const alt = e.altKey;
  const ctrl = e.ctrlKey || e.metaKey;
  if (modifier === 'alt') return alt && !ctrl;
  if (modifier === 'ctrl') return ctrl && !alt;
  return alt || ctrl;
}

function scrollPanelIntoView(panelId: PanelId | 'settings'): void {
  const el = document.querySelector(`[data-panel-id="${panelId}"]`);
  el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function focusChatInput(): void {
  panelFocusStore.getState().clear();
  const input = document.getElementById(CHAT_INPUT_ID);
  if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) {
    input.focus();
    input.select();
  }
}

/** Wire global desktop chords on `document` while the desktop shell is mounted. */
export function useDesktopKeybinds(
  enabled: boolean,
  {
    openSpawn,
    openPlan,
    openHelp,
    openNoteCapture,
    openWorkflowLibrary,
  }: CreationDialogsApi,
): void {
  const storeApi = useAppStoreApi();
  const composer = useComposerStores();
  const { panels } = composer;

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const toggleTargetGroupHandler = (): void => {
      const state = storeApi.getState();
      const activeAgentId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
      const lockedVisibleTargetIds = selectOpenTranscriptPanes(
        state.roster,
        state.favorites,
        state.conversations.paneOverrides,
      ).panes.map((pane) => pane.agentId);
      const locked = new Set(lockedVisibleTargetIds);
      const favoriteOnlyTargetIds = selectFavoriteTranscriptPanes(state.roster, state.favorites)
        .panes.map((pane) => pane.agentId)
        .filter((agentId) => !locked.has(agentId));
      const destination = locked.has(activeAgentId ?? '')
        ? (favoriteOnlyTargetIds[0] ?? null)
        : (lockedVisibleTargetIds[0] ?? null);
      if (destination !== null) {
        state.actions.conversations.setActivePaneAgentId(destination);
      }
    };

    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.repeat) {
        return;
      }

      // Plain ctrl+n → quick note (before typing-target bail so it works while composing).
      if (
        e.key.toLowerCase() === 'n' &&
        (e.ctrlKey || e.metaKey) &&
        !e.altKey &&
        !e.shiftKey
      ) {
        e.preventDefault();
        openNoteCapture();
        return;
      }

      // Plain ctrl+j → toggleTargetGroup (chat-scoped; works while composing like TUI).
      if (
        e.key.toLowerCase() === 'j' &&
        (e.ctrlKey || e.metaKey) &&
        !e.altKey &&
        !e.shiftKey
      ) {
        const panelFocused = panelFocusStore.getState().focusedId !== null;
        if (panelFocused) {
          return;
        }
        e.preventDefault();
        toggleTargetGroupHandler();
        return;
      }

      // Plain ctrl+m → arm murder confirm for the active crow (browser delivers ctrl+m cleanly).
      if (
        e.key.toLowerCase() === 'm' &&
        (e.ctrlKey || e.metaKey) &&
        !e.altKey &&
        !e.shiftKey
      ) {
        const typing = isTypingTarget(e.target);
        const inComposer =
          e.target instanceof HTMLElement && e.target.id === CHAT_INPUT_ID;
        if (typing && !inComposer) {
          return;
        }
        e.preventDefault();
        const state = storeApi.getState();
        const agentId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
        if (agentId === null) {
          toastStore.getState().push('no crow to murder', { ttlMs: 4000 });
          return;
        }
        const row = state.roster.rows.find((r) => r.agentId === agentId);
        const identity = row === undefined ? null : deriveAgentIdentity(row);
        murderConfirmStore.getState().arm({ agentId, name: identity?.label ?? agentId });
        return;
      }

      if (isTypingTarget(e.target)) {
        return;
      }

      // Bare `?` opens help (TUI `global.keyHelp`) — not modifier-gated, suppressed while typing.
      if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        openHelp();
        return;
      }

      const { settings, conversations, roster, favorites, actions } = storeApi.getState();
      const modifier = settings.modifier;
      if (!commandModifierDown(e, modifier)) {
        return;
      }

      const key = e.key.toLowerCase();

      // `<Cmd>+Shift+J/K` — workspace next/prev (bindings.ts workspace.next / workspace.prev).
      if (e.shiftKey && (key === 'j' || key === 'k')) {
        e.preventDefault();
        const wsStores = toWorkspaceStores(composer, storeApi);
        if (key === 'j') workspaceNext(wsStores);
        else workspacePrev(wsStores);
        return;
      }

      // `<Cmd>+Shift+1–9` — jump to workspace N (bindings.ts workspace.jump.N).
      if (e.shiftKey && key.length === 1 && key >= '1' && key <= '9') {
        e.preventDefault();
        workspaceJump(toWorkspaceStores(composer, storeApi), Number(key) - 1);
        return;
      }

      // Modifier + digit → toggle panel visibility (ctrl/alt+1–0).
      if (key.length === 1 && key >= '0' && key <= '9' && !e.shiftKey) {
        const panelId = panelForDigit(key);
        if (panelId !== null) {
          e.preventDefault();
          togglePanelVisibility(panels, panelId);
        }
        return;
      }

      if (key === ' ' || e.code === 'Space') {
        e.preventDefault();
        focusChatInput();
        return;
      }

      if (key === 'o') {
        e.preventDefault();
        scrollPanelIntoView('settings');
        return;
      }

      if (key === 's') {
        e.preventDefault();
        openSpawn();
        return;
      }

      if (key === 'p') {
        e.preventDefault();
        openPlan();
        return;
      }

      // Alt+t → cycle chat view (TUIchat-3; ticket is `:ticket` only).
      if (key === 't') {
        e.preventDefault();
        const agentId = selectActiveAgentId(conversations, roster, favorites);
        if (agentId === null) {
          toastStore.getState().push('no transcript pane to cycle', { ttlMs: 4000 });
          return;
        }
        actions.conversations.cyclePaneViewMode(agentId);
        return;
      }

      // Alt+g → workflow library (TUI wires `global.workflowEditor` to the library open).
      if (key === 'g') {
        e.preventDefault();
        openWorkflowLibrary();
        return;
      }

      // Modifier+w → toggle transcript pane (TUI global.toggleTargetPane); closes doc when no target.
      if (key === 'w') {
        e.preventDefault();
        closeOrToggleActiveTranscriptPane(storeApi);
        return;
      }

      // Modifier+h/j/k/l — chat focus: h/l cycle recipients; otherwise geometric panel/stage hops.
      // j/k always hop (recipient cycle is h/l only). Matches TUI dispatcher gate.
      const direction = directionFromVimKey(key);
      if (direction !== null) {
        const panelFocused = panelFocusStore.getState().focusedId !== null;
        const cycleTargets =
          !panelFocused && (key === 'h' || key === 'l');
        if (cycleTargets) {
          const result = selectCycledRecipientTarget(
            conversations,
            roster,
            favorites,
            key === 'h' ? -1 : 1,
          );
          if (result !== null) {
            e.preventDefault();
            actions.conversations.setActivePaneAgentId(result.agentId);
          }
          return;
        }
        e.preventDefault();
        hopPanelFocus(direction);
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [
    enabled,
    storeApi,
    panels,
    composer,
    openSpawn,
    openPlan,
    openHelp,
    openNoteCapture,
    openWorkflowLibrary,
  ]);
}

export { CHAT_INPUT_ID };
