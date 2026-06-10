/**
 * `<Toast>` — the bottom-right transient-feedback rack (F9, salvaging Textual's `ToastRack`).
 *
 * Mounted once at the app root (last child of {@link ./App.js Shell}'s root box). It subscribes to
 * the {@link ../store/toast/toastStore.js toastStore} singleton and paints the currently-*live*
 * toasts (the pure {@link selectLiveToasts} filter at the current `now`), newest-on-top, capped at
 * {@link MAX_VISIBLE_TOASTS}. When nothing is live it returns `null`, so the slot is zero layout
 * cost in the common case.
 *
 * ## Subtle by design (the plan's TODO-T)
 *
 * A toast is ambient confirmation, not an alert: `info` toasts are `dimColor` with **no** colour
 * flash (the `→ sent` whisper); only `error` earns colour (`red`). Short TTL (~2–3s) means it is a
 * glance, not a thing to dismiss.
 *
 * ## Ink has no z-layer — "bottom-right" = flex, not floating
 *
 * Ink can't paint over an already-rendered tree (see {@link ./Overlay.js}'s doc). So "overlay
 * bottom-right" is expressed as: render as the root box's last child, right-aligned
 * (`alignItems="flex-end"`), each toast a one-line right-justified row. It rides below the bars in
 * normal layout; during a fullscreen mode the shell early-returns and the rack isn't shown — which is
 * consistent with "subtle / non-attention-grabbing".
 *
 * ## Why the `now` tick
 *
 * Removal is driven by the store's per-toast `setTimeout`, but the pure liveness filter lets a toast
 * vanish exactly at its deadline. To re-evaluate that filter as time passes the component holds a
 * `now` that ticks on a short interval *only while toasts exist* (the interval is torn down the
 * moment the rack empties), so an idle app schedules nothing.
 */

import { Box, Text } from 'ink';
import { type JSX, useEffect, useState } from 'react';
import { useStore } from 'zustand';
import {
  MAX_VISIBLE_TOASTS,
  selectLiveToasts,
  type Toast as ToastData,
  type ToastSeverity,
  toastStore,
} from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';

/** How often the rack re-evaluates liveness while toasts are present. Fine-grained enough that a
 * toast disappears within a frame of its deadline; only runs while the rack is non-empty. */
const TICK_MS = 200;

/** One toast row. `error` earns colour (`red`); `info` is a dim whisper with no colour flash. */
function ToastRow({
  severity,
  text,
}: {
  readonly severity: ToastSeverity;
  readonly text: string;
}): JSX.Element {
  const theme = useTheme();
  if (severity === 'error') {
    return <Text color={theme.error}>{text}</Text>;
  }
  return <Text dimColor>{text}</Text>;
}

/**
 * The toast rack. Subscribes to the singleton's `toasts`, filters to the live set at the ticking
 * `now`, shows the newest {@link MAX_VISIBLE_TOASTS} newest-on-top, and right-aligns the column.
 * Returns `null` when nothing is live.
 */
export function Toast(): JSX.Element | null {
  const toasts = useStore(toastStore, (s) => s.toasts);
  const [now, setNow] = useState(() => Date.now());

  // Tick `now` only while toasts exist so the pure liveness filter re-evaluates; tear the interval
  // down the moment the rack empties (an idle app schedules nothing).
  useEffect(() => {
    if (toasts.length === 0) {
      return;
    }
    const handle = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(handle);
  }, [toasts.length]);

  const live = selectLiveToasts(toasts, now);
  if (live.length === 0) {
    return null;
  }
  // Newest-on-top, capped: take the tail (newest pushes are at the end), reverse, then cap.
  const shown: readonly ToastData[] = [...live].reverse().slice(0, MAX_VISIBLE_TOASTS);

  return (
    <Box flexDirection="column" alignItems="flex-end">
      {shown.map((t) => (
        <ToastRow key={t.id} severity={t.severity} text={t.text} />
      ))}
    </Box>
  );
}
