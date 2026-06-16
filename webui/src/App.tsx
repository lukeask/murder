/**
 * App — the web/mobile shell, rebuilt on the design-system primitives (Phase C1, "desktop cockpit").
 *
 * ## Layout (chrome only — data flow / IA unchanged)
 *  - Desktop (> 768px): a `.cockpit` grid of three rows — DS {@link NavBar} (Fraunces `murder` brand +
 *    a connection indicator in the trailing slot), a 3-rail body `[ left rail | Stage | right rail ]`,
 *    and a DS {@link KeybindBar} of display-only chord hints. Rails scroll independently; the Stage
 *    grows. The left/center/right PANEL ASSIGNMENTS are identical to the pre-reskin shell.
 *  - Mobile (≤ 768px, {@link MOBILE_QUERY}): a single pane switched by a bottom pill {@link Tabs} bar
 *    (stacked icon+label), with a DS header showing the brand + the current-view label. Same 10 tabs,
 *    same `MobilePane` switch — only the chrome changed.
 *
 * Responsive switching stays a single JS decision ({@link useMediaQuery}); everything else is CSS in
 * `styles/cockpit.css`. Store/bus wiring (the onConnect re-prime, every selector) is untouched.
 */

import { useAppStoreApi } from '@core/hooks/useAppStore.js';
import { useEffect, useState } from 'react';
import type { WsBusClient } from './bus/WsBusClient.js';
import { useThemeCssVars } from './theme/useThemeCssVars.js';
import { type ConnectionStatus, useConnectionStatus } from './useConnectionStatus.js';
import { MOBILE_QUERY, useMediaQuery } from './useMediaQuery.js';
import { PlansPanel } from './components/panels/PlansPanel.js';
import { NotesPanel } from './components/panels/NotesPanel.js';
import { ReportsPanel } from './components/panels/ReportsPanel.js';
import { TicketsPanel } from './components/panels/TicketsPanel.js';
import { HistoryPanel } from './components/panels/HistoryPanel.js';
import { RosterPanel } from './components/panels/RosterPanel.js';
import { UsagePanel } from './components/panels/UsagePanel.js';
import { TransitPanel } from './components/panels/TransitPanel.js';
import { SettingsPanel } from './components/panels/SettingsPanel.js';
import { Stage } from './components/stage/Stage.js';
import { NavBar, KeybindBar, type KeybindHint, StatusDot, type StatusDotStatus, Tabs, type TabItem, Icon, type IconName } from './components/ds/index.js';

/** The mobile tab set — one entry per top-level destination (panels + the chat Stage). */
const MOBILE_TABS = [
  'chat',
  'crows',
  'tickets',
  'plans',
  'notes',
  'reports',
  'history',
  'usage',
  'transit',
  'settings',
] as const;
type MobileTab = (typeof MOBILE_TABS)[number];

/** Per-tab DS line icon (stacked above the label in the pill switcher). */
const MOBILE_TAB_ICON: Record<MobileTab, IconName> = {
  chat: 'message-square',
  crows: 'crosshair',
  tickets: 'ticket',
  plans: 'file-text',
  notes: 'file-text',
  reports: 'file-text',
  history: 'git-branch',
  usage: 'gauge',
  transit: 'git-branch',
  settings: 'settings',
};

/**
 * Display-only keybind hints for the desktop bottom bar. Lowercase verb-noun per brand rules; these
 * mirror the desktop-cockpit template and are NOT wired to live handlers in this phase.
 */
const KEYBIND_HINTS: readonly KeybindHint[] = [
  { chord: 'C-1-0', desc: 'panels' },
  { chord: 'C-jk', desc: 'nav' },
  { chord: 'C-space', desc: 'chat' },
  { chord: 'C-s', desc: 'spawn' },
  { chord: 'C-p', desc: 'new plan' },
  { chord: 'C-t', desc: 'new ticket' },
  { chord: 'C-hl', desc: 'target' },
  { chord: 'C-o', desc: 'settings' },
];

