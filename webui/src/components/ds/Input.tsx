/**
 * Input — single-line mono text field with optional leading/trailing slots.
 * Visuals in ds-forms.css. `size` is the design variant ('md' | 'lg'), not the DOM attr.
 */

import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx.js';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  /** Field label rendered above the control. */
  label?: string;
  /** Leading slot — a line icon. */
  leading?: ReactNode;
  /** Trailing slot (icon button, unit, key hint). */
  trailing?: ReactNode;
  /** Helper or error text below the field. */
  hint?: string;
  /** Error state — red border, error-colored hint. */
  invalid?: boolean;
  disabled?: boolean;
  /** @default "md" */
  size?: 'md' | 'lg';
}

/** murder Input — single-line mono text field; border greens on focus. */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, leading, trailing, hint, invalid = false, disabled = false, size = 'md', id, className, ...rest },
  ref,
) {
  const autoId = useId();
  const fieldId = id !== undefined ? id : autoId;
  return (
    <div className="mds-field">
      {label !== undefined ? (
        <label className="mds-field__label" htmlFor={fieldId}>
          {label}
        </label>
      ) : null}
      <div
        className={cx(
          'mds-input',
          size === 'lg' && 'mds-input--lg',
          invalid && 'mds-input--invalid',
          disabled && 'mds-input--disabled',
          className,
        )}
      >
        {leading !== undefined && leading !== null ? (
          <span className="mds-input__glyph">{leading}</span>
        ) : null}
        <input
          ref={ref}
          id={fieldId}
          className="mds-input__el"
          disabled={disabled}
          aria-invalid={invalid ? true : undefined}
          {...rest}
        />
        {trailing}
      </div>
      {hint !== undefined ? (
        <span className={cx('mds-field__hint', invalid && 'mds-field__hint--error')}>{hint}</span>
      ) : null}
    </div>
  );
});
