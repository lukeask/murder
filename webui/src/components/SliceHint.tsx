/**
 * SliceHint — the shared loading / error / empty lifecycle renderer for a list slice. Every panel
 * shows the same three states off the slice's `{ status, error }`, so it lives once here. Renders
 * `null` once there are rows (the panel draws them). Loading is suppressed when rows already exist
 * (background refresh keeps `status: 'ready'`).
 */

export interface SliceLike {
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  readonly error: string | null;
  readonly isEmpty: boolean;
}

export function SliceHint({
  state,
  empty,
}: {
  readonly state: SliceLike;
  readonly empty: string;
}): React.JSX.Element | null {
  if (state.status === 'error') {
    return <p className="panel__hint panel__hint--error">{state.error ?? 'Failed to load.'}</p>;
  }
  if (state.status === 'idle' || (state.status === 'loading' && state.isEmpty)) {
    return <p className="panel__hint">Loading…</p>;
  }
  if (state.isEmpty) {
    return <p className="panel__hint">{empty}</p>;
  }
  return null;
}
