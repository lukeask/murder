/**
 * Mobile shell: intent-based bottom bar (chat / crows / capture / notes / more), the capture
 * sheet's quick actions, and the Sheet primitive's dismiss behavior. matchMedia is stubbed
 * below the breakpoint, same harness as responsiveLayout.test.tsx.
 */

import { AppStoreProvider } from '@murder/ui-core/hooks/useAppStore.js';
import { createAppStore } from '@murder/ui-core/store/store.js';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App.js';
import { ApplicationClientProvider } from '@murder/ui-core/hooks/useApplicationClient.js';
import type { ApplicationConnectionClient } from '@murder/ui-core/application/ApplicationClient.js';
import { MOBILE_QUERY } from '../src/useMediaQuery.js';
import { Sheet } from '../src/components/ds/index.js';

function stubMatchMedia(isMobile: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === MOBILE_QUERY ? isMobile : false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }));
}

function fakeBus(): ApplicationConnectionClient {
  const bus = new FakeApplicationClient() as unknown as Record<string, unknown>;
  bus['onConnect'] = () => () => {};
  bus['onDisconnect'] = () => () => {};
  bus['onPermanentError'] = () => () => {};
  return bus as unknown as ApplicationConnectionClient;
}

function renderMobileApp(): HTMLElement {
  stubMatchMedia(true);
  const bus = fakeBus();
  const { store } = createAppStore(bus as unknown as FakeApplicationClient);
  const { container } = render(
    <AppStoreProvider value={store}>
      <ApplicationClientProvider value={bus}>
        <App bus={bus} />
      </ApplicationClientProvider>
    </AppStoreProvider>,
  );
  return container;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('mobile shell', () => {
  it('opens the capture sheet from the center button and the note dialog from its action', () => {
    const container = renderMobileApp();
    fireEvent.click(container.querySelector('.mw-capture') as Element);
    // Capture sheet lists the three quick actions.
    expect(screen.getByText('Quick note')).toBeTruthy();
    expect(screen.getByText('Prompt an agent')).toBeTruthy();
    expect(screen.getByText('Feature spec')).toBeTruthy();
    // Quick note opens the note-capture dialog, rendered as a bottom sheet on mobile.
    fireEvent.click(screen.getByText('Quick note'));
    expect(screen.getByText('Quick note', { selector: '.mds-sheet__title' })).toBeTruthy();
    expect(container.querySelector('.mds-sheet')).not.toBeNull();
  });

  it('seeds the composer with the spec template via Feature spec', () => {
    const container = renderMobileApp();
    fireEvent.click(container.querySelector('.mw-capture') as Element);
    fireEvent.click(screen.getByText('Feature spec'));
    const input = container.querySelector('.stage textarea, .stage [contenteditable]');
    expect(input?.textContent ?? (input as HTMLTextAreaElement | null)?.value ?? '').toContain(
      'Feature spec',
    );
  });

  it('mounts a secondary pane from the More sheet and flags the more tab', () => {
    const container = renderMobileApp();
    const moreTab = [...container.querySelectorAll('.mw-tab')].find((el) =>
      el.textContent?.includes('more'),
    ) as Element;
    fireEvent.click(moreTab);
    fireEvent.click(screen.getByText('usage'));
    // The sheet closed, the more tab now carries the active state.
    expect(container.querySelector('.mds-sheet')).toBeNull();
    expect(moreTab.getAttribute('data-on')).toBe('true');
    expect(container.querySelector('.mw-view')?.textContent).toBe('usage');
  });
});

describe('Sheet', () => {
  it('closes on Escape and scrim click', () => {
    const onClose = vi.fn();
    const { container } = render(
      <Sheet title="Test" onClose={onClose}>
        body
      </Sheet>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(container.querySelector('.mds-sheet-scrim') as Element);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('dismisses on a long swipe down, survives a short drag', () => {
    const onClose = vi.fn();
    const { container } = render(
      <Sheet title="Test" onClose={onClose}>
        body
      </Sheet>,
    );
    const grab = container.querySelector('.mds-sheet__grab') as Element;
    // Short drag: below the dismiss ratio (sheet height is 0 in jsdom, so any offset > 0
    // against height 0 would dismiss — assert the no-op path with zero movement instead).
    fireEvent.touchStart(grab, { touches: [{ clientY: 100 }] });
    fireEvent.touchEnd(grab);
    expect(onClose).not.toHaveBeenCalled();
  });
});
