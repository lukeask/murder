/**
 * Select — native <select> styled to match Input, with a chevron caret. Ported from the DS bundle
 * (forms/Select). Mirrors the Button exemplar; `React.forwardRef` forwards to the underlying
 * <select>. The bundle's inline caret SVG (chevron-down geometry) is rendered via the shared
 * {@link Icon} (`name="chevron-down"`) rather than re-inlined. Visuals live in ds-forms.css.
 */

import { forwardRef, useId, type SelectHTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';
import { Icon } from './Icon.js';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  /** Options as strings or {value,label}. Ignored if children are passed. */
  options?: Array<string | SelectOption>;
  disabled?: boolean;
  children?: ReactNode;
}

/** murder Select — native select styled to match Input, chevron caret. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { label, options = [], disabled = false, id, className, children, ...rest },
  ref,
) {
  const autoId = useId();
  const fieldId = id !== undefined ? id : autoId;
  return (
    <div className={cx('mds-select', disabled && 'mds-select--disabled', className)}>
      {label !== undefined ? (
        <label className="mds-select__label" htmlFor={fieldId}>
          {label}
        </label>
      ) : null}
      <div className="mds-select__wrap">
        <select ref={ref} id={fieldId} className="mds-select__el" disabled={disabled} {...rest}>
          {children !== undefined && children !== null
            ? children
            : options.map((o) => {
                const opt = typeof o === 'string' ? { value: o, label: o } : o;
                return (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                );
              })}
        </select>
        <span className="mds-select__caret">
          <Icon name="chevron-down" size={16} />
        </span>
      </div>
    </div>
  );
});
