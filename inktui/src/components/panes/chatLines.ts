import type { ChatTurn, TurnSpeaker } from '../../selectors/conversationsSelectors.js';
import { type BlockKind, classifyBlocks } from '../../transcript/blocks.js';
import { wrapTextToRows } from '../../utils/wrapText.js';

export interface ChatLine {
  readonly speaker: TurnSpeaker;
  readonly tone?: ChatTurn['tone'];
  readonly kind: BlockKind | 'blank';
  readonly text: string;
  readonly firstOfTurn: boolean;
  readonly gutter?: 'none';
}

export function formatTurnLines(turn: ChatTurn): readonly ChatLine[] {
  const out: ChatLine[] = [];
  if (turn.text.trim() === '') {
    return out;
  }

  const lines = turn.text.split('\n');
  const first = lines.findIndex((line) => line.trim() !== '');
  const last = lines.findLastIndex((line) => line.trim() !== '');
  if (first < 0 || last < first) {
    return out;
  }

  const tone = turn.tone === undefined ? {} : { tone: turn.tone };
  let emittedContentLine = false;
  const pushLine = (kind: BlockKind | 'blank', text: string): void => {
    out.push({
      speaker: turn.speaker,
      ...tone,
      kind,
      text,
      firstOfTurn: kind !== 'blank' && !emittedContentLine,
    });
    if (kind !== 'blank') {
      emittedContentLine = true;
    }
  };

  const plainRun: string[] = [];
  const flushPlain = (): void => {
    if (plainRun.length === 0) return;
    for (const block of classifyBlocks(plainRun.join('\n'))) {
      for (const text of block.lines) {
        pushLine(block.kind, text);
      }
    }
    plainRun.length = 0;
  };

  for (let i = first; i <= last; i++) {
    const line = lines[i] ?? '';
    if (/^\s*```/.test(line)) {
      flushPlain();
      i += 1;
      while (i <= last) {
        const inner = lines[i] ?? '';
        if (/^\s*```/.test(inner)) {
          break;
        }
        pushLine('code', inner);
        i += 1;
      }
      continue;
    }
    if (line.trim() === '') {
      flushPlain();
      pushLine('blank', '');
      continue;
    }
    plainRun.push(line);
  }
  flushPlain();
  return out;
}

/** Word-wrap flattened lines so each {@link ChatLine} is one terminal row. */
export function wrapChatLines(lines: readonly ChatLine[], textWidth: number): readonly ChatLine[] {
  if (textWidth < 1) {
    return lines;
  }
  const out: ChatLine[] = [];
  for (const line of lines) {
    if (line.kind === 'blank' || line.text === '') {
      out.push(line);
      continue;
    }
    const verbatim = line.kind === 'code' || line.kind === 'pre';
    const rows = wrapTextToRows(line.text, textWidth, {
      hard: verbatim,
      wordWrap: !verbatim,
    });
    if (rows.length <= 1) {
      out.push({ ...line, text: rows[0] ?? '' });
      continue;
    }
    for (let i = 0; i < rows.length; i++) {
      out.push({
        ...line,
        text: rows[i] ?? '',
        firstOfTurn: i === 0 ? line.firstOfTurn : false,
      });
    }
  }
  return out;
}

export function flattenTurns(turns: readonly ChatTurn[]): readonly ChatLine[] {
  const lines: ChatLine[] = [];
  let previousRenderedTurn: ChatTurn | null = null;
  for (const turn of turns) {
    const turnLines = formatTurnLines(turn);
    if (turnLines.length === 0) continue;
    const continuesVisualRun =
      previousRenderedTurn?.speaker === turn.speaker && previousRenderedTurn.tone === turn.tone;
    if (lines.length > 0) {
      lines.push({
        speaker: turn.speaker,
        ...(turn.tone === undefined ? {} : { tone: turn.tone }),
        kind: 'blank',
        text: '',
        firstOfTurn: false,
        ...(continuesVisualRun ? {} : { gutter: 'none' as const }),
      });
    }
    for (const line of turnLines) {
      lines.push(
        continuesVisualRun && line.kind !== 'blank' ? { ...line, firstOfTurn: false } : line,
      );
    }
    previousRenderedTurn = turn;
  }
  return lines;
}
