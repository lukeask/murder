/**
 * Plans actions wire-contract test — proves the full chain:
 *   wire (snake_case stub) → toPlanRow projection → PlansState → selectPlansView (C11 ordering)
 *
 * The Python backend's `PlanSummary` now sends `parent`, `updated_at`, and `char_count` (with
 * `dto_to_wire` serialising `datetime` → ISO-8601 string via `.isoformat()`). This test locks the
 * Ink consumer to those real field names so blank-row regression is caught at the seam.
 *
 * datetime serialisation finding (logged here, not in Python):
 *   `dto_to_wire` (client_api.py:461-462) hits the `isinstance(datetime)` branch → `.isoformat()`
 *   → ISO-8601 string on the wire. `PlanDto.updated_at` is typed `string`; the selector compares
 *   ISO strings lexicographically for recency ordering — correct because ISO-8601 sorts correctly
 *   as a string. No backend flag needed: `updated_at` IS serialisable, and `read_model.py` sets
 *   it to `_parse_datetime(...) or as_of` so it is never null in practice.
 *
 * Two complementary test groups:
 *  1. Wire→row mapping: stub `state.plans_snapshot` with snake_case DTO fields, run refresh(),
 *     assert the slice rows carry the mapped camelCase values.
 *  2. Wire→selector: same stub fed through event invalidation, assert selectPlansView produces
 *     correct indentation (4 spaces) and recency order (child bubbles parent).
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import { selectPlansView } from '../../../src/selectors/plansSelectors.js';
import type { PlansSnapshotReply } from '../../../src/store/plans/plansActions.js';
import { createAppStore } from '../../../src/store/store.js';
import { selectLiveToasts, toastStore } from '../../../src/store/toast/toastStore.js';

// ── Helpers ───────────────────────────────────────────────────────────────────────────────────────

/** Minimal store with the plans stub pre-loaded. The fake wraps stubs in {ok,value} and unwraps
 * them on delivery, modelling the live `state.*` envelope round-trip. */
