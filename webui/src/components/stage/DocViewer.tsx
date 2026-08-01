/** DocViewer — open plan/note/report from docView; Panel chrome shared with TicketDetail. */

import type { ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { Panel, Tag, IconButton, Icon, Button } from '../ds/index.js';
import { SimpleMarkdown } from './SimpleMarkdown.js';
import { TmuxFrameView } from './TmuxFrameView.js';
import { GotoLineOverlay } from './GotoLineOverlay.js';
import { useDocumentEditor } from './useDocumentEditor.js';
import { useWebGotoLine } from './useWebGotoLine.js';

/** Shared overlay Panel + close + loading/error for DocViewer / TicketDetail. */
export function StageOverlayPanel({
  className,
  title,
  onClose,
  status,
  error,
  actions,
  children,
}: {
  readonly className: string;
  readonly title: ReactNode;
  readonly onClose: () => void;
  readonly status: string;
  readonly error: string | null;
  readonly actions?: ReactNode;
  readonly children: ReactNode;
}): React.JSX.Element {
  return (
    <div className={`mds-stage-overlay ${className}`}>
      <Panel
        active
        flush
        title={title}
        actions={
          <>
            {actions}
            <IconButton label="close" size="md" onClick={onClose}>
              <Icon name="x" />
            </IconButton>
          </>
        }
      >
        {status === 'loading' ? (
          <p className="mds-stage__empty">Loading…</p>
        ) : status === 'error' ? (
          <p className="mds-stage__empty">{error ?? 'Failed to load.'}</p>
        ) : (
          children
        )}
      </Panel>
    </div>
  );
}

const CHAR_W = 8.4;
const CHAR_H = 16;

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
}

export function DocViewer(): React.JSX.Element | null {
  const docView = useAppStore((s) => s.docView, shallow);
  const displayMode = useAppStore((s) => s.settings.documentDisplayMode);
  const close = useAppStore((s) => s.actions.docView.close);
  const spawnPlanner = useAppStore((s) => s.actions.plans.spawnPlanner);
  const scrollRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState<{ columns: number; rows: number } | null>(null);

  const open = docView.open;
  const kind = open?.kind ?? 'note';
  const name = open?.name ?? '';

  useEffect(() => {
    const el = surfaceRef.current;
    if (el === null) return;
    const measure = (): void => {
      const rect = el.getBoundingClientRect();
      setViewport({
        columns: Math.max(20, Math.floor(rect.width / CHAR_W)),
        rows: Math.max(1, Math.floor(rect.height / CHAR_H)),
      });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [open?.name]);

  const { editor, start, stop } = useDocumentEditor({
    kind: kind === 'plan' || kind === 'note' || kind === 'report' ? kind : 'note',
    name,
    columns: viewport?.columns ?? null,
    rows: viewport?.rows ?? null,
  });

  const jump = useCallback(
    (line: number) => {
      const el = scrollRef.current;
      if (el === null) return;
      const body = docView.body ?? '';
      const lineCount = Math.max(1, body.split('\n').length);
      const target = Math.min(Math.max(line, 1), lineCount);
      const maxScroll = Math.max(0, el.scrollHeight - el.clientHeight);
      const ratio = (target - 1) / Math.max(1, lineCount - 1);
      el.scrollTop = ratio * maxScroll;
    },
    [docView.body],
  );

  const editing = editor.status === 'active' || editor.status === 'starting';
  const goto = useWebGotoLine(jump, open !== null && !editing);

  useEffect(() => {
    if (open === null || editing) return;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isEditableTarget(event.target)) return;
      if (goto.pending !== null) return;
      if (event.key === 'i') {
        event.preventDefault();
        start();
        return;
      }
      if (event.key === 'p' && open.kind === 'plan') {
        event.preventDefault();
        void spawnPlanner(open.name);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [close, editing, goto.pending, open, spawnPlanner, start]);

  useEffect(() => () => stop(), [stop]);

  if (open === null) return null;

  const body = docView.body ?? '';
  const showDoc = editor.status === 'inactive';

  return (
    <StageOverlayPanel
      className="mds-doc"
      title={
        <span className="mds-stage-overlay__title">
          <Tag tone="accent">{open.kind}</Tag>
          <span className="mds-doc__name">
            {open.name}
            {editor.status === 'active' ? ' [editor]' : ''}
          </span>
        </span>
      }
      onClose={() => {
        stop();
        close();
      }}
      status={docView.status}
      error={docView.error}
      actions={
        editing ? (
          <Button variant="ghost" size="sm" onClick={() => stop()} title="Leave editor">
            Done
          </Button>
        ) : (
          <>
            {open.kind === 'plan' ? (
              <Button
                variant="ghost"
                size="sm"
                title="Spawn planner (p)"
                onClick={() => void spawnPlanner(open.name)}
              >
                plan
              </Button>
            ) : null}
            <Button variant="ghost" size="sm" title="Edit document (i)" onClick={() => start()}>
              Edit
            </Button>
          </>
        )
      }
    >
      {editor.status === 'inactive' && editor.error !== undefined ? (
        <p className="mds-doc__editor-error" role="alert">
          {editor.error}
        </p>
      ) : null}
      <div className="mds-doc__surface" ref={surfaceRef}>
        {showDoc ? (
          <div className="mds-doc__scroll" ref={scrollRef}>
            {displayMode === 'markdown' ? (
              <div className="mds-doc__body mds-doc__body--md">
                <SimpleMarkdown source={body} />
              </div>
            ) : (
              <pre className="mds-doc__body">{body}</pre>
            )}
          </div>
        ) : editor.status === 'active' ? (
          <div className="mds-doc__editor">
            <TmuxFrameView sessionId={editor.terminalSessionId} />
          </div>
        ) : (
          <p className="mds-stage__empty">Starting editor…</p>
        )}
      </div>
      <GotoLineOverlay pending={goto.pending} />
    </StageOverlayPanel>
  );
}
