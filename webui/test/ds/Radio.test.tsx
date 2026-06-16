/**
 * Radio smoke test: renders a radiogroup of options, selected value gets `--on`, inline toggles the
 * `--col` layout, className merges, onChange fires with the option value, and ref forwards.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Radio } from '../../src/components/ds/Radio.js';

afterEach(cleanup);

describe('ds/Radio', () => {
  it('renders a column group by default with the selected option on', () => {
    const { container } = render(
      <Radio options={['fast', { value: 'smart', label: 'Smart' }]} value="smart" />,
    );
    const group = screen.getByRole('radiogroup');
    expect(group.className).toContain('mds-radiogroup');
    expect(group.className).toContain('mds-radiogroup--col');
    const labels = container.querySelectorAll('.mds-radio');
    const on = container.querySelector('.mds-radio--on');
    expect(labels.length).toBe(2);
    expect(on?.textContent).toContain('Smart');
  });

  it('drops --col when inline and merges className', () => {
    render(<Radio options={['a']} inline className="extra" />);
    const group = screen.getByRole('radiogroup');
    expect(group.className).not.toContain('mds-radiogroup--col');
    expect(group.className).toContain('extra');
  });

  it('forwards ref and calls onChange with the option value', () => {
    const ref = createRef<HTMLDivElement>();
    const onChange = vi.fn();
    render(<Radio ref={ref} options={['a', 'b']} value="a" onChange={onChange} />);
    expect(ref.current).toBeInstanceOf(HTMLDivElement);
    const radios = screen.getAllByRole('radio');
    fireEvent.click(radios[1]!);
    expect(onChange).toHaveBeenCalledWith('b');
  });
});
