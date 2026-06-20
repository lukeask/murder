/**
 * ChatInput — the always-visible chat input and the focus *home* (the re-home destination). It is a
 * focusable like a panel — registers its rect, reads {@link useEffectiveFocus} for its highlight —
 * but it is **not** a `PanelId`: it can never be toggled off, so it uses the {@link CHAT_FOCUS}
 * literal as its focus id and lives outside the panel store.
 *
 * Scope here: the always-visible, always-focusable input box with its highlight, AND (C11, part F)
 * the live message buffer + send pipeline — the **persistent chat-input mode**.
 *
 * ## Persistent chat-input mode (C11, part F)
 *
 * Chat is the app's permanent focus home, so its text entry is NOT a transient {@link
 * ../input/modeStore.js modeStore} frame (that primitive is capture + focus-restore, which chat does
 * not want). Instead, when chat is the effective focus, the ONE root dispatcher (rule 5) routes the
 * (non-chord) event through its layer-2 chat short-circuit to a {@link ../input/dispatcher.js
 * ChatInputHandler} (built in `App.tsx`'s `Shell`). That handler buffers printable chars into the
 * {@link ../input/chatInputStore.js chatInput store}, deletes on Backspace, and on Enter sends the
 * buffer to the active agent via `conversations.send` (rule 3) and clears it. This component only
 * *renders* the buffer (read via `useChatInputStore`) with a {@link ./TextInput.js TextInput} — it
 * adds NO `useInput` (rule 5: still exactly one in the tree). Global ctrl-chords still fire while
 * typing because layer 1 preempts layer 2 in the dispatcher.
 *
 * ## Inline-title border (item 2 restyle)
 * The input box wears the same inline-title border as {@link ./Pane.tsx Pane}, via the shared
 * {@link ./paneBorder.js PaneBorderTop} row + a content box with `borderTop={false}`. The send
 * TARGET lives on the top border as `╭─ → <label> ──╮` (the `›` prompt is dropped — the `→`
 * suffices), with a `★ ` prefix when the target is favorited. The content row below is a bare
 * cursor input (the {@link ./TextInput.js TextInput}); a long draft wraps and the box grows in
 * height (cursor-at-end rendering suffices since the buffer is end-append).
 *
 * ## Multiple-choice takeover
 * When the target's transcript ends in a LIVE (unanswered, trailing) `choice_prompt` — a CC
 * AskUserQuestion / trust dialog parsed by the service — the input box is TAKEN OVER by the choice
 * menu: the content area renders the question + options (cursor + checkboxes from parser ground
 * truth) instead of the text field, and the chat handler (App.tsx) forwards keys to the agent's
 * pane via `agent.send_key`. The pane is the source of truth; the parser's block-updated events
 * move the rendered cursor. The same selector drives both ({@link selectLiveChoicePrompt}).
 *
 * ## Queued-message line
 * When the service holds a queued-but-undelivered message for the target (accepted while the
 * harness was busy — `ConversationMeta.queuedMessage`), a one-line styled row renders ABOVE the
 * input border: `⏸ queued · <message>`, with an `⏎ interrupt & send now` hint when the input is
 * not taken over (Enter then fires `agent.interrupt`; the service delivers the queued message at
 * the next input-ready parse).
 */

import { Box, Text } from 'ink';
import { memo } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useBindings,
  useChatInputStore,
  useChatVimStore,
  useEffectiveFocus,
  useFocusRef,
  useFocusStore,
  useMeasureFocus,
} from '../hooks/useInputStores.js';
import { type BufferState, layout as bufferLayout } from '../input/chatBuffer.js';
import { SPAN_CLOSE, SPAN_OPEN } from '../input/chatInputStore.js';
import { CHAT_FOCUS } from '../input/focusStore.js';
import { isDefaultFavorited } from '../selectors/agentIdentity.js';
import {
  isFreeformChoiceLabel,
  isFreeformChoiceSelected,
  type LiveChoicePromptView,
  selectAdjacentTargets,
  selectConversationMeta,
  selectLiveChoicePrompt,
  useActiveAgent,
} from '../selectors/conversationsSelectors.js';
import { isFavorited } from '../selectors/favoritesSelectors.js';
import { useTheme } from '../theme/themeStore.js';
import { TRI_LEFT, TRI_RIGHT } from './glyphs.js';
import { PaneBorderBottom, PaneBorderTop } from './paneBorder.js';

