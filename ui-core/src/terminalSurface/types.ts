/** Protocol-independent terminal stream input accepted by the terminal surface. */
export type TerminalSizingPolicy =
  | { readonly kind: 'fixed'; readonly columns: number; readonly rows: number }
  | { readonly kind: 'follow_viewport' };

export const HARNESS_TERMINAL_SIZING: TerminalSizingPolicy = {
  kind: 'fixed',
  columns: 220,
  rows: 50,
};

export const FOLLOW_VIEWPORT_TERMINAL_SIZING: TerminalSizingPolicy = {
  kind: 'follow_viewport',
};

export type TerminalViewportCommand =
  | {
      readonly sequence: number;
      readonly kind: 'pan';
      readonly deltaColumns: number;
      readonly deltaRows: number;
    }
  | { readonly sequence: number; readonly kind: 'follow_cursor' };

export interface TerminalViewportMetrics {
  readonly sizingPolicy: TerminalSizingPolicy;
  readonly geometryMatchesPolicy: boolean;
  readonly terminalColumns: number;
  readonly terminalRows: number;
  readonly viewportColumns: number;
  readonly viewportRows: number;
  readonly offsetColumn: number;
  readonly offsetRow: number;
  readonly followingCursor: boolean;
  readonly cropped: boolean;
}

export interface TerminalKeyframeInput {
  readonly type: 'terminal.keyframe';
  readonly sequence?: number;
  readonly columns: number;
  readonly rows: number;
  /** Legacy / compact active-buffer row-major cells. */
  readonly cells?: readonly (readonly TerminalCellInput[])[];
  /** Complete terminal state sent by the persistent-stream protocol. */
  readonly primary?: TerminalBufferInput;
  readonly alternate?: TerminalBufferInput;
  readonly active_buffer?: 'primary' | 'alternate';
  readonly cursor?: TerminalCursorInput;
  readonly modes?: TerminalModesInput;
  readonly rendition?: TerminalRenditionInput;
}

export interface TerminalChunkInput {
  readonly type: 'terminal.chunk';
  readonly sequence?: number;
  readonly encoding: 'base64' | 'utf-8';
  readonly data: string;
}

/** Compatibility input while old replace-frame producers are still connected. */
export interface TerminalFrameInput {
  readonly type: 'terminal.frame';
  readonly sequence?: number;
  readonly columns?: number;
  readonly rows?: number;
  readonly data: string;
  readonly reset?: boolean;
}

export interface TerminalGapInput {
  readonly type: 'terminal.gap';
  readonly expected_sequence?: number;
  readonly next_sequence?: number;
}

export type TerminalSurfaceUpdate =
  | TerminalKeyframeInput
  | TerminalChunkInput
  | TerminalFrameInput
  | TerminalGapInput;

export type TerminalColor = string | number | undefined;

export interface TerminalCellInput {
  readonly text?: string;
  readonly width?: 0 | 1 | 2;
  readonly continuation?: boolean;
  readonly fg?: TerminalColor;
  readonly bg?: TerminalColor;
  readonly bold?: boolean;
  readonly dim?: boolean;
  readonly italic?: boolean;
  readonly underline?: boolean;
  readonly inverse?: boolean;
  readonly hidden?: boolean;
  readonly strikethrough?: boolean;
  /** Protocol may group visual attributes below `rendition`. */
  readonly rendition?: Omit<TerminalCellInput, 'text' | 'width' | 'continuation' | 'rendition'>;
}

export interface TerminalRenditionInput {
  readonly fg?: TerminalColor;
  readonly bg?: TerminalColor;
  readonly bold?: boolean;
  readonly dim?: boolean;
  readonly italic?: boolean;
  readonly underline?: boolean;
  readonly inverse?: boolean;
  readonly hidden?: boolean;
  readonly strikethrough?: boolean;
}

export interface TerminalBufferInput {
  readonly cells: readonly (readonly TerminalCellInput[])[];
  readonly cursor?: TerminalCursorInput;
  readonly saved_cursor?: TerminalCursorInput;
  readonly savedCursor?: TerminalCursorInput;
  readonly rendition?: TerminalRenditionInput;
  readonly saved_rendition?: TerminalRenditionInput;
  readonly savedRendition?: TerminalRenditionInput;
  readonly scroll_top?: number;
  readonly scroll_bottom?: number;
  readonly scrollTop?: number;
  readonly scrollBottom?: number;
  readonly wrap_pending?: boolean;
  readonly wrapPending?: boolean;
}

export interface TerminalCursorInput {
  readonly x: number;
  readonly y: number;
  readonly visible?: boolean;
  readonly shape?: 'block' | 'underline' | 'bar';
}

export interface TerminalModesInput {
  readonly applicationCursor?: boolean;
  readonly applicationKeypad?: boolean;
  readonly bracketedPaste?: boolean;
  readonly autoWrap?: boolean;
  readonly origin?: boolean;
  readonly cursorVisible?: boolean;
  readonly insert?: boolean;
  readonly alternate?: boolean;
  readonly synchronizedUpdates?: boolean;
}

export interface TerminalCell {
  text: string;
  width: 0 | 1 | 2;
  continuation: boolean;
  fg: TerminalColor;
  bg: TerminalColor;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  inverse: boolean;
  hidden: boolean;
  strikethrough: boolean;
}

export interface TerminalCursor {
  x: number;
  y: number;
  visible: boolean;
  shape: 'block' | 'underline' | 'bar';
}

export interface TerminalModes {
  applicationCursor: boolean;
  applicationKeypad: boolean;
  bracketedPaste: boolean;
  autoWrap: boolean;
  origin: boolean;
  cursorVisible: boolean;
  insert: boolean;
  alternate: boolean;
  synchronizedUpdates: boolean;
}

export interface TerminalGridSnapshot {
  readonly columns: number;
  readonly rows: number;
  readonly cells: readonly (readonly TerminalCell[])[];
  readonly cursor: TerminalCursor;
  readonly modes: TerminalModes;
  /** Increasing value used by React to render a changed grid. */
  readonly version: number;
  /** Dirty physical rows since the preceding snapshot; `null` means whole grid. */
  readonly dirtyRows: readonly number[] | null;
  /** Per-row revisions allow the renderer to skip untouched rows. */
  readonly rowVersions: readonly number[];
  readonly keyframeRequired: boolean;
}

/** Complete read-only emulator state for tests, persistence, and snapshot diagnostics. */
export interface TerminalSurfaceState {
  readonly columns: number;
  readonly rows: number;
  readonly primary: TerminalBufferState;
  readonly alternate: TerminalBufferState;
  readonly activeBuffer: 'primary' | 'alternate';
  readonly modes: TerminalModes;
  readonly keyframeRequired: boolean;
}

export interface TerminalBufferState {
  readonly cells: readonly (readonly TerminalCell[])[];
  readonly cursor: TerminalCursor;
  readonly savedCursor: TerminalCursor;
  readonly rendition: TerminalRenditionInput;
  readonly savedRendition: TerminalRenditionInput;
  readonly scrollTop: number;
  readonly scrollBottom: number;
  readonly wrapPending: boolean;
}
