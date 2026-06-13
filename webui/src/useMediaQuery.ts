/**
 * `useMediaQuery` — subscribe to a CSS media query and re-render on match changes.
 *
 * This is the ONE place the responsive desktop↔mobile switch is decided in JS. The visual layout
 * itself is CSS (media queries in app.css); this hook only governs the structural difference that
 * CSS alone cannot express — desktop renders three regions side-by-side, mobile renders ONE active
 * panel plus a tab bar (a genuinely different DOM tree, not just restyled). The breakpoint string
 * lives next to the CSS `--bp-*` tokens it mirrors (see STYLING.md / theme.css).
 *
 * Implemented over `window.matchMedia` so a test can stub `matchMedia` and assert the layout switch.
 */

import { useEffect, useState } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const mql = window.matchMedia(query);
    const onChange = (): void => setMatches(mql.matches);
    onChange();
    // `addEventListener` is the modern API; older Safari only has `addListener` — support both.
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, [query]);

  return matches;
}

/** The single source of truth for the mobile breakpoint, mirroring the CSS `--bp-mobile` token.
 * Below this width the app collapses to a single-column, tab-navigated layout. */
export const MOBILE_QUERY = '(max-width: 768px)';
