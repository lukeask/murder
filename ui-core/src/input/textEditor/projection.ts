import stringWidth from 'string-width';
import { graphemeBoundaries } from './graphemes.js';

export interface DisplayAtom {
  readonly sourceStart: number;
  readonly sourceEnd: number;
  readonly text: string;
  readonly columns: number;
  readonly atomic: boolean;
}

export type DisplayProjection = (text: string) => readonly DisplayAtom[];

/** Tabs entered through editor policies are normalized away; legacy tabs occupy four cells. */
export const TAB_COLUMNS = 4;

export const plainTextProjection: DisplayProjection = (text) => {
  const boundaries = graphemeBoundaries(text);
  const atoms: DisplayAtom[] = [];
  for (let i = 0; i < boundaries.length - 1; i++) {
    const sourceStart = boundaries[i] ?? 0;
    const sourceEnd = boundaries[i + 1] ?? text.length;
    const atom = text.slice(sourceStart, sourceEnd);
    atoms.push({
      sourceStart,
      sourceEnd,
      text: atom,
      columns: atom === '\n' ? 0 : atom === '\t' ? TAB_COLUMNS : stringWidth(atom),
      atomic: false,
    });
  }
  return atoms;
};
