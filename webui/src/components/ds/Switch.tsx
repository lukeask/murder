/**
 * Switch — compact settings toggle; green track when on, no bounce. Ported from the DS bundle
 * (forms/Switch). Mirrors the Button exemplar; `React.forwardRef` forwards to the native
 * <input type="checkbox" role="switch"> (the focusable element). Visuals live in ds-forms.css.
 */

import { forwardRef, useState, type InputHTMLAttributes, type ChangeEvent, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  checked?: boolean;
  defaultChecked?: boolean;
  disabled?: boolean;
  label?: ReactNode;
}

/** murder Switch — settings toggle; green track when on, no bounce. */
export const Switch = forwardRef<HTMLInputElement, SwitchProps>(function Switch(
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
    <label className={cx('mds-switch', on === true && 'mds-switch--on', disabled && 'mds-switch--disabled', className)}>
      <input
        ref={ref}
        type="checkbox"
        role="switch"
        className="mds-switch__native"
        checked={on}
        onChange={handle}
        disabled={disabled}
        {...rest}
      />
      <span className="mds-switch__track">
        <span className="mds-switch__thumb" />
      </span>
      {label !== undefined ? <span>{label}</span> : null}
    </label>
  );
});
