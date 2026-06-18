/**
 * KeybindBar smoke test: renders hints, fires onHelp (and the onCommand alias) on the help button,
 * the scroll variant maps to the right class, and help=null hides the button.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { KeybindBar } from '../../src/components/ds/KeybindBar.js';

afterEach(cleanup);

describe('ds/KeybindBar', () => {
  it('renders hints and the default help button, applying the scroll class', () => {
    const { container } = render(
      <KeybindBar hints={[{ chord: 'C-s', desc: 'spawn' }]} scroll />,
    );
    expect(container.querySelector('.mds-keybar')?.className).toContain('mds-keybar--scroll');
    expect(screen.getByText('C-s').className).toContain('mds-keybar__chord');
    expect(screen.getByText('spawn').className).toContain('mds-keybar__desc');
    expect(screen.getByRole('button', { name: /help/ })).toBeTruthy();
  });

  it('fires onHelp when the help button is pressed', () => {
    const onHelp = vi.fn();
    render(<KeybindBar onHelp={onHelp} />);
    fireEvent.click(screen.getByRole('button', { name: /help/ }));
    expect(onHelp).toHaveBeenCalledTimes(1);
  });

  it('falls back to the onCommand alias when onHelp is absent', () => {
    const onCommand = vi.fn();
    render(<KeybindBar onCommand={onCommand} />);
    fireEvent.click(screen.getByRole('button', { name: /help/ }));
    expect(onCommand).toHaveBeenCalledTimes(1);
  });

  it('hides the help button when help is null', () => {
    const { container } = render(<KeybindBar help={null} />);
    expect(container.querySelector('.mds-keybar__help')).toBeNull();
  });
});
