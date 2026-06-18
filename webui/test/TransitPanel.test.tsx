/**
 * TransitPanel (Phase C2 DS reskin) smoke test — mirrors the TicketsPanel exemplar. Seeds a ready
 * `transit` slice (raw lanes/commits — Transit reads the slice directly, no selector) and asserts:
 * the DS Panel title, a lane branch name, a commit row (short sha + subject), and that clicking a
 * commit opens its detail block. Also checks the empty hint. The local selectedSha + ageLabel logic
 * is kept byte-for-byte from the original; this exercises the reskinned DOM.
 */

import type { TransitLane } from '@core/store/transit/transitSlice.js';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { TransitPanel } from '../src/components/panels/TransitPanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

const lane = (over: Partial<TransitLane>): TransitLane => ({
  branch: 'main',
  isMain: true,
  worktreePath: null,
  headSha: 'aaaaaaa',
  forkSha: null,
  commits: [
    {
      sha: 'aaaaaaa0000',
      short: 'aaaaaaa',
      subject: 'split orchestrator',
      body: 'a longer body line',
      tsEpoch: Math.floor(Date.now() / 1000) - 3600,
      parents: [],
    },
  ],
  ...over,
});

describe('TransitPanel (DS reskin)', () => {
  it('renders lanes + commits in the DS Panel and opens a commit detail on click', () => {
    const { store } = makeStore();
    seedSlice(store, 'transit', {
      lanes: [lane({})],
      status: 'ready',
      error: null,
    });
    renderWithStore(<TransitPanel />, { store });

    // DS Panel container + title.
    expect(document.querySelector('.mds-panel')).toBeTruthy();
    expect(screen.getByText('git tree')).toBeTruthy();
    // Lane branch + commit row.
    expect(screen.getByText('main')).toBeTruthy();
    expect(screen.getByText('aaaaaaa')).toBeTruthy();
    expect(screen.getByText('split orchestrator')).toBeTruthy();

    // Clicking a commit opens its detail block (body surfaces).
    const commit = document.querySelector('.transit-commit') as HTMLElement;
    fireEvent.click(commit);
    expect(document.querySelector('.transit-detail')).toBeTruthy();
    expect(screen.getByText('a longer body line')).toBeTruthy();
  });

  it('shows the empty hint when the slice is ready with no lanes', () => {
    const { store } = makeStore();
    seedSlice(store, 'transit', { lanes: [], status: 'ready', error: null });
    renderWithStore(<TransitPanel />, { store });
    expect(screen.getByText('No branches.')).toBeTruthy();
  });
});