/** Matches one marked image span (`U+E000 <id> U+E001`) for render-time substitution. */
const SPAN_RE = new RegExp(`${SPAN_OPEN}[^${SPAN_OPEN}${SPAN_CLOSE}]*${SPAN_CLOSE}`, 'g');

/** Render-time display of the buffer: replace each invisible marked span with its derived visible
 * `[Image N]` label, numbered by position. The buffer holds *ids*, never the visible number, so this
 * positional counting renumbers for free when a span is deleted (F9). Plain text passes through. */
export function displayBuffer(text: string): string {
  let n = 0;
  return text.replace(SPAN_RE, () => `[Image ${++n}]`);
}

/** Collapse a queued message to one renderable line (first line, whitespace-squashed). */
export function queuedPreview(message: string): string {
  return message.replace(/\s+/g, ' ').trim();
}

/**
 * Render the chat buffer as a column of soft-wrapped rows with a block cursor (chat-input overhaul).
 * The wrap is OWNED by {@link ../input/chatBuffer.js layout} (deterministic — render and visual-nav
 * cannot disagree; `measureElement` is unreliable for wrapped text), so we render `layout.rows`
 * AS-IS — including the synthetic trailing row `layout` may append so the block cursor is always
 * drawable — and draw the cursor at `cursorRow`/`cursorCol`.
 *
 * Image spans are already rendered as `[Image N]` inside `layout` (so we DON'T re-run the old
 * `displayBuffer` span substitution here; plain text is identical). When empty + unfocused, a dim
 * placeholder shows; empty + focused puts the block cursor on the placeholder's first glyph.
 *
 * `cursorOnGlyph` (vim normal mode) draws the block ON the character at the cursor (inverse video);
 * otherwise (insert mode + non-vim) the block sits AT the cursor position as a trailing `█`-style
 * inverse cell.
 */
function BufferDisplay({
  buffer,
  width,
  focused,
  placeholder,
  cursorOnGlyph,
}: {
  readonly buffer: BufferState;
  readonly width: number;
  readonly focused: boolean;
  readonly placeholder: string;
  readonly cursorOnGlyph: boolean;
}): React.JSX.Element {
  const theme = useTheme();
  if (buffer.text.length === 0) {
    // Empty buffer → phantom placeholder; focused puts the cursor on its first glyph.
    if (!focused || placeholder.length === 0) {
      return <Text dimColor>{placeholder.length === 0 ? ' ' : placeholder}</Text>;
    }
    return (
      <Text dimColor>
        <Text inverse>{placeholder.slice(0, 1)}</Text>
        {placeholder.slice(1)}
      </Text>
    );
  }
  const lay = bufferLayout(buffer, width);
  return (
    <Box flexDirection="column">
      {lay.rows.map((row, rowIndex) => {
        // Stable-ish key: the row's buffer offset + index (offsets repeat only on empty rows).
        const key = `${row.startBufferOffset}:${rowIndex}`;
        if (!focused || rowIndex !== lay.cursorRow) {
          return (
            <Box key={key} flexShrink={0}>
              <Text color={theme.text}>{row.text.length === 0 ? ' ' : row.text}</Text>
            </Box>
          );
        }
        // The cursor row: split before/at/after the cursor column so the block highlights one cell.
        const col = lay.cursorCol;
        const before = row.text.slice(0, col);
        const atChar = row.text.slice(col, col + 1);
        const after = row.text.slice(col + 1);
        return (
          <Box key={key} flexShrink={0}>
            <Text color={theme.text}>
              {before}
              {cursorOnGlyph ? (
                // Block ON the glyph (vim normal): inverse the char under the cursor (or a space at
                // end-of-row so the block is still visible).
                <Text inverse>{atChar.length === 0 ? ' ' : atChar}</Text>
              ) : (
                // Block AT the position (insert / non-vim): an inverse cell, then the rest verbatim.
                <Text inverse> </Text>
              )}
              {cursorOnGlyph ? after : atChar + after}
            </Text>
          </Box>
        );
      })}
    </Box>
  );
}

/** The live choice menu rendered inside the input box during the takeover. Pure view over the
 * selector's {@link LiveChoicePromptView}; key routing lives in the chat handler (App.tsx). */
