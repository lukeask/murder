/**
 * Creation-dialog openers — tiny React context so panel header buttons and desktop keybinds can
 * open Spawn / New Ticket / New Plan without prop-drilling through every rail.
 */

import { createContext, useContext, type ReactNode } from 'react';

export interface CreationDialogsApi {
  readonly openSpawn: () => void;
  readonly openTicket: () => void;
  readonly openPlan: () => void;
}

const CreationDialogsContext = createContext<CreationDialogsApi | null>(null);

export function CreationDialogsProvider({
  value,
  children,
}: {
  readonly value: CreationDialogsApi;
  readonly children: ReactNode;
}): React.JSX.Element {
  return <CreationDialogsContext.Provider value={value}>{children}</CreationDialogsContext.Provider>;
}

/** Openers for spawn / ticket / plan dialogs. Throws outside the provider. */
export function useCreationDialogs(): CreationDialogsApi {
  const api = useContext(CreationDialogsContext);
  if (api === null) {
    throw new Error('useCreationDialogs must be used within a <CreationDialogsProvider>');
  }
  return api;
}
