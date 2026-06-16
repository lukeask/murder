/** StatusDot smoke test: status class, pulse only on running, label string vs null vs omitted. */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { StatusDot } from '../../src/components/ds/StatusDot.js';

afterEach(cleanup);

describe('ds/StatusDot', () => {
  it('defaults to idle with no label and no pulse', () => {
    const { container } = render(<StatusDot />);
    const el = container.querySelector('.mds-statusdot');
    expect(el?.className).toContain('mds-statusdot--idle');
    expect(el?.className).not.toContain('mds-statusdot--pulse');
    expect(el?.querySelector('span:nth-child(2)')).toBeNull();
  });

  it('pulses only when running', () => {
    const running = render(<StatusDot status="running" pulse />).container;
    expect(running.querySelector('.mds-statusdot')?.className).toContain('mds-statusdot--pulse');
    cleanup();
    const pending = render(<StatusDot status="pending" pulse />).container;
    expect(pending.querySelector('.mds-statusdot')?.className).not.toContain('mds-statusdot--pulse');
  });

  it('renders a string label, echoes the status word for null', () => {
    const withLabel = render(<StatusDot status="done" label="finished" />);
    expect(withLabel.getByText('finished')).toBeTruthy();
    cleanup();
    const echo = render(<StatusDot status="failed" label={null} />);
    expect(echo.getByText('failed')).toBeTruthy();
  });
});