function ChoiceMenu({
  prompt,
  compose,
}: {
  readonly prompt: LiveChoicePromptView;
  /** The local free-text draft, shown inline on the "Type something." row while that option is
   * selected (the handler buffers it here instead of round-tripping each key — see App.tsx). */
  readonly compose: string;
}): React.JSX.Element {
  const theme = useTheme();
  const composing = isFreeformChoiceSelected(prompt);
  const hint = prompt.multi
    ? '↑/↓ move · space toggle · enter select · esc cancel'
    : '↑/↓ move · 1-9 jump · enter select · esc cancel';
  // Multi-select dialogs have a dedicated unnumbered Submit row; CC renders it between the last
  // checkbox option and the checkbox-less trailing rows ("Chat about this"), so mirror that
  // position — the pane cursor travels through it in that order. `selected === null` is the
  // parser reporting the cursor is on it.
  const submitAfter = prompt.multi
    ? prompt.options.reduce((acc, o, i) => (o.checked !== null ? i : acc), -1)
    : -1;
  const submitCursor = prompt.multi && prompt.selected === null;
  const submitRow = prompt.multi ? (
    <Box key="submit" flexShrink={0}>
      <Text
        color={submitCursor ? theme.active : theme.text}
        bold={submitCursor}
        wrap="truncate-end"
      >
        {submitCursor ? '❯ ' : '  '}
        Submit
      </Text>
    </Box>
  ) : null;
  return (
    <Box flexDirection="column">
      <Box flexShrink={0}>
        {/* Wrap (not truncate) so a long, pane-wrapped CC question is fully visible — the parser now
            carries the whole multi-line question, and truncating here would re-hide it. */}
        <Text bold color={theme.text} wrap="wrap">
          {prompt.question}
        </Text>
      </Box>
      {prompt.options.flatMap((option, index) => {
        const isCursor = prompt.selected !== null && option.number === prompt.selected;
        const box = option.checked === null ? '' : option.checked ? '[✔] ' : '[ ] ';
        // The selected freeform row becomes an inline text field: show the local draft (or the dim
        // placeholder when empty) plus a caret, instead of the static "Type something." label.
        const isComposeRow = composing && isCursor && isFreeformChoiceLabel(option.label);
        const body = isComposeRow ? (
          <>
            {compose.length > 0 ? (
              compose
            ) : (
              <Text color={theme.muted} bold={false}>
                {option.label}
              </Text>
            )}
            <Text color={theme.active}>▏</Text>
          </>
        ) : (
          <>
            {option.label}
            {option.description !== null ? (
              <Text color={theme.muted} bold={false}>
                {'  '}
                {option.description}
              </Text>
            ) : null}
          </>
        );
        const row = (
          <Box key={option.number} flexShrink={0}>
            <Text
              color={isCursor ? theme.active : theme.text}
              bold={isCursor}
              wrap={isComposeRow ? 'wrap' : 'truncate-end'}
            >
              {isCursor ? '❯ ' : '  '}
              {option.number}. {box}
              {body}
            </Text>
          </Box>
        );
        return index === submitAfter && submitRow !== null ? [row, submitRow] : [row];
      })}
      {submitAfter === -1 && submitRow !== null ? submitRow : null}
      <Box flexShrink={0}>
        <Text color={theme.muted} wrap="truncate-end">
          {hint}
        </Text>
      </Box>
    </Box>
  );
}

