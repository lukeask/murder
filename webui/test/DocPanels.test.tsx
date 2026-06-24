/**
 * Doc-list panels (Plans / Notes / Reports) reskin smoke tests (Phase C2). Each panel is a thin
 * wrapper over DocListPanel, which now composes the DS Panel + ListRow. We seed the relevant `*`
 * slice directly and assert the DS chrome (`.mds-panel`, `.mds-row`), the doc name, the meta cells,
 * the pin toggle, selection wiring, and the plans-only "spawn planner" trailing action. Mirrors the
 * TicketsPanel exemplar smoke test.
 */

import type { PlanRow } from '@core/store/plans/plansSlice.js';
import type { NoteRow } from '@core/store/notes/notesSlice.js';
import type { ReportRow } from '@core/store/reports/reportsSlice.js';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { PlansPanel } from '../src/components/panels/PlansPanel.js';
import { NotesPanel } from '../src/components/panels/NotesPanel.js';
import { ReportsPanel } from '../src/components/panels/ReportsPanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const plan = (over: Partial<PlanRow> = {}): PlanRow => ({
  name: 'v0push.md',
  charCount: 4200,
  updatedAt: '2026-06-15T09:32:00Z',
  parent: null,
  ...over,
});

const note = (over: Partial<NoteRow> = {}): NoteRow => ({
  name: 'design.md',
  charCount: 1200,
  updatedAt: '2026-06-15T09:32:00Z',
  ...over,
});

const report = (over: Partial<ReportRow> = {}): ReportRow => ({
  name: 'run-42.md',
  charCount: 8800,
  updatedAt: '2026-06-15T09:32:00Z',
  ...over,
});

describe('PlansPanel (DS reskin)', () => {
  it('renders plan rows with the DS Panel + ListRow, meta, and spawn-planner action', () => {
    const { store } = makeStore();
    seedSlice(store, 'plans', { rows: [plan()], status: 'ready', error: null });
    seedSlice(store, 'favorites', { ids: new Set<string>(), status: 'ready', error: null });
    renderWithStore(<PlansPanel />, { store });

    expect(document.querySelector('.mds-panel')).toBeTruthy();
    expect(screen.getByText('plans')).toBeTruthy();
    expect(document.querySelector('.mds-row')).toBeTruthy();
    expect(screen.getByText('v0push.md')).toBeTruthy();
    // meta cells: char count + update time.
    expect(document.querySelector('.doc-meta')).toBeTruthy();
    // plans-only trailing action.
    expect(screen.getByTitle('Spawn planner')).toBeTruthy();
  });

  it('toggles the favorite via the DS pin button', () => {
    const { store } = makeStore();
    seedSlice(store, 'plans', { rows: [plan()], status: 'ready', error: null });
    seedSlice(store, 'favorites', { ids: new Set<string>(), status: 'ready', error: null });
    renderWithStore(<PlansPanel />, { store });
    // The pin renders as the DS star toggle button.
    const pin = document.querySelector('.mds-row__star');
    expect(pin).toBeTruthy();
    fireEvent.click(pin as Element);
  });

  it('shows the empty hint when ready with no rows', () => {
    const { store } = makeStore();
    seedSlice(store, 'plans', { rows: [], status: 'ready', error: null });
    seedSlice(store, 'favorites', { ids: new Set<string>(), status: 'ready', error: null });
    renderWithStore(<PlansPanel />, { store });
    expect(screen.getByText('No plans.')).toBeTruthy();
  });
});

describe('NotesPanel (DS reskin)', () => {
  it('renders note rows with the DS Panel + ListRow (no spawn action)', () => {
    const { store } = makeStore();
    seedSlice(store, 'notes', { rows: [note()], status: 'ready', error: null });
    seedSlice(store, 'favorites', { ids: new Set<string>(), status: 'ready', error: null });
    renderWithStore(<NotesPanel />, { store });

    expect(screen.getByText('notes')).toBeTruthy();
    expect(document.querySelector('.mds-row')).toBeTruthy();
    expect(screen.getByText('design.md')).toBeTruthy();
    expect(screen.queryByTitle('Spawn planner')).toBeNull();
  });
});

describe('ReportsPanel (DS reskin)', () => {
  it('renders report rows with the DS Panel + ListRow', () => {
    const { store } = makeStore();
    seedSlice(store, 'reports', { rows: [report()], status: 'ready', error: null });
    seedSlice(store, 'favorites', { ids: new Set<string>(), status: 'ready', error: null });
    renderWithStore(<ReportsPanel />, { store });

    expect(screen.getByText('reports')).toBeTruthy();
    expect(document.querySelector('.mds-row')).toBeTruthy();
    expect(screen.getByText('run-42.md')).toBeTruthy();
  });
});
