/**
 * cx — the DS className-merge helper. The ONE way DS components compose class names.
 *
 * Mirrors the bundle's `[...].filter(Boolean).join(" ")` idiom: pass any mix of strings,
 * `false`/`null`/`undefined` (conditionally-applied classes), and the falsy ones drop out. Keeps the
 * base class + variant classes + the caller's `className` merge identical across every component.
 *
 *   cx('mds-btn', `mds-btn--${variant}`, block && 'mds-btn--block', className)
 */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}
