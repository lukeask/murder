/**
 * Dialog actions — the *only* code that calls the bus for dialog operations (rule 3).
 *
 * Covers operations triggered from the C12 modal dialogs:
 *  - `ticket.quick_create` — create a new ticket (`ctrl+t`). NOT a standalone RPC: it is an
 *    orchestrator command kind, routed through the LIVE `command.submit` choke point (F2). See
 *    {@link ../commandSubmit.js}.
 *  - `ticket.next_id` — fetch the next free ticket id. LIVE RPC.
 *  - `ticket.exists` — check if a ticket handle already exists. LIVE RPC.
 *  - `plan.create` — create a plan + start its planning agent for the new-plan form (`super+p`).
 *    LIVE — registered in `murder/app/service/host.py` (`_plan_create`).
 *
 * The RpcMethods augmentation below keeps the C1/C2 bus files byte-identical (rule 4 — the seam).
 */

import type { BusClient } from '../../bus/BusClient.js';
import { submitCommand } from '../commandSubmit.js';

/**
 * C12's RPC method declarations, augmenting the shared {@link RpcMethods} registry without
 * editing the frozen C1/C2 bus files. Each key is distinct from every other slice's keys —
 * the compiler will catch a collision if a later chunk redeclares the same method name.
 *
 * `ticket.next_id`, `ticket.exists`, and `plan.create` are all LIVE on the bus.
 */
declare module '../../bus/BusClient.js' {
  interface QueryMethods {
    /**
     * Fetch the next free ticket id (the id the service would assign to a new ticket).
     * LIVE — registered in `murder/app/service/host.py`.
     */
    'ticket.next_id': {
      params: Record<string, never>;
      result: NextIdResult;
    };
    /**
     * Check whether a ticket handle already exists.
     * LIVE — registered in `murder/app/service/host.py`.
     */
    'ticket.exists': {
      params: { handle: string };
      result: ExistsResult;
    };
  }
  interface CommandMethods {
    /**
     * Create a new plan and start its planning agent. LIVE — registered in
     * `murder/app/service/host.py` (`_plan_create` → `Orchestrator.create_plan`).
     *
     * Payload (all optional individually, but one of `plan_name`/`auto_name` is required by the
     * service): `body` seeds the plan's markdown; `auto_name: true` derives the name from `body` via a
     * mini-LLM naming call (created under the FINAL name — no rename); a non-empty `message` starts the
     * planning agent. Returns the FINAL `plan_name` (the auto-named result, when `auto_name`).
     */
    'plan.create': {
      params: {
        plan_name?: string;
        body?: string;
        message?: string;
        auto_name?: boolean;
      };
      result: PlanCreateResult;
    };
  }
}

/** Worker reply for the `ticket.quick_create` command kind (via command.submit). */
export interface QuickCreateResult {
  readonly handled: boolean;
  readonly ticket_id: string;
  readonly title: string;
}

/** Reply from the LIVE `ticket.next_id` RPC. Python returns `ticket_id` (not `next_id`). */
export interface NextIdResult {
  readonly ticket_id: string;
}

/** Reply from the LIVE `ticket.exists` RPC. */
export interface ExistsResult {
  readonly exists: boolean;
}

/** Reply from `plan.create`. The service returns the FINAL `plan_name` (the auto-named result when
 * `auto_name` was set), plus the spawned planner's `agent_id`. */
export interface PlanCreateResult {
  readonly handled: boolean;
  readonly ok?: boolean;
  readonly plan_name: string;
  readonly agent_id?: string;
}

/**
 * The new-plan submit input. The form fills `body` (the typed plan content) and either asks for an
 * auto name (`autoName: true`) or supplies a `planName`. `message`, when supplied, is sent to the
 * planning agent after creation.
 */
export interface CreatePlanInput {
  /** The plan's markdown body (whatever was typed in the body box). */
  readonly body: string;
  /** When `true`, the service derives the plan name from `body` via the mini-LLM naming call. */
  readonly autoName: boolean;
  /** The user-chosen plan name; ignored when `autoName` is set. */
  readonly planName?: string;
  /** Optional message sent to the planning agent after creation. */
  readonly message?: string;
}

/** The actions exposed to the dialog components for writing operations. */
export interface DialogActions {
  /**
   * Create a new ticket via `ticket.quick_create`. Resolves with the created ticket's id.
   * Rejects on bus error — the caller (modal's onIntent) handles the rejection.
   */
  quickCreateTicket(title: string): Promise<QuickCreateResult>;
  /**
   * Fetch the next free ticket id via `ticket.next_id`.
   */
  fetchNextTicketId(): Promise<NextIdResult>;
  /**
   * Check if a ticket handle exists via `ticket.exists`.
   */
  ticketExists(handle: string): Promise<ExistsResult>;
  /**
   * Create a plan and start its planning agent via `plan.create`. Resolves with the FINAL plan name
   * (the auto-named result when `autoName` was set). Rejects on bus error — the caller handles it.
   */
  createPlan(input: CreatePlanInput): Promise<PlanCreateResult>;
}

/**
 * Build the dialog actions bound to one injected {@link BusClient}. No store ref needed — these
 * are fire-and-resolve operations (not slice invalidations), so they return the result directly
 * to the calling modal intent handler. The modal handles success/failure in its own UI state.
 *
 * Rule 3: these are the ONLY callers of the bus for dialog writes. Components never touch bus.rpc.
 */
export function createDialogActions(bus: BusClient): DialogActions {
  return {
    async quickCreateTicket(title: string): Promise<QuickCreateResult> {
      // `ticket.quick_create` is an orchestrator command kind, not a standalone RPC — route it
      // through the live `command.submit` choke point. The worker returns `{ ticket_id, title }`.
      const result = await submitCommand(bus, 'ticket.quick_create', { title });
      return {
        handled: result['handled'] === true,
        ticket_id: String(result['ticket_id'] ?? ''),
        title: String(result['title'] ?? title),
      };
    },

    async fetchNextTicketId(): Promise<NextIdResult> {
      return bus.query('ticket.next_id', {});
    },

    async ticketExists(handle: string): Promise<ExistsResult> {
      return bus.query('ticket.exists', { handle });
    },

    async createPlan(input: CreatePlanInput): Promise<PlanCreateResult> {
      // Auto path: send `auto_name` + body, no plan_name (the service derives it). Custom path: send
      // the chosen plan_name. Only include message when the caller supplied one.
      const params = input.autoName
        ? {
            auto_name: true,
            body: input.body,
            ...(input.message !== undefined ? { message: input.message } : {}),
          }
        : {
            plan_name: input.planName ?? '',
            body: input.body,
            ...(input.message !== undefined ? { message: input.message } : {}),
          };
      return bus.command('plan.create', params);
    },
  };
}
