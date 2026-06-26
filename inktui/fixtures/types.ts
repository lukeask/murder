import type React from 'react';
import type { FixtureSize } from './renderInkFixture.js';

export type PaneFixtureId = string;
export type PaneFixtureDataId = string;

export interface PaneFixtureRenderArgs<Data = unknown> {
  readonly data: Data;
  readonly width: number;
  readonly height: number;
  readonly focused: boolean;
}

export interface PaneFixture<Data = unknown> {
  readonly id: PaneFixtureId;
  readonly description: string;
  readonly sizes: readonly FixtureSize[];
  readonly data: Record<PaneFixtureDataId, Data>;
  readonly focusStates?: readonly boolean[];
  readonly render: (args: PaneFixtureRenderArgs<Data>) => React.ReactNode;
}
