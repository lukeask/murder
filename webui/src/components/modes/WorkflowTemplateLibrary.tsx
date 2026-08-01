/**
 * Workflow template library — list/filter saved templates (mine vs built-in), then Run → launch
 * review or New/Copy/Edit → graph-editor hook. Loads the registry on open. No inktui imports.
 */

import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { shallow } from 'zustand/shallow';
import { useEffect, useMemo, useState } from 'react';
import {
  copyWorkflowTemplate,
  filterWorkflowTemplates,
  partitionWorkflowTemplates,
  sortWorkflowTemplates,
} from '../../workflowTemplates/libraryHelpers.js';
import { Button, Dialog, Input, ListRow, Badge } from '../ds/index.js';

/**
 * Graph-editor open request. Wave B3 owns the canvas; this library only emits the source.
 * Mirrors TUI `workflowTemplateEditorMode` source kinds.
 */
export type WorkflowEditorSource =
  | { readonly kind: 'blank' }
  | { readonly kind: 'existing'; readonly workflow: WorkflowTemplate }
  | { readonly kind: 'draft'; readonly workflow: WorkflowTemplate };

export interface WorkflowTemplateLibraryProps {
  readonly open?: boolean;
  readonly onClose: () => void;
  /** Exact name to select when opened from `:workflows "Name"`. */
  readonly focusedName?: string | null;
  /** Run → parent opens launch review (mandatory compile path). */
  readonly onRun: (workflow: WorkflowTemplate) => void;
  /**
   * New / Copy / Edit → parent opens the graph editor (or a stub toast until Wave B3).
   * Prefer this over ad-hoc templateId unions so copy can pass a detached draft.
   */
  readonly onEdit: (source: WorkflowEditorSource) => void;
}

