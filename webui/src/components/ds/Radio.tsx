/**
 * Radio — single-choice group; the selected option fills with an accent core. Ported from the DS
 * bundle (forms/Radio). Mirrors the Button exemplar; the root is the `role="radiogroup"` <div>, so
 * `React.forwardRef` forwards there (the per-option natives are internal). Visuals in ds-forms.css.
 */

import { forwardRef, useId, type HTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface RadioOption {
  value: string;
  label: ReactNode;
}

export interface RadioProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onChange'> {
  /** Options as strings or {value,label}. */
  options?: Array<string | RadioOption>;
  /** Selected value. */
  value?: string;
  onChange?: (value: string) => void;
  /** Shared input name (auto-generated if omitted). */
  name?: string;
  /** Lay options out horizontally. */
  inline?: boolean;
  disabled?: boolean;
}

/** murder Radio — single-choice group; selected option fills green. */
export const Radio = forwardRef<HTMLDivElement, RadioProps>(function Radio(
  { options = [], value, onChange, name, inline = false, disabled = false, className, ...rest },
  ref,
) {
  const auto = useId();
  const groupName = name !== undefined ? name : auto;
  return (
    <div
      ref={ref}
      className={cx('mds-radiogroup', !inline && 'mds-radiogroup--col', className)}
      role="radiogroup"
      {...rest}
    >
      {options.map((o) => {
        const opt = typeof o === 'string' ? { value: o, label: o } : o;
        const on = opt.value === value;
        return (
          <label
            key={opt.value}
            className={cx('mds-radio', on && 'mds-radio--on', disabled && 'mds-radio--disabled')}
          >
            <input
              type="radio"
              className="mds-radio__native"
              name={groupName}
              checked={on}
              disabled={disabled}
              onChange={() => {
                if (onChange !== undefined) onChange(opt.value);
              }}
            />
            <span className="mds-radio__ring">
              <span className="mds-radio__core" />
            </span>
            <span>{opt.label}</span>
          </label>
        );
      })}
    </div>
  );
});
