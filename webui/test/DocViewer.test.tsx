/**
 * DocViewer (DS reskin) renders the open doc off a seeded `docView` slice: the DS Panel with the kind
 * Tag + name in the title, and the markdown body in the scroll region. The close action stays wired
 * through `docView.close()` (a DS IconButton).
 */

import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { DocViewer } from '../src/components/stage/DocViewer.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

describe('DocViewer (DS reskin)', () => {
  it('renders nothing when no doc is open', () => {
    const { store } = makeStore();
    const { container } = renderWithStoreContainer(store);
    expect(container.querySelector('.mds-doc')).toBeNull();
  });

  it('renders the open doc in a DS Panel with kind Tag + body', () => {
    const { store } = makeStore();
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'split-orchestrator' },
      body: '# Decompose the Orchestrator',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store });

    expect(document.querySelector('.mds-doc .mds-panel')).toBeTruthy();
    expect(screen.getByText('plan')).toBeTruthy();
    expect(screen.getByText('split-orchestrator')).toBeTruthy();
    expect(screen.getByText(/Decompose the Orchestrator/)).toBeTruthy();
    // Close routes through a DS IconButton.
    expect(screen.getByLabelText('close')).toBeTruthy();
  });
});

function renderWithStoreContainer(store: ReturnType<typeof makeStore>['store']): {
  container: HTMLElement;
} {
  renderWithStore(<DocViewer />, { store });
  return { container: document.body };
}
