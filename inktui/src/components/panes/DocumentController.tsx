import { type JSX, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import { type GotoIntent, useGotoLine } from '@murder/ui-core/hooks/useGotoLine.js';
import { useInputStores, usePanelKeymap, usePaneScrollBus } from '../../hooks/useInputStores.js';
import { stageDocFocusId } from '@murder/ui-core/input/focusIds.js';
import { selectResolvedFocus } from '@murder/ui-core/input/focusStore.js';
import type { PanelKeymap } from '@murder/ui-core/input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import {
  type DocumentStyles,
  layoutDocument,
  rowForSourceLine,
} from '../../render/documentLayout.js';
import { DOC_DIR } from '@murder/ui-core/store/docView/docViewSlice.js';
import type { AppStore } from '@murder/ui-core/store/store.js';
import { useTheme } from '@murder/ui-core/theme/themeStore.js';
import type { TerminalInputLease } from '@murder/ui-core/application/ApplicationClient.js';
import { matchReservedPaneNavigation, TerminalInputWriter } from '../../terminal/rawEditorInput.js';
import { adaptTerminalUpdate } from '@murder/ui-core/terminalSurface/protocolAdapter.js';
import {
  FOLLOW_VIEWPORT_TERMINAL_SIZING,
  type TerminalSurfaceUpdate,
} from '@murder/ui-core/terminalSurface/types.js';
import {
  DocumentSurface,
  documentContentInnerHeight,
  documentContentInnerWidth,
} from './DocumentSurface.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import { computeDocumentWindow } from './shared/scrollWindow.js';
import { usePaneScrollState } from './shared/usePaneScrollState.js';
import { TranscriptPane } from './TranscriptPane.js';

const DOC_SCROLL_STEP = 1;

type DocumentIntent =
  | 'close'
  | 'edit'
  | 'scrollDown'
  | 'scrollUp'
  | 'pageDown'
  | 'pageUp'
  | 'spawnPlanner';

type DocumentEditorState =
  | { readonly status: 'inactive'; readonly error?: string }
  | { readonly status: 'starting' }
  | {
      readonly status: 'active';
      readonly documentPath: string;
      readonly terminalSessionId: string;
      readonly inputError?: string;
    };

const EDITOR_RESIZE_DEBOUNCE_MS = 100;
const EDITOR_STATUS_POLL_MS = 500;

const EMPTY_DOCUMENT_KEYMAP: PanelKeymap<DocumentIntent | GotoIntent> = {
  keymap: [],
  onIntent() {},
};

export interface DocumentControllerProps {
  readonly presentation: PanePresentation;
  readonly open: NonNullable<AppStore['docView']['open']>;
}

