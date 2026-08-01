/**
 * Workflows panel view-model — run-first rows assembled from `workflow.runs.list` + schedule tickets.
 *
 * Membership comes from each run’s `stage_map` (not ticket parentage). Synthetic parent “run”
 * tickets are hidden; standalone tickets not in any stage_map become legacy one-node runs.
 */

import { useMemo } from 'react';
import type { TicketRow, TicketsState } from '../store/tickets/ticketsSlice.js';
import type {
  WorkflowRunListItem,
  WorkflowRunsState,
  WorkflowRunStatus,
} from '../store/workflowRuns/workflowRunsSlice.js';
import { type StatusGlyph, type StatusTone, statusGlyphOf } from './ticketsSelectors.js';

export type WorkflowPanelRow =
  | {
      readonly kind: 'run';
      readonly workflowId: string;
      readonly templateName: string;
      readonly templateVersion: number;
      readonly status: WorkflowRunStatus;
      readonly createdAt: string;
      readonly updatedAt: string;
      readonly parentTicketId: string | null;
      readonly nodeCount: number;
    }
  | {
      readonly kind: 'node';
      readonly workflowId: string;
      readonly stageId: string;
      readonly ticketId: string;
      readonly status: string;
      readonly title: string;
      readonly ticket: TicketRow | null;
    }
  | {
      readonly kind: 'legacy-ticket-run';
      readonly syntheticId: `ticket:${string}`;
      readonly templateName: 'Ticket';
      readonly ticketId: string;
      readonly status: string;
      readonly title: string;
      readonly createdAt: string;
      readonly ticket: TicketRow;
    };

export interface WorkflowPanelRowView {
  readonly id: string;
  readonly kind: WorkflowPanelRow['kind'];
  readonly idCell: string;
  readonly titleCell: string;
  readonly statusCell: string;
  readonly statusTone: StatusTone;
  readonly lastUpdateCell: string;
  readonly depsCell: string;
  readonly depsSatisfied: boolean;
  readonly scheduleCell: string;
  readonly harnessCell: string;
  readonly modelCell: string;
  readonly planCell: string;
  readonly worktreeCell: string;
  readonly depth: number;
  /** Ticket id to open in TicketEditorMode, when Enter should open a ticket. */
  readonly openTicketId: string | null;
  /** Workflow / synthetic id used for expand/collapse of group headers. */
  readonly groupId: string | null;
}

