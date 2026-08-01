/**
 * Apply a {@link reduceVimNormal} effect to the chat + vim stores (TUI parity).
 */

import type { ChatInputStoreApi } from '@murder/ui-core/input/chatInputStore.js';
import type { ChatVimStoreApi } from '@murder/ui-core/input/chatVimStore.js';
import type { VimEffect } from '@murder/ui-core/input/chatVimReducer.js';

export function applyVimEffect(
  chatInput: ChatInputStoreApi,
  chatVim: ChatVimStoreApi,
  effect: VimEffect,
): void {
  const cin = chatInput.getState();
  const vim = chatVim.getState();
  switch (effect.kind) {
    case 'buffer':
      cin.setBuffer(effect.state);
      vim.setPending(null);
      break;
    case 'enterInsert':
      cin.setBuffer(effect.state);
      vim.setSubmode('insert');
      vim.setPending(null);
      break;
    case 'setRegister':
      cin.setBuffer(effect.state);
      vim.setRegister(effect.register);
      vim.setPending(null);
      break;
    case 'paste':
      cin.setBuffer(effect.state);
      vim.setPending(null);
      break;
    case 'pending':
      vim.setPending(effect.pending);
      break;
    case 'none':
      break;
  }
}