export const ChatInput = memo(function ChatInput(): React.JSX.Element {
  const theme = useTheme();
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === CHAT_FOCUS;
  useMeasureFocus(CHAT_FOCUS, ref);
  // Rule 1: read the chat buffer (text + cursor). The dispatcher's chat handler owns mutation (rule 5).
  const text = useChatInputStore((s) => s.text);
  const buffer = useChatInputStore((s) => s.buffer);
  // The chat box content width in cells, from the measured rect (border 2 + paddingX 2 = 4). Falls
  // back to a stdout-columns width before first measure so wrapping is sane on the first frame.
  const chatRect = useFocusStore((s) => s.rects.get(CHAT_FOCUS));
  const contentWidth =
    chatRect !== undefined && chatRect.width > 4
      ? chatRect.width - 4
      : Math.max(1, (process.stdout.columns ?? 80) - 4);
  // Vim state for the border tag + cursor style. Only meaningful when vimMode is on.
  const vimMode = useAppStore((s) => s.settings.vimMode);
  const vimSubmode = useChatVimStore((s) => s.submode);
  // The active send target (rule 2: derived in the selector), so the box shows *who* a typed message
  // goes to — the same resolution the Enter handler uses, surfaced live.
  const conversations = useAppStore((s) => s.conversations, shallow);
  const roster = useAppStore((s) => s.roster, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const target = useActiveAgent(conversations, roster, favorites);
  const targetAgentId = target?.agentId ?? null;
  // Liveness + takeover state for the target — the same selectors the chat handler reads, so what
  // is rendered and how keys route can never disagree.
  const meta = selectConversationMeta(conversations, targetAgentId);
  const livePrompt = selectLiveChoicePrompt(conversations, targetAgentId);
  // The target moves onto the top border as `▸ <label>` (item 2; the `›` prompt is dropped — the
  // triangle suffices). A `★ ` precedes the name when the target is favorited (explicit star OR kind-default),
  // mirroring the Crows-pane glyph.
  const starred =
    target !== null && isFavorited(favorites, target.agentId, isDefaultFavorited(target));
  // The parser's live state rides the border title as a dim suffix: `· choice` while a dialog is
  // up (takeover), `· working` while busy, nothing when input-ready/unknown. When vim mode is on, a
  // mode tag (`· NORMAL` / `· INSERT`) is appended too (chat-input overhaul, user ask #3).
  const vimSuffix = vimMode ? (vimSubmode === 'normal' ? ' · NORMAL' : ' · INSERT') : '';
  const stateSuffix =
    (livePrompt !== null ? ' · choice' : meta.liveState === 'working' ? ' · working' : '') +
    vimSuffix;
  const targetLabel =
    target === null ? 'no target' : `${starred ? '★ ' : ''}${target.label}${stateSuffix}`;
  const borderColor = focused ? theme.active : theme.inactive;
  const queued = meta.queuedMessage;
  // The crows a step in each direction reaches (cycleTargetPrev `◂` / cycleTargetNext `▸`), shown on
  // the bottom border so the user sees who ctrl+h / ctrl+l would target — WITHOUT opening any pane.
  // The chord labels come live from the bindings table so they track the user's alt/ctrl choice.
  const bindings = useBindings();
  const { prev, next } = selectAdjacentTargets(conversations, roster, favorites);
  const prevChord = bindings.label('global.cycleTargetPrev');
  const nextChord = bindings.label('global.cycleTargetNext');
  const footerLeft =
    prev !== null ? <Text dimColor>{`${prevChord} ${TRI_LEFT} ${prev.label}`}</Text> : undefined;
  const footerRight =
    next !== null ? <Text dimColor>{`${next.label} ${TRI_RIGHT} ${nextChord}`}</Text> : undefined;
  return (
    // Inline-title border (Pane recipe): the `▸ <target>` sits on the top border line (item 2); the
    // content box below is a bare cursor-input line that grows in height as a long draft wraps —
    // or, during the multiple-choice takeover, the live choice menu.
    <Box ref={ref} flexDirection="column">
      {queued !== null ? (
        <Box flexShrink={0} paddingX={1}>
          <Text color={theme.warning} wrap="truncate-end">
            ⏸ queued · {queuedPreview(queued)}
            {livePrompt === null ? (
              <Text color={theme.muted}> · ⏎ interrupt & send now</Text>
            ) : null}
          </Text>
        </Box>
      ) : null}
      <PaneBorderTop
        title={`${TRI_RIGHT} ${targetLabel}`}
        borderColor={borderColor}
        titleColor={focused ? theme.active : theme.inactive}
        bold={focused}
      />
      <Box borderStyle="round" borderTop={false} borderColor={borderColor} paddingX={1}>
        {livePrompt !== null ? (
          <ChoiceMenu prompt={livePrompt} compose={text} />
        ) : (
          <BufferDisplay
            buffer={buffer}
            width={contentWidth}
            focused={focused}
            placeholder="type a message"
            cursorOnGlyph={vimMode && vimSubmode === 'normal'}
          />
        )}
      </Box>
      {/* Bottom border overlay: `╰─ ⌃h ◂ prev ──…── next ▸ ⌃l ─╯` riding Ink's own bottom border
          above (net-zero height). The prev/next labels appear only with ≥2 crows to cycle through. */}
      <PaneBorderBottom borderColor={borderColor} leftExtra={footerLeft} rightExtra={footerRight} />
    </Box>
  );
});
