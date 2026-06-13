/**
 * App — the web/mobile shell. Composes the full UI ported from the Ink TUI: a left rail (plans,
 * notes, reports, tickets, history), a center Stage (chat transcript + chat input + tmux terminal
 * frame, with doc/ticket overlays), and a right rail (roster/crows, usage, transit, settings).
 *
 * ## Responsive strategy (the one JS-level layout decision; the rest is CSS)
 *  - Desktop (> 768px): three regions side-by-side — `[ left rail | Stage | right rail ]` — mirroring
 *    the TUI's landscape layout. Each rail scrolls independently; the Stage grows to fill the middle.
 *  - Mobile (≤ 768px, {@link MOBILE_QUERY}): a single column showing ONE panel at a time, chosen by a
 *    thumb-friendly bottom tab bar. This is a genuinely different DOM tree (one panel, not three
 *    regions) — the only thing CSS alone can't express — so {@link useMediaQuery} governs it.
 *
 * All visual styling lives in CSS (`styles/app.css`) keyed off the theme CSS vars + layout tokens;
 * this file only chooses the structural tree and wires slice refresh on (re)connect.
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

  return (
    <div className="app" data-layout={isMobile ? 'mobile' : 'desktop'}>
      <header className="app__header">
        <span className="app__brand">murder</span>
        <ConnectionPill status={status} />
      </header>
      {isMobile ? <MobileLayout /> : <DesktopLayout />}
    </div>
  );
}

/** Desktop: the three-region TUI-like layout. Each rail is a scroll column of panels. */
function DesktopLayout(): React.JSX.Element {
  return (
    <main className="app__body app__body--desktop">
      <aside className="rail rail--left">
        <TicketsPanel />
        <PlansPanel />
        <NotesPanel />
        <ReportsPanel />
        <HistoryPanel />
      </aside>
      <section className="app__stage">
        <Stage />
      </section>
      <aside className="rail rail--right">
        <RosterPanel />
        <UsagePanel />
        <TransitPanel />
        <SettingsPanel />
      </aside>
    </main>
  );
}

/** Mobile: a single panel at a time, switched by the bottom tab bar (thumb-friendly hit targets). */
function MobileLayout(): React.JSX.Element {
  const [tab, setTab] = useState<MobileTab>('chat');
  return (
    <>
      <main className="app__body app__body--mobile">
        <MobilePane tab={tab} />
      </main>
      <nav className="tabbar" aria-label="Sections">
        {MOBILE_TABS.map((t) => (
          <button
            key={t}
            type="button"
            className="tabbar__tab"
            data-on={t === tab}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
    </>
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

function ConnectionPill({ status }: { readonly status: ConnectionStatus }): React.JSX.Element {
  const label: Record<ConnectionStatus, string> = {
    connecting: 'connecting…',
    connected: 'connected',
    reconnecting: 'reconnecting…',
    error: 'version mismatch — restart murder',
  };
  const variant = status === 'connecting' ? 'reconnecting' : status;
  return (
    <span className={`conn conn--${variant}`}>
      <span className="conn__dot" />
      {label[status]}
    </span>
  );
}
