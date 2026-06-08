/**
 * Dialog actions — the *only* code that calls the bus for dialog operations (rule 3).
 *
 * Covers two operations triggered from the C12 modal dialogs:
 *  - `ticket.quick_create` — create a new ticket from the new-ticket dialog (`ctrl+t`).
 *  - `ticket.next_id` — fetch the next free ticket id for the new-ticket dialog.
 *  - `ticket.exists` — check if a ticket handle already exists.
 *  - `plan.create` — message a fresh planning agent for the new-plan dialog (`ctrl+p`). Uses the
 *    existing `agent.message` RPC surface; the plan-create RPC is modeled here as `plan.create`
 *    (a `domain.verb` name mirroring the bus-contract style — NOT yet on the live bus, flagged below).
 *
 * ## B13 dependency flag
 *
 * `ticket.quick_create`, `ticket.next_id`, `ticket.exists`, and `plan.create` are ALL service B13
 * methods (the V-list). They are **modeled here but NOT yet live on the bus**. Tests drive against
 * `FakeBusClient` with canned stubs. When service B13 lands:
 *  1. Confirm the method names + shapes against the running service.
 *  2. Update `QuickCreateResult`, `NextIdResult`, `ExistsResult`, and `PlanCreateResult` if the
 *     wire shapes differ from what is modeled here.
 *  3. Remove this B13 flag from the doc comment.
 *
 * The RpcMethods augmentation below keeps the C1/C2 bus files byte-identical (rule 4 — the seam).
 */

import type { BusClient } from '../../bus/BusClient.js';

/**
 * C12's RPC method declarations, augmenting the shared {@link RpcMethods} registry without
 * editing the frozen C1/C2 bus files. Each key is distinct from every other slice's keys —
 * the compiler will catch a collision if a later chunk redeclares the same method name.
 *
 * **B13 flag: ALL four methods below are modeled (not live).** Confirm shapes when B13 lands.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /**
     * Create a new ticket from a title string.
     * Returns the new ticket's id and title.
     * B13 / V1 — NOT yet live on the bus.
     */
    'ticket.quick_create': {
      params: { title: string };
      result: QuickCreateResult;
    };
    /**
     * Fetch the next free ticket id (the id the service would assign to a new ticket).
     * B13 / V4 — NOT yet live on the bus.
     */
    'ticket.next_id': {
      params: Record<string, never>;
      result: NextIdResult;
    };
    /**
     * Check whether a ticket handle already exists.
     * B13 / V5 — NOT yet live on the bus.
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

/** Reply from `ticket.quick_create`. B13 shape — confirm when service lands. */
export interface QuickCreateResult {
  readonly handled: boolean;
  readonly ticket_id: string;
  readonly title: string;
}

/** Reply from `ticket.next_id`. B13 shape — confirm when service lands. */
export interface NextIdResult {
  readonly next_id: string;
}

/** Reply from `ticket.exists`. B13 shape — confirm when service lands. */
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
   * B13 dependency: not live; FakeBusClient only until B13 lands.
   */
  quickCreateTicket(title: string): Promise<QuickCreateResult>;
  /**
   * Fetch the next free ticket id via `ticket.next_id`.
   * B13 dependency: not live; FakeBusClient only until B13 lands.
   */
  fetchNextTicketId(): Promise<NextIdResult>;
  /**
   * Check if a ticket handle exists via `ticket.exists`.
   * B13 dependency: not live; FakeBusClient only until B13 lands.
   */
  ticketExists(handle: string): Promise<ExistsResult>;
  /**
   * Message a fresh planning agent to create a plan via `plan.create`.
   * B13 dependency: not live; FakeBusClient only until B13 lands.
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
      return bus.rpc('ticket.quick_create', { title });
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
