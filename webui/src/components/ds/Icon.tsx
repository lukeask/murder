/**
 * Icon — the design-system's line-icon set, Lucide-style: 24px grid, stroke 1.75, `currentColor`,
 * no fill. Paths are INLINED (we do NOT depend on `lucide-react`). This is the canonical icon API for
 * DS components.
 *
 * AUTHORING CONTRACT (Phase B copies this):
 *  - Use `<Icon name="search" />`. The SVG inherits `color` (stroke = currentColor), so size/color it
 *    via the parent's `font-size`/`color` or pass `size`.
 *  - `size` (default 18) sets width/height. All other SVG props spread through (`className`,
 *    `aria-hidden`, `onClick`, …). Decorative by default (`aria-hidden`); pass `aria-label` +
 *    `role="img"` for a meaningful icon.
 *  - To add an icon: add one entry to `ICON_PATHS` (a JSX fragment of <path>/<circle>/<line>/<polyline>
 *    on the 24px grid) and its name to `IconName`. Nothing else changes.
 */

import type { SVGProps } from 'react';

/** Every icon the DS templates use. Extend here + in {@link ICON_PATHS} to add one. */
export type IconName =
  | 'chevron-down'
  | 'chevron-right'
  | 'search'
  | 'settings'
  | 'plus'
  | 'x'
  | 'check'
  | 'file-text'
  | 'ticket'
  | 'git-branch'
  | 'git-commit'
  | 'gauge'
  | 'message-square'
  | 'crosshair'
  | 'help'
  | 'star'
  | 'send'
  | 'paperclip'
  | 'more'
  | 'back';

/**
 * The 24px-grid path geometry per icon (Lucide-derived). Stroke styling (width 1.75, round caps,
 * currentColor, no fill) lives on the parent <svg> in {@link Icon}, so these are geometry only.
 */
const ICON_PATHS: Record<IconName, React.JSX.Element> = {
  'chevron-down': <path d="m6 9 6 6 6-6" />,
  'chevron-right': <path d="m9 18 6-6-6-6" />,
  search: (
    <>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </>
  ),
  settings: (
    <>
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  plus: (
    <>
      <path d="M5 12h14" />
      <path d="M12 5v14" />
    </>
  ),
  x: (
    <>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  'file-text': (
    <>
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v4a2 2 0 0 0 2 2h4" />
      <path d="M10 9H8" />
      <path d="M16 13H8" />
      <path d="M16 17H8" />
    </>
  ),
  ticket: (
    <>
      <path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z" />
      <path d="M13 5v2" />
      <path d="M13 17v2" />
      <path d="M13 11v2" />
    </>
  ),
  'git-branch': (
    <>
      <line x1="6" x2="6" y1="3" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </>
  ),
  'git-commit': (
    <>
      <circle cx="12" cy="12" r="4" />
      <line x1="1.05" x2="7" y1="12" y2="12" />
      <line x1="17.01" x2="22.96" y1="12" y2="12" />
    </>
  ),
  gauge: (
    <>
      <path d="m12 14 4-4" />
      <path d="M3.34 19a10 10 0 1 1 17.32 0" />
    </>
  ),
  'message-square': <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  crosshair: (
    <>
      <circle cx="12" cy="12" r="10" />
      <line x1="22" x2="18" y1="12" y2="12" />
      <line x1="6" x2="2" y1="12" y2="12" />
      <line x1="12" x2="12" y1="6" y2="2" />
      <line x1="12" x2="12" y1="22" y2="18" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
      <path d="M12 17h.01" />
    </>
  ),
  star: (
    <path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z" />
  ),
  send: (
    <>
      <path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" />
      <path d="m21.854 2.147-10.94 10.939" />
    </>
  ),
  paperclip: (
    <path d="M13.234 20.252 21 12.3a1 1 0 0 0 0-1.42l-7.066-7.066a3 3 0 0 0-4.243 0L3.515 9.99a5 5 0 0 0 0 7.072l.001.001a5 5 0 0 0 7.07 0l6.187-6.186a3 3 0 0 0 0-4.243v0a3 3 0 0 0-4.242 0L6.343 12.586" />
  ),
  more: (
    <>
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
      <circle cx="5" cy="12" r="1" />
    </>
  ),
  back: (
    <>
      <path d="m12 19-7-7 7-7" />
      <path d="M19 12H5" />
    </>
  ),
};

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  /** Which icon to render. */
  name: IconName;
  /** Pixel size for width & height. @default 18 */
  size?: number;
}

/** A single DS line icon. Inherits `currentColor`; size via `size` or the parent's `font-size`. */
export function Icon({ name, size = 18, ...rest }: IconProps): React.JSX.Element {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={rest['aria-label'] === undefined ? true : undefined}
      {...rest}
    >
      {ICON_PATHS[name]}
    </svg>
  );
}
