/**
 * Desktop keyboard shortcuts for the web cockpit — mirrors the Ink dispatcher's global chords that
 * have a sensible web analogue (focus chat, scroll a panel into view, cycle the recipient target).
 * Respects the persisted command modifier from settings (`alt` / `ctrl` / `both`).
 */

import { useAppStoreApi } from '@core/hooks/useAppStore.js';
import { panelForDigit } from '@core/input/panels.js';
import type { PanelId } from '@core/input/panels.js';
import { selectCycledRecipientTarget } from '@core/selectors/conversationsSelectors.js';
import type { SettingsModifier } from '@core/store/settings/settingsSlice.js';
import { useEffect } from 'react';

const CHAT_INPUT_ID = 'chat-composer-input';

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
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
  const input = document.getElementById(CHAT_INPUT_ID);
  if (input instanceof HTMLInputElement) {
    input.focus();
    input.select();
  }
}

/** Wire global desktop chords on `document` while the desktop shell is mounted. */
export function useDesktopKeybinds(enabled: boolean): void {
  const storeApi = useAppStoreApi();

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.repeat || isTypingTarget(e.target)) {
        return;
      }

      const { settings, conversations, roster, favorites, actions } = storeApi.getState();
      const modifier = settings.modifier;
      if (!commandModifierDown(e, modifier)) {
        return;
      }

      // Modifier + digit → scroll the bound panel into view (ctrl/alt+1–0).
      if (e.key.length === 1 && e.key >= '0' && e.key <= '9' && !e.shiftKey) {
        const panelId = panelForDigit(e.key);
        if (panelId !== null) {
          e.preventDefault();
          scrollPanelIntoView(panelId);
        }
        return;
      }

      if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault();
        focusChatInput();
        return;
      }

      if (e.key === 'o' || e.key === 'O') {
        e.preventDefault();
        scrollPanelIntoView('settings');
        return;
      }

      if (e.key === 'h' || e.key === 'H') {
        const result = selectCycledRecipientTarget(conversations, roster, favorites, -1);
        if (result !== null) {
          e.preventDefault();
          actions.conversations.setActivePaneAgentId(result.agentId);
        }
        return;
      }

      if (e.key === 'l' || e.key === 'L') {
        const result = selectCycledRecipientTarget(conversations, roster, favorites, 1);
        if (result !== null) {
          e.preventDefault();
          actions.conversations.setActivePaneAgentId(result.agentId);
        }
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [enabled, storeApi]);
}

export { CHAT_INPUT_ID };
