/**
 * document.editor.* open/attach/lease path used by TUI DocumentController, for Web DocViewer.
 * Presentation uses TmuxFrameView (TerminalSurfaceStore + useTerminalInputLease).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';

const EDITOR_RESIZE_DEBOUNCE_MS = 100;
const EDITOR_STATUS_POLL_MS = 500;

export type DocumentEditorState =
  | { readonly status: 'inactive'; readonly error?: string }
  | { readonly status: 'starting' }
  | {
      readonly status: 'active';
      readonly documentPath: string;
      readonly terminalSessionId: string;
    };

export function useDocumentEditor(opts: {
  readonly kind: 'plan' | 'note' | 'report';
  readonly name: string;
  /** Measured editor viewport; null skips resize. */
  readonly columns: number | null;
  readonly rows: number | null;
}): {
  readonly editor: DocumentEditorState;
  readonly start: () => void;
  readonly stop: () => void;
} {
  const bus = useApplicationClient();
  const [editor, setEditor] = useState<DocumentEditorState>({ status: 'inactive' });
  const editorRef = useRef(editor);
  editorRef.current = editor;
  const lifecycleGeneration = useRef(0);

  const stop = useCallback(() => {
    lifecycleGeneration.current += 1;
    const inactive: DocumentEditorState = { status: 'inactive' };
    editorRef.current = inactive;
    setEditor(inactive);
  }, []);

  const start = useCallback(() => {
    if (editorRef.current.status !== 'inactive') return;
    const starting: DocumentEditorState = { status: 'starting' };
    const generation = lifecycleGeneration.current + 1;
    lifecycleGeneration.current = generation;
    editorRef.current = starting;
    setEditor(starting);
    const columns = Math.max(20, opts.columns ?? 80);
    const rows = Math.max(1, opts.rows ?? 24);
    void bus
      .command('document.editor.start', {
        kind: opts.kind,
        name: opts.name,
        columns,
        rows,
      })
      .then((result) => {
        if (lifecycleGeneration.current !== generation) return;
        const active: DocumentEditorState = {
          status: 'active',
          documentPath: result.document_path,
          terminalSessionId: result.terminal_session_id,
        };
        editorRef.current = active;
        setEditor(active);
      })
      .catch((cause: unknown) => {
        if (lifecycleGeneration.current !== generation) return;
        const inactive: DocumentEditorState = {
          status: 'inactive',
          error: cause instanceof Error ? cause.message : String(cause),
        };
        editorRef.current = inactive;
        setEditor(inactive);
      });
  }, [bus, opts.columns, opts.kind, opts.name, opts.rows]);

  // Resize while active.
  useEffect(() => {
    if (editor.status !== 'active') return;
    if (opts.columns === null || opts.rows === null) return;
    const columns = Math.max(20, opts.columns);
    const rows = Math.max(1, opts.rows);
    const timer = setTimeout(() => {
      void bus
        .command('document.editor.resize', {
          terminal_session_id: editor.terminalSessionId,
          columns,
          rows,
        })
        .catch(() => {});
    }, EDITOR_RESIZE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [bus, editor, opts.columns, opts.rows]);

  // Poll exit.
  useEffect(() => {
    if (editor.status !== 'active') return;
    const timer = setInterval(() => {
      void bus
        .command('document.editor.status', { terminal_session_id: editor.terminalSessionId })
        .then((result) => {
          if (result.status === 'exited') stop();
        })
        .catch(() => {});
    }, EDITOR_STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [bus, editor, stop]);

  useEffect(
    () => () => {
      lifecycleGeneration.current += 1;
    },
    [],
  );

  return { editor, start, stop };
}
