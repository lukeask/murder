/**
 * Toast smoke test: tone maps to the rail modifier class, the default glyph follows the tone, and
 * the × dismiss only appears with onClose and fires it.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Toast } from '../../src/components/ds/Toast.js';

afterEach(cleanup);

describe('ds/Toast', () => {
  it('maps tone to the rail modifier class', () => {
    const { container } = render(<Toast tone="failed" title="ticket failed" />);
    const root = container.querySelector('.mds-toast') as HTMLElement;
    expect(root.className).toContain('mds-toast--failed');
    expect(root.getAttribute('role')).toBe('status');
    expect(screen.getByText('ticket failed').className).toContain('mds-toast__title');
  });

  it('renders the default glyph for the tone', () => {
    const { container } = render(<Toast tone="done" title="crow finished" />);
    expect((container.querySelector('.mds-toast__glyph') as HTMLElement).textContent).toBe('✓');
  });

  it('shows the × dismiss only with onClose and fires it', () => {
    const { container, rerender } = render(<Toast tone="neutral" title="hi" />);
    expect(container.querySelector('.mds-toast__close')).toBeNull();

    const onClose = vi.fn();
    rerender(<Toast tone="neutral" title="hi" onClose={onClose} />);
    fireEvent.click(screen.getByLabelText('dismiss'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
