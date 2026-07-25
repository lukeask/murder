/**
 * ToastHost — mounts the core {@link toastStore} into the web shell via the DS {@link Toast}.
 *
 * Core actions (conversations send, settings save, plans spawn, …) push here; inktui's BottomBar
 * already renders the rack in the TUI. The web shell had the Toast primitive but no subscriber —
 * this is the one global host so operators see failure/success feedback on both desktop and mobile.
 *
 * Subscription is the vanilla-Zustand singleton (same instance actions import). Dismiss wires the
 * DS × button to {@link ToastState.dismiss}. Visibility caps at {@link MAX_VISIBLE_TOASTS}
 * (newest-on-top), matching BottomBar policy.
 */

import {
  MAX_VISIBLE_TOASTS,
  selectLiveToasts,
  type ToastSeverity,
  toastStore,
} from '@core/store/toast/toastStore.js';
import { useStore } from 'zustand';
import { Toast, type ToastTone } from './ds/Toast.js';

/** Map store severity → DS Toast tone (names align 1:1 on info/warning/error). */
function toneFor(severity: ToastSeverity): ToastTone {
  return severity;
}

function labelFor(text: string, count: number): string {
  return count > 1 ? `${text} (×${count})` : text;
}

/** Fixed rack of live toasts; returns null when empty so the host adds no DOM noise. */
export function ToastHost(): React.JSX.Element | null {
  const toasts = useStore(toastStore, (s) => s.toasts);
  const dismiss = useStore(toastStore, (s) => s.dismiss);

  // Newest-on-top: live filter (exit-grace aware) → take the tail cap → reverse for display order.
  const visible = selectLiveToasts(toasts, Date.now()).slice(-MAX_VISIBLE_TOASTS).toReversed();
  if (visible.length === 0) {
    return null;
  }

  return (
    <div className="toast-host" aria-live="polite">
      {visible.map((t) => (
        <Toast
          key={t.id}
          tone={toneFor(t.severity)}
          title={labelFor(t.text, t.count)}
          onClose={() => dismiss(t.id)}
        />
      ))}
    </div>
  );
}