export interface WorkflowsPanelView {
  readonly rows: readonly WorkflowPanelRowView[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  readonly error: string | null;
  readonly isEmpty: boolean;
}

const CHILD_INDENT = '    ';
const ID_WIDTH = 12;
const TITLE_WIDTH = 28;
const LAST_UPDATE_WIDTH = 20;

function truncate(text: string, width: number): string {
  return text.length <= width ? text : `${text.slice(0, width - 1)}…`;
}

function modelBasename(model: string | null): string {
  const raw = (model ?? '').trim();
  if (raw === '') {
    return '—';
  }
  const slash = raw.lastIndexOf('/');
  return slash === -1 ? raw : raw.slice(slash + 1);
}

interface SnapshotStage {
  readonly id: string;
  readonly title: string;
}

function stagesFromSnapshot(
  snapshot: WorkflowRunListItem['definition_snapshot'],
): readonly SnapshotStage[] {
  if (snapshot == null || typeof snapshot !== 'object') {
    return [];
  }
  const stages = snapshot['stages'];
  if (!Array.isArray(stages)) {
    return [];
  }
  const out: SnapshotStage[] = [];
  for (const entry of stages) {
    if (entry == null || typeof entry !== 'object' || Array.isArray(entry)) {
      continue;
    }
    const record = entry as { readonly [key: string]: unknown };
    const id = record['id'];
    if (typeof id !== 'string' || id.length === 0) {
      continue;
    }
    const title = record['title'];
    out.push({ id, title: typeof title === 'string' && title.length > 0 ? title : id });
  }
  return out;
}

function runStatusGlyph(status: WorkflowRunStatus): StatusGlyph {
  switch (status) {
    case 'failed':
      return { glyph: '✗', tone: 'error' };
    case 'completed':
      return { glyph: '✓', tone: 'success' };
    case 'cancelled':
      return { glyph: '⊘', tone: 'blocked' };
    case 'paused':
      return { glyph: '◌', tone: 'neutral' };
    case 'waiting':
      return { glyph: '◍', tone: 'neutral' };
    case 'running':
      return { glyph: '●', tone: 'warning' };
    default:
      return { glyph: '○', tone: 'neutral' };
  }
}

function formatCreatedAt(iso: string): string {
  return truncate(iso.slice(0, 10), LAST_UPDATE_WIDTH);
}

function ticketCells(
  ticket: TicketRow | null,
  now: number,
): Pick<
  WorkflowPanelRowView,
  | 'statusCell'
  | 'statusTone'
  | 'lastUpdateCell'
  | 'depsCell'
  | 'depsSatisfied'
  | 'scheduleCell'
  | 'harnessCell'
  | 'modelCell'
  | 'planCell'
  | 'worktreeCell'
> {
  if (ticket === null) {
    return {
      statusCell: '○',
      statusTone: 'neutral',
      lastUpdateCell: '—',
      depsCell: '—',
      depsSatisfied: true,
      scheduleCell: '—',
      harnessCell: '—',
      modelCell: '—',
      planCell: '—',
      worktreeCell: '—',
    };
  }
  const status = statusGlyphOf(ticket, now);
  const satisfied = ticket.pendingDepIds.length === 0;
  return {
    statusCell: status.glyph,
    statusTone: status.tone,
    lastUpdateCell: truncate(
      `${ticket.lastUpdateAt.slice(0, 10)} ${ticket.lastUpdateLabel}`,
      LAST_UPDATE_WIDTH,
    ),
    depsCell: satisfied ? 'ok' : truncate(ticket.pendingDepIds.join(', '), 24),
    depsSatisfied: satisfied,
    scheduleCell: ticket.scheduleAt ?? '—',
    harnessCell: ticket.harness ?? '—',
    modelCell: modelBasename(ticket.model),
    planCell: '—',
    worktreeCell: '—',
  };
}

function toRunView(row: Extract<WorkflowPanelRow, { kind: 'run' }>): WorkflowPanelRowView {
  const status = runStatusGlyph(row.status);
  return {
    id: row.workflowId,
    kind: 'run',
    idCell: truncate(row.templateName, ID_WIDTH),
    titleCell: truncate(`v${row.templateVersion} · ${row.nodeCount} node${row.nodeCount === 1 ? '' : 's'}`, TITLE_WIDTH),
    statusCell: status.glyph,
    statusTone: status.tone,
    lastUpdateCell: formatCreatedAt(row.createdAt),
    depsCell: '—',
    depsSatisfied: true,
    scheduleCell: '—',
    harnessCell: '—',
    modelCell: '—',
    planCell: '—',
    worktreeCell: '—',
    depth: 0,
    openTicketId: null,
    groupId: row.workflowId,
  };
}

function toNodeView(
  row: Extract<WorkflowPanelRow, { kind: 'node' }>,
  now: number,
): WorkflowPanelRowView {
  const cells = ticketCells(row.ticket, now);
  const title = row.ticket?.title ?? row.title;
  const missing = row.status === 'missing';
  return {
    id: `${row.workflowId}:${row.stageId}`,
    kind: 'node',
    idCell: truncate(row.ticketId, ID_WIDTH),
    titleCell: `${CHILD_INDENT}${truncate(title, TITLE_WIDTH)}`,
    ...cells,
    depth: 1,
    // Synthetic `?stageId` placeholders must not open TicketEditorMode.
    openTicketId: missing ? null : row.ticketId,
    groupId: null,
  };
}

function toLegacyView(
  row: Extract<WorkflowPanelRow, { kind: 'legacy-ticket-run' }>,
  now: number,
): WorkflowPanelRowView {
  const cells = ticketCells(row.ticket, now);
  return {
    id: row.syntheticId,
    kind: 'legacy-ticket-run',
    idCell: truncate(row.ticketId, ID_WIDTH),
    titleCell: truncate(row.title, TITLE_WIDTH),
    ...cells,
    depth: 0,
    openTicketId: row.ticketId,
    groupId: row.syntheticId,
  };
}

function byCreatedDesc(a: WorkflowRunListItem, b: WorkflowRunListItem): number {
  const cmp = b.created_at.localeCompare(a.created_at);
  return cmp !== 0 ? cmp : a.workflow_id.localeCompare(b.workflow_id);
}

function byLastUpdateDesc(a: TicketRow, b: TicketRow): number {
  const cmp = b.lastUpdateAt.localeCompare(a.lastUpdateAt);
  return cmp !== 0 ? cmp : a.id.localeCompare(b.id);
}

/**
 * Snapshot stage order, then any `stage_map` keys the snapshot omitted (so claimed tickets
 * still render as nodes instead of vanishing or becoming legacy).
 */
function stageOrderForRun(
  stages: readonly SnapshotStage[],
  stageMap: Readonly<Record<string, string>>,
): readonly SnapshotStage[] {
  if (stages.length === 0) {
    return Object.keys(stageMap).map((id) => ({ id, title: id }));
  }
  const seen = new Set(stages.map((s) => s.id));
  const extras: SnapshotStage[] = [];
  for (const id of Object.keys(stageMap)) {
    if (!seen.has(id)) {
      extras.push({ id, title: id });
    }
  }
  return extras.length === 0 ? stages : [...stages, ...extras];
}

export interface SelectWorkflowPanelRowsOptions {
  /**
   * When false, skip legacy ticket rows. Use until `workflow.runs.list` is ready/error so
   * hydrated schedule tickets are not briefly mis-labeled as standalone runs.
   */
  readonly includeLegacy?: boolean;
}

/**
 * Flatten runs + tickets into panel domain rows. `collapsedGroupIds` hides node rows under a run.
 */
export function selectWorkflowPanelRows(
  runs: readonly WorkflowRunListItem[],
  tickets: readonly TicketRow[],
  collapsedGroupIds: ReadonlySet<string> = new Set(),
  options: SelectWorkflowPanelRowsOptions = {},
): readonly WorkflowPanelRow[] {
  const includeLegacy = options.includeLegacy !== false;
  const ticketsById = new Map(tickets.map((t) => [t.id, t] as const));
  const claimedTicketIds = new Set<string>();
  const parentTicketIds = new Set<string>();

  for (const run of runs) {
    if (run.parent_ticket_id != null && run.parent_ticket_id.length > 0) {
      parentTicketIds.add(run.parent_ticket_id);
      claimedTicketIds.add(run.parent_ticket_id);
    }
    for (const ticketId of Object.values(run.stage_map ?? {})) {
      claimedTicketIds.add(ticketId);
    }
  }

  const orderedRuns = [...runs].sort(byCreatedDesc);
  const rows: WorkflowPanelRow[] = [];

  for (const run of orderedRuns) {
    const stages = stagesFromSnapshot(run.definition_snapshot);
    const stageMap = run.stage_map ?? {};
    const stageOrder = stageOrderForRun(stages, stageMap);

    rows.push({
      kind: 'run',
      workflowId: run.workflow_id,
      templateName: run.definition_name,
      templateVersion: run.definition_version,
      status: run.status,
      createdAt: run.created_at,
      updatedAt: run.updated_at,
      parentTicketId: run.parent_ticket_id ?? null,
      nodeCount: stageOrder.length,
    });

    if (collapsedGroupIds.has(run.workflow_id)) {
      continue;
    }

    for (const stage of stageOrder) {
      const ticketId = stageMap[stage.id];
      if (ticketId === undefined) {
        rows.push({
          kind: 'node',
          workflowId: run.workflow_id,
          stageId: stage.id,
          ticketId: `?${stage.id}`,
          status: 'missing',
          title: stage.title,
          ticket: null,
        });
        continue;
      }
      const ticket = ticketsById.get(ticketId) ?? null;
      rows.push({
        kind: 'node',
        workflowId: run.workflow_id,
        stageId: stage.id,
        ticketId,
        status: ticket?.status ?? 'unknown',
        title: ticket?.title ?? stage.title,
        ticket,
      });
    }
  }

  if (includeLegacy) {
    const legacyTickets = tickets
      .filter((t) => !claimedTicketIds.has(t.id) && !parentTicketIds.has(t.id))
      .slice()
      .sort(byLastUpdateDesc);

    for (const ticket of legacyTickets) {
      const syntheticId = `ticket:${ticket.id}` as const;
      rows.push({
        kind: 'legacy-ticket-run',
        syntheticId,
        templateName: 'Ticket',
        ticketId: ticket.id,
        status: ticket.status,
        title: ticket.title,
        createdAt: ticket.lastUpdateAt,
        ticket,
      });
    }
  }

  return rows;
}

export function selectWorkflowsPanelView(
  workflowRuns: WorkflowRunsState,
  tickets: TicketsState,
  collapsedGroupIds: ReadonlySet<string> = new Set(),
  now: number = Date.now(),
): WorkflowsPanelView {
  const listReady = workflowRuns.listStatus === 'ready' || workflowRuns.listStatus === 'error';
  const domainRows = selectWorkflowPanelRows(workflowRuns.runs, tickets.rows, collapsedGroupIds, {
    // Hold legacy until runs list is authoritative so stage/parent tickets aren't flash-labeled.
    includeLegacy: listReady,
  });
  const rows = domainRows.map((row) => {
    switch (row.kind) {
      case 'run':
        return toRunView(row);
      case 'node':
        return toNodeView(row, now);
      case 'legacy-ticket-run':
        return toLegacyView(row, now);
      default:
        return row satisfies never;
    }
  });

  const ticketsReady = tickets.status === 'ready' || tickets.status === 'error';
  let status: WorkflowsPanelView['status'] = 'ready';
  let error: string | null = null;
  if (workflowRuns.listStatus === 'error') {
    status = 'error';
    error = workflowRuns.listError;
  } else if (tickets.status === 'error' && workflowRuns.runs.length === 0) {
    status = 'error';
    error = tickets.error;
  } else if (
    workflowRuns.listStatus === 'loading' ||
    (workflowRuns.listStatus === 'idle' && !listReady) ||
    (tickets.status === 'loading' && !ticketsReady && workflowRuns.runs.length === 0)
  ) {
    status = workflowRuns.listStatus === 'idle' && tickets.status === 'idle' ? 'idle' : 'loading';
  }

  return {
    rows,
    status,
    error,
    isEmpty: rows.length === 0,
  };
}

export function useWorkflowsPanelView(
  workflowRuns: WorkflowRunsState,
  tickets: TicketsState,
  collapsedGroupIds: ReadonlySet<string>,
): WorkflowsPanelView {
  return useMemo(
    () => selectWorkflowsPanelView(workflowRuns, tickets, collapsedGroupIds),
    [workflowRuns, tickets, collapsedGroupIds],
  );
}
