/**
 * Checkbox — square check with a box-drawing tick; controlled or uncontrolled. Ported from the DS
 * bundle (forms/Checkbox). Mirrors the Button exemplar; `React.forwardRef` forwards to the native
 * <input type="checkbox"> (the focusable element). Visuals live in ds-forms.css.
 *
 * The bundle paints the tick with a literal `✓` glyph (not the Icon set), kept byte-faithful here.
 */

import { forwardRef, useState, type InputHTMLAttributes, type ChangeEvent, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  /** Controlled checked state. */
  checked?: boolean;
  defaultChecked?: boolean;
  disabled?: boolean;
  /** Inline label to the right of the box. */
  label?: ReactNode;
}

/** murder Checkbox — square check with a box-drawing tick; green when on. */
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { checked, defaultChecked, onChange, disabled = false, label, className, ...rest },
  ref,
) {
  const isControlled = checked !== undefined;
  const [internal, setInternal] = useState<boolean>(defaultChecked === true);
  const on = isControlled ? checked : internal;
  const handle = (e: ChangeEvent<HTMLInputElement>): void => {
    if (!isControlled) setInternal(e.target.checked);
    if (onChange !== undefined) onChange(e);
  };
  return (
    <label className={cx('mds-check', on === true && 'mds-check--on', disabled && 'mds-check--disabled', className)}>
      <input
        ref={ref}
        type="checkbox"
        className="mds-check__native"
        checked={on}
        onChange={handle}
        disabled={disabled}
        {...rest}
      />
      <span className="mds-check__box">{on === true ? '✓' : ''}</span>
      {label !== undefined ? <span>{label}</span> : null}
    </label>
  );
});
