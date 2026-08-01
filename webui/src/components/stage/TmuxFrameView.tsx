/**
 * TmuxFrameView — live terminal via ui-core TerminalSurfaceStore (keyframes, chunks, reset/delta frames)
 * plus an interactive writer lease while the Stage Terminal tab is mounted.
 */

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';
import { adaptTerminalUpdate } from '@murder/ui-core/terminalSurface/protocolAdapter.js';
import { TerminalSurfaceStore } from '@murder/ui-core/terminalSurface/TerminalSurfaceStore.js';
import { encodeTerminalKey } from './encodeTerminalKey.js';
import { terminalSnapshotToHtml } from './terminalSnapshotHtml.js';
import { useTerminalInputLease } from './useTerminalInputLease.js';

export function TmuxFrameView({
  sessionId,
}: {
  readonly sessionId: string | null;
}): React.JSX.Element {
  const bus = useApplicationClient();
  const [store, setStore] = useState(() => new TerminalSurfaceStore());
  const [ready, setReady] = useState(false);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const { status: inputStatus, error: inputError, writerRef } = useTerminalInputLease(bus, sessionId);

  useEffect(() => {
    setReady(false);
    if (sessionId === null) return;
    const surface = new TerminalSurfaceStore();
    setStore(surface);
    const off = bus.attachTerminal(sessionId, (update) => {
      surface.ingest(adaptTerminalUpdate(update));
      setReady(true);
    });
    return off;
  }, [bus, sessionId]);

  // Focus the black mass once interactive so keydown reaches the surface without an extra click.
  useEffect(() => {
    if (inputStatus !== 'interactive') return;
    surfaceRef.current?.focus({ preventScroll: true });
  }, [inputStatus]);

  const snapshot = useSyncExternalStore(store.subscribe.bind(store), store.getSnapshot, store.getSnapshot);
  const html = useMemo(() => (ready ? terminalSnapshotToHtml(snapshot) : ''), [ready, snapshot]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (inputStatus !== 'interactive' || writerRef.current === null) return;
    const bytes = encodeTerminalKey(event.nativeEvent);
    if (bytes === null) return;
    event.preventDefault();
    event.stopPropagation();
    writerRef.current.enqueue(bytes);
  };

  if (sessionId === null) {
    return <div className="mds-tmux__empty">No terminal session for this crow.</div>;
  }

  if (!ready) {
    return <div className="mds-tmux__empty">Waiting for the agent's terminal…</div>;
  }

  const readOnly =
    inputStatus === 'read_only' || (inputStatus !== 'interactive' && inputStatus !== 'acquiring');
  const hint =
    inputError !== null
      ? `read-only: ${inputError}`
      : inputStatus === 'acquiring'
        ? 'acquiring input…'
        : inputStatus === 'read_only'
          ? 'read-only'
          : null;

  return (
    <div
      ref={surfaceRef}
      className="mds-tmux"
      tabIndex={inputStatus === 'interactive' ? 0 : -1}
      role="application"
      aria-label="Agent terminal"
      aria-readonly={readOnly || undefined}
      data-terminal-input={inputStatus === 'interactive' ? 'true' : undefined}
      onKeyDown={onKeyDown}
    >
      {hint !== null ? <div className="mds-tmux__hint">{hint}</div> : null}
      <pre
        className="mds-tmux__frame"
        // Snapshot HTML escapes text and only emits <span style> + <br>.
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
