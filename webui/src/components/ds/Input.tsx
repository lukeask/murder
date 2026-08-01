/**
 * Input — mono text field with optional leading/trailing slots.
 * Visuals in ds-forms.css. `size` is the design variant ('md' | 'lg'), not the DOM attr.
 * When `multiline`, renders a textarea (composer: Enter=submit, Shift+Enter=newline at the call site).
 */

import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';
import { cx } from './cx.js';

type SharedProps = {
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
};

export type InputProps = SharedProps &
  (
    | ({ multiline?: false } & Omit<InputHTMLAttributes<HTMLInputElement>, 'size'>)
    | ({ multiline: true; rows?: number } & Omit<
        TextareaHTMLAttributes<HTMLTextAreaElement>,
        'size'
      >)
  );

/** murder Input — mono text field; border greens on focus. Multline uses a textarea. */
export const Input = forwardRef<HTMLInputElement | HTMLTextAreaElement, InputProps>(function Input(
  {
    label,
    leading,
    trailing,
    hint,
    invalid = false,
    disabled = false,
    size = 'md',
    id,
    className,
    multiline = false,
    ...rest
  },
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
          multiline && 'mds-input--multiline',
          invalid && 'mds-input--invalid',
          disabled && 'mds-input--disabled',
          className,
        )}
      >
        {leading !== undefined && leading !== null ? (
          <span className="mds-input__glyph">{leading}</span>
        ) : null}
        {multiline ? (
          <textarea
            ref={ref as React.Ref<HTMLTextAreaElement>}
            id={fieldId}
            className="mds-input__el"
            disabled={disabled}
            aria-invalid={invalid ? true : undefined}
            {...(rest as TextareaHTMLAttributes<HTMLTextAreaElement>)}
            rows={(rest as TextareaHTMLAttributes<HTMLTextAreaElement>).rows ?? 2}
          />
        ) : (
          <input
            ref={ref as React.Ref<HTMLInputElement>}
            id={fieldId}
            className="mds-input__el"
            disabled={disabled}
            aria-invalid={invalid ? true : undefined}
            {...(rest as InputHTMLAttributes<HTMLInputElement>)}
          />
        )}
        {trailing}
      </div>
      {hint !== undefined ? (
        <span className={cx('mds-field__hint', invalid && 'mds-field__hint--error')}>{hint}</span>
      ) : null}
    </div>
  );
});