function setup(plansReply: PlansSnapshotReply) {
  const fake = new FakeBusClient();
  // crow_snapshot required for store boot
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  fake.stubRpc('state.plans_snapshot', plansReply);
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

// ── Wire → row mapping ────────────────────────────────────────────────────────────────────────────

describe('plansActions — wire→row mapping (snake_case DTO → camelCase PlanRow)', () => {
  it('maps char_count to charCount, updated_at to updatedAt, parent to parent', async () => {
    const { store, dispose } = setup({
      invalidation_key: 'iv-p',
      plans: [
        {
          name: 'alpha',
          char_count: 1234,
          updated_at: '2026-06-01T12:00:00',
          parent: null,
        },
      ],
    });

    await store.getState().actions.plans.refresh();

    const row = store.getState().plans.rows[0];
    expect(row?.name).toBe('alpha');
    expect(row?.charCount).toBe(1234);
    expect(row?.updatedAt).toBe('2026-06-01T12:00:00');
    expect(row?.parent).toBeNull();
    dispose();
  });

  it('maps parent field (string) when present', async () => {
    const { store, dispose } = setup({
      invalidation_key: 'iv-p',
      plans: [
        { name: 'parent-plan', char_count: 500, updated_at: '2026-06-01T00:00:00' },
        {
          name: 'child-plan',
          char_count: 200,
          updated_at: '2026-06-02T00:00:00',
          parent: 'parent-plan',
        },
      ],
    });

    await store.getState().actions.plans.refresh();

    const rows = store.getState().plans.rows;
    expect(rows).toHaveLength(2);
    expect(rows.find((r) => r.name === 'parent-plan')?.parent).toBeNull();
    expect(rows.find((r) => r.name === 'child-plan')?.parent).toBe('parent-plan');
    dispose();
  });

  it('normalises absent parent (undefined) to null', async () => {
    // Python sends `parent: null` or omits the field for top-level plans. Both must map to null.
    const { store, dispose } = setup({
      invalidation_key: 'iv-p',
      plans: [
        // `parent` field absent — the DTO type marks it optional (`parent?: string | null`)
        { name: 'top-level', char_count: 100, updated_at: '2026-06-01T00:00:00' },
      ],
    });

    await store.getState().actions.plans.refresh();

    expect(store.getState().plans.rows[0]?.parent).toBeNull();
    dispose();
  });

  it('slice is ready and rows are populated after refresh', async () => {
    const { store, dispose } = setup({
      invalidation_key: 'iv-p',
      plans: [{ name: 'only-plan', char_count: 50, updated_at: '2026-06-08T00:00:00' }],
    });

    await store.getState().actions.plans.refresh();

    expect(store.getState().plans.status).toBe('ready');
    expect(store.getState().plans.rows).toHaveLength(1);
    dispose();
  });
});

// ── Wire → selector (C11 ordering) ───────────────────────────────────────────────────────────────

describe('plansActions — wire→selector: C11 indentation + recency ordering from live fields', () => {
  it('child plan is indented 4 spaces under its parent in the selector output', async () => {
    const { store, dispose } = setup({
      invalidation_key: 'iv-p',
      plans: [
        { name: 'root', char_count: 100, updated_at: '2026-06-01T00:00:00' },
        { name: 'leaf', char_count: 50, updated_at: '2026-05-01T00:00:00', parent: 'root' },
      ],
    });

    await store.getState().actions.plans.refresh();

    const view = selectPlansView(store.getState().plans, store.getState().favorites);
    expect(view.rows.map((r) => r.name)).toEqual(['root', '    leaf']);
    expect(view.rows[1]?.depth).toBe(1);
    // The row id is the un-indented name (used for star/open actions).
    expect(view.rows[1]?.id).toBe('leaf');
    dispose();
  });

  it("child's more-recent updated_at bubbles the parent's group above an unrelated plan", async () => {
    // group A: parent old (2026-01-01), but child very recent (2026-06-08) → group A sorts first.
    // group B: moderately recent (2026-03-01), no children.
    const { store, dispose } = setup({
      invalidation_key: 'iv-p',
      plans: [
        { name: 'A-parent', char_count: 10, updated_at: '2026-01-01T00:00:00' },
        {
          name: 'A-child',
          char_count: 5,
          updated_at: '2026-06-08T00:00:00',
          parent: 'A-parent',
        },
        { name: 'B-parent', char_count: 20, updated_at: '2026-03-01T00:00:00' },
      ],
    });

    await store.getState().actions.plans.refresh();

    const view = selectPlansView(store.getState().plans, store.getState().favorites);
    // A-group first (child bubbled it), then B-group.
    expect(view.rows.map((r) => r.name)).toEqual(['A-parent', '    A-child', 'B-parent']);
    dispose();
  });

  it('selector formats char_count and updated_at from the live wire fields', async () => {
    const { store, dispose } = setup({
      invalidation_key: 'iv-p',
      plans: [{ name: 'demo-plan', char_count: 4200, updated_at: '2026-06-09T14:30:00' }],
    });

    await store.getState().actions.plans.refresh();

    const view = selectPlansView(store.getState().plans, store.getState().favorites);
    const row = view.rows[0];
    // formatCharCount: n.toLocaleString() + ' chars', unpadded (no trailing pad spaces) — exact
    // locale output is env-dependent, so assert the suffix with no trailing whitespace.
    expect(row?.charCount).toMatch(/chars$/);
    // formatUpdatedAt: `Mon. dd HH:MM` from the ISO slice → 'Jun. 09 14:30'.
    expect(row?.updatedAt).toBe('Jun. 09 14:30');
    dispose();
  });
});

// ── spawnPlanner — the `p` bind's plans-domain verb ───────────────────────────────────────────────

describe('plansActions — spawnPlanner', () => {
  it('spawns a planning rogue over the plan: crow.spawn_rogue with the planner defaults, then the kickoff-by-path message', async () => {
    const { fake, store, dispose } = setup({ invalidation_key: 'iv', plans: [] });
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', {
      ok: true,
      status: 'done',
      result_json: JSON.stringify({ handled: true, agent_id: 'rogue-1' }),
    });
    toastStore.getState().clear();

    await store.getState().actions.plans.spawnPlanner('alpha');

    const submits = fake.rpcCalls.filter((c) => c.method === 'command.submit');
    const kinds = submits.map((c) => (c.params as { kind: string }).kind);
    expect(kinds).toContain('crow.spawn_rogue');
    expect(kinds).toContain('agent.message'); // the kickoff rides out-of-band after the spawn

    const spawn = submits.find((c) => (c.params as { kind: string }).kind === 'crow.spawn_rogue');
    const payload = (spawn?.params as { payload: Record<string, unknown> }).payload;
    // The planner defaults (plannerSpawnParams): deep-thinking tier, named after the plan.
    expect(payload).toMatchObject({
      harness: 'claude_code',
      model: 'opus',
      effort: 'high',
      name: 'plan-alpha',
    });
    expect(payload).not.toHaveProperty('worktree_branch'); // the planner edits .murder/, not the tree

    const kickoff = submits.find((c) => (c.params as { kind: string }).kind === 'agent.message');
    const kickoffPayload = (kickoff?.params as { payload: { agent_id: string; message: string } })
      .payload;
    expect(kickoffPayload.agent_id).toBe('rogue-1');
    // Reference-by-path (the locked mechanism): the planner READS the plan file, never an inlined body.
    expect(kickoffPayload.message).toContain('.murder/plans/alpha.md');

    const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
    expect(live.map((t) => t.text)).toContain('planner spawned for "alpha"');
    toastStore.getState().clear();
    dispose();
  });

  it('routes a spawn failure into an error toast (never throws past the action)', async () => {
    const { fake, store, dispose } = setup({ invalidation_key: 'iv', plans: [] });
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', { ok: true, status: 'failed', last_error: 'no capacity' });
    toastStore.getState().clear();

    await store.getState().actions.plans.spawnPlanner('alpha'); // resolves — no throw

    const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
    expect(live.some((t) => t.severity === 'error' && t.text.includes('no capacity'))).toBe(true);
    toastStore.getState().clear();
    dispose();
  });
});
