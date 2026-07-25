/**
 * ToastHost: a pushed toastStore entry renders via the DS Toast (tone + title). Dismiss clears it.
 * The toast singleton is shared global state — clear between cases (toastStore's own idiom).
 */

import { toastStore } from '@core/store/toast/toastStore.js';
import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ToastHost } from '../src/components/ToastHost.js';
import { renderWithStore } from './helpers.js';

beforeEach(() => {
  toastStore.getState().clear();
});

afterEach(() => {
  toastStore.getState().clear();
  cleanup();
});

describe('ToastHost', () => {
  it('renders a pushed toast with the severity tone', () => {
    renderWithStore(<ToastHost />);
    expect(document.querySelector('.toast-host')).toBeNull();

    act(() => {
      toastStore.getState().push('send failed: boom', { severity: 'error', ttlMs: 60_000 });
    });

    const host = document.querySelector('.toast-host') as HTMLElement;
    expect(host).toBeTruthy();
    const root = host.querySelector('.mds-toast') as HTMLElement;
    expect(root.className).toContain('mds-toast--error');
    expect(screen.getByText('send failed: boom')).toBeTruthy();
  });

  it('dismiss removes the toast from the rack', () => {
    act(() => {
      toastStore.getState().push('orphan toast', { ttlMs: 60_000 });
    });
    renderWithStore(<ToastHost />);
    expect(screen.getByText('orphan toast')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('dismiss'));
    expect(screen.queryByText('orphan toast')).toBeNull();
    expect(document.querySelector('.toast-host')).toBeNull();
  });
});
