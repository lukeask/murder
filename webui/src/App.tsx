/** App — web/mobile shell on design-system primitives (desktop cockpit + mobile tabs). */

import { useAppStore, useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { DEFAULT_THEME_ID, hasTheme, type ThemeId } from '@murder/ui-core/theme/palettes.js';
import { setTheme } from '@murder/ui-core/theme/themeStore.js';
import { resolveBarWidgetConfig } from '@murder/ui-core/selectors/barWidgetRegistry.js';
import { useEffect, useMemo, useState, type ComponentType } from 'react';
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
import { NewWorkDialog } from './components/modals/NewWorkDialog.js';
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
import {
  NavBar,
  KeybindBar,
  StatusDot,
  type StatusDotStatus,
  Icon,
  IconButton,
  type IconName,
  cx,
} from './components/ds/index.js';
import { PanelToggleStrip } from './components/PanelToggleStrip.js';
import { CrowMark } from './components/CrowMark.js';
import { CaptureSheet } from './components/mobile/CaptureSheet.js';
import { MoreSheet } from './components/mobile/MoreSheet.js';
import { useComposerStores } from './composer/ComposerStoresProvider.js';
import { WorkspaceStrip } from './components/WorkspaceStrip.js';
import { UsageBarSegment } from './components/UsageBarSegment.js';
import { desktopKeybindHints } from './commandModifierPrefix.js';
import { useFocusedPanelId } from './panelFocus.js';
import { SETTINGS_PANEL_HINTS, useKeybindModeHints } from './keybindModeHints.js';
import { useWorkspaceCountSync, useWorkspaceSwitchFlash } from './composer/useWorkspaceBridge.js';
import { useDesktopKeybinds } from './useDesktopKeybinds.js';
import { usePanelIsVisible } from './panelVisibility.js';
import { resolveProjectName } from './projectName.js';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import { enabledBarWidgetIds } from '@murder/ui-core/selectors/barWidgetRegistry.js';

/** Every pane mountable in the mobile single-pane body. */
const MOBILE_PANES = {
  chat: Stage,
  crows: RosterPanel,
  notes: NotesPanel,
  workflows: WorkflowsPanel,
  plans: PlansPanel,
  reports: ReportsPanel,
  history: HistoryPanel,
  usage: UsagePanel,
  tree: TreePanel,
  settings: SettingsPanel,
} satisfies Record<string, ComponentType>;
type MobilePaneId = keyof typeof MOBILE_PANES;

/** Primary thumb destinations flanking the center capture button. */
const MOBILE_PRIMARY_TABS: readonly { readonly id: MobilePaneId; readonly icon: IconName }[] = [
  { id: 'chat', icon: 'message-square' },
  { id: 'crows', icon: 'crosshair' },
  { id: 'notes', icon: 'file-text' },
];

/** Secondary panes behind the "More" sheet. */
const MOBILE_MORE_ITEMS: readonly { readonly id: MobilePaneId; readonly icon: IconName }[] = [
  { id: 'workflows', icon: 'git-branch' },
  { id: 'plans', icon: 'file-text' },
  { id: 'reports', icon: 'file-text' },
  { id: 'history', icon: 'git-branch' },
  { id: 'usage', icon: 'gauge' },
  { id: 'tree', icon: 'git-commit' },
  { id: 'settings', icon: 'settings' },
];

/** Composer seed for the capture sheet's "Feature spec" action. */
const SPEC_TEMPLATE = `Feature spec —

Problem
…

Proposal
…

Done when
…
`;

/** Theme applied on phones when the user hasn't picked one (still on the shipped default). */
const MOBILE_DEFAULT_THEME_ID: ThemeId = 'crow-light';

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
  | { readonly kind: 'newWork' }
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

export function App({
  bus,
  onSwitchRepo,
  repositoryHint = null,
}: {
  readonly bus: ApplicationConnectionClient;
  /** Return to the boot picker (closes this repo's WS; other repos' client_ids stay). */
  readonly onSwitchRepo?: () => void;
  /** Basename hint when settings.project is not yet hydrated. */
  readonly repositoryHint?: string | null;
}): React.JSX.Element {
  useThemeCssVars();
  const status = useConnectionStatus(bus);
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const storeApi = useAppStoreApi();

  const [dialog, setDialog] = useState<ShellDialog>(null);
  const closeDialog = (): void => setDialog(null);

  const creationApi = useMemo(
    () => ({
      openSpawn: () => setDialog({ kind: 'spawn' }),
      openNewWork: () => setDialog({ kind: 'newWork' }),
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
      let id: ThemeId = hasTheme(theme) ? theme : DEFAULT_THEME_ID;
      // Mobile defaults to the ink-and-paper crow theme while the user is still on the shipped
      // default; an explicit theme choice always wins.
      if (isMobile && id === DEFAULT_THEME_ID && hasTheme(MOBILE_DEFAULT_THEME_ID)) {
        id = MOBILE_DEFAULT_THEME_ID;
      }
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
  }, [storeApi, isMobile]);

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
          {...(onSwitchRepo !== undefined ? { onSwitchRepo } : {})}
          {...(repositoryHint !== undefined ? { repositoryHint } : {})}
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
  onSwitchRepo,
  repositoryHint,
}: {
  readonly status: ConnectionStatus;
  readonly isMobile: boolean;
  readonly creationApi: CreationDialogsApi;
  readonly dialog: ShellDialog;
  readonly setDialog: (d: ShellDialog) => void;
  readonly closeDialog: () => void;
  readonly onSwitchRepo?: () => void;
  readonly repositoryHint?: string | null;
}): React.JSX.Element {
  useDesktopKeybinds(!isMobile, creationApi);
  useWorkspaceCountSync();

  return (
    <div className="app" data-layout={isMobile ? 'mobile' : 'desktop'}>
      {isMobile ? (
        <MobileLayout
          status={status}
          creationApi={creationApi}
          {...(onSwitchRepo !== undefined ? { onSwitchRepo } : {})}
          {...(repositoryHint !== undefined ? { repositoryHint } : {})}
        />
      ) : (
        <DesktopLayout
          status={status}
          {...(onSwitchRepo !== undefined ? { onSwitchRepo } : {})}
          {...(repositoryHint !== undefined ? { repositoryHint } : {})}
        />
      )}
      <ToastHost />
      <MurderConfirmDialog />
      {dialog?.kind === 'spawn' && <SpawnRogueDialog onClose={closeDialog} />}
      {dialog?.kind === 'newWork' && <NewWorkDialog onClose={closeDialog} />}
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
function DesktopLayout({
  status,
  onSwitchRepo,
  repositoryHint = null,
}: {
  readonly status: ConnectionStatus;
  readonly onSwitchRepo?: () => void;
  readonly repositoryHint?: string | null;
}): React.JSX.Element {
  const modifier = useAppStore((s) => s.settings.modifier);
  const keyOverrides = useAppStore((s) => s.settings.keyOverrides);
  const barWidgets = useAppStore((s) => s.settings.barWidgets);
  const focusedPanelId = useFocusedPanelId();
  const modeHintsFromDialog = useKeybindModeHints();
  const docOpen = useAppStore((s) => s.docView.open !== null);
  const modeHints =
    modeHintsFromDialog ?? (focusedPanelId === 'settings' ? SETTINGS_PANEL_HINTS : null);
  const stageSurface =
    focusedPanelId === null && modeHints == null
      ? docOpen
        ? ('doc' as const)
        : ('transcript' as const)
      : null;
  const hints = useMemo(
    () =>
      desktopKeybindHints(
        modifier,
        keyOverrides,
        focusedPanelId,
        undefined,
        modeHints,
        stageSurface,
      ),
    [modifier, keyOverrides, focusedPanelId, modeHints, stageSurface],
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
  const storeProject = useAppStore((s) => s.settings.project);
  const project = useMemo(
    () => resolveProjectName({ fromStore: storeProject }) ?? repositoryHint,
    [storeProject, repositoryHint],
  );
  return (
    <div className="cockpit">
      <NavBar
        brand="murder"
        project={project}
        panels={<PanelToggleStrip />}
        trailing={
          <>
            {onSwitchRepo !== undefined ? (
              <span className="repo-picker__switch">
                <IconButton label="Switch repository" onClick={onSwitchRepo}>
                  <Icon name="git-branch" size={16} />
                </IconButton>
              </span>
            ) : null}
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
        <section
          className={cx('cockpit__stage', stageFlash && 'cockpit__stage--workspace-flash')}
          data-focus-id="stage"
        >
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

/**
 * Mobile: header / single pane / bottom bar. The bar is intent-based — chat, crows, a raised
 * center capture button, notes, and a "More" sheet for the secondary panes — rather than a
 * transplanted TUI panel list.
 */
function MobileLayout({
  status,
  creationApi,
  onSwitchRepo,
  repositoryHint = null,
}: {
  readonly status: ConnectionStatus;
  readonly creationApi: CreationDialogsApi;
  readonly onSwitchRepo?: () => void;
  readonly repositoryHint?: string | null;
}): React.JSX.Element {
  const [pane, setPane] = useState<MobilePaneId>('chat');
  const [captureOpen, setCaptureOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const { chatInput } = useComposerStores();
  const Pane = MOBILE_PANES[pane];
  const storeProject = useAppStore((s) => s.settings.project);
  const project = useMemo(
    () => resolveProjectName({ fromStore: storeProject }) ?? repositoryHint,
    [storeProject, repositoryHint],
  );

  const isPrimary = MOBILE_PRIMARY_TABS.some((t) => t.id === pane);
  const goToChat = (): void => setPane('chat');
  const seedSpec = (): void => {
    chatInput.getState().clear();
    chatInput.getState().insert(SPEC_TEMPLATE);
    setPane('chat');
  };

  const renderTab = (id: MobilePaneId, icon: IconName): React.JSX.Element => (
    <button
      key={id}
      type="button"
      className="mw-tab"
      data-on={pane === id ? 'true' : undefined}
      aria-current={pane === id ? 'page' : undefined}
      onClick={() => setPane(id)}
    >
      <Icon name={icon} size={20} />
      <span className="mw-tab__label">{id}</span>
    </button>
  );

  return (
    <div className="mw-app">
      <header className="mw-header">
        <span className="mw-brand">
          <CrowMark size={20} />
          murder
          {project !== null && project !== '' ? (
            <>
              <span className="mw-brand-sep" aria-hidden="true">
                ·
              </span>
              <span className="mw-project">{project}</span>
            </>
          ) : null}
        </span>
        {!isPrimary ? <span className="mw-view">{pane}</span> : null}
        <span className="mw-spacer" />
        {onSwitchRepo !== undefined ? (
          <IconButton label="Switch repository" onClick={onSwitchRepo}>
            <Icon name="git-branch" size={16} />
          </IconButton>
        ) : null}
        <WorkspaceStrip />
        <ConnectionIndicator status={status} />
      </header>
      <main className="app__body app__body--mobile mw-main">
        <Pane />
      </main>
      <nav className="mw-tabbar" aria-label="Sections">
        {renderTab('chat', 'message-square')}
        {renderTab('crows', 'crosshair')}
        <button
          type="button"
          className="mw-capture"
          aria-label="Capture"
          onClick={() => setCaptureOpen(true)}
        >
          <Icon name="plus" size={24} />
        </button>
        {renderTab('notes', 'file-text')}
        <button
          type="button"
          className="mw-tab"
          data-on={!isPrimary || moreOpen ? 'true' : undefined}
          aria-current={!isPrimary ? 'page' : undefined}
          onClick={() => setMoreOpen(true)}
        >
          <Icon name="menu" size={20} />
          <span className="mw-tab__label">more</span>
        </button>
      </nav>
      {captureOpen ? (
        <CaptureSheet
          onClose={() => setCaptureOpen(false)}
          onNote={creationApi.openNoteCapture}
          onPrompt={goToChat}
          onSpec={seedSpec}
        />
      ) : null}
      {moreOpen ? (
        <MoreSheet
          items={MOBILE_MORE_ITEMS.map((t) => ({ id: t.id, label: t.id, icon: t.icon }))}
          activeId={isPrimary ? null : pane}
          onSelect={(id) => setPane(id as MobilePaneId)}
          onClose={() => setMoreOpen(false)}
        />
      ) : null}
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
