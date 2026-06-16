/**
 * Dialog smoke test: hidden when closed, renders title/body/footer when open, and closes via the ×
 * button, scrim click, and Escape. Pins the feedback exemplar contract.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Dialog } from '../../src/components/ds/Dialog.js';

afterEach(cleanup);

describe('ds/Dialog', () => {
  it('renders nothing when closed', () => {
    const { container } = render(
      <Dialog open={false} title="Confirm">
        body
      </Dialog>,
    );
    expect(container.querySelector('.mds-dialog')).toBeNull();
  });

  it('renders title, body and footer when open', () => {
    render(
      <Dialog title="Confirm" footer={<button type="button">ok</button>}>
        Are you sure?
      </Dialog>,
    );
    expect(screen.getByRole('dialog').getAttribute('aria-modal')).toBe('true');
    expect(screen.getByText('Confirm').className).toContain('mds-dialog__title');
    expect(screen.getByText('Are you sure?')).toBeTruthy();
    expect(screen.getByText('ok').closest('.mds-dialog__foot')).toBeTruthy();
  });

  it('closes via the × button, scrim click and Escape', () => {
    const onClose = vi.fn();
    const { container } = render(
      <Dialog title="Confirm" onClose={onClose}>
        body
      </Dialog>,
    );
    // × button
    fireEvent.click(screen.getByLabelText('close'));
    // scrim (outer) click
    fireEvent.click(container.querySelector('.mds-scrim') as Element);
    // Escape key
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('does not close when the dialog body itself is clicked', () => {
    const onClose = vi.fn();
    render(
      <Dialog title="Confirm" onClose={onClose}>
        body
      </Dialog>,
    );
    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).not.toHaveBeenCalled();
  });
});
