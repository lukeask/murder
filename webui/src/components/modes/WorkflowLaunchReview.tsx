/**
 * Workflow launch review — mandatory compile-before-start. Compiles via `workflows.compile`,
 * maps wizard fields with ui-core helpers, then fires `workflows.run` only from explicit Launch.
 */

import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';
import {
  requiredInputIssues,
  type WizardField,
  wizardFieldsFromCompileResult,
} from '@murder/ui-core/workflowEditor/compile.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { useEffect, useId, useState } from 'react';
import { Button, Dialog, Input } from '../ds/index.js';
import { WORKFLOW_LAUNCH_HINTS, publishModeHints } from '../../keybindModeHints.js';

export interface WorkflowLaunchReviewProps {
  readonly open?: boolean;
  readonly onClose: () => void;
  readonly workflow: WorkflowTemplate;
  /**
   * Just-saved editor draft may compile inline before its registry projection arrives.
   * Library launches omit this and compile by exact saved name.
   */
  readonly compileTemplate?: WorkflowTemplate;
  /** Unsaved prompt-template edits — editor preview only; library launches omit. */
  readonly promptTemplates?: Readonly<Record<string, string>>;
  readonly onLaunched?: (workflow: WorkflowTemplate) => void;
}

type ReviewStatus = 'loading' | 'ready' | 'error';

export function WorkflowLaunchReview({
  open = true,
  onClose,
  workflow,
  compileTemplate,
  promptTemplates,
  onLaunched,
}: WorkflowLaunchReviewProps): React.JSX.Element {
  const compile = useAppStore((s) => s.actions.workflows.compile);
  const run = useAppStore((s) => s.actions.workflows.run);
  const formId = useId();

  const [status, setStatus] = useState<ReviewStatus>('loading');
  const [fields, setFields] = useState<readonly WizardField[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!open) return;
    return publishModeHints(WORKFLOW_LAUNCH_HINTS);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setStatus('loading');
    setFields([]);
    setValues({});
    setError(null);
    setPending(false);

    void compile({
      ...(compileTemplate === undefined ? { name: workflow.name } : { template: compileTemplate }),
      ...(promptTemplates === undefined ? {} : { promptTemplates }),
    })
      .then((result) => {
        if (cancelled) return;
        const compiled = wizardFieldsFromCompileResult(result);
        const blocking = compiled.issues.find((issue) => issue.severity === 'error');
        if (!result.ok || blocking !== undefined) {
          setStatus('error');
          setError(blocking?.message ?? 'Workflow template compile failed.');
          return;
        }
        setStatus('ready');
        setFields(compiled.fields);
        setValues(
          Object.fromEntries(compiled.fields.map((field) => [field.name, field.defaultValue])),
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus('error');
        setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      cancelled = true;
    };
  }, [open, workflow.name, compile, compileTemplate, promptTemplates]);

  const launch = (): void => {
    if (status !== 'ready' || pending) return;
    const missing = requiredInputIssues(fields, values);
    if (missing.length > 0) {
      setError(missing[0]?.message ?? 'Required workflow input is not filled.');
      return;
    }
    setPending(true);
    setError(null);
    void run(workflow.name, values).finally(() => {
      setPending(false);
      onLaunched?.(workflow);
      onClose();
    });
  };

  const inputs = Object.keys(workflow.inputs ?? {});
  const canLaunch = status === 'ready' && !pending;

  return (
    <Dialog
      open={open}
      title="Workflow launch review"
      onClose={onClose}
      className="wflaunch-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={launch} disabled={!canLaunch}>
            {pending ? 'Launching…' : 'Launch'}
          </Button>
        </>
      }
    >
      <div className="wflaunch">
        <header className="wflaunch__head">
          <h3 className="wflaunch__name">{workflow.name}</h3>
          <p className="wflaunch__desc">
            {workflow.description?.trim() || 'No description'}
          </p>
          <p className="wflaunch__stats">
            {`Stages: ${workflow.stages?.length ?? 0} · Declared inputs: ${inputs.length} · Mode: ${workflow.mode ?? 'static'}`}
          </p>
        </header>

        {status === 'loading' ? (
          <p className="wflaunch__status">Compiling declared and inferred inputs…</p>
        ) : null}

        {status === 'error' ? (
          <p className="wflaunch__error" role="alert">
            {error ?? 'Compile failed.'}
          </p>
        ) : null}

        {status === 'ready' && fields.length === 0 ? (
          <p className="wflaunch__status">
            This workflow has no launch inputs. Press Launch to start.
          </p>
        ) : null}

        {status === 'ready' && fields.length > 0 ? (
          <form
            id={formId}
            className="wflaunch__form creation-form"
            onSubmit={(e) => {
              e.preventDefault();
              launch();
            }}
          >
            {fields.map((field) => {
              const value = values[field.name] ?? '';
              const label = `${field.label}${field.required ? ' *' : ''}`;
              if (field.kind === 'multiline') {
                return (
                  <Input
                    key={field.name}
                    multiline
                    rows={4}
                    label={label}
                    value={value}
                    disabled={pending}
                    invalid={error !== null && field.required && !value.trim()}
                    onChange={(e) => {
                      setValues((prev) => ({ ...prev, [field.name]: e.target.value }));
                      setError(null);
                    }}
                  />
                );
              }
              return (
                <Input
                  key={field.name}
                  label={label}
                  value={value}
                  disabled={pending}
                  invalid={error !== null && field.required && !value.trim()}
                  onChange={(e) => {
                    setValues((prev) => ({ ...prev, [field.name]: e.target.value }));
                    setError(null);
                  }}
                />
              );
            })}
          </form>
        ) : null}

        {error !== null && status === 'ready' ? (
          <p className="mds-field__hint mds-field__hint--error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
