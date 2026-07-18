import type { BusEvent } from '../../src/bus/protocol.js';
import {
  PRESENCE_USER_KINDS,
  SOCKET_BASENAME,
  SOCKET_RUNTIME_SUBDIR,
} from '../../src/bus/protocol.js';
import {
  APPLICATION_PROTOCOL_VERSION,
  type ClientMessage,
  type ServerMessage,
} from '../../src/generated/applicationProtocol.js';

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
    case 'note':
    case 'escalation':
    case 'status_change':
    case 'error':
    case 'command':
    case 'state.snapshot':
    case 'presence':
    case 'scheduler.mode':
    case 'scheduler.decision':
    case 'completion.verdict':
    case 'agent.lifecycle':
    case 'usage.reset':
    case 'conversation.block':
    case 'conversation.state':
    case 'tmux.frame':
    case 'harness.decision.request':
    case 'harness.decision.response':
      return event.type;
    default: {
      const unreachable: never = event;
      return unreachable;
    }
  }
}

/** Compile-time exhaustiveness guard over generated client messages. */
function clientOp(message: ClientMessage): string {
  switch (message.op) {
    case 'client.hello':
    case 'request':
    case 'subscribe':
    case 'unsubscribe':
    case 'terminal.attach':
    case 'terminal.detach':
      return message.op;
    default: {
      const unreachable: never = message;
      return unreachable;
    }
  }
}

/** Compile-time exhaustiveness guard over generated server messages. */
function serverOp(message: ServerMessage): string {
  switch (message.op) {
    case 'server.hello':
    case 'reply':
    case 'subscription.ready':
    case 'subscription.event':
    case 'terminal.attached':
    case 'terminal.frame':
    case 'error':
      return message.op;
    default: {
      const unreachable: never = message;
      return unreachable;
    }
  }
}

describe('protocol', () => {
  it('pins the generated application protocol version', () => {
    expect(APPLICATION_PROTOCOL_VERSION).toBe(1);
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

  it('dispatches every generated client and server op (exhaustiveness)', () => {
    const client: ClientMessage = {
      op: 'request',
      request_id: 'r1',
      request: { kind: 'query', name: 'roster.get', params: {} },
      timeout_s: 30,
    };
    const server: ServerMessage = {
      op: 'reply',
      request_id: 'r1',
      result: { ok: true },
    };
    expect(clientOp(client)).toBe('request');
    expect(serverOp(server)).toBe('reply');
  });
});
