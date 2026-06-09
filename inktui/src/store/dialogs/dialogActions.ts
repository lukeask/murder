/**
 * Dialog actions — the *only* code that calls the bus for dialog operations (rule 3).
 *
 * Covers operations triggered from the C12 modal dialogs:
 *  - `ticket.quick_create` — create a new ticket (`ctrl+t`). NOT a standalone RPC: it is an
 *    orchestrator command kind, routed through the LIVE `command.submit` choke point (F2). See
 *    {@link ../commandSubmit.js}.
 *  - `ticket.next_id` — fetch the next free ticket id. LIVE RPC.
 *  - `ticket.exists` — check if a ticket handle already exists. LIVE RPC.
 *  - `plan.create` — message a fresh planning agent for the new-plan dialog (`ctrl+p`). The
 *    plan-create RPC is modeled as `plan.create` — NOT yet on the live bus (built in F3, flagged
 *    below).
 *
 * ## F3 dependency flag
 *
 * `plan.create` is the only method here still **modeled but NOT yet live on the bus** (F3 builds it).
 * Tests drive it against `FakeBusClient`. When F3 lands, confirm the name/shape and remove this flag.
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
 * `ticket.next_id` + `ticket.exists` are LIVE; `plan.create` is still modeled (F3 builds it).
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
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
    /**
     * Create a new plan by messaging a fresh planning agent with the plan name and an initial
     * message. Modeled on the existing `agent.message` RPC surface, lifted to a plan-scoped
     * method so the service can start the planning agent and wire the plan document.
     * B13 — NOT yet live on the bus.
     */
    'plan.create': {
      params: { plan_name: string; message: string };
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

/** Reply from the LIVE `ticket.next_id` RPC. */
export interface NextIdResult {
  readonly next_id: string;
}

/** Reply from the LIVE `ticket.exists` RPC. */
export interface ExistsResult {
  readonly exists: boolean;
}

/** Reply from `plan.create`. B13 shape — confirm when service lands. */
export interface PlanCreateResult {
  readonly handled: boolean;
  readonly plan_name: string;
  readonly agent_id?: string;
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
   * Message a fresh planning agent to create a plan via `plan.create`.
   */
  createPlan(planName: string, message: string): Promise<PlanCreateResult>;
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
      return bus.rpc('ticket.next_id', {});
    },

    async ticketExists(handle: string): Promise<ExistsResult> {
      return bus.rpc('ticket.exists', { handle });
    },

    async createPlan(planName: string, message: string): Promise<PlanCreateResult> {
      return bus.rpc('plan.create', { plan_name: planName, message });
    },
  };
}
