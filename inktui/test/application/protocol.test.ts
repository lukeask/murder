import {
  APPLICATION_PROTOCOL_VERSION,
  type ClientMessage,
  type ProjectionEvent,
  type ServerMessage,
} from '../../src/generated/applicationProtocol.js';

// protocol.ts is types + constants only (rule 4), so the assertions that matter are: the version
// stays pinned to the Python source, and the discriminated unions are exhaustively dispatchable —
// the property the switch-heavy store relies on. Exhaustiveness is enforced at compile time by the
// `never` arms below; the runtime test just exercises the dispatchers.

/** Compile-time exhaustiveness guard over generated client messages. */
function clientOp(message: ClientMessage): string {
  switch (message.op) {
    case 'client.hello':
    case 'request':
    case 'subscribe':
    case 'unsubscribe':
    case 'terminal.attach':
    case 'terminal.detach':
    case 'terminal.resync':
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
    case 'terminal.chunk':
    case 'terminal.gap':
    case 'terminal.resynced':
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

  it('models projection invalidations as the only subscription event payload', () => {
    const event: ProjectionEvent = {
      type: 'projection.invalidate',
      projection: 'schedule',
      subject_key: 'T-1',
      generation: 1,
    };
    expect(event.type).toBe('projection.invalidate');
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
