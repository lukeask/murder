/**
 * Compose the three input stores into one wired bundle — the focus store bound to the panel store
 * it resolves against, plus the keymap registry. The app shell (C5) and the C4 tests both build the
 * bundle through here, so the wiring (focus ← panels) lives in exactly one place.
 */

import { createChatInputStore } from './chatInputStore.js';
import { createFocusStore, type FocusId } from './focusStore.js';
import { createKeymapRegistry } from './keymapRegistry.js';
import { createModeStore } from './modeStore.js';
import { createPanelStore } from './panelStore.js';
import type { PanelId } from './panels.js';

/** The wired input stores. Matches the `InputStores` context value the React provider carries. */
export interface InputStoreBundle {
  readonly panels: ReturnType<typeof createPanelStore>;
  readonly focus: ReturnType<typeof createFocusStore>;
  readonly keymaps: ReturnType<typeof createKeymapRegistry>;
  readonly modes: ReturnType<typeof createModeStore>;
  readonly chatInput: ReturnType<typeof createChatInputStore>;
}

/** Build the bundle. `initialVisible` seeds the toggled-on panels; `initialFocus` seeds intended
 * focus (defaults to chat — the always-present home). The mode store starts empty (no mode up) and
 * is bound to the focus store so its enter/exit saves+restores focus. The chat-input buffer (C11)
 * starts empty. */
export function createInputStores(
  initialVisible: Iterable<PanelId> = [],
  initialFocus?: FocusId,
): InputStoreBundle {
  const panels = createPanelStore(initialVisible);
  const focus = createFocusStore(panels, initialFocus);
  const keymaps = createKeymapRegistry();
  const modes = createModeStore(focus);
  const chatInput = createChatInputStore();
  return { panels, focus, keymaps, modes, chatInput };
}
