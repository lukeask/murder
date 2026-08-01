/**
 * Panel “+ create new” actions — the same handlers global chords use, registered by App so
 * list controllers can invoke create without reimplementing modals/WS commands.
 */

export interface PanelCreateActions {
  readonly newPlan: () => void;
  readonly quickNote: () => void;
  readonly newReport: () => void;
  /** Open the blank workflow template editor (same as Alt/Ctrl+G with no name). */
  readonly newWorkflow: () => void;
}

const NOOP: PanelCreateActions = {
  newPlan() {},
  quickNote() {},
  newReport() {},
  newWorkflow() {},
};

let current: PanelCreateActions = NOOP;

/** Called once from App when create handlers are ready (and on handler identity changes). */
export function setPanelCreateActions(actions: PanelCreateActions): void {
  current = actions;
}

export function getPanelCreateActions(): PanelCreateActions {
  return current;
}
