import { describe, expect, it } from 'vitest';
import { decodeEditorKey } from '../../src/input/textEditor/keyDecoder.js';

const single = { newline: 'never', homeEnd: 'logical-line', ctrlLineMotion: true } as const;

describe('editor key decoder', () => {
  it('leaves structural keys to the owning mode', () => {
    expect(decodeEditorKey('', { return: true }, single)).toBeNull();
    expect(decodeEditorKey('', { tab: true }, single)).toBeNull();
    expect(decodeEditorKey('', { escape: true }, single)).toBeNull();
  });

  it('maps printable and navigation input without a second dispatcher', () => {
    expect(decodeEditorKey('ab', {}, single)).toEqual({ type: 'insert', text: 'ab' });
    expect(decodeEditorKey('', { delete: true }, single)).toEqual({ type: 'deleteForward' });
    expect(decodeEditorKey('a', { ctrl: true }, single)).toEqual({ type: 'moveLineStart' });
  });
});
