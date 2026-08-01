/**
 * Build a {@link CommandCtx} for the WebUI chat submit path — same capability bag the TUI wires in
 * `App.tsx`, adapted to web overlays (creation dialogs) and toasts where a surface is missing.
 */

import type { ApplicationClient } from '@murder/ui-core/application/ApplicationClient.js';
import type { CommandCtx } from '@murder/ui-core/input/commandDispatch.js';
import {
  isRogueAgentId,
  planNameFromPlannerAgentId,
} from '@murder/ui-core/selectors/agentIdentity.js';
import { selectActiveAgentId } from '@murder/ui-core/selectors/conversationsSelectors.js';
import { submitCommand } from '@murder/ui-core/store/commandSubmit.js';
import type { AppStoreApi } from '@murder/ui-core/store/store.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';

export type WebCommandCtxOptions = {
  readonly store: AppStoreApi;
  readonly bus: ApplicationClient;
  /** Open the New Ticket dialog (`:ticket`). */
  readonly openTicket?: () => void;
  /** Open help (`:help` / `?`). Defaults to a short keybind-bar toast when omitted. */
  readonly openHelp?: () => void;
  /** Open the workflow template library (`:workflows`). */
  readonly openWorkflows?: (name: string | null) => void;
};

/** Capability bag for `dispatchCommand` — mirrors TUI wiring without importing inktui. */
export function buildWebCommandCtx(opts: WebCommandCtxOptions): CommandCtx {
  const { store, bus, openTicket, openHelp, openWorkflows } = opts;
  return {
    sendKey: (agentId, key, literal, enter) => {
      void store.getState().actions.conversations.sendKey(agentId, key, literal, enter);
    },
    clearTranscript: (agentId) => {
      store.getState().actions.conversations.clearTranscript(agentId);
    },
    openHelp:
      openHelp ??
      (() => {
        toastStore.getState().push('help — see the keybind bar', { ttlMs: 6000 });
      }),
    captureNote: (text) => {
      void submitCommand(bus, 'notetaker.capture.submit', { raw: text }).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
      toastStore.getState().push('note captured', { ttlMs: 6000 });
    },
    pushToast: (text, options) => toastStore.getState().push(text, options),
    clearToasts: () => toastStore.getState().clear(),
    saveTemplate: (name, body) => {
      void store.getState().actions.templates.save(name, body);
    },
    setPaneViewMode: (agentId, mode) => {
      store.getState().actions.conversations.setPaneViewMode(agentId, mode);
    },
    ...(openWorkflows !== undefined ? { openWorkflows } : {}),
    ...(openTicket !== undefined ? { openTicket } : {}),
    resolveRenameTarget: () => {
      const state = store.getState();
      const activeAgentId = selectActiveAgentId(state.conversations, state.roster, state.favorites);
      if (activeAgentId !== null) {
        if (isRogueAgentId(activeAgentId)) {
          return { kind: 'rogue', agentId: activeAgentId };
        }
        const planName = planNameFromPlannerAgentId(activeAgentId);
        if (planName !== null) {
          return { kind: 'plan', oldName: planName };
        }
      }
      const open = state.docView.open;
      if (open?.kind === 'plan') {
        return { kind: 'plan', oldName: open.name };
      }
      return null;
    },
    renameRogue: (agentId, name) => {
      void submitCommand(bus, 'crow.rename_rogue', { agent_id: agentId, name })
        .then((result) => {
          const newId = typeof result['agent_id'] === 'string' ? result['agent_id'] : agentId;
          toastStore.getState().push(`renamed crow → ${name}`, { ttlMs: 6000 });
          if (newId !== agentId) {
            store.getState().actions.conversations.setActivePaneAgentId(newId);
          }
        })
        .catch((error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
        });
    },
    renamePlan: (oldName, newName) => {
      void submitCommand(bus, 'plan.rename', { old_name: oldName, new_name: newName })
        .then(() => {
          toastStore.getState().push(`renamed plan "${oldName}" → "${newName}"`, { ttlMs: 6000 });
          const open = store.getState().docView.open;
          if (open?.kind === 'plan' && open.name === oldName) {
            void store.getState().actions.docView.open('plan', newName);
          }
        })
        .catch((error: unknown) => {
          const message = error instanceof Error ? error.message : String(error);
          toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
        });
    },
  };
}
