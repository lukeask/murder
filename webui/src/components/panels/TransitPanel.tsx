/**
 * TransitPanel — the git "transit map": branch lanes and their commits, over the `transit` slice.
 *
 * The inktui {@link selectTransitView} renders an ASCII railway tuned to a fixed terminal cell
 * width — that geometry is terminal-specific and does not translate to the DOM. So this panel reads
 * the raw slice (`lanes[].commits[]`, all display-ready scalar fields: short sha, subject, ts) and
 * renders a natural web commit graph: a column of lanes, each listing its commits; clicking a commit
 * shows its full subject/body. No formatting logic is reimplemented beyond a relative-age label and
 * the slice's own fields. This is the documented divergence from the Ink railway (see STYLING.md).
 */

import { useAppStore } from '@core/hooks/useAppStore.js';
import { shallow } from 'zustand/shallow';
import { useState } from 'react';
import { Panel } from '../Panel.js';
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
    <Panel title="Transit">
      <SliceHint state={view} empty="No branches." />
      {transit.lanes.map((lane) => (
        <div key={lane.branch} className="transit__lane">
          <div className="transit__branch">
            <span className={lane.isMain ? 'transit__name transit__name--main' : 'transit__name'}>
              {lane.branch}
            </span>
            {lane.isMain ? <span className="transit__home">⌂</span> : null}
          </div>
          <ul className="list transit__commits">
            {lane.commits.map((c) => (
              <li
                key={c.sha}
                className="transit__commit"
                data-selected={c.sha === selectedSha ? 'true' : undefined}
                onClick={() => setSelectedSha(c.sha)}
              >
                <span className="transit__node">●</span>
                <span className="transit__sha">{c.short}</span>
                <span className="transit__subject">{c.subject}</span>
                <span className="transit__age">{ageLabel(c.tsEpoch, now)}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {selected !== undefined ? (
        <div className="transit__detail">
          <div className="transit__detail-head">
            {selected.short} · {selected.subject}
          </div>
          {selected.body.length > 0 ? <pre className="transit__body">{selected.body}</pre> : null}
        </div>
      ) : null}
    </Panel>
  );
}