export const DocumentController = memo(function DocumentController({
  presentation,
  open,
}: DocumentControllerProps): JSX.Element {
  const body = useAppStore((state) => state.docView.body);
  const status = useAppStore((state) => state.docView.status);
  const error = useAppStore((state) => state.docView.error);
  const displayMode = useAppStore((state) => state.settings.documentDisplayMode);
  const closeAction = useAppStore((state) => state.actions.docView.close);
  const spawnPlanner = useAppStore((state) => state.actions.plans.spawnPlanner);
  const focusId = stageDocFocusId(open.name);
  const theme = useTheme();
  const bus = useApplicationClient();
  const { modes, focus } = useInputStores();
  const editorModeId = `document-editor:${focusId}`;
  const [editor, setEditor] = useState<DocumentEditorState>({ status: 'inactive' });
  const [editorUpdate, setEditorUpdate] = useState<TerminalSurfaceUpdate | null>(null);
  const editorRef = useRef(editor);
  const inputWriterRef = useRef<TerminalInputWriter | null>(null);
  const lifecycleGeneration = useRef(0);
  editorRef.current = editor;

  const createInputWriter = useCallback(
    (terminalInput: TerminalInputLease): TerminalInputWriter => {
      let writer: TerminalInputWriter;
      writer = new TerminalInputWriter(
        bus,
        terminalInput.streamId,
        terminalInput.sessionId,
        terminalInput.leaseId,
        terminalInput.fence,
        () => {
          if (inputWriterRef.current !== writer) return;
          // The raw route's isActive predicate immediately turns false, so new bytes are never
          // forwarded under a stale lease. The editor frame remains visible as a read-only surface.
          inputWriterRef.current = null;
          setEditor((current) =>
            current.status === 'active'
              ? { ...current, inputError: 'editor input unavailable; reconnect to regain control' }
              : current,
          );
        },
      );
      return writer;
    },
    [bus],
  );

  const stopEditorPresentation = useCallback(() => {
    lifecycleGeneration.current += 1;
    inputWriterRef.current?.close();
    inputWriterRef.current = null;
    modes.getState().exit(editorModeId);
    setEditorUpdate(null);
    const inactive: DocumentEditorState = { status: 'inactive' };
    editorRef.current = inactive;
    setEditor(inactive);
  }, [editorModeId, modes]);

  const startEditor = useCallback(async () => {
    if (editorRef.current.status !== 'inactive') return;
    const starting: DocumentEditorState = { status: 'starting' };
    const generation = lifecycleGeneration.current + 1;
    lifecycleGeneration.current = generation;
    editorRef.current = starting;
    setEditor(starting);
    try {
      const viewportColumns = documentContentInnerWidth(presentation.width);
      const viewportRows = Math.max(1, documentContentInnerHeight(presentation.height));
      const result = await bus.command('document.editor.start', {
        kind: open.kind,
        name: open.name,
        columns: viewportColumns,
        rows: viewportRows,
      });
      if (lifecycleGeneration.current !== generation) return;
      const terminalInput = await bus.openTerminalInput(result.terminal_session_id);
      if (lifecycleGeneration.current !== generation) return;
      const writer = createInputWriter(terminalInput);
      inputWriterRef.current = writer;
      const active: DocumentEditorState = {
        status: 'active',
        documentPath: result.document_path,
        terminalSessionId: result.terminal_session_id,
      };
      editorRef.current = active;
      setEditor(active);
      modes.getState().enter({
        id: editorModeId,
        presentation: 'inlayout',
        passThrough: true,
        captureCtrlC: true,
        restoreFocus: false,
        stdinRoute: {
          kind: 'terminal',
          isActive: () =>
            inputWriterRef.current !== null && selectResolvedFocus(focus).id === focusId,
          consumeReservedChord(buffer) {
            const match = matchReservedPaneNavigation(buffer);
            if (match.direction !== undefined && match.result.kind === 'matched') {
              focus.getState().navigate(match.direction);
            }
            return match.result;
          },
          write(bytes) {
            inputWriterRef.current?.enqueue(bytes);
          },
        },
        render: () => null,
        keymap: [],
        onIntent() {},
        onUncaptured(_input, _key) {
          if (selectResolvedFocus(focus).id !== focusId) return false;
          // Bytes normally never reach this parser while the raw route is active. If route
          // activation races one Ink event, swallow it rather than reconstructing altered bytes.
          return true;
        },
      });
    } catch (cause) {
      if (lifecycleGeneration.current !== generation) return;
      const inactive: DocumentEditorState = {
        status: 'inactive',
        error: cause instanceof Error ? cause.message : String(cause),
      };
      editorRef.current = inactive;
      setEditor(inactive);
    }
  }, [
    bus,
    createInputWriter,
    editorModeId,
    focus,
    focusId,
    modes,
    open.kind,
    open.name,
    presentation.height,
    presentation.width,
  ]);

  // A socket reconnect never reuses an old writer lease or its sequence numbers. Input remains
  // visibly read-only while reconnecting, then receives a fresh stream/fence only after acquire.
  useEffect(() => {
    const reconnectable = bus as typeof bus & {
      onConnect?: (listener: () => void) => () => void;
      onDisconnect?: (listener: () => void) => () => void;
    };
    const onDisconnect = (): void => inputWriterRef.current?.close('disconnected');
    const onConnect = (): void => {
      const current = editorRef.current;
      if (current.status !== 'active' || inputWriterRef.current !== null) return;
      void bus
        .openTerminalInput(current.terminalSessionId)
        .then((lease) => {
          const latest = editorRef.current;
          if (
            latest.status !== 'active' ||
            latest.terminalSessionId !== current.terminalSessionId ||
            inputWriterRef.current !== null
          ) {
            return;
          }
          inputWriterRef.current = createInputWriter(lease);
          setEditor((state) => {
            if (state.status !== 'active') return state;
            const { inputError: _inputError, ...rest } = state;
            return rest;
          });
        })
        .catch(() => {});
    };
    const unhookConnect = reconnectable.onConnect?.(onConnect);
    const unhookDisconnect = reconnectable.onDisconnect?.(onDisconnect);
    return () => {
      unhookConnect?.();
      unhookDisconnect?.();
    };
  }, [bus, createInputWriter]);

  const [scroll, setScroll] = usePaneScrollState(focusId);
  const styles: DocumentStyles = useMemo(
    () => ({
      text: { fg: theme.text },
      heading: { fg: theme.heading, bold: true },
      emphasis: { italic: true },
      strong: { bold: true },
      delete: { strikethrough: true },
      code: { fg: theme.warning, bg: theme.panelHeaderBg },
      quote: { fg: theme.muted, italic: true },
      link: { fg: theme.accent, underline: true },
      marker: { fg: theme.accent, bold: true },
      muted: { fg: theme.muted, dim: true },
    }),
    [theme],
  );
  const documentLayout = useMemo(
    () =>
      layoutDocument(
        body ?? '',
        displayMode,
        documentContentInnerWidth(presentation.width),
        styles,
      ),
    [body, displayMode, presentation.width, styles],
  );
  const effectiveHeight = Math.max(1, documentContentInnerHeight(presentation.height));
  const { start: clampedScroll, maxScroll } = computeDocumentWindow(
    documentLayout.rows.length,
    scroll,
    effectiveHeight,
  );

  const jump = useCallback(
    (line: number) => setScroll(Math.min(rowForSourceLine(documentLayout, line), maxScroll)),
    [documentLayout, maxScroll, setScroll],
  );
  const goto = useGotoLine(jump);

  const keymap: PanelKeymap<DocumentIntent | GotoIntent> = useMemo(
    () => ({
      keymap: [
        ...goto.entries,
        { chord: { input: 'i' }, intent: 'edit', description: 'edit' },
        { chord: { key: { return: true } }, intent: 'close', description: 'close' },
        { chord: { key: { escape: true } }, intent: 'close', description: 'close' },
        { chord: { input: 'j' }, intent: 'scrollDown', description: 'scroll down' },
        { chord: { key: { downArrow: true } }, intent: 'scrollDown', description: 'scroll down' },
        { chord: { input: 'k' }, intent: 'scrollUp', description: 'scroll up' },
        { chord: { key: { upArrow: true } }, intent: 'scrollUp', description: 'scroll up' },
        { chord: { input: ' ' }, intent: 'pageDown', description: 'page down' },
        { chord: { key: { pageDown: true } }, intent: 'pageDown', description: 'page down' },
        { chord: { input: 'b' }, intent: 'pageUp', description: 'page up' },
        { chord: { key: { pageUp: true } }, intent: 'pageUp', description: 'page up' },
        ...(open.kind === 'plan'
          ? [
              {
                chord: { input: 'p' },
                intent: 'spawnPlanner',
                description: 'spawn planner',
              } as const,
            ]
          : []),
      ],
      onIntent(intent) {
        if (goto.handle(intent)) {
          return;
        }
        goto.clear();
        switch (intent as DocumentIntent) {
          case 'close':
            closeAction();
            return;
          case 'edit':
            void startEditor();
            return;
          case 'scrollDown':
            setScroll((current) => Math.min(current + DOC_SCROLL_STEP, maxScroll));
            return;
          case 'scrollUp':
            setScroll((current) => Math.max(current - DOC_SCROLL_STEP, 0));
            return;
          case 'pageDown':
            setScroll((current) => Math.min(current + effectiveHeight, maxScroll));
            return;
          case 'pageUp':
            setScroll((current) => Math.max(current - effectiveHeight, 0));
            return;
          case 'spawnPlanner':
            void spawnPlanner(open.name);
            return;
        }
      },
    }),
    [
      closeAction,
      effectiveHeight,
      goto,
      maxScroll,
      open.kind,
      open.name,
      setScroll,
      spawnPlanner,
      startEditor,
    ],
  );
  usePanelKeymap(focusId, presentation.focused ? keymap : EMPTY_DOCUMENT_KEYMAP);

  const paneScroll = usePaneScrollBus();
  const maxScrollRef = useRef(maxScroll);
  maxScrollRef.current = maxScroll;
  useEffect(
    () =>
      paneScroll.subscribe(focusId, (direction, amount) => {
        setScroll((current) =>
          direction === 'up'
            ? Math.max(current - amount, 0)
            : Math.min(current + amount, maxScrollRef.current),
        );
      }),
    [focusId, paneScroll, setScroll],
  );

  useEffect(() => {
    if (editor.status !== 'active') return;
    return bus.attachTerminal(editor.terminalSessionId, (update) => {
      setEditorUpdate(adaptTerminalUpdate(update));
    });
  }, [bus, editor]);

  useEffect(() => {
    if (editor.status !== 'active') return;
    const timer = setTimeout(() => {
      const viewportColumns = documentContentInnerWidth(presentation.width);
      const viewportRows = Math.max(1, documentContentInnerHeight(presentation.height));
      void bus
        .command('document.editor.resize', {
          terminal_session_id: editor.terminalSessionId,
          columns: viewportColumns,
          rows: viewportRows,
        })
        .catch(() => {});
    }, EDITOR_RESIZE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [bus, editor, presentation.height, presentation.width]);

  useEffect(() => {
    if (editor.status !== 'active') return;
    const timer = setInterval(() => {
      void bus
        .command('document.editor.status', { terminal_session_id: editor.terminalSessionId })
        .then((result) => {
          if (result.status === 'exited') stopEditorPresentation();
        })
        .catch(() => {});
    }, EDITOR_STATUS_POLL_MS);
    return () => clearInterval(timer);
  }, [bus, editor, stopEditorPresentation]);

  useEffect(
    () => () => {
      lifecycleGeneration.current += 1;
      inputWriterRef.current?.close();
      inputWriterRef.current = null;
      modes.getState().exit(editorModeId);
    },
    [editorModeId, modes],
  );

  // Persisted pane scroll survives re-layout, but it must not remain beyond the new rendered tail
  // after a resize or display-mode switch.
  useEffect(() => {
    if (scroll !== clampedScroll) {
      setScroll(clampedScroll);
    }
  }, [clampedScroll, scroll, setScroll]);

  const title = `.murder/${DOC_DIR[open.kind]}/${open.name}.md`;
  return (
    <AllocatedPaneFrame id={focusId} presentation={presentation}>
      {editor.status === 'active' ? (
        <TranscriptPane
          width={presentation.width}
          height={presentation.height}
          focused={presentation.focused}
          title={`${open.name} [editor${editor.inputError === undefined ? '' : ' — read-only'}]`}
          footerLeft=""
          footerRight=""
          turns={[]}
          viewMode="tmux"
          scrollUp={0}
          gotoLine={null}
          tmuxUpdate={editorUpdate}
          tmuxWaitingText="[waiting for editor frame…]"
          terminalSizingPolicy={FOLLOW_VIEWPORT_TERMINAL_SIZING}
        />
      ) : (
        <DocumentSurface
          width={presentation.width}
          height={presentation.height}
          focused={presentation.focused}
          title={title}
          rows={documentLayout.rows}
          scroll={clampedScroll}
          gotoPending={goto.pending}
          status={
            editor.status === 'starting'
              ? 'loading'
              : editor.error !== undefined
                ? 'error'
                : status === 'idle'
                  ? 'ready'
                  : status
          }
          error={editor.status === 'inactive' ? (editor.error ?? error) : error}
        />
      )}
    </AllocatedPaneFrame>
  );
});