export function WorkflowTemplateLibrary({
  open = true,
  onClose,
  focusedName = null,
  onRun,
  onEdit,
}: WorkflowTemplateLibraryProps): React.JSX.Element {
  const workflows = useAppStore((s) => s.workflows, shallow);
  const load = useAppStore((s) => s.actions.workflows.load);
  const deleteWorkflow = useAppStore((s) => s.actions.workflows.delete);

  const [filter, setFilter] = useState('');
  const [selectedName, setSelectedName] = useState<string | null>(focusedName);
  const [confirmDelete, setConfirmDelete] = useState<WorkflowTemplate | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!open) return;
    void load();
    setFilter('');
    setConfirmDelete(null);
    setNotice(null);
    setSelectedName(focusedName);
  }, [open, load, focusedName]);

  const visible = useMemo(
    () => filterWorkflowTemplates(sortWorkflowTemplates(workflows.items), filter),
    [workflows.items, filter],
  );
  const { mine, builtIn } = useMemo(() => partitionWorkflowTemplates(visible), [visible]);

  const selected =
    visible.find((w) => w.name === selectedName) ??
    (confirmDelete !== null ? confirmDelete : null) ??
    visible[0] ??
    null;

  useEffect(() => {
    if (selectedName === null || visible.length === 0) return;
    if (!visible.some((w) => w.name === selectedName)) {
      setSelectedName(visible[0]?.name ?? null);
    }
  }, [visible, selectedName]);

  const handleClose = (): void => {
    if (confirmDelete !== null) {
      setConfirmDelete(null);
      return;
    }
    onClose();
  };

  const beginDelete = (workflow: WorkflowTemplate): void => {
    if (workflow.builtin === true) {
      setNotice('Built-in workflow templates are read-only.');
      return;
    }
    setConfirmDelete(workflow);
    setNotice(null);
  };

  const resolveDelete = (confirmed: boolean): void => {
    if (confirmDelete === null) return;
    const workflow = confirmDelete;
    setConfirmDelete(null);
    if (!confirmed) return;
    setDeleting(true);
    setNotice(`Deleting ${workflow.name}…`);
    void deleteWorkflow(workflow.name)
      .then((result) => {
        setDeleting(false);
        if (!result.ok) {
          const message = result.conflict
            ? 'workflow registry changed remotely — reload and try again'
            : (result.issues?.[0]?.message ?? 'workflow was rejected');
          setNotice(message);
          toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
          return;
        }
        setNotice(null);
        setSelectedName(null);
        toastStore.getState().push(`deleted workflow “${workflow.name}”`, { ttlMs: 6000 });
      })
      .catch((error: unknown) => {
        setDeleting(false);
        const message = error instanceof Error ? error.message : String(error);
        setNotice(message);
        toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
      });
  };

  const handleEdit = (workflow: WorkflowTemplate): void => {
    if (workflow.builtin === true) {
      setNotice('Built-in workflow templates are read-only. Copy one to customize it.');
      return;
    }
    onEdit({ kind: 'existing', workflow });
  };

  const handleCopy = (workflow: WorkflowTemplate): void => {
    const draft = copyWorkflowTemplate(
      workflow,
      new Set(workflows.items.map((item) => item.name)),
    );
    onEdit({ kind: 'draft', workflow: draft });
  };

  return (
    <Dialog
      open={open}
      title="Workflow template library"
      onClose={handleClose}
      className="wflib-dialog"
      footer={
        confirmDelete !== null ? (
          <>
            <Button variant="ghost" onClick={() => resolveDelete(false)} disabled={deleting}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => resolveDelete(true)} disabled={deleting}>
              {deleting ? 'Deleting…' : 'Delete'}
            </Button>
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            <Button variant="secondary" onClick={() => onEdit({ kind: 'blank' })}>
              New
            </Button>
            <Button
              variant="primary"
              disabled={selected === null}
              onClick={() => {
                if (selected !== null) onRun(selected);
              }}
            >
              Run
            </Button>
          </>
        )
      }
    >
      <div className="wflib">
        <div className="wflib__toolbar">
          <Input
            label="Filter"
            value={filter}
            placeholder="Filter by name…"
            onChange={(e) => setFilter(e.target.value)}
          />
          <p className="wflib__count">{`${workflows.items.length} saved`}</p>
        </div>

        <div className="wflib__body">
          <div className="wflib__list" role="listbox" aria-label="Workflow templates">
            <p className="wflib__section">My workflow templates</p>
            {mine.length === 0 ? <p className="wflib__empty">none</p> : null}
            {mine.map((workflow) => (
              <TemplateRow
                key={workflow.name}
                workflow={workflow}
                selected={selected?.name === workflow.name && confirmDelete === null}
                onSelect={() => {
                  setSelectedName(workflow.name);
                  setNotice(null);
                }}
              />
            ))}
            <p className="wflib__section">Built-in workflow templates</p>
            {builtIn.length === 0 ? <p className="wflib__empty">none</p> : null}
            {builtIn.map((workflow) => (
              <TemplateRow
                key={workflow.name}
                workflow={workflow}
                selected={selected?.name === workflow.name && confirmDelete === null}
                onSelect={() => {
                  setSelectedName(workflow.name);
                  setNotice(null);
                }}
              />
            ))}
            {visible.length === 0 ? (
              <p className="wflib__empty">No matching workflow templates.</p>
            ) : null}
          </div>

          <div className="wflib__detail">
            {confirmDelete !== null ? (
              <div className="wflib__confirm">
                <p className="wflib__confirm-title">{`Delete “${confirmDelete.name}”?`}</p>
                <p className="wflib__muted">
                  This cannot be undone. The current registry revision will be checked.
                </p>
              </div>
            ) : selected === null ? (
              <p className="wflib__muted">Select a workflow template to inspect it.</p>
            ) : (
              <TemplateDetails
                workflow={selected}
                notice={notice}
                onRun={() => onRun(selected)}
                onCopy={() => handleCopy(selected)}
                onEdit={() => handleEdit(selected)}
                onDelete={() => beginDelete(selected)}
              />
            )}
          </div>
        </div>

        {notice !== null && confirmDelete === null ? (
          <p className="wflib__notice" role="status">
            {notice}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

function TemplateRow({
  workflow,
  selected,
  onSelect,
}: {
  readonly workflow: WorkflowTemplate;
  readonly selected: boolean;
  readonly onSelect: () => void;
}): React.JSX.Element {
  return (
    <ListRow
      className="wflib__row"
      selected={selected}
      onClick={onSelect}
      title={workflow.name}
      trailing={
        workflow.builtin === true ? (
          <Badge tone="warning">built-in</Badge>
        ) : (
          <Badge tone="neutral">mine</Badge>
        )
      }
    />
  );
}

function TemplateDetails({
  workflow,
  notice,
  onRun,
  onCopy,
  onEdit,
  onDelete,
}: {
  readonly workflow: WorkflowTemplate;
  readonly notice: string | null;
  readonly onRun: () => void;
  readonly onCopy: () => void;
  readonly onEdit: () => void;
  readonly onDelete: () => void;
}): React.JSX.Element {
  const inputs = Object.keys(workflow.inputs ?? {});
  const stageCount = workflow.stages?.length ?? 0;
  const builtin = workflow.builtin === true;

  return (
    <div className="wflib__details">
      <h3 className="wflib__name">{workflow.name}</h3>
      <dl className="wflib__meta">
        <div>
          <dt>Description</dt>
          <dd className={workflow.description?.trim() ? undefined : 'wflib__muted'}>
            {workflow.description?.trim() || 'No description'}
          </dd>
        </div>
        <div>
          <dt>Stages</dt>
          <dd>{stageCount}</dd>
        </div>
        <div>
          <dt>Inputs</dt>
          <dd>{inputs.length === 0 ? 'none' : `${inputs.length}: ${inputs.join(', ')}`}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{workflow.mode ?? 'static'}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd className={builtin ? 'wflib__warn' : undefined}>
            {builtin ? 'Built-in · read-only' : 'My template'}
          </dd>
        </div>
      </dl>
      <div className="wflib__actions">
        <Button variant="primary" size="sm" onClick={onRun}>
          Run
        </Button>
        <Button variant="secondary" size="sm" onClick={onCopy}>
          Copy
        </Button>
        <Button variant="secondary" size="sm" onClick={onEdit} disabled={builtin}>
          Edit
        </Button>
        <Button variant="danger" size="sm" onClick={onDelete} disabled={builtin}>
          Delete
        </Button>
      </div>
      {notice !== null ? (
        <p className="wflib__notice" role="status">
          {notice}
        </p>
      ) : (
        <p className="wflib__muted">
          {builtin
            ? 'Run opens launch review; Copy makes an editable draft.'
            : 'Run opens launch review; Edit opens the graph editor.'}
        </p>
      )}
    </div>
  );
}
