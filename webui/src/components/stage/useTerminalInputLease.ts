/**
 * Acquire / renew / reacquire / release a session writer lease while the Stage terminal tab is mounted.
 * Mirrors inktui TranscriptController's terminal-input lifecycle (without pane-navigation chords).
 */

import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import type { ApplicationClient, TerminalInputLease } from '@murder/ui-core/application/ApplicationClient.js';
import { TerminalInputWriter } from './terminalInputWriter.js';

export type TerminalInputStatus = 'inactive' | 'acquiring' | 'interactive' | 'read_only';

const TERMINAL_LEASE_RENEW_MS = 5_000;

export interface TerminalInputLeaseState {
  readonly status: TerminalInputStatus;
  readonly error: string | null;
  readonly writerRef: MutableRefObject<TerminalInputWriter | null>;
}

export function useTerminalInputLease(
  bus: ApplicationClient,
  sessionId: string | null,
): TerminalInputLeaseState {
  const [status, setStatus] = useState<TerminalInputStatus>('inactive');
  const [error, setError] = useState<string | null>(null);
  const writerRef = useRef<TerminalInputWriter | null>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    if (sessionId === null) {
      setStatus('inactive');
      setError(null);
      return;
    }

    const generation = generationRef.current + 1;
    generationRef.current = generation;
    let lease: TerminalInputLease | null = null;
    let renewTimer: ReturnType<typeof setInterval> | undefined;
    let disposed = false;
    let acquiring = false;
    let reacquireAfterConnect = false;

    const relinquish = (showError?: string): void => {
      if (renewTimer !== undefined) {
        clearInterval(renewTimer);
        renewTimer = undefined;
      }
      const writer = writerRef.current;
      writerRef.current = null;
      writer?.close();
      if (lease !== null) {
        const releasing = lease;
        lease = null;
        void bus.closeTerminalInput(releasing).catch(() => {});
      }
      if (!disposed && showError !== undefined) {
        setError(showError);
        setStatus('read_only');
      }
    };

    const acquire = (): void => {
      if (disposed || acquiring || writerRef.current !== null) return;
      acquiring = true;
      setError(null);
      setStatus('acquiring');
      void bus
        .openTerminalInput(sessionId)
        .then((opened) => {
          acquiring = false;
          if (disposed || generationRef.current !== generation) {
            void bus.closeTerminalInput(opened).catch(() => {});
            return;
          }
          lease = opened;
          let writer: TerminalInputWriter;
          writer = new TerminalInputWriter(
            bus,
            opened.streamId,
            opened.sessionId,
            opened.leaseId,
            opened.fence,
            () => {
              if (writerRef.current !== writer) return;
              relinquish('terminal input unavailable; surface is read-only');
            },
          );
          writerRef.current = writer;
          setStatus('interactive');
          renewTimer = setInterval(() => {
            const current = lease;
            if (current === null) return;
            void bus
              .renewTerminalInput(current)
              .then((renewed) => {
                if (!disposed && lease === current) lease = renewed;
              })
              .catch(() => relinquish('terminal input lease expired; surface is read-only'));
          }, TERMINAL_LEASE_RENEW_MS);
        })
        .catch((cause: unknown) => {
          acquiring = false;
          if (disposed || generationRef.current !== generation) return;
          setError(
            cause instanceof Error
              ? cause.message
              : 'terminal input unavailable; surface is read-only',
          );
          setStatus('read_only');
        });
    };

    const reconnectable = bus as ApplicationClient & {
      onConnect?: (listener: () => void) => () => void;
      onDisconnect?: (listener: () => void) => () => void;
    };
    const unhookDisconnect = reconnectable.onDisconnect?.(() => {
      reacquireAfterConnect = true;
      relinquish('terminal disconnected; reconnecting read-only');
    });
    const unhookConnect = reconnectable.onConnect?.(() => {
      if (!reacquireAfterConnect) return;
      reacquireAfterConnect = false;
      acquire();
    });
    acquire();

    return () => {
      disposed = true;
      generationRef.current += 1;
      unhookConnect?.();
      unhookDisconnect?.();
      relinquish();
    };
  }, [bus, sessionId]);

  return { status, error, writerRef };
}
