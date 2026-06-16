/**
 * StatusDot — small colored crow/agent-state dot with optional soft opacity breathe on "running"
 * (never a hard blink; reduced-motion safe via ds-data.css). Optional label. Ported from the DS
 * bundle (data/StatusDot); follows the Panel exemplar. Visuals live in ds-data.css (`.mds-statusdot*`).
 *
 * Pulse rule (faithful to the bundle): the breathe only renders when `pulse` AND status === "running".
 * Pass `label` as a string to show it; pass `null` to echo the status word; omit for no label.
 */

import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx.js';

export type StatusDotStatus = 'running' | 'done' | 'failed' | 'archived' | 'pending' | 'idle';

export interface StatusDotProps extends HTMLAttributes<HTMLSpanElement> {
  /** @default "idle" */
  status?: StatusDotStatus;
  /** Soft opacity breathe on "running" only (never a hard blink). */
  pulse?: boolean;
  /** Pass a string to show a label; pass null to echo the status word; omit for none. */
  label?: ReactNode | null;
}

/** murder StatusDot — colored crow-state dot with optional soft pulse + label. */
export function StatusDot({
  status = 'idle',
  pulse = false,
  label,
  className,
  ...rest
}: StatusDotProps): React.JSX.Element {
  const showPulse = pulse && status === 'running';
  return (
    <span
      className={cx(
        'mds-statusdot',
        `mds-statusdot--${status}`,
        showPulse && 'mds-statusdot--pulse',
        className,
      )}
      {...rest}
    >
      <span className="mds-statusdot__dot" />
      {label !== undefined ? <span>{label !== null ? label : status}</span> : null}
    </span>
  );
}
