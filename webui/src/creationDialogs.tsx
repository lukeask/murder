/**
 * Creation-dialog openers — tiny React context so panel header buttons and desktop keybinds can
 * open Spawn / Start work / New Plan / New Report / Note capture / workflow library / prompt
 * templates / help without prop-drilling through every rail.
 */

import { createContext, useContext, type ReactNode } from 'react';
import type { WorkflowEditorSource } from './components/modes/WorkflowTemplateLibrary.js';

export type { WorkflowEditorSource };

export interface CreationDialogsApi {
  readonly openSpawn: () => void;
  readonly openNewWork: () => void;
  readonly openPlan: () => void;
  readonly openReport: () => void;
  readonly openNoteCapture: () => void;
  readonly openPromptTemplates: () => void;
  readonly openHelp: () => void;
  /** Open the workflow template library (`:workflows`, panel “+”). */
  readonly openWorkflowLibrary: (focusedName?: string | null) => void;
  /** Open launch review for a saved template by exact name (or id alias — names are canonical). */
  readonly openWorkflowLaunch: (opts: { readonly name: string } | { readonly id: string }) => void;
  /**
   * Open the workflow graph editor (Wave B3). Library New/Copy/Edit call this.
   * Until the canvas lands, App may stub with a toast.
   */
  readonly openWorkflowEditor: (source: WorkflowEditorSource) => void;
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

/** Openers for creation / workflow / help surfaces. Throws outside the provider. */
export function useCreationDialogs(): CreationDialogsApi {
  const api = useContext(CreationDialogsContext);
  if (api === null) {
    throw new Error('useCreationDialogs must be used within a <CreationDialogsProvider>');
  }
  return api;
}
