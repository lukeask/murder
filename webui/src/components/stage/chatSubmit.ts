/**
 * Chat submit orchestration — TUI `makeChatInputHandler` Enter path including image-span expansion.
 * History recording lives in ChatInput (record on successful plain send).
 *
 * Order (locked): expandSpans → workflow fire → template expansion → `/`|`:` commands → normal send.
 * Submit while any span is still uploading is blocked (caller keeps the buffer).
 */

import {
  BUILTIN_COMMAND_NAMES,
  dispatchCommand,
  type CommandCtx,
} from '@murder/ui-core/input/commandDispatch.js';
import { expandSpans, spanIds } from '@murder/ui-core/input/chatInputStore.js';
import { expandTemplates } from '@murder/ui-core/input/expandTemplates.js';
import { parseWorkflowFire } from '@murder/ui-core/input/fireWorkflow.js';

export type ImageDraftLookup = {
  readonly drafts: Readonly<Record<string, { readonly status: string }>>;
  pathsById(): ReadonlyMap<string, string>;
};

export type ChatSubmitDeps = {
  /** Composer buffer (may contain marked image spans). */
  readonly message: string;
  readonly agentId: string | null;
  readonly workflowNames: ReadonlySet<string>;
  readonly templateRegistry: ReadonlyMap<string, string>;
  readonly commandCtx: CommandCtx;
  readonly runWorkflow: (name: string, args: Record<string, string>) => void;
  readonly send: (agentId: string, message: string) => void;
  /** When set, expand/strip image spans and block while any are still uploading. */
  readonly imageDraft?: ImageDraftLookup;
  /** Toast when submit is blocked because an image is still uploading. */
  readonly onUploading?: () => void;
};

export type ChatSubmitResult =
  | { readonly kind: 'empty' }
  | { readonly kind: 'uploading' }
  | { readonly kind: 'workflow'; readonly name: string; readonly spanIds: readonly string[] }
  | { readonly kind: 'command'; readonly spanIds: readonly string[] }
  | { readonly kind: 'send'; readonly message: string; readonly spanIds: readonly string[] }
  | { readonly kind: 'noop'; readonly spanIds: readonly string[] };

/**
 * Run the TUI-equivalent submit pipeline. Side-effects go through `deps`; the result describes
 * which branch ran so callers/tests can assert without mocking every leaf.
 */
export function processChatSubmit(deps: ChatSubmitDeps): ChatSubmitResult {
  const raw = deps.message;
  if (raw.length === 0) {
    return { kind: 'empty' };
  }

  const ids = spanIds(raw);
  const draft = deps.imageDraft;
  if (draft !== undefined && ids.length > 0) {
    const stillUploading = ids.some((id) => draft.drafts[id]?.status === 'uploading');
    if (stillUploading) {
      deps.onUploading?.();
      return { kind: 'uploading' };
    }
  }

  let message =
    draft !== undefined && ids.length > 0 ? expandSpans(raw, draft.pathsById()) : raw;
  // After stripping failed spans the buffer may be empty — treat like noop (clear caller-side).
  if (message.length === 0 && ids.length > 0) {
    return { kind: 'noop', spanIds: ids };
  }
  if (message.length === 0) {
    return { kind: 'empty' };
  }

  const fire = parseWorkflowFire(message, BUILTIN_COMMAND_NAMES, deps.workflowNames);
  if (fire !== null) {
    deps.runWorkflow(fire.name, fire.args);
    return { kind: 'workflow', name: fire.name, spanIds: ids };
  }

  message = expandTemplates(message, deps.templateRegistry, BUILTIN_COMMAND_NAMES);
  if (message.length === 0) {
    return { kind: 'noop', spanIds: ids };
  }

  if (dispatchCommand(message, deps.agentId, deps.commandCtx)) {
    return { kind: 'command', spanIds: ids };
  }

  if (deps.agentId !== null) {
    deps.send(deps.agentId, message);
    return { kind: 'send', message, spanIds: ids };
  }
  return { kind: 'noop', spanIds: ids };
}
