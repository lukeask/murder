import { type JSX, memo, useCallback, useEffect, useMemo, useRef } from 'react';
import { useAppStore } from '../../hooks/useAppStore.js';
import { type GotoIntent, useGotoLine } from '../../hooks/useGotoLine.js';
import { usePanelKeymap, usePaneScrollBus } from '../../hooks/useInputStores.js';
import { stageDocFocusId } from '../../input/focusIds.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import {
  type DocumentStyles,
  layoutDocument,
  rowForSourceLine,
} from '../../render/documentLayout.js';
import { DOC_DIR } from '../../store/docView/docViewSlice.js';
import type { AppStore } from '../../store/store.js';
import { useTheme } from '../../theme/themeStore.js';
import {
  DocumentSurface,
  documentContentInnerHeight,
  documentContentInnerWidth,
} from './DocumentSurface.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import { computeDocumentWindow } from './shared/scrollWindow.js';
import { usePaneScrollState } from './shared/usePaneScrollState.js';

const DOC_SCROLL_STEP = 1;

type DocumentIntent = 'close' | 'scrollDown' | 'scrollUp' | 'pageDown' | 'pageUp' | 'spawnPlanner';

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
    [closeAction, effectiveHeight, goto, maxScroll, open.kind, open.name, setScroll, spawnPlanner],
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

  // Persisted pane scroll survives re-layout, but it must not remain beyond the new rendered tail
  // after a resize or display-mode switch.
  useEffect(() => {
    if (scroll !== clampedScroll) {
      setScroll(clampedScroll);
    }
  }, [clampedScroll, scroll, setScroll]);

  return (
    <AllocatedPaneFrame id={focusId} presentation={presentation}>
      <DocumentSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        title={`.murder/${DOC_DIR[open.kind]}/${open.name}.md`}
        rows={documentLayout.rows}
        scroll={clampedScroll}
        gotoPending={goto.pending}
        status={status === 'idle' ? 'ready' : status}
        error={error}
      />
    </AllocatedPaneFrame>
  );
});
