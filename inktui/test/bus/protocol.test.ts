import type { BusEvent, WireMessage } from '../../src/bus/protocol.js';
import {
  PRESENCE_USER_KINDS,
  PROTOCOL_VERSION,
  SOCKET_BASENAME,
  SOCKET_RUNTIME_SUBDIR,
} from '../../src/bus/protocol.js';

// protocol.ts is types + constants only (rule 4), so the assertions that matter are: the version
// stays pinned to the Python source, and the discriminated unions are exhaustively dispatchable —
// the property the switch-heavy store relies on. Exhaustiveness is enforced at compile time by the
// `never` arms below; the runtime test just exercises the dispatchers.

/** Compile-time exhaustiveness guard: if a BusEvent kind is added without a case here, the
 * argument is no longer assignable to `never` and `npm run typecheck` fails. */
function eventKind(event: BusEvent): string {
  switch (event.type) {
    case 'heartbeat':
    case 'summary':
    case 'question':
    case 'escalation':
    case 'status_change':
    case 'error':
    case 'command':
    case 'state.snapshot':
    case 'presence':
    case 'scheduler.mode':
    case 'scheduler.decision':
    case 'usage.reset':
    case 'conversation.block':
    case 'tmux.frame':
      return event.type;
    default: {
      const unreachable: never = event;
      return unreachable;
    }
  }
}

/** Compile-time exhaustiveness guard over the wire envelope discriminant. */
function wireOp(message: WireMessage): string {
  switch (message.op) {
    case 'hello':
    case 'pub':
    case 'sub':
    case 'rpc':
    case 'ack':
    case 'err':
    case 'wake':
      return message.op;
    default: {
      const unreachable: never = message;
      return unreachable;
    }
  }
}

describe('protocol', () => {
  it('pins PROTOCOL_VERSION to the Python source (murder/bus/protocol.py)', () => {
    expect(PROTOCOL_VERSION).toBe(1);
  });

  it('carries the socket-path constants for the real client (C2)', () => {
    expect(SOCKET_RUNTIME_SUBDIR).toBe('murder');
    expect(SOCKET_BASENAME).toBe('bus.sock');
  });

  it('counts only tui + web client kinds toward presence', () => {
    expect([...PRESENCE_USER_KINDS]).toEqual(['tui', 'web']);
  });

  it('dispatches every BusEvent kind (exhaustiveness)', () => {
    const event: BusEvent = {
      type: 'state.snapshot',
      id: 'e1',
      ts: '2026-06-08T00:00:00Z',
      run_id: 'r1',
      agent_id: '',
      entity: 'ticket',
      key: 'T-1',
      entity_version: 1,
    };
    expect(eventKind(event)).toBe('state.snapshot');
  });

  it('dispatches every WireMessage op (exhaustiveness)', () => {
    const message: WireMessage = {
      op: 'ack',
      schema_version: PROTOCOL_VERSION,
      correlation_id: 'c1',
      body: { kind: 'rpc_reply', result: { ok: true } },
    };
    expect(wireOp(message)).toBe('ack');
  });
});