export function App({ bus }: { readonly bus: WsBusClient }): React.JSX.Element {
  useThemeCssVars();
  const status = useConnectionStatus(bus);
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const storeApi = useAppStoreApi();

  // Re-prime every slice on each (re)connect. Slice invalidation is key-only, so a slice that
  // changed while disconnected stays stale until an unrelated event; priming closes that gap.
  // `onConnect` fires immediately if already connected (no race). The favorites + settings loads
  // also fire so the stars and theme reflect persisted state.
  useEffect(() => {
    const off = bus.onConnect(() => {
      const a = storeApi.getState().actions;
      void a.roster.refresh();
      void a.tickets.refresh();
      void a.plans.refresh();
      void a.notes.refresh();
      void a.reports.refresh();
      void a.history.refresh();
      void a.transit.refresh();
      void a.usage.refresh();
      void a.conversations.refresh();
      void a.favorites.load();
      void a.settings.load();
    });
    return off;
  }, [bus, storeApi]);

  // `data-layout` is preserved (tests + any external hooks key off it). The DOM tree differs between
  // desktop (cockpit grid) and mobile (single pane) — the one thing CSS alone can't express.
  return (
    <div className="app" data-layout={isMobile ? 'mobile' : 'desktop'}>
      {isMobile ? <MobileLayout status={status} /> : <DesktopLayout status={status} />}
    </div>
  );
}

/** Desktop: NavBar (brand + connection) / 3-rail body / KeybindBar. Panel assignments unchanged. */
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
          <TransitPanel />
          <SettingsPanel />
        </aside>
      </div>
      <KeybindBar hints={[...KEYBIND_HINTS]} help={null} />
    </div>
  );
}

/** Mobile: DS header (brand + view label) / single pane / bottom pill tab bar. */
function MobileLayout({ status }: { readonly status: ConnectionStatus }): React.JSX.Element {
  const [tab, setTab] = useState<MobileTab>('chat');
  const tabItems: TabItem[] = MOBILE_TABS.map((t) => ({
    id: t,
    label: t,
    icon: <Icon name={MOBILE_TAB_ICON[t]} size={18} />,
  }));
  return (
    <div className="mw-app">
      <header className="mw-header">
        <span className="mw-brand">murder</span>
        <span className="mw-view">{tab}</span>
        <span className="mw-spacer" />
        <ConnectionIndicator status={status} />
      </header>
      <main className="app__body app__body--mobile mw-main">
        <MobilePane tab={tab} />
      </main>
      <nav className="tabbar mw-tabbar" aria-label="Sections">
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

function MobilePane({ tab }: { readonly tab: MobileTab }): React.JSX.Element {
  switch (tab) {
    case 'chat':
      return <Stage />;
    case 'crows':
      return <RosterPanel />;
    case 'tickets':
      return <TicketsPanel />;
    case 'plans':
      return <PlansPanel />;
    case 'notes':
      return <NotesPanel />;
    case 'reports':
      return <ReportsPanel />;
    case 'history':
      return <HistoryPanel />;
    case 'usage':
      return <UsagePanel />;
    case 'transit':
      return <TransitPanel />;
    case 'settings':
      return <SettingsPanel />;
    default:
      return tab satisfies never;
  }
}

/**
 * ConnectionIndicator — the reskinned connection pill: a DS {@link StatusDot} + a terse lowercase
 * label. Drives all four {@link ConnectionStatus} states off the existing `useConnectionStatus`. The
 * status→dot mapping reuses the DS crow-state palette (connected→done/green, connecting/reconnecting→
 * running/pending, error→failed/red); the `pulse` breathe is only meaningful on the "running" status.
 */
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
  // Visual variant class for the label color (kept distinct from the dot's crow-state palette).
  const variant = status === 'connecting' ? 'reconnecting' : status;
  return (
    <span className={`conn cockpit__conn cockpit__conn--${variant}`} title={status === 'error' ? 'version mismatch — restart murder' : label[status]}>
      <StatusDot status={dotStatus[status]} pulse />
      <span className="cockpit__conn-label">{label[status]}</span>
    </span>
  );
}
