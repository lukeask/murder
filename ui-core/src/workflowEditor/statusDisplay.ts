/**
 * statusDisplay — one glyph vocabulary for a stage's runtime state, shared by every workflow-editor
 * surface (graph nodes, the inspector, the narrow outline) so the same state always reads the same
 * way. Glyphs come from the families the rest of the TUI already wears (`●`/`○` from the roster,
 * `✓`/`✗`/`⊘` from the workflows list) rather than a per-surface grab-bag.
 */

import type { Theme } from '../theme/buildTheme.js';

/** The static-DAG stage statuses the editor can render. */
export const STAGE_STATUSES = [
  'blocked',
  'ready',
  'requested',
  'running',
  'waiting_approval',
  'succeeded',
  'failed',
  'cancelled',
] as const;

const GLYPHS: Readonly<Record<string, string>> = {
  blocked: '◌',
  ready: '○',
  requested: '◑',
  running: '●',
  waiting_approval: '◆',
  succeeded: '✓',
  failed: '✗',
  cancelled: '⊘',
};

/** A one-cell badge for a runtime status (`·` for a stage with no runtime state). */
export function stageStatusGlyph(status: string | undefined): string {
  if (status === undefined) return '';
  return GLYPHS[status] ?? '•';
}

/** Human wording for a status — underscores read as machine noise in a label. */
export function stageStatusLabel(status: string | undefined): string {
  if (status === undefined) return 'not running';
  return status.replaceAll('_', ' ');
}

/** The theme role a status should be painted in. */
export function stageStatusColor(status: string | undefined, theme: Theme): string {
  switch (status) {
    case 'succeeded':
      return theme.success;
    case 'failed':
      return theme.error;
    case 'waiting_approval':
      return theme.warning;
    case 'running':
      return theme.accent;
    case 'requested':
      return theme.heading;
    case 'ready':
      return theme.active;
    case 'cancelled':
      return theme.inactive;
    default:
      return theme.muted;
  }
}
