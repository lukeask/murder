import stringWidth from 'string-width';
import type { DisplayAtom, DisplayProjection } from '../textEditor/projection.js';
import { plainTextProjection } from '../textEditor/projection.js';
import { scanChatSpans } from './chatSpans.js';

/** Replace each marked source span with a cursor-atomic `[Image N]` display atom. */
export const chatProjection: DisplayProjection = (text) => {
  const atoms: DisplayAtom[] = [];
  let offset = 0;
  for (const [index, span] of scanChatSpans(text).entries()) {
    for (const atom of plainTextProjection(text.slice(offset, span.start))) {
      atoms.push({
        ...atom,
        sourceStart: atom.sourceStart + offset,
        sourceEnd: atom.sourceEnd + offset,
      });
    }
    const label = `[Image ${index + 1}]`;
    atoms.push({
      sourceStart: span.start,
      sourceEnd: span.end,
      text: label,
      columns: stringWidth(label),
      atomic: true,
    });
    offset = span.end;
  }
  for (const atom of plainTextProjection(text.slice(offset))) {
    atoms.push({
      ...atom,
      sourceStart: atom.sourceStart + offset,
      sourceEnd: atom.sourceEnd + offset,
    });
  }
  return atoms;
};
