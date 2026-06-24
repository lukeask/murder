/**
 * TransitPanel — the git "transit map": branch lanes and their commits, over the `transit` slice
 * (Phase C2 reskin onto the design system).
 *
 * The inktui {@link selectTransitView} renders an ASCII railway tuned to a fixed terminal cell
 * width — that geometry is terminal-specific and does not translate to the DOM. So this panel reads
 * the RAW slice (`lanes[].commits[]`: short sha, subject, body, tsEpoch) and renders a natural web
 * commit list: a column of lanes, each listing its commits; clicking a commit shows its full
 * subject/body. The data wiring is UNCHANGED — same raw slice read, same local `selectedSha` state,
 * same inline `ageLabel()`. Only the DOM is reskinned onto the DS {@link Panel} + tokens.
 *
 * Per-branch identity color uses the crow palette `--crow-1..6` (cycled by lane index); short sha is
 * mono/muted, subject is primary text, age is muted. Lifecycle stays {@link SliceHint}. Bespoke CSS
 * lives in `styles/panels-transit.css` (wired in via main.tsx). This is the documented divergence
 * from the Ink railway (see STYLING.md) — no ASCII railway / graphical DAG here.
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Panel } from '../ds/index.js';
import { SliceHint } from '../SliceHint.js';

function ageLabel(tsEpochSec: number, nowMs: number): string {
  const deltaSec = Math.max(0, Math.floor(nowMs / 1000 - tsEpochSec));
  if (deltaSec < 60) return 'now';
  const m = Math.floor(deltaSec / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d`;
  return `${Math.floor(d / 7)}w`;
}

/** The crow identity palette has 6 slots; cycle lanes through them. */
const CROW_SLOTS = 6;

export function TransitPanel(): React.JSX.Element {
  const transit = useAppStore((s) => s.transit, shallow);
  const [selectedSha, setSelectedSha] = useState<string | null>(null);
  const now = Date.now();

  const view = {
    status: transit.status,
    error: transit.error,
    isEmpty: transit.lanes.length === 0,
  };

  const selected = transit.lanes
    .flatMap((l) => l.commits)
    .find((c) => c.sha === selectedSha);

  return (
    <Panel title="transit" count={view.isEmpty ? null : transit.lanes.length} data-panel-id="transit">
      <SliceHint state={view} empty="No branches." />
      {transit.lanes.map((lane, laneIdx) => {
        const laneColor = `var(--crow-${(laneIdx % CROW_SLOTS) + 1})`;
        return (
          <div
            key={lane.branch}
            className="transit-lane"
            style={{ '--lane-color': laneColor } as React.CSSProperties}
          >
            <div className="transit-lane__branch">
              <span className="transit-lane__dot" />
              <span
                className={
                  lane.isMain
                    ? 'transit-lane__name transit-lane__name--main'
                    : 'transit-lane__name'
                }
              >
                {lane.branch}
              </span>
              {lane.isMain ? <span className="transit-lane__home">⌂</span> : null}
            </div>
            <ul className="transit-lane__commits">
              {lane.commits.map((c) => (
                <li
                  key={c.sha}
                  className="transit-commit"
                  data-selected={c.sha === selectedSha ? 'true' : undefined}
                  onClick={() => setSelectedSha(c.sha)}
                >
                  <span className="transit-commit__node" />
                  <span className="transit-commit__sha">{c.short}</span>
                  <span className="transit-commit__subject">{c.subject}</span>
                  <span className="transit-commit__age">{ageLabel(c.tsEpoch, now)}</span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      {selected !== undefined ? (
        <div className="transit-detail">
          <div className="transit-detail__head">
            <span className="transit-detail__sha">{selected.short}</span>
            <span className="transit-detail__subject">{selected.subject}</span>
          </div>
          {selected.body.length > 0 ? (
            <pre className="transit-detail__body">{selected.body}</pre>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}
