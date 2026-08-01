/** ToastHost — mounts core {@link toastStore} into the web shell via DS {@link Toast}. */

import {
  MAX_VISIBLE_TOASTS,
  selectLiveToasts,
  toastStore,
} from '@murder/ui-core/store/toast/toastStore.js';
import { useStore } from 'zustand';
import { Toast } from './ds/Toast.js';

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
          tone={t.severity}
          title={t.count > 1 ? `${t.text} (×${t.count})` : t.text}
          onClose={() => dismiss(t.id)}
        />
      ))}
    </div>
  );
}
