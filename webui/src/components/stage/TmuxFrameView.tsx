/**
 * TmuxFrameView — the "watch the agent's terminal" view. Attaches to the independent terminal
 * stream for the selected session and renders each raw ANSI replacement frame.
 *
 * ## ANSI rendering choice
 * tmux frames are full-screen SNAPSHOT strings from `tmux capture-pane -e` (not an incremental PTY
 * byte stream), so a full terminal emulator (xterm.js) is overkill and heavy — xterm wants a live
 * stream and an explicit cols/rows geometry we don't control here. Instead we convert the ANSI SGR
 * codes to HTML with the lightweight `ansi-to-html` and render into a `<pre>` styled with the theme
 * monospace font + colors. This is accurate for colored snapshots and ~10x smaller than xterm.
 * (Documented in STYLING.md.)
 *
 * The bus is reached directly here (not via a store action) because tmux frames are streaming
 * DISPLAY data the store does not own — the same exception inktui's TmuxMode makes. The subscription
 * is torn down on unmount / agent change.
 */

import Convert from 'ansi-to-html';
import { useEffect, useMemo, useState } from 'react';
import { useBus } from '../../bus/BusContext.js';

export function TmuxFrameView({ agentId }: { readonly agentId: string }): React.JSX.Element {
  const bus = useBus();
  const [frame, setFrame] = useState<string>('');

  // One converter instance, reading the current theme colors off CSS vars so the ANSI palette tracks
  // the active theme. `escapeXML` guards against raw `<`/`>` in pane content rendering as HTML.
  const convert = useMemo(() => new Convert({ escapeXML: true, newline: false }), []);

  useEffect(() => {
    setFrame('');
    const off = bus.attachTerminal(agentId, (terminalFrame) => {
      // The current tmux stream mode is full-frame replacement, never incremental terminal bytes.
      setFrame((current) =>
        terminalFrame.type === 'terminal.frame' && terminalFrame.reset
          ? terminalFrame.data
          : `${current}${terminalFrame.data}`,
      );
    });
    return off;
  }, [bus, agentId]);

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
