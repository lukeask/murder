/** Desktop keyboard shortcuts for the web cockpit (focus chat, scroll panels, cycle target, open creation dialogs). */

import { useAppStoreApi } from '@core/hooks/useAppStore.js';
import { panelForDigit } from '@core/input/panels.js';
import type { PanelId } from '@core/input/panels.js';
import { selectCycledRecipientTarget } from '@core/selectors/conversationsSelectors.js';
import type { SettingsModifier } from '@core/store/settings/settingsSlice.js';
import { useEffect } from 'react';
import type { CreationDialogsApi } from './creationDialogs.js';

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
export function useDesktopKeybinds(enabled: boolean, { openSpawn, openTicket, openPlan }: CreationDialogsApi): void {
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

      const key = e.key.toLowerCase();

      // Modifier + digit → scroll the bound panel into view (ctrl/alt+1–0).
      if (key.length === 1 && key >= '0' && key <= '9' && !e.shiftKey) {
        const panelId = panelForDigit(key);
        if (panelId !== null) {
          e.preventDefault();
          scrollPanelIntoView(panelId);
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

      const openers = { s: openSpawn, t: openTicket, p: openPlan } as const;
      if (key === 's' || key === 't' || key === 'p') {
        e.preventDefault();
        openers[key]();
        return;
      }

      if (key === 'h' || key === 'l') {
        const result = selectCycledRecipientTarget(conversations, roster, favorites, key === 'h' ? -1 : 1);
        if (result !== null) {
          e.preventDefault();
          actions.conversations.setActivePaneAgentId(result.agentId);
        }
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [enabled, storeApi, openSpawn, openTicket, openPlan]);
}

export { CHAT_INPUT_ID };
