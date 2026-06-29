import type { PanelId } from './panels.js';

/** The chat input focus home. The graph expands it into virtual recipient-target vertices. */
export const CHAT_FOCUS = 'chat' as const;

const STAGE_TRANSCRIPT_PREFIX = 'stage:transcript:' as const;
const STAGE_DOC_PREFIX = 'stage:doc:' as const;
const RECIPIENT_TARGET_PREFIX = 'recipient:target:' as const;

/** A mounted Stage pane: committed transcript (`stage:transcript:<agentId>`) or document (`stage:doc:<name>`). */
export type StagePaneId = `stage:${string}`;

/** Anything the rest of the input system can route to directly. */
export type FocusId = PanelId | typeof CHAT_FOCUS | StagePaneId;

/** Internal graph vertex id for one resolved message recipient target. */
export type RecipientTargetVertexId = `recipient:target:${string}`;

/** A focus graph vertex id, including virtual recipient-target vertices. */
export type FocusGraphTargetId = FocusId | RecipientTargetVertexId;

export type StagePaneFocusTarget =
  | { readonly kind: 'transcriptPane'; readonly agentId: string }
  | { readonly kind: 'docPane'; readonly name: string };

export type FocusTarget =
  | { readonly kind: 'panel'; readonly id: PanelId }
  | { readonly kind: 'composer' }
  | StagePaneFocusTarget;

export function isStagePaneId(id: FocusId): id is StagePaneId {
  return typeof id === 'string' && id.startsWith('stage:');
}

export function stageTranscriptFocusId(agentId: string): StagePaneId {
  return `${STAGE_TRANSCRIPT_PREFIX}${agentId}`;
}

/** Extract the agent id from a committed transcript pane focus id. */
export function agentIdFromStageTranscriptFocusId(focusId: FocusId): string | null {
  return typeof focusId === 'string' && focusId.startsWith(STAGE_TRANSCRIPT_PREFIX)
    ? focusId.slice(STAGE_TRANSCRIPT_PREFIX.length)
    : null;
}

export function stageDocFocusId(name: string): StagePaneId {
  return `${STAGE_DOC_PREFIX}${name}`;
}

export function nameFromStageDocFocusId(focusId: FocusId): string | null {
  return typeof focusId === 'string' && focusId.startsWith(STAGE_DOC_PREFIX)
    ? focusId.slice(STAGE_DOC_PREFIX.length)
    : null;
}

export function decodeStagePaneFocusId(focusId: FocusId): StagePaneFocusTarget | null {
  const agentId = agentIdFromStageTranscriptFocusId(focusId);
  if (agentId !== null) {
    return { kind: 'transcriptPane', agentId };
  }
  const name = nameFromStageDocFocusId(focusId);
  if (name !== null) {
    return { kind: 'docPane', name };
  }
  return null;
}

export function focusTargetFromFocusId(focusId: FocusId): FocusTarget | null {
  if (focusId === CHAT_FOCUS) {
    return { kind: 'composer' };
  }
  if (isStagePaneId(focusId)) {
    return decodeStagePaneFocusId(focusId);
  }
  return { kind: 'panel', id: focusId };
}

export function recipientTargetVertexId(targetId: string): RecipientTargetVertexId {
  return `${RECIPIENT_TARGET_PREFIX}${targetId}`;
}

export function isRecipientTargetVertexId(id: FocusGraphTargetId): id is RecipientTargetVertexId {
  return typeof id === 'string' && id.startsWith(RECIPIENT_TARGET_PREFIX);
}

export function recipientTargetIdFromVertexId(id: FocusGraphTargetId): string | null {
  return isRecipientTargetVertexId(id) ? id.slice(RECIPIENT_TARGET_PREFIX.length) : null;
}
