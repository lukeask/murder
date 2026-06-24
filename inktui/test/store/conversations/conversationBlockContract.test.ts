/**
 * Cross-language `conversation.block` shape contract test (F11 H3).
 *
 * `conversation.block` is the one deliberate content-bearing exception to murder's key-only bus
 * contract, so the inner `block` wire shape is a REAL contract — not an invalidation key. This test
 * pins the Ink consumer (`parseBlock` + `applyBlock` + `selectConversationView`) against a
 * **golden produced by the real Python producer** (`block_to_wire`, via
 * `tests/unit/test_conversation_block_golden.py`), NOT a hand-invented `{type,id,text}` shape.
 *
 * The golden (`../../fixtures/conversation-block-golden.json`) is the storage-row wire shape:
 *   `{ id:int, conversation_id, ordinal, kind, payload, sealed, service_received_at }`
 * with the segment dict nested under `payload`. A hand-built flat shape would NOT exercise the
 * unwrap, so this fixture is the anchor.
 *
 * Drift detection:
 *  - Python side: if `block_to_wire`'s keys/types change, the Python golden test fails (the golden
 *    no longer matches the real producer) — forcing a deliberate regenerate.
 *  - Ink side: if the consumer starts reading a key the producer doesn't emit (or stops unwrapping
 *    `payload`, or reverts `id` to string-only), the rendered turns below go wrong → this fails.
 *
 * Coverage: every block `kind` (user, assistant_intermediate/final, tool_call, plan_update,
 * agent_event, choice_prompt incl. the live-prompt trailing-segment heuristic, notice) AND the
 * `block-updated` live-tail growth path (numeric id round-trip).
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import type { ConversationBlockEvent } from '../../../src/bus/protocol.js';
import { selectConversationView } from '../../../src/selectors/conversationsSelectors.js';
import { createAppStore } from '../../../src/store/store.js';
import golden from '../../fixtures/conversation-block-golden.json' with { type: 'json' };

/** The committed golden is an array of `conversation.block` wire events. */
interface GoldenEvent {
  type: 'conversation.block';
  agent_id: string;
  conversation_id: string;
  action: 'block-appended' | 'block-updated';
  block: Record<string, unknown>;
}

const GOLDEN = golden as readonly GoldenEvent[];
const AGENT_ID = 'crow-7';

function setup() {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

/** Replay the golden through the live bus → store subscription → `applyBlock` path. */
function replayGolden(fake: FakeBusClient): void {
  for (const ev of GOLDEN) {
    const event: ConversationBlockEvent = {
      type: 'conversation.block',
      id: `ev-${ev.block['ordinal']}`,
      ts: '2026-06-09T00:00:00Z',
      run_id: 'run-1',
      agent_id: ev.agent_id,
      conversation_id: ev.conversation_id,
      action: ev.action,
      block: ev.block,
    };
    fake.emit(event);
  }
}

describe('conversation.block cross-language shape contract (H3)', () => {
  it('the golden is the real storage-row wire shape (sanity: nested payload, numeric id, kind)', () => {
    expect(GOLDEN.length).toBeGreaterThan(0);
    const first = GOLDEN[0]?.block;
    // Block-level fields are the storage row, not the segment.
    expect(typeof first?.['id']).toBe('number'); // numeric row id, not string
    expect(first).toHaveProperty('kind'); // storage discriminant
    expect(first).toHaveProperty('payload'); // segment nested here
    expect(first).not.toHaveProperty('type'); // `type` lives on payload, not the block
    expect((first?.['payload'] as Record<string, unknown>)['type']).toBe('user');
  });

  it('replaying the golden builds a transcript that survives the live-tail block-updated merge', () => {
    const { fake, store, dispose } = setup();
    replayGolden(fake);

    const blocks = store.getState().conversations.transcripts[AGENT_ID];
    expect(blocks).toBeDefined();
    // 10 golden events, but the 3rd is a block-updated on the live assistant_intermediate (id 2):
    // it must REPLACE in place, not append. So the transcript has 9 blocks, not 10.
    expect(GOLDEN).toHaveLength(10);
    expect(blocks).toHaveLength(9);
    // The replaced block carries the grown text (proves numeric-id replace-by-id worked).
    const grown = blocks?.find((b) => b.id === '2');
    expect(grown?.raw['text']).toBe('Sure, starting — reading files');
    expect(grown?.kind).toBe('assistant_intermediate');
    dispose();
  });

  it('renders every block kind to the correct turn (selectors anchored to the real payload)', () => {
    const { fake, store, dispose } = setup();
    replayGolden(fake);

    const view = selectConversationView(AGENT_ID, store.getState().conversations);
    const turns = view.turns;

    // Map speaker → texts for readable assertions.
    const bySpeaker = (s: string) => turns.filter((t) => t.speaker === s).map((t) => t.text);

    // user
    expect(bySpeaker('user')).toEqual(['build the thing']);
    // assistant: the live-tail merged to the grown text; final assistant rendered too.
    expect(bySpeaker('assistant')).toEqual(['Sure, starting — reading files', 'Done.']);
    // tool_call: title + $input + result + [collapsed]
    expect(bySpeaker('tool')).toEqual(['Bash\n$ ls -la\ntotal 0\n[collapsed]']);
    // plan_update: title + checkbox lines
    expect(bySpeaker('plan')).toEqual(['Updated Plan\n[x] read files\n[ ] write code']);
    // agent_event: status · name · elapsed
    expect(bySpeaker('agent')).toEqual(['completed · explorer · 12s']);
    // notice: severity: message
    expect(bySpeaker('notice')).toEqual(['warning: rate limit approaching']);
    dispose();
  });

  it('choice_prompt: answered renders the selection; trailing unanswered is flagged live', () => {
    const { fake, store, dispose } = setup();
    replayGolden(fake);

    const view = selectConversationView(AGENT_ID, store.getState().conversations);
    const prompts = view.turns.filter((t) => t.speaker === 'prompt');
    expect(prompts).toHaveLength(2);

    // Answered choice_prompt (mid-transcript): shows the chosen option, NOT flagged live.
    const answered = prompts[0];
    expect(answered?.text).toBe('Pick an approach\nselected: 2. patch');
    expect(answered?.isLivePrompt).toBeFalsy();

    // Unanswered choice_prompt (LAST block): lists every option AND is flagged live.
    const live = prompts[1];
    expect(live?.text).toBe('Continue?\n1. yes\n2. no');
    expect(live?.isLivePrompt).toBe(true);

    // The live flag must be the trailing turn only.
    const lastTurn = view.turns[view.turns.length - 1];
    expect(lastTurn?.isLivePrompt).toBe(true);
    dispose();
  });
});
