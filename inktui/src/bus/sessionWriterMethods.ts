/**
 * Typed session writer-lease commands/queries for the application protocol.
 *
 * Declaration-merges into {@link QueryMethods} / {@link CommandMethods}. Imported from
 * {@link ./BusClient.js} so the augmentation is always part of the client surface.
 */

export type WriterMode = 'structured' | 'raw_terminal';

export type PrincipalKind = 'user' | 'client' | 'workflow' | 'service' | 'reviewer';

export interface PrincipalRef {
  readonly kind: PrincipalKind;
  readonly id: string;
}

export interface WriterLease {
  readonly lease_id: string;
  readonly resource: { readonly type: 'harness_session'; readonly session_id: string };
  readonly holder: PrincipalRef;
  readonly mode: WriterMode;
  readonly fence: number;
  readonly issued_at: string;
  readonly renewed_at: string;
  readonly expires_at: string;
  readonly revoked_at: string | null;
  readonly revocation_reason: string | null;
}

export interface WriterLeaseGranted {
  readonly type: 'session.writer.granted';
  readonly request_id: string;
  readonly lease: WriterLease;
}

export interface WriterLeaseDenied {
  readonly type: 'session.writer.denied';
  readonly request_id: string;
  readonly current_holder: PrincipalRef | null;
  readonly current_mode: WriterMode | null;
  readonly retry_after: string | null;
  readonly reason: string;
}

export type WriterLeaseReply = WriterLeaseGranted | WriterLeaseDenied;

declare module './BusClient.js' {
  interface QueryMethods {
    'session.writer.get': {
      params: { session_id: string };
      result: { ok: true; lease: WriterLease | null } | { ok: false; error: 'not_found' };
    };
  }

  interface CommandMethods {
    'session.writer.acquire': {
      params: {
        session_id: string;
        mode: WriterMode;
        ttl_seconds?: number;
        force?: boolean;
        request_id?: string;
        expected_revision?: number;
      };
      result: WriterLeaseReply;
    };
    'session.writer.renew': {
      params: {
        session_id: string;
        lease_id: string;
        fence: number;
        ttl_seconds?: number;
        request_id?: string;
        expected_revision?: number;
      };
      result: WriterLeaseReply;
    };
    'session.writer.release': {
      params: {
        session_id: string;
        lease_id: string;
        fence: number;
        request_id?: string;
        expected_revision?: number;
        reason?: string;
      };
      result: WriterLeaseReply;
    };
  }
}
