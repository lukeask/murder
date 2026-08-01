/** App — web/mobile shell on design-system primitives (desktop cockpit + mobile tabs). */

import { useAppStoreApi } from '@murder/ui-core/hooks/useAppStore.js';
import { DEFAULT_THEME_ID, hasTheme, type ThemeId } from '@murder/ui-core/theme/palettes.js';
import { setTheme } from '@murder/ui-core/theme/themeStore.js';
import { useEffect, useMemo, useRef, useState, type ComponentType } from 'react';
import { CreationDialogsProvider } from './creationDialogs.js';
import { useThemeCssVars } from './theme/useThemeCssVars.js';
import { type ConnectionStatus, useConnectionStatus } from './useConnectionStatus.js';
import type { ApplicationConnectionClient } from '@murder/ui-core/application/ApplicationClient.js';
import { MOBILE_QUERY, useMediaQuery } from './useMediaQuery.js';
import { PlansPanel } from './components/panels/PlansPanel.js';
import { NotesPanel } from './components/panels/NotesPanel.js';
import { ReportsPanel } from './components/panels/ReportsPanel.js';
import { TicketsPanel } from './components/panels/TicketsPanel.js';
import { HistoryPanel } from './components/panels/HistoryPanel.js';
import { RosterPanel } from './components/panels/RosterPanel.js';
import { UsagePanel } from './components/panels/UsagePanel.js';
import { TreePanel } from './components/panels/TreePanel.js';
import { SettingsPanel } from './components/panels/SettingsPanel.js';
import { Stage } from './components/stage/Stage.js';
import { ToastHost } from './components/ToastHost.js';
import { NewTicketDialog } from './components/modals/NewTicketDialog.js';
import { NewPlanDialog } from './components/modals/NewPlanDialog.js';
import { SpawnRogueDialog } from './components/modals/SpawnRogueDialog.js';
import { NavBar, KeybindBar, type KeybindHint, StatusDot, type StatusDotStatus, Tabs, type TabItem, Icon, type IconName, cx } from './components/ds/index.js';
import { useDesktopKeybinds } from './useDesktopKeybinds.js';

const MOBILE_TAB_DEFS: readonly {
  readonly id: string;
  readonly icon: IconName;
  readonly Pane: ComponentType;
}[] = [
  { id: 'chat', icon: 'message-square', Pane: Stage },
  { id: 'crows', icon: 'crosshair', Pane: RosterPanel },
  { id: 'workflows', icon: 'ticket', Pane: TicketsPanel },
  { id: 'plans', icon: 'file-text', Pane: PlansPanel },
  { id: 'notes', icon: 'file-text', Pane: NotesPanel },
  { id: 'reports', icon: 'file-text', Pane: ReportsPanel },
  { id: 'history', icon: 'git-branch', Pane: HistoryPanel },
  { id: 'usage', icon: 'gauge', Pane: UsagePanel },
  { id: 'tree', icon: 'git-commit', Pane: TreePanel },
  { id: 'settings', icon: 'settings', Pane: SettingsPanel },
];
type MobileTab = (typeof MOBILE_TAB_DEFS)[number]['id'];

/** Desktop bottom-bar chords (`C-` label; live handler reads `settings.modifier`). */
const KEYBIND_HINTS: readonly KeybindHint[] = [
  { chord: 'C-1-0', desc: 'panels' },
  { chord: 'C-space', desc: 'chat' },
  { chord: 'C-hl', desc: 'target' },
  { chord: 'C-s', desc: 'spawn' },
  { chord: 'C-t', desc: 'ticket' },
  { chord: 'C-p', desc: 'plan' },
  { chord: 'C-o', desc: 'settings' },
];

const REFRESH_ON_CONNECT = [
  'roster',
  'tickets',
  'plans',
  'notes',
  'reports',
  'history',
  'transit',
  'usage',
  'conversations',
] as const;
const LOAD_ON_CONNECT = ['favorites', 'themes', 'settings'] as const;

export function App({ bus }: { readonly bus: ApplicationConnectionClient }): React.JSX.Element {
  useThemeCssVars();
  const status = useConnectionStatus(bus);
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const storeApi = useAppStoreApi();

  const [dialog, setDialog] = useState<'spawn' | 'ticket' | 'plan' | null>(null);
  const creationApi = useMemo(
    () => ({
      openSpawn: () => setDialog('spawn'),
      openTicket: () => setDialog('ticket'),
      openPlan: () => setDialog('plan'),
    }),
    [],
  );

  useDesktopKeybinds(!isMobile, creationApi);

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
      <div className="app" data-layout={isMobile ? 'mobile' : 'desktop'}>
        {isMobile ? <MobileLayout status={status} /> : <DesktopLayout status={status} />}
        <ToastHost />
        {dialog === 'spawn' && <SpawnRogueDialog onClose={() => setDialog(null)} />}
        {dialog === 'ticket' && <NewTicketDialog onClose={() => setDialog(null)} />}
        {dialog === 'plan' && <NewPlanDialog onClose={() => setDialog(null)} />}
      </div>
    </CreationDialogsProvider>
  );
}

/** Desktop: NavBar / 3-rail body / KeybindBar. */
function DesktopLayout({ status }: { readonly status: ConnectionStatus }): React.JSX.Element {
  return (
    <div className="cockpit">
      <NavBar brand="murder" trailing={<ConnectionIndicator status={status} />} />
      <div className="cockpit__cols">
        <aside className="rail cockpit__rail cockpit__rail--left">
          <TicketsPanel />
          <PlansPanel />
          <NotesPanel />
          <ReportsPanel />
          <HistoryPanel />
        </aside>
        <section className="cockpit__stage">
          <Stage />
        </section>
        <aside className="rail cockpit__rail cockpit__rail--right">
          <RosterPanel />
          <UsagePanel />
          <TreePanel />
          <SettingsPanel />
        </aside>
      </div>
      <KeybindBar hints={KEYBIND_HINTS} />
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
