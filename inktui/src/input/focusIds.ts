import type { PanelId } from './panels.js';

/** The chat input focus home. The graph expands it into virtual chat-target vertices. */
export const CHAT_FOCUS = 'chat' as const;

/** A mounted Stage pane: chat history (`stage:chat:<agentId>`) or document (`stage:doc:<name>`). */
export type StagePaneId = `stage:${string}`;

/** Anything the rest of the input system can route to directly. */
export type FocusId = PanelId | typeof CHAT_FOCUS | StagePaneId;

/** Internal graph vertex id for one chat input target. */
export type ChatTargetVertexId = `chat:target:${string}`;

/** A focus graph vertex id, including virtual chat-target vertices. */
export type FocusGraphTargetId = FocusId | ChatTargetVertexId;

export function isStagePaneId(id: FocusId): id is StagePaneId {
  return typeof id === 'string' && id.startsWith('stage:');
}

export function chatTargetVertexId(targetId: string): ChatTargetVertexId {
  return `chat:target:${targetId}`;
}

export function isChatTargetVertexId(id: FocusGraphTargetId): id is ChatTargetVertexId {
  return typeof id === 'string' && id.startsWith('chat:target:');
}
