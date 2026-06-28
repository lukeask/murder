import type { Orientation } from '../hooks/useOrientation.js';
import type { PanelId } from '../input/panels.js';

export type CellSize = {
  readonly width: number;
  readonly height: number;
};

export type CellPoint = {
  readonly x: number;
  readonly y: number;
};

export type PaneRect = CellPoint & CellSize;

export type PaneId = string;

export type PaneKind = 'listPane' | 'usage' | 'tree' | 'stageChat' | 'stageDoc';

export type PaneRegion = 'leftAligned' | 'centerStage' | 'rightAligned';

export type PaneSizing = {
  readonly min: CellSize;
  readonly preferred: CellSize;
};

export type PaneDensity = 'full' | 'compact' | 'minimal';

export type PanePresentationConstraints = {
  readonly horizontallyCramped: boolean;
  readonly verticallyCramped: boolean;
};

export type PanePresentation = {
  readonly width: number;
  readonly height: number;
  readonly density: PaneDensity;
  readonly constraints: PanePresentationConstraints;
  readonly focused: boolean;
};

export type PaneSource =
  | { readonly type: 'panel'; readonly panelId: PanelId }
  | {
      readonly type: 'stageChat';
      readonly agentId: string;
      readonly locked: boolean;
      readonly ephemeral: boolean;
      readonly current: boolean;
    }
  | { readonly type: 'stageDoc'; readonly name: string };

export type PaneRequest = {
  readonly id: PaneId;
  readonly kind: PaneKind;
  readonly region: PaneRegion;
  readonly sizing: PaneSizing;
  readonly reapPriority: number;
  readonly orderKey: number;
  readonly source: PaneSource;
};

export type PaneDenialReason =
  | 'terminalTooSmall'
  | 'regionTooSmall'
  | 'belowMinimum'
  | 'preemptedByReapPriority';

export type PaneAllocation = {
  readonly request: PaneRequest;
  readonly region: PaneRegion;
  readonly rect: PaneRect;
  readonly presentation: PanePresentation;
};

export type PaneDenial = {
  readonly request: PaneRequest;
  readonly reason: PaneDenialReason;
  readonly detail: string;
};

export type PaneChromeHeights = {
  readonly topBar: number;
  readonly bottomBar: number;
  readonly chatInput: number;
};

export type ChatTargetState = {
  readonly activeTargetId: string | null;
  readonly lockedVisibleTargetIds: readonly string[];
  readonly favoriteOnlyTargetIds: readonly string[];
  readonly ephemeralTargetId: string | null;
};

export type PaneLayoutInput = {
  readonly terminal: CellSize;
  readonly chrome: PaneChromeHeights;
  /**
   * Optional precomputed body dimensions. When omitted, the body is derived from terminal height
   * minus top bar, bottom bar, and chat input chrome.
   */
  readonly body?: CellSize;
  /**
   * Optional terminal-space origin for the body. When omitted, x=0 and y=topBar.
   */
  readonly bodyOrigin?: CellPoint;
  readonly orientation: Orientation;
  readonly gap: number;
  readonly requests: readonly PaneRequest[];
  readonly focusedPaneId?: PaneId;
  readonly chatTargets?: ChatTargetState;
};

export type PaneRegionPlan = {
  readonly region: PaneRegion;
  readonly rect: PaneRect | null;
  readonly allocations: readonly PaneAllocation[];
};

export type PaneStageGroupPlan = {
  readonly docs: readonly PaneAllocation[];
  readonly chats: readonly PaneAllocation[];
  readonly other: readonly PaneAllocation[];
};

export type PaneLayoutPlan = {
  readonly terminal: CellSize;
  readonly chrome: PaneChromeHeights;
  readonly body: CellSize;
  readonly bodyRect: PaneRect;
  readonly orientation: Orientation;
  readonly gap: number;
  readonly allocations: readonly PaneAllocation[];
  readonly denials: readonly PaneDenial[];
  readonly regions: Readonly<Record<PaneRegion, PaneRegionPlan>>;
  readonly stage: PaneStageGroupPlan;
};
