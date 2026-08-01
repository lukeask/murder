import { describe, expect, it } from 'vitest';
import {
  PLAN_NAME_REQUIRED_ERROR,
  TICKET_TITLE_REQUIRED_ERROR,
  prepareCreatePlan,
  prepareTicketTitle,
} from '@murder/ui-core/create/creationPayloads.js';

describe('creation payload preparation', () => {
  it('constructs the auto-name payload and derives its initial message from a non-blank body', () => {
    expect(
      prepareCreatePlan({
        body: '  map the migration  ',
        naming: 'auto',
        planName: 'ignored',
      }),
    ).toEqual({
      ok: true,
      value: {
        body: '  map the migration  ',
        autoName: true,
        message: '  map the migration  ',
      },
    });
  });

  it('constructs the custom-name payload with a normalized name', () => {
    expect(
      prepareCreatePlan({ body: 'body', naming: 'custom', planName: '  migration-plan  ' }),
    ).toEqual({
      ok: true,
      value: { body: 'body', autoName: false, planName: 'migration-plan', message: 'body' },
    });
  });

  it('does not add an initial message for a blank plan body', () => {
    expect(prepareCreatePlan({ body: ' \n ', naming: 'auto', planName: '' })).toEqual({
      ok: true,
      value: { body: ' \n ', autoName: true },
    });
  });

  it('returns the shared custom-name validation error', () => {
    expect(prepareCreatePlan({ body: '', naming: 'custom', planName: ' \t ' })).toEqual({
      ok: false,
      error: PLAN_NAME_REQUIRED_ERROR,
    });
  });

  it('normalizes a ticket title and rejects a blank one with the shared error', () => {
    expect(prepareTicketTitle('  Fix the race  ')).toEqual({ ok: true, value: 'Fix the race' });
    expect(prepareTicketTitle(' \n ')).toEqual({
      ok: false,
      error: TICKET_TITLE_REQUIRED_ERROR,
    });
  });
});
