/**
 * TmuxFrameView — ANSI snapshot frames via ansi-to-html (not xterm). Attach needs a real session UUID.
 */

import Convert from 'ansi-to-html';
import { useEffect, useMemo, useState } from 'react';
import { useApplicationClient } from '@murder/ui-core/hooks/useApplicationClient.js';

export function TmuxFrameView({
  sessionId,
}: {
  readonly sessionId: string | null;
}): React.JSX.Element {
  const bus = useApplicationClient();
  const [frame, setFrame] = useState<string>('');

  const convert = useMemo(() => new Convert({ escapeXML: true, newline: false }), []);

  useEffect(() => {
    setFrame('');
    if (sessionId === null) return;
    const off = bus.attachTerminal(sessionId, (terminalFrame) => {
      if (terminalFrame.type === 'terminal.frame' && terminalFrame.reset) {
        setFrame(terminalFrame.data);
      }
    }, 'replace');
    return off;
  }, [bus, sessionId]);

  if (sessionId === null) {
    return <div className="mds-tmux__empty">No terminal session for this crow.</div>;
  }

  if (frame === '') {
    return <div className="mds-tmux__empty">Waiting for the agent's terminal…</div>;
  }

  return (
    <div className="mds-tmux">
      <pre
        className="mds-tmux__frame"
        // ansi-to-html output is sanitized (escapeXML) and only emits <span style> + <br>.
        dangerouslySetInnerHTML={{ __html: convert.toHtml(frame) }}
      />
    </div>
  );
}
