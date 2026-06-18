/**
 * Panel smoke test: renders title/count/actions, and active/flush props map to the right `.mds-panel*`
 * classes. Pins the exemplar contract for Phase B data components.
 */

import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Panel } from '../../src/components/ds/Panel.js';

afterEach(cleanup);

describe('ds/Panel', () => {
  it('renders title, count pill and body children', () => {
    render(
      <Panel title="Tickets" count={3}>
        body-content
      </Panel>,
    );
    expect(screen.getByText('Tickets').className).toContain('mds-panel__title');
    expect(screen.getByText('3').className).toContain('mds-panel__count');
    expect(screen.getByText('body-content')).toBeTruthy();
  });

  it('applies active and flush modifiers and merges className', () => {
    const { container } = render(<Panel title="Plans" active flush className="extra" />);
    const section = container.querySelector('section');
    expect(section?.className).toContain('mds-panel--active');
    expect(section?.className).toContain('mds-panel--flush');
    expect(section?.className).toContain('extra');
  });

  it('omits the header when no title is given and renders actions when present', () => {
    const { container, rerender } = render(<Panel>only-body</Panel>);
    expect(container.querySelector('.mds-panel__head')).toBeNull();
    rerender(
      <Panel title="X" actions={<button type="button">act</button>}>
        b
      </Panel>,
    );
    expect(screen.getByText('act').closest('.mds-panel__actions')).toBeTruthy();
  });
});
