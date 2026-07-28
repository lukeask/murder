/** KeybindBar smoke test: renders hints and the scroll variant class. */

import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { KeybindBar } from '../../src/components/ds/KeybindBar.js';

afterEach(cleanup);

describe('ds/KeybindBar', () => {
  it('renders hints and applies the scroll class', () => {
    const { container } = render(
      <KeybindBar hints={[{ chord: 'C-s', desc: 'spawn' }]} scroll />,
    );
    expect(container.querySelector('.mds-keybar')?.className).toContain('mds-keybar--scroll');
    expect(screen.getByText('C-s').className).toContain('mds-keybar__chord');
    expect(screen.getByText('spawn').className).toContain('mds-keybar__desc');
    expect(container.querySelector('.mds-keybar__help')).toBeNull();
  });
});
