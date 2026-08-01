/** App — web/mobile shell on design-system primitives (desktop cockpit + mobile tabs). */

import { useAppStore, useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { DEFAULT_THEME_ID, hasTheme, type ThemeId } from '@murder/ui-core/theme/palettes.js';
import { setTheme } from '@murder/ui-core/theme/themeStore.js';
import { resolveBarWidgetConfig } from '@murder/ui-core/selectors/barWidgetRegistry.js';
import { useEffect, useMemo, useRef, useState, type ComponentType } from 'react';
import { ComposerStoresProvider } from './composer/ComposerStoresProvider.js';
import { CreationDialogsProvider, type CreationDialogsApi } from './creationDialogs.js';
import { useThemeCssVars } from './theme/useThemeCssVars.js';
import { type ConnectionStatus, useConnectionStatus } from './useConnectionStatus.js';
import type { ApplicationConnectionClient } from '@murder/ui-core/application/ApplicationClient.js';
import { MOBILE_QUERY, useMediaQuery } from './useMediaQuery.js';
import { PlansPanel } from './components/panels/PlansPanel.js';
import { NotesPanel } from './components/panels/NotesPanel.js';
import { ReportsPanel } from './components/panels/ReportsPanel.js';
import { WorkflowsPanel } from './components/panels/WorkflowsPanel.js';
import { HistoryPanel } from './components/panels/HistoryPanel.js';
import { RosterPanel } from './components/panels/RosterPanel.js';
import { UsagePanel } from './components/panels/UsagePanel.js';
import { TreePanel } from './components/panels/TreePanel.js';
import { SettingsPanel } from './components/panels/SettingsPanel.js';
import { Stage } from './components/stage/Stage.js';
import { ToastHost } from './components/ToastHost.js';
import { NewTicketDialog } from './components/modals/NewTicketDialog.js';
import { NewPlanDialog } from './components/modals/NewPlanDialog.js';
import { NewReportDialog } from './components/modals/NewReportDialog.js';
import { NoteCaptureDialog } from './components/modals/NoteCaptureDialog.js';
import { SpawnRogueDialog } from './components/modals/SpawnRogueDialog.js';
import { HelpDialog } from './components/modals/HelpDialog.js';
import { MurderConfirmDialog } from './components/modals/MurderConfirmDialog.js';
import { PromptTemplateManager } from './components/modes/PromptTemplateManager.js';
import {
  WorkflowTemplateLibrary,
  type WorkflowEditorSource,
} from './components/modes/WorkflowTemplateLibrary.js';
import { WorkflowLaunchReview } from './components/modes/WorkflowLaunchReview.js';
import { WorkflowTemplateEditor } from './components/modes/WorkflowTemplateEditor.js';
import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';
import { toWire } from '@murder/ui-core/workflowEditor/wire.js';
import type { EditorWorkflow } from '@murder/ui-core/workflowEditor/model.js';
import { NavBar, KeybindBar, StatusDot, type StatusDotStatus, Tabs, type TabItem, Icon, type IconName, cx } from './components/ds/index.js';
import { WorkspaceStrip } from './components/WorkspaceStrip.js';
import { UsageBarSegment } from './components/UsageBarSegment.js';
import { desktopKeybindHints } from './commandModifierPrefix.js';
import { useWorkspaceCountSync, useWorkspaceSwitchFlash } from './composer/useWorkspaceBridge.js';
import { useDesktopKeybinds } from './useDesktopKeybinds.js';
import { usePanelIsVisible } from './panelVisibility.js';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import { enabledBarWidgetIds } from '@murder/ui-core/selectors/barWidgetRegistry.js';

const MOBILE_TAB_DEFS: readonly {
  readonly id: string;
  readonly icon: IconName;
  readonly Pane: ComponentType;
}[] = [
  { id: 'chat', icon: 'message-square', Pane: Stage },
  { id: 'crows', icon: 'crosshair', Pane: RosterPanel },
  { id: 'workflows', icon: 'ticket', Pane: WorkflowsPanel },
  { id: 'plans', icon: 'file-text', Pane: PlansPanel },
  { id: 'notes', icon: 'file-text', Pane: NotesPanel },
  { id: 'reports', icon: 'file-text', Pane: ReportsPanel },
  { id: 'history', icon: 'git-branch', Pane: HistoryPanel },
  { id: 'usage', icon: 'gauge', Pane: UsagePanel },
  { id: 'tree', icon: 'git-commit', Pane: TreePanel },
  { id: 'settings', icon: 'settings', Pane: SettingsPanel },
];
type MobileTab = (typeof MOBILE_TAB_DEFS)[number]['id'];

const REFRESH_ON_CONNECT = [
  'roster',
  'tickets',
  'workflowRuns',
  'plans',
  'notes',
  'reports',
  'history',
  'transit',
  'usage',
  'conversations',
] as const;
const LOAD_ON_CONNECT = ['favorites', 'themes', 'settings'] as const;

type ShellDialog =
  | { readonly kind: 'spawn' }
  | { readonly kind: 'ticket' }
  | { readonly kind: 'plan' }
  | { readonly kind: 'report' }
  | { readonly kind: 'note' }
  | { readonly kind: 'promptTemplates' }
  | { readonly kind: 'help' }
  | { readonly kind: 'workflowLibrary'; readonly focusedName: string | null }
  | { readonly kind: 'workflowLaunch'; readonly workflow: WorkflowTemplate; readonly compileTemplate?: WorkflowTemplate }
  | {
      readonly kind: 'workflowEditor';
      readonly source: WorkflowEditorSource;
    }
  | null;

function editorWorkflowToTemplate(draft: EditorWorkflow): WorkflowTemplate {
  return toWire(draft);
}

export function App({ bus }: { readonly bus: ApplicationConnectionClient }): React.JSX.Element {
  useThemeCssVars();
  const status = useConnectionStatus(bus);
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const storeApi = useAppStoreApi();

  const [dialog, setDialog] = useState<ShellDialog>(null);
  const closeDialog = (): void => setDialog(null);

  const creationApi = useMemo(
    () => ({
      openSpawn: () => setDialog({ kind: 'spawn' }),
      openTicket: () => setDialog({ kind: 'ticket' }),
      openPlan: () => setDialog({ kind: 'plan' }),
      openReport: () => setDialog({ kind: 'report' }),
      openNoteCapture: () => setDialog({ kind: 'note' }),
      openPromptTemplates: () => setDialog({ kind: 'promptTemplates' }),
      openHelp: () => setDialog({ kind: 'help' }),
      openWorkflowLibrary: (focusedName: string | null = null) => {
        setDialog({ kind: 'workflowLibrary', focusedName });
      },
      openWorkflowLaunch: (opts: { readonly name: string } | { readonly id: string }) => {
        const key = 'name' in opts ? opts.name : opts.id;
        const workflow = storeApi.getState().workflows.items.find((item) => item.name === key);
        if (workflow === undefined) {
          toastStore.getState().push(`workflow template “${key}” not found`, {
            severity: 'error',
            ttlMs: 8000,
          });
          return;
        }
        setDialog({ kind: 'workflowLaunch', workflow });
      },
      openWorkflowEditor: (source: WorkflowEditorSource) => {
        setDialog({ kind: 'workflowEditor', source });
      },
    }),
    [storeApi],
  );

  // Re-prime every slice on each (re)connect so key-only invalidation can't leave stale data.
  useEffect(() => {
    const off = bus.onConnect(() => {
      const a = storeApi.getState().actions;
      for (const key of REFRESH_ON_CONNECT) void a[key].refresh();
      for (const key of LOAD_ON_CONNECT) void a[key].load();
    });
    return off;
  }, [bus, storeApi]);

  useEffect(() => {
    const syncTheme = (theme: string): void => {
      const id: ThemeId = hasTheme(theme) ? theme : DEFAULT_THEME_ID;
      setTheme(id);
    };
    syncTheme(storeApi.getState().settings.theme);
    return storeApi.subscribe((state, prev) => {
      if (
        state.settings.theme !== prev.settings.theme ||
        state.themes.items !== prev.themes.items
      ) {
        syncTheme(state.settings.theme);
      }
    });
  }, [storeApi]);

  return (
    <CreationDialogsProvider value={creationApi}>
      <ComposerStoresProvider>
        <AppShell
          status={status}
          isMobile={isMobile}
          creationApi={creationApi}
          dialog={dialog}
          setDialog={setDialog}
          closeDialog={closeDialog}
        />
      </ComposerStoresProvider>
    </CreationDialogsProvider>
  );
}

/** Inside ComposerStoresProvider — keybinds + workspace count bridge need the input stores. */
function AppShell({
  status,
  isMobile,
  creationApi,
  dialog,
  setDialog,
  closeDialog,
}: {
  readonly status: ConnectionStatus;
  readonly isMobile: boolean;
  readonly creationApi: CreationDialogsApi;
  readonly dialog: ShellDialog;
  readonly setDialog: (d: ShellDialog) => void;
  readonly closeDialog: () => void;
}): React.JSX.Element {
  useDesktopKeybinds(!isMobile, creationApi);
  useWorkspaceCountSync();

  return (
    <div className="app" data-layout={isMobile ? 'mobile' : 'desktop'}>
      {isMobile ? <MobileLayout status={status} /> : <DesktopLayout status={status} />}
      <ToastHost />
      <MurderConfirmDialog />
      {dialog?.kind === 'spawn' && <SpawnRogueDialog onClose={closeDialog} />}
      {dialog?.kind === 'ticket' && <NewTicketDialog onClose={closeDialog} />}
      {dialog?.kind === 'plan' && <NewPlanDialog onClose={closeDialog} />}
      {dialog?.kind === 'report' && <NewReportDialog onClose={closeDialog} />}
      {dialog?.kind === 'note' && <NoteCaptureDialog onClose={closeDialog} />}
      {dialog?.kind === 'promptTemplates' && (
        <PromptTemplateManager onClose={closeDialog} />
      )}
      {dialog?.kind === 'help' && <HelpDialog onClose={closeDialog} />}
      {dialog?.kind === 'workflowLibrary' && (
        <WorkflowTemplateLibrary
          focusedName={dialog.focusedName}
          onClose={closeDialog}
          onRun={(workflow) => setDialog({ kind: 'workflowLaunch', workflow })}
          onEdit={(source) => {
            closeDialog();
            creationApi.openWorkflowEditor(source);
          }}
        />
      )}
      {dialog?.kind === 'workflowLaunch' && (
        <WorkflowLaunchReview
          workflow={dialog.workflow}
          {...(dialog.compileTemplate !== undefined
            ? { compileTemplate: dialog.compileTemplate }
            : {})}
          onClose={closeDialog}
        />
      )}
      {dialog?.kind === 'workflowEditor' && (
        <WorkflowTemplateEditor
          {...(dialog.source.kind === 'existing'
            ? { templateName: dialog.source.workflow.name }
            : dialog.source.kind === 'draft'
              ? { initialDraft: dialog.source.workflow }
              : {})}
          onClose={closeDialog}
          onOpenLibrary={() => setDialog({ kind: 'workflowLibrary', focusedName: null })}
          onLaunch={(draft) => {
            const workflow = editorWorkflowToTemplate(draft);
            setDialog({
              kind: 'workflowLaunch',
              workflow,
              compileTemplate: workflow,
            });
          }}
        />
      )}
    </div>
  );
}

/** Conditionally mount a rail panel when its visibility toggle is on. */
function VisiblePanel({
  id,
  children,
}: {
  readonly id: PanelId;
  readonly children: React.ReactNode;
}): React.JSX.Element | null {
  return usePanelIsVisible(id) ? <>{children}</> : null;
}

/** Desktop: NavBar / 3-rail body / KeybindBar (live bar widgets when enabled). */
function DesktopLayout({ status }: { readonly status: ConnectionStatus }): React.JSX.Element {
  const modifier = useAppStore((s) => s.settings.modifier);
  const keyOverrides = useAppStore((s) => s.settings.keyOverrides);
  const barWidgets = useAppStore((s) => s.settings.barWidgets);
  const hints = useMemo(
    () => desktopKeybindHints(modifier, keyOverrides),
    [modifier, keyOverrides],
  );
  const hintsEnabled = resolveBarWidgetConfig('hints', barWidgets).enabled;
  const workspaceCfg = resolveBarWidgetConfig('workspace', barWidgets);
  const usageCfg = resolveBarWidgetConfig('usage', barWidgets);
  const showWorkspaceTop = workspaceCfg.enabled && workspaceCfg.placement === 'top';
  const showWorkspaceBottom = workspaceCfg.enabled && workspaceCfg.placement === 'bottom';
  const showUsageBottom = usageCfg.enabled && usageCfg.placement === 'bottom';
  const bottomWidgets = enabledBarWidgetIds(barWidgets, 'bottom');
  const showKeybindBar = bottomWidgets.length > 0;
  const keybarLeading =
    showUsageBottom || showWorkspaceBottom ? (
      <>
        <UsageBarSegment placement="bottom" />
        {showWorkspaceBottom ? <WorkspaceStrip /> : null}
      </>
    ) : undefined;
  const stageFlash = useWorkspaceSwitchFlash();
  return (
    <div className="cockpit">
      <NavBar
        brand="murder"
        trailing={
          <>
            <UsageBarSegment placement="top" />
            {showWorkspaceTop ? <WorkspaceStrip /> : null}
            <ConnectionIndicator status={status} />
          </>
        }
      />
      <div className="cockpit__cols">
        <aside className="rail cockpit__rail cockpit__rail--left">
          <VisiblePanel id="workflows">
            <WorkflowsPanel />
          </VisiblePanel>
          <VisiblePanel id="plans">
            <PlansPanel />
          </VisiblePanel>
          <VisiblePanel id="notes">
            <NotesPanel />
          </VisiblePanel>
          <VisiblePanel id="reports">
            <ReportsPanel />
          </VisiblePanel>
          <VisiblePanel id="history">
            <HistoryPanel />
          </VisiblePanel>
        </aside>
        <section className={cx('cockpit__stage', stageFlash && 'cockpit__stage--workspace-flash')}>
          <Stage />
        </section>
        <aside className="rail cockpit__rail cockpit__rail--right">
          <VisiblePanel id="crows">
            <RosterPanel />
          </VisiblePanel>
          <VisiblePanel id="usage">
            <UsagePanel />
          </VisiblePanel>
          <VisiblePanel id="tree">
            <TreePanel />
          </VisiblePanel>
          <SettingsPanel />
        </aside>
      </div>
      {showKeybindBar ? (
        <KeybindBar hints={hintsEnabled ? hints : []} leading={keybarLeading} />
      ) : null}
    </div>
  );
}

/** Mobile: header / single pane / bottom pill tab bar. */
function MobileLayout({ status }: { readonly status: ConnectionStatus }): React.JSX.Element {
  const [tab, setTab] = useState<MobileTab>('chat');
  const [tabScroll, setTabScroll] = useState({ left: false, right: true });
  const tabsRef = useRef<HTMLDivElement>(null);
  const tabItems: TabItem[] = MOBILE_TAB_DEFS.map((t) => ({
    id: t.id,
    label: t.id,
    icon: <Icon name={t.icon} size={18} />,
  }));
  const Pane = MOBILE_TAB_DEFS.find((t) => t.id === tab)?.Pane ?? Stage;

  const syncTabScroll = (): void => {
    const el = tabsRef.current?.querySelector('.mds-tabs--full');
    if (!(el instanceof HTMLElement)) {
      return;
    }
    const max = el.scrollWidth - el.clientWidth;
    setTabScroll({
      left: el.scrollLeft > 4,
      right: max > 4 && el.scrollLeft < max - 4,
    });
  };

  useEffect(() => {
    syncTabScroll();
    const el = tabsRef.current?.querySelector('.mds-tabs--full');
    if (!(el instanceof HTMLElement)) {
      return;
    }
    const onScroll = (): void => syncTabScroll();
    el.addEventListener('scroll', onScroll, { passive: true });
    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(onScroll);
      ro.observe(el);
    }
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro?.disconnect();
    };
  }, []);

  return (
    <div className="mw-app">
      <header className="mw-header">
        <span className="mw-brand">murder</span>
        <span className="mw-view">{tab}</span>
        <span className="mw-spacer" />
        <WorkspaceStrip />
        <ConnectionIndicator status={status} />
      </header>
      <main className="app__body app__body--mobile mw-main">
        <Pane />
      </main>
      <nav
        ref={tabsRef}
        className={cx(
          'tabbar mw-tabbar',
          tabScroll.left && 'mw-tabbar--scroll-left',
          tabScroll.right && 'mw-tabbar--scroll-right',
        )}
        aria-label="Sections"
      >
        <Tabs
          variant="pill"
          full
          tabs={tabItems}
          value={tab}
          onChange={(id) => setTab(id as MobileTab)}
        />
      </nav>
    </div>
  );
}

/** Connection pill: DS StatusDot + lowercase label for all four ConnectionStatus values. */
function ConnectionIndicator({ status }: { readonly status: ConnectionStatus }): React.JSX.Element {
  const label: Record<ConnectionStatus, string> = {
    connecting: 'connecting…',
    connected: 'connected',
    reconnecting: 'reconnecting…',
    error: 'version mismatch',
  };
  const dotStatus: Record<ConnectionStatus, StatusDotStatus> = {
    connecting: 'running',
    connected: 'done',
    reconnecting: 'running',
    error: 'failed',
  };
  const variant = status === 'connecting' ? 'reconnecting' : status;
  return (
    <span className={`conn cockpit__conn cockpit__conn--${variant}`} title={status === 'error' ? 'version mismatch — restart murder' : label[status]}>
      <StatusDot status={dotStatus[status]} pulse />
      <span className="cockpit__conn-label">{label[status]}</span>
    </span>
  );
}
