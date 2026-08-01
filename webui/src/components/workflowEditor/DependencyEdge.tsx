import {
  BaseEdge,
  type EdgeProps,
  getSmoothStepPath,
  EdgeLabelRenderer,
} from '@xyflow/react';
import { memo } from 'react';
import type { DependencyFlowEdge } from './flowGraph.js';

/** Smooth dependency edge; coral stroke reserved for illegal stubs (unused in v1 resolved-only). */
function DependencyEdgeInner({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
  data,
  markerEnd,
  style,
}: EdgeProps<DependencyFlowEdge>): React.JSX.Element {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 8,
  });
  const illegal = data?.illegal === true;
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        className={`wfe-edge${selected ? ' wfe-edge--selected' : ''}${illegal ? ' wfe-edge--illegal' : ''}`}
        {...(markerEnd !== undefined ? { markerEnd } : {})}
        {...(style !== undefined ? { style } : {})}
      />
      {illegal && data?.dependency !== undefined ? (
        <EdgeLabelRenderer>
          <div
            className="wfe-edge-label wfe-edge-label--illegal"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            }}
          >
            ? {data.dependency}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const DependencyEdge = memo(DependencyEdgeInner);
