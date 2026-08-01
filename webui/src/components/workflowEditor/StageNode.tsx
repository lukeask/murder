import { Handle, Position, type NodeProps } from '@xyflow/react';
import { memo } from 'react';
import {
  stageStatusGlyph,
  stageStatusLabel,
} from '@murder/ui-core/workflowEditor/statusDisplay.js';
import type { StageFlowNode } from './flowGraph.js';

/** Stage mass on the canvas — stone block + sparse coral when invalid; run glyph when live. */
function StageNodeInner({ data, selected }: NodeProps<StageFlowNode>): React.JSX.Element {
  const tone = data.hasError ? 'error' : data.hasWarning ? 'warning' : 'ok';
  const runStatus = data.runStatus;
  const glyph = stageStatusGlyph(runStatus);
  return (
    <div
      className={`wfe-stage wfe-stage--${tone}${selected ? ' wfe-stage--selected' : ''}${
        runStatus !== undefined ? ` wfe-stage--run-${runStatus}` : ''
      }`}
      data-stage-key={data.stageKey}
      data-run-status={runStatus}
    >
      <Handle type="target" position={Position.Left} className="wfe-handle wfe-handle--in" />
      <div className="wfe-stage__body">
        <div className="wfe-stage__id-row">
          <div className="wfe-stage__id">{data.id || '(blank)'}</div>
          {glyph !== '' ? (
            <span
              className="wfe-stage__run"
              title={stageStatusLabel(runStatus)}
              aria-label={stageStatusLabel(runStatus)}
            >
              {glyph}
            </span>
          ) : null}
        </div>
        {data.title !== data.id && data.title.length > 0 ? (
          <div className="wfe-stage__title">{data.title}</div>
        ) : null}
        <div className="wfe-stage__meta">
          <span>{data.harness ?? '—'}</span>
          <span className="wfe-stage__dot" aria-hidden="true" />
          <span>{data.model ?? '—'}</span>
          {data.gate !== 'auto' ? (
            <>
              <span className="wfe-stage__dot" aria-hidden="true" />
              <span className="wfe-stage__gate">{data.gate}</span>
            </>
          ) : null}
        </div>
        {data.issueCount > 0 ? (
          <div className="wfe-stage__issues" aria-label={`${data.issueCount} issues`}>
            {data.issueCount}
          </div>
        ) : null}
      </div>
      <Handle type="source" position={Position.Right} className="wfe-handle wfe-handle--out" />
    </div>
  );
}

export const StageNode = memo(StageNodeInner);
