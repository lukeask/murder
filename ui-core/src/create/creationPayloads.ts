/**
 * Renderer-neutral preparation for the small create dialogs.
 *
 * The renderers retain their respective modal and focus lifecycles; they share the validation and
 * payload contract so the same user input always produces the same application command.
 */

import type { CreatePlanInput } from '../store/dialogs/dialogActions.js';

/** The naming choices presented by the new-plan dialogs. */
export type PlanNaming = 'auto' | 'custom';

/** The exact inline validation text used by both new-plan dialogs. */
export const PLAN_NAME_REQUIRED_ERROR = 'Plan name is required (or pick "auto").';

/** The exact inline validation text used by both new-ticket dialogs. */
export const TICKET_TITLE_REQUIRED_ERROR = 'Ticket title is required.';

/** A pure create-workflow operation either produces a ready payload or an inline validation error. */
export type CreatePreparationResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: string };

/** Raw values collected by a renderer's new-plan form. */
export interface CreatePlanFormValues {
  /** The plan body, preserved verbatim in the create payload. */
  readonly body: string;
  readonly naming: PlanNaming;
  /** The raw custom-name editor value. It is normalized only for the custom path. */
  readonly planName: string;
}

/**
 * Validate a new-plan form and construct its dialog action input.
 *
 * A non-blank body is also the planner's initial message, but the original body (including its
 * surrounding whitespace) is retained as that message for compatibility with both existing UIs.
 */
export function prepareCreatePlan(
  values: CreatePlanFormValues,
): CreatePreparationResult<CreatePlanInput> {
  const autoName = values.naming === 'auto';
  const planName = values.planName.trim();
  if (!autoName && planName.length === 0) {
    return { ok: false, error: PLAN_NAME_REQUIRED_ERROR };
  }

  const message = values.body.trim().length > 0 ? values.body : undefined;
  return {
    ok: true,
    value: autoName
      ? { body: values.body, autoName: true, ...(message !== undefined ? { message } : {}) }
      : {
          body: values.body,
          autoName: false,
          planName,
          ...(message !== undefined ? { message } : {}),
        },
  };
}

/** Trim and validate a ticket title before either ticket creation workflow is started. */
export function prepareTicketTitle(title: string): CreatePreparationResult<string> {
  const value = title.trim();
  return value.length > 0
    ? { ok: true, value }
    : { ok: false, error: TICKET_TITLE_REQUIRED_ERROR };
}
