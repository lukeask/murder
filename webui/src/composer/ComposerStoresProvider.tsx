/**
 * React bindings for the WebUI chat-composer + workspace-pipeline stores.
 */

import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { ChatHistoryState } from '@murder/ui-core/input/chatHistoryStore.js';
import type { ChatInputState } from '@murder/ui-core/input/chatInputStore.js';
import type { ChatVimState } from '@murder/ui-core/input/chatVimStore.js';
import type { WorkspaceStoreState } from '@murder/ui-core/input/workspaceStore.js';
import { createComposerStores, type ComposerStores } from './createComposerStores.js';

const ComposerStoresContext = createContext<ComposerStores | null>(null);

export function ComposerStoresProvider({
  children,
  stores: storesProp,
}: {
  readonly children: ReactNode;
  /** Optional prebuilt bundle (tests); otherwise one is created per provider mount. */
  readonly stores?: ComposerStores;
}): React.JSX.Element {
  const stores = useMemo(() => storesProp ?? createComposerStores(), [storesProp]);
  return <ComposerStoresContext.Provider value={stores}>{children}</ComposerStoresContext.Provider>;
}

export function useComposerStores(): ComposerStores {
  const stores = useContext(ComposerStoresContext);
  if (stores === null) {
    throw new Error('composer hooks must be used within a <ComposerStoresProvider>.');
  }
  return stores;
}

export function useChatInputStore<T>(
  selector: (state: ChatInputState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { chatInput } = useComposerStores();
  return useStoreWithEqualityFn(chatInput, selector, equality);
}

export function useChatHistoryStore<T>(
  selector: (state: ChatHistoryState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { chatHistory } = useComposerStores();
  return useStoreWithEqualityFn(chatHistory, selector, equality);
}

export function useChatVimStore<T>(
  selector: (state: ChatVimState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { chatVim } = useComposerStores();
  return useStoreWithEqualityFn(chatVim, selector, equality);
}

/** Subscribe to the workspace store (count / activeIndex / slots / transition). */
export function useWorkspaceStore<T>(
  selector: (state: WorkspaceStoreState) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const { workspace } = useComposerStores();
  return useStoreWithEqualityFn(workspace, selector, equality);
}
