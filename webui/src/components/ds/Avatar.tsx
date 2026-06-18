/**
 * Avatar — a crow identity tile: a monospace lowercase initial on a tinted square, colored by a stable
 * hash of the name rotating through the crow identity palette (--crow-1..6). Falls back to an <img> when
 * `src` is given. Ported from the DS bundle (data/Avatar); follows the Panel exemplar.
 *
 * The color is computed at runtime (the bundle does the same) because it's a per-name `color-mix` of a
 * `--crow-N` token — the one place we set inline style. The static structural rules live in ds-data.css
 * (`.mds-avatar*`). Hashing: FNV-ish `h = (h*31 + charCode) >>> 0`, then `% 6` into the crow palette —
 * preserved verbatim from the bundle so initials map to the same color as elsewhere.
 */

import type { CSSProperties, HTMLAttributes } from 'react';
import { cx } from './cx.js';

const CROW_COLORS = [
  'var(--crow-1)',
  'var(--crow-2)',
  'var(--crow-3)',
  'var(--crow-4)',
  'var(--crow-5)',
  'var(--crow-6)',
] as const;

export interface AvatarProps extends HTMLAttributes<HTMLSpanElement> {
  /** Crow / user name — drives the initial and the identity color. */
  name?: string;
  /** Optional image source; falls back to the colored initial tile. */
  src?: string;
  /** @default "md" */
  size?: 'sm' | 'md' | 'lg';
}

function hash(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return h;
}

/** murder Avatar — crow identity tile; color hashed from the name. */
export function Avatar({
  name = '?',
  src,
  size = 'md',
  className,
  ...rest
}: AvatarProps): React.JSX.Element {
  const color = CROW_COLORS[hash(name) % CROW_COLORS.length] as string;
  const initial = name.trim().charAt(0).toLowerCase() || '?';
  const style: CSSProperties | undefined =
    src !== undefined
      ? undefined
      : {
          color,
          borderColor: color,
          background: `color-mix(in srgb, ${color} 16%, var(--surface-panel))`,
        };
  return (
    <span
      className={cx('mds-avatar', size !== 'md' && `mds-avatar--${size}`, className)}
      style={style}
      title={name}
      {...rest}
    >
      {src !== undefined ? <img src={src} alt={name} /> : initial}
    </span>
  );
}
