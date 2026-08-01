/**
 * PromptTemplateManager — browser CRUD for the prompt-template registry (TUI
 * `PromptTemplateManagerMode`). List + preview / edit with expansion helpers from ui-core refs;
 * workflow reference warnings on rename/delete. Persistence via store `actions.templates`.
 */

import {
  collectBodyPlaceholders,
  collectUnknownInlinePromptTemplateRefs,
  expandInlinePromptTemplatePreview,
  findWorkflowPromptTemplateReferences,
  formatPromptTemplateMacro,
  formatWorkflowTemplateRef,
  previewBodyFlat,
  validatePromptTemplateName,
  type WorkflowTemplateRef,
} from '@murder/ui-core/components/promptTemplates/refs.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import type { PromptTemplateRecord } from '@murder/ui-core/store/templates/templatesSlice.js';
import { useEffect, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { Button, Dialog, Input } from '../ds/index.js';

export interface PromptTemplateManagerProps {
  /** Optional while App remounts-on-open; Dialog defaults to true. */
  readonly open?: boolean;
  readonly onClose: () => void;
}

type Interaction =
  | { readonly kind: 'browse' }
  | { readonly kind: 'createName' }
  | { readonly kind: 'editBody'; readonly name: string; readonly isNew: boolean }
  | { readonly kind: 'rename'; readonly name: string }
  | {
      readonly kind: 'confirmDelete';
      readonly name: string;
      readonly refs: readonly WorkflowTemplateRef[];
    }
  | {
      readonly kind: 'confirmRename';
      readonly oldName: string;
      readonly newName: string;
      readonly refs: readonly WorkflowTemplateRef[];
    };

export function PromptTemplateManager({
  open = true,
  onClose,
}: PromptTemplateManagerProps): React.JSX.Element {
  const templates = useAppStore((s) => s.templates.items, shallow);
  const templatesStatus = useAppStore((s) => s.templates.status);
  const workflows = useAppStore((s) => s.workflows.items, shallow);
  const workflowsStatus = useAppStore((s) => s.workflows.status);
  const save = useAppStore((s) => s.actions.templates.save);
  const remove = useAppStore((s) => s.actions.templates.remove);
  const rename = useAppStore((s) => s.actions.templates.rename);
  const loadTemplates = useAppStore((s) => s.actions.templates.load);
  const loadWorkflows = useAppStore((s) => s.actions.workflows.load);

  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [interaction, setInteraction] = useState<Interaction>({ kind: 'browse' });
  const [draftName, setDraftName] = useState('');
  const [draftBody, setDraftBody] = useState('');
  const [notice, setNotice] = useState<{ text: string; tone: 'info' | 'warning' } | null>(null);

  useEffect(() => {
    if (!open) return;
    void loadTemplates();
    if (workflowsStatus === 'idle') {
      void loadWorkflows();
    }
  }, [open, loadTemplates, loadWorkflows, workflowsStatus]);

  // Keep selection valid when the registry mutates under us.
  useEffect(() => {
    if (selectedName !== null && !templates.some((t) => t.name === selectedName)) {
      setSelectedName(templates[0]?.name ?? null);
    } else if (selectedName === null && templates.length > 0) {
      setSelectedName(templates[0]?.name ?? null);
    }
  }, [templates, selectedName]);

  const selected = useMemo(
    () => (selectedName === null ? null : (templates.find((t) => t.name === selectedName) ?? null)),
    [templates, selectedName],
  );

  const bodyForPreview =
    interaction.kind === 'editBody' ? draftBody : (selected?.body ?? null);
  const previewName =
    interaction.kind === 'editBody'
      ? interaction.name
      : interaction.kind === 'rename'
        ? interaction.name
        : interaction.kind === 'confirmDelete'
          ? interaction.name
          : interaction.kind === 'confirmRename'
            ? interaction.oldName
            : selectedName;

  const knownNames = useMemo(() => new Set(templates.map((t) => t.name)), [templates]);
  const workflowNames = useMemo(() => new Set(workflows.map((w) => w.name)), [workflows]);
  const placeholders =
    bodyForPreview === null ? [] : collectBodyPlaceholders(bodyForPreview);
  const unknownRefs =
    bodyForPreview === null
      ? []
      : collectUnknownInlinePromptTemplateRefs(
          bodyForPreview,
          knownNames,
          previewName ?? undefined,
        );
  const registry = useMemo(
    () => new Map(templates.map((t) => [t.name, t.body] as const)),
    [templates],
  );
  const expansion =
    bodyForPreview === null
      ? null
      : expandInlinePromptTemplatePreview(bodyForPreview, registry);
  const workflowRefs =
    previewName === null ? [] : findWorkflowPromptTemplateReferences(previewName, workflows);

  const resetBrowse = (): void => {
    setInteraction({ kind: 'browse' });
    setDraftName('');
    setDraftBody('');
  };

  const beginCreate = (): void => {
    setInteraction({ kind: 'createName' });
    setDraftName('');
    setNotice(null);
  };

  const beginRename = (name: string): void => {
    setInteraction({ kind: 'rename', name });
    setDraftName(name);
    setNotice(null);
  };

  const beginEdit = (record: PromptTemplateRecord, isNew: boolean): void => {
    setInteraction({ kind: 'editBody', name: record.name, isNew });
    setDraftBody(record.body);
    setSelectedName(record.name);
    setNotice(null);
  };

  const beginDelete = (name: string): void => {
    const refs = findWorkflowPromptTemplateReferences(name, workflows);
    setInteraction({ kind: 'confirmDelete', name, refs });
    setNotice(null);
  };

  const commitCreateName = (): void => {
    const name = draftName.trim();
    const error = validatePromptTemplateName(name, null, templates);
    if (error !== null) {
      setNotice({ text: error, tone: 'warning' });
      return;
    }
    beginEdit({ name, body: '' }, true);
  };

  const commitEditBody = (): void => {
    if (interaction.kind !== 'editBody') return;
    const { name, isNew } = interaction;
    void save(name, draftBody);
    setSelectedName(name);
    resetBrowse();
    setNotice({
      text: isNew
        ? `created ${formatPromptTemplateMacro(name)}`
        : `saved ${formatPromptTemplateMacro(name)}`,
      tone: 'info',
    });
  };

  const finishRename = (oldName: string, newName: string): void => {
    void rename(oldName, newName);
    const refs = findWorkflowPromptTemplateReferences(oldName, workflows);
    setSelectedName(newName);
    resetBrowse();
    setNotice({
      text:
        refs.length > 0
          ? `renamed to ${formatPromptTemplateMacro(newName)} — ${refs.length} workflow field${refs.length === 1 ? '' : 's'} still use ${formatPromptTemplateMacro(oldName)}`
          : `renamed to ${formatPromptTemplateMacro(newName)}`,
      tone: refs.length > 0 ? 'warning' : 'info',
    });
  };

  const commitRename = (): void => {
    if (interaction.kind !== 'rename') return;
    const oldName = interaction.name;
    const newName = draftName.trim();
    const error = validatePromptTemplateName(newName, oldName, templates);
    if (error !== null) {
      setNotice({ text: error, tone: 'warning' });
      return;
    }
    if (newName === oldName) {
      resetBrowse();
      setNotice(null);
      return;
    }
    const refs = findWorkflowPromptTemplateReferences(oldName, workflows);
    if (refs.length > 0) {
      setInteraction({ kind: 'confirmRename', oldName, newName, refs });
      setNotice(null);
      return;
    }
    finishRename(oldName, newName);
  };

  const resolveDelete = (confirmed: boolean): void => {
    if (interaction.kind !== 'confirmDelete') return;
    const { name } = interaction;
    if (confirmed) {
      void remove(name);
    }
    resetBrowse();
    setNotice(null);
  };

  const resolveRenameConfirm = (confirmed: boolean): void => {
    if (interaction.kind !== 'confirmRename') return;
    const { oldName, newName } = interaction;
    if (confirmed) {
      finishRename(oldName, newName);
      return;
    }
    resetBrowse();
    setNotice(null);
  };

  const requestClose = (): void => {
    if (interaction.kind !== 'browse') {
      resetBrowse();
      setNotice(null);
      return;
    }
    onClose();
  };

  const confirmingRefs =
    interaction.kind === 'confirmRename' || interaction.kind === 'confirmDelete'
      ? interaction.refs
      : null;

  const loading =
    templatesStatus === 'loading' || templatesStatus === 'idle';

  return (
    <Dialog
      open={open}
      title="Prompt Templates"
      onClose={requestClose}
      className="ptm-dialog"
      footer={
        interaction.kind === 'editBody' ? (
          <>
            <Button variant="ghost" onClick={resetBrowse}>
              Cancel
            </Button>
            <Button variant="primary" onClick={commitEditBody}>
              Save
            </Button>
          </>
        ) : interaction.kind === 'createName' || interaction.kind === 'rename' ? (
          <>
            <Button variant="ghost" onClick={resetBrowse}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={interaction.kind === 'createName' ? commitCreateName : commitRename}
            >
              Continue
            </Button>
          </>
        ) : confirmingRefs !== null ? (
          <>
            <Button
              variant="ghost"
              onClick={() =>
                interaction.kind === 'confirmDelete'
                  ? resolveDelete(false)
                  : resolveRenameConfirm(false)
              }
            >
              Keep
            </Button>
            <Button
              variant={interaction.kind === 'confirmDelete' ? 'danger' : 'primary'}
              onClick={() =>
                interaction.kind === 'confirmDelete'
                  ? resolveDelete(true)
                  : resolveRenameConfirm(true)
              }
            >
              {interaction.kind === 'confirmDelete' ? 'Delete' : 'Rename'}
            </Button>
          </>
        ) : (
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        )
      }
    >
      <div className="ptm">
        <p className="ptm__hint">
          :name: or :&quot;Name With Spaces&quot;: expands inside stage prompts
          {templates.length > 0 ? ` · ${templates.length} saved` : ''}
        </p>

        <div className="ptm__layout">
          <div className="ptm__list" role="list" aria-label="Prompt templates">
            <button type="button" className="ptm__row ptm__row--new" onClick={beginCreate}>
              + new prompt template
            </button>
            {loading && templates.length === 0 ? (
              <p className="ptm__empty">loading…</p>
            ) : templates.length === 0 ? (
              <p className="ptm__empty">nothing yet — create one</p>
            ) : (
              templates.map((t) => {
                const active =
                  (selectedName === t.name && interaction.kind === 'browse') ||
                  (interaction.kind === 'editBody' && interaction.name === t.name) ||
                  (interaction.kind === 'rename' && interaction.name === t.name) ||
                  (interaction.kind === 'confirmDelete' && interaction.name === t.name) ||
                  (interaction.kind === 'confirmRename' && interaction.oldName === t.name);
                const collides = workflowNames.has(t.name);
                return (
                  <button
                    key={t.name}
                    type="button"
                    role="listitem"
                    aria-label={formatPromptTemplateMacro(t.name)}
                    className={active ? 'ptm__row ptm__row--active' : 'ptm__row'}
                    onClick={() => {
                      setSelectedName(t.name);
                      if (interaction.kind !== 'browse') resetBrowse();
                      setNotice(null);
                    }}
                  >
                    <span>{formatPromptTemplateMacro(t.name)}</span>
                    {collides ? <span className="ptm__warn">⚠</span> : null}
                  </button>
                );
              })
            )}
          </div>

          <div className="ptm__detail">
            {interaction.kind === 'confirmDelete' || interaction.kind === 'confirmRename' ? (
              <ConfirmPane interaction={interaction} refs={interaction.refs} />
            ) : interaction.kind === 'createName' ? (
              <Input
                label="name"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                placeholder="template name"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    commitCreateName();
                  }
                }}
              />
            ) : interaction.kind === 'rename' ? (
              <Input
                label="rename"
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    commitRename();
                  }
                }}
              />
            ) : interaction.kind === 'editBody' ? (
              <Input
                multiline
                rows={10}
                label={formatPromptTemplateMacro(interaction.name)}
                value={draftBody}
                onChange={(e) => setDraftBody(e.target.value)}
                placeholder="(body)"
                autoFocus
              />
            ) : bodyForPreview !== null && previewName !== null ? (
              <PreviewPane
                name={previewName}
                body={bodyForPreview}
                placeholders={placeholders}
                unknownRefs={unknownRefs}
                workflowRefs={workflowRefs}
                expansion={expansion}
              />
            ) : (
              <p className="ptm__empty">Pick a prompt template to see its body.</p>
            )}

            {interaction.kind === 'browse' && selected !== null ? (
              <div className="ptm__actions">
                <Button
                  size="sm"
                  onClick={() => beginEdit(selected, false)}
                >
                  Edit
                </Button>
                <Button size="sm" onClick={() => beginRename(selected.name)}>
                  Rename
                </Button>
                <Button size="sm" variant="danger" onClick={() => beginDelete(selected.name)}>
                  Delete
                </Button>
              </div>
            ) : null}

            {interaction.kind === 'editBody' ? (
              <dl className="ptm__meta">
                <div>
                  <dt>Inputs</dt>
                  <dd>
                    {placeholders.length === 0
                      ? 'none'
                      : placeholders.map((n) => `{${n}}`).join(', ')}
                  </dd>
                </div>
                {unknownRefs.length > 0 ? (
                  <div>
                    <dt>Refs</dt>
                    <dd className="ptm__warn-text">
                      ⚠ unknown {unknownRefs.map(formatPromptTemplateMacro).join(', ')}
                    </dd>
                  </div>
                ) : null}
              </dl>
            ) : null}
          </div>
        </div>

        {notice !== null ? (
          <p className={notice.tone === 'warning' ? 'ptm__notice ptm__notice--warn' : 'ptm__notice'}>
            {notice.tone === 'warning' ? '⚠' : '✓'} {notice.text}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

function ConfirmPane({
  interaction,
  refs,
}: {
  readonly interaction: Extract<Interaction, { kind: 'confirmDelete' | 'confirmRename' }>;
  readonly refs: readonly WorkflowTemplateRef[];
}): React.JSX.Element {
  const rename = interaction.kind === 'confirmRename' ? interaction : null;
  const remove = interaction.kind === 'confirmDelete' ? interaction : null;
  return (
    <div className="ptm__confirm">
      <p className="ptm__confirm-title">
        {rename !== null
          ? `Rename ${formatPromptTemplateMacro(rename.oldName)} to ${formatPromptTemplateMacro(rename.newName)}?`
          : `Delete ${formatPromptTemplateMacro(remove?.name ?? '')}?`}
      </p>
      <p>
        {rename !== null
          ? `${refs.length} workflow field${refs.length === 1 ? '' : 's'} keep the old name:`
          : refs.length === 0
            ? 'No workflow references it.'
            : `${refs.length} workflow field${refs.length === 1 ? ' references' : 's reference'} it:`}
      </p>
      {refs.map((ref) => (
        <p key={`${ref.workflowName}/${ref.stageId}.${ref.field}`} className="ptm__warn-text">
          ⚠ {formatWorkflowTemplateRef(ref)}
        </p>
      ))}
    </div>
  );
}

function PreviewPane({
  name,
  body,
  placeholders,
  unknownRefs,
  workflowRefs,
  expansion,
}: {
  readonly name: string;
  readonly body: string;
  readonly placeholders: readonly string[];
  readonly unknownRefs: readonly string[];
  readonly workflowRefs: readonly WorkflowTemplateRef[];
  readonly expansion: { text: string; missing: readonly string[] } | null;
}): React.JSX.Element {
  return (
    <div className="ptm__preview">
      <h3 className="ptm__preview-title">{formatPromptTemplateMacro(name)}</h3>
      <pre className="ptm__body">{previewBodyFlat(body, 400)}</pre>
      <dl className="ptm__meta">
        <div>
          <dt>Inputs</dt>
          <dd>
            {placeholders.length === 0
              ? 'none'
              : placeholders.map((n) => `{${n}}`).join(', ')}
          </dd>
        </div>
        <div>
          <dt>Refs</dt>
          <dd className={unknownRefs.length > 0 ? 'ptm__warn-text' : undefined}>
            {unknownRefs.length === 0
              ? 'all resolve'
              : `⚠ unknown ${unknownRefs.map(formatPromptTemplateMacro).join(', ')}`}
          </dd>
        </div>
        <div>
          <dt>Used by</dt>
          <dd>
            {workflowRefs.length === 0
              ? 'no workflow'
              : `${workflowRefs.length} workflow field${workflowRefs.length === 1 ? '' : 's'}`}
          </dd>
        </div>
      </dl>
      {workflowRefs.map((ref) => (
        <p key={`${ref.workflowName}/${ref.stageId}.${ref.field}`} className="ptm__ref">
          {formatWorkflowTemplateRef(ref)}
        </p>
      ))}
      {expansion !== null && expansion.text !== body ? (
        <dl className="ptm__meta">
          <div>
            <dt>Expands</dt>
            <dd>{previewBodyFlat(expansion.text, 120)}</dd>
          </div>
        </dl>
      ) : null}
    </div>
  );
}
