/**
 * blocks.ts tests (TUIchat-2) — the shared presentation-time block classifier.
 *
 * Mirrors the Phase-1 Python classifier's conceptual fixtures (`tests/unit/test_reflow_classifier.py`)
 * so the two stay consistent: fenced code spans verbatim, list leads → list, columnar/indented/box →
 * pre, everything else prose. The bias is toward PRESERVE (label pre) over wrap.
 */

import { describe, expect, it } from 'vitest';
import { type Block, classifyBlocks } from '../../src/transcript/blocks.js';

/** Convenience: assert the (kind, joined-lines) shape of the produced blocks. */
function shape(text: string): { kind: Block['kind']; text: string; lang?: string }[] {
  return classifyBlocks(text).map((b) =>
    b.lang === undefined
      ? { kind: b.kind, text: b.lines.join('\n') }
      : { kind: b.kind, text: b.lines.join('\n'), lang: b.lang },
  );
}

describe('classifyBlocks — empty / whitespace', () => {
  it('returns [] for an empty string', () => {
    expect(classifyBlocks('')).toEqual([]);
  });

  it('returns [] for whitespace-only / blank lines', () => {
    expect(classifyBlocks('\n\n   \n\n')).toEqual([]);
  });
});

describe('classifyBlocks — prose', () => {
  it('labels a single running paragraph as prose', () => {
    expect(shape('This is a normal sentence that the model wrote.')).toEqual([
      { kind: 'prose', text: 'This is a normal sentence that the model wrote.' },
    ]);
  });

  it('splits two blank-separated paragraphs into two prose blocks (one blank collapsed away)', () => {
    expect(shape('First paragraph.\n\nSecond paragraph.')).toEqual([
      { kind: 'prose', text: 'First paragraph.' },
      { kind: 'prose', text: 'Second paragraph.' },
    ]);
  });

  it('keeps a multi-line prose paragraph as one block (faithful newlines preserved)', () => {
    expect(shape('line one of prose\nline two of prose')).toEqual([
      { kind: 'prose', text: 'line one of prose\nline two of prose' },
    ]);
  });
});

describe('classifyBlocks — code (fenced)', () => {
  it('captures a fenced span verbatim and strips the fences', () => {
    const text = '```\nconst x = 1;\nconst y = 2;\n```';
    expect(shape(text)).toEqual([{ kind: 'code', text: 'const x = 1;\nconst y = 2;' }]);
  });

  it('surfaces the fence language hint', () => {
    const text = '```ts\nconst x = 1;\n```';
    expect(shape(text)).toEqual([{ kind: 'code', text: 'const x = 1;', lang: 'ts' }]);
  });

  it('keeps inner blank lines inside the code island', () => {
    const text = '```\na\n\nb\n```';
    expect(shape(text)).toEqual([{ kind: 'code', text: 'a\n\nb' }]);
  });

  it('renders an unterminated fence (no closing ```) as a code island through EOF', () => {
    const text = '```py\nprint(1)\nprint(2)';
    expect(shape(text)).toEqual([{ kind: 'code', text: 'print(1)\nprint(2)', lang: 'py' }]);
  });

  it('separates prose, code, prose into three blocks', () => {
    const text = 'before\n\n```\ncode()\n```\n\nafter';
    expect(shape(text)).toEqual([
      { kind: 'prose', text: 'before' },
      { kind: 'code', text: 'code()' },
      { kind: 'prose', text: 'after' },
    ]);
  });

  it('preserves leading indentation inside a code island (no strip)', () => {
    const text = '```\n  indented\n    more\n```';
    expect(shape(text)).toEqual([{ kind: 'code', text: '  indented\n    more' }]);
  });
});

describe('classifyBlocks — list', () => {
  it('labels dash bullets as a list', () => {
    expect(shape('- one\n- two\n- three')[0]?.kind).toBe('list');
  });

  it('labels star bullets as a list', () => {
    expect(shape('* alpha\n* beta')[0]?.kind).toBe('list');
  });

  it('labels numbered items (N. and N)) as a list', () => {
    expect(shape('1. first\n2. second')[0]?.kind).toBe('list');
    expect(shape('1) first\n2) second')[0]?.kind).toBe('list');
  });
});

describe('classifyBlocks — pre (preserve bias)', () => {
  it('labels a columnar (2+-space gap) block as pre', () => {
    // A two-column model list: name then a gap then a description — column alignment.
    const text = 'gpt-5.5      planning, opus-quality\ngpt-5.3      impl default';
    expect(shape(text)[0]?.kind).toBe('pre');
  });

  it('labels a box-drawing table as pre', () => {
    const text = '┌──────┬──────┐\n│ a    │ b    │\n└──────┴──────┘';
    expect(shape(text)[0]?.kind).toBe('pre');
  });

  it('labels a uniformly-indented block as pre', () => {
    const text = '  indented line one\n  indented line two';
    expect(shape(text)[0]?.kind).toBe('pre');
  });

  it('preserves internal column spacing verbatim in a pre block', () => {
    const text = 'col1      col2\nval       other';
    const block = classifyBlocks(text)[0];
    expect(block?.kind).toBe('pre');
    expect(block?.lines).toEqual(['col1      col2', 'val       other']);
  });
});

describe('classifyBlocks — mixed turn', () => {
  it('classifies a realistic mixed turn (prose + code + list) block by block', () => {
    const text = [
      "Here's the fix:",
      '',
      '```ts',
      'export const x = 1;',
      '```',
      '',
      'Steps:',
      '',
      '- apply it',
      '- run tests',
    ].join('\n');
    expect(shape(text)).toEqual([
      { kind: 'prose', text: "Here's the fix:" },
      { kind: 'code', text: 'export const x = 1;', lang: 'ts' },
      { kind: 'prose', text: 'Steps:' },
      { kind: 'list', text: '- apply it\n- run tests' },
    ]);
  });
});
