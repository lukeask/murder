/**
 * TopBar tests — focuses on the connection-state badge (first-run UX step 4). The branding +
 * panel-label formatting is covered by `selectors/barSelectors.test.ts`; here we drive the
 * process-global `connectionStore` singleton (the way the index.tsx transport wiring does) and
 * assert the right-pinned badge appears/omits per status.
 *
 * The singleton is reset to `'unknown'` in `beforeEach`/`afterEach` (mirroring the
 * `toastStore.getState().clear()` reset idiom in NewTicketModal.test.tsx) so cases never leak status
 * into one another.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { TopBar } from '../../src/components/TopBar.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import {
  type ConnectionStatus,
  connectionStore,
} from '../../src/store/connection/connectionStore.js';

function Harness(): JSX.Element {
  // A minimal panel-store context so TopBar's `usePanelStore` resolves; the visible set is irrelevant
  // to the badge assertions.
  const stores = createInputStores(['plans'], 'plans');
  return (
    <InputStoresProvider value={stores}>
      <TopBar project="demo" />
    </InputStoresProvider>
  );
}

function frameFor(status: ConnectionStatus): string {
  connectionStore.getState().setStatus(status);
  const { lastFrame } = render(<Harness />);
  return lastFrame() ?? '';
}

describe('TopBar — connection badge', () => {
  beforeEach(() => {
    connectionStore.getState().setStatus('unknown');
  });
  afterEach(() => {
    connectionStore.getState().setStatus('unknown');
  });

  it('shows no badge for unknown', () => {
    const frame = frameFor('unknown');
    expect(frame).not.toContain('connecting');
    expect(frame).not.toContain('reconnecting');
    expect(frame).not.toContain('version mismatch');
  });

  it('shows no badge for connected', () => {
    const frame = frameFor('connected');
    expect(frame).not.toContain('connecting');
    expect(frame).not.toContain('reconnecting');
    expect(frame).not.toContain('version mismatch');
  });

  it('shows connecting… for connecting', () => {
    expect(frameFor('connecting')).toContain('connecting…');
  });

  it('shows [reconnecting] for reconnecting', () => {
    expect(frameFor('reconnecting')).toContain('[reconnecting]');
  });

  it('shows the restart prompt for version-mismatch', () => {
    expect(frameFor('version-mismatch')).toContain('[version mismatch — restart murder]');
  });

  it('keeps the branding mark regardless of status', () => {
    expect(frameFor('reconnecting')).toContain('murder');
  });
});
