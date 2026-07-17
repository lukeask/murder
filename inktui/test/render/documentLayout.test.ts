import { describe, expect, it } from 'vitest';
import {
  computeDocumentWindow,
  computeScrollThumb,
} from '../../src/components/panes/shared/scrollWindow.js';
import {
  documentRowText,
  documentRowWidth,
  layoutDocument,
  rowForSourceLine,
} from '../../src/render/documentLayout.js';

function texts(source: string, mode: 'plain' | 'markdown', width: number): readonly string[] {
  return layoutDocument(source, mode, width).rows.map(documentRowText);
}

describe('document layout', () => {
  it('wraps a Markdown paragraph into physical rows and responds predictably to resize', () => {
    const source = 'one two three four five six';
    expect(texts(source, 'markdown', 10)).toEqual(['one two', 'three four', 'five six']);
    expect(texts(source, 'markdown', 20)).toEqual(['one two three four', 'five six']);
  });

  it('uses rendered physical rows for viewport and scrollbar geometry', () => {
    const layout = layoutDocument('one two three four five six', 'markdown', 10);
    expect(layout.rows).toHaveLength(3);
    expect(computeDocumentWindow(layout.rows.length, 99, 2)).toEqual({
      start: 1,
      end: 3,
      maxScroll: 1,
    });
    expect(computeScrollThumb(layout.rows.length, 1, 2)).toEqual({ size: 1, offset: 1 });
  });

  it.each([
    ['notes.txt', 'markdown', '# Rendered', '# Rendered'],
    ['notes.md', 'plain', '# Literal', '# Literal'],
    ['README', 'markdown', '**bold**', 'bold'],
    ['LICENSE', 'plain', '**literal**', '**literal**'],
  ] as const)('%s obeys explicitly selected %s mode', (_filename, mode, source, expected) => {
    const rows = layoutDocument(source, mode, 40).rows;
    expect(rows.map(documentRowText).join('\n')).toContain(expected);
    if (mode === 'markdown') {
      expect(rows.some((row) => row.runs.some((run) => run.style.bold === true))).toBe(true);
    }
  });

  it('keeps nested list and blockquote indentation inside the allocated width', () => {
    const source = [
      '- outer item with enough words to wrap',
      '  - nested item with enough words to wrap again',
      '',
      '> quoted words that also need wrapping',
      '>',
      '> - quoted list item',
    ].join('\n');
    const layout = layoutDocument(source, 'markdown', 18);
    const rendered = layout.rows.map(documentRowText);
    expect(rendered.some((line) => line.startsWith('• outer'))).toBe(true);
    expect(rendered.some((line) => line.startsWith('  • nested'))).toBe(true);
    expect(rendered.some((line) => line.startsWith('│ '))).toBe(true);
    expect(layout.rows.every((line) => documentRowWidth(line) <= 18)).toBe(true);
  });

  it('preserves code whitespace and hard-wraps code at the content width', () => {
    const source = ['```txt', '  indented', 'abcdefghijk', '```'].join('\n');
    const rendered = texts(source, 'markdown', 8);
    expect(rendered).toContain('│   inde');
    expect(rendered).toContain('│ nted');
    expect(rendered).toContain('│ abcdef');
    expect(rendered).toContain('│ ghijk');
    expect(rendered.every((line) => line.length <= 8)).toBe(true);
  });

  it('covers headings, soft/explicit breaks, ordered lists, rules, links, and indented code', () => {
    const source = [
      'Setext heading',
      '==============',
      '',
      'soft line',
      'continues here  ',
      'explicit next',
      '',
      '3. ordered item',
      '',
      '---',
      '',
      '[label](https://example.com)',
      '',
      '    keep  spacing',
    ].join('\n');
    const rendered = texts(source, 'markdown', 40);
    expect(rendered).toContain('# Setext heading');
    expect(rendered).toContain('soft line continues here');
    expect(rendered).toContain('explicit next');
    expect(rendered).toContain('3. ordered item');
    expect(rendered).toContain('─'.repeat(40));
    expect(rendered).toContain('label (https://example.com)');
    expect(rendered).toContain('│ keep  spacing');
  });

  it('renders GFM task lists, strikethrough, autolinks, and tables without overflow', () => {
    const source = [
      '- [x] done and ~~obsolete~~',
      '',
      'Visit https://example.com.',
      '',
      '| Name | Value |',
      '| --- | --- |',
      '| alpha | a very long value |',
    ].join('\n');
    for (const width of [40, 12, 3, 1]) {
      const layout = layoutDocument(source, 'markdown', width);
      expect(layout.rows.length).toBeGreaterThan(0);
      expect(layout.rows.every((line) => documentRowWidth(line) <= width)).toBe(true);
    }
    const wide = layoutDocument(source, 'markdown', 40);
    expect(wide.rows.some((line) => documentRowText(line).includes('[x]'))).toBe(true);
    expect(
      wide.rows.some((line) => line.runs.some((run) => run.style.strikethrough === true)),
    ).toBe(true);
    expect(wide.rows.some((line) => documentRowText(line).includes('https://example.com'))).toBe(
      true,
    );
  });

  it('handles empty documents and extremely narrow panes', () => {
    expect(layoutDocument('', 'markdown', 1).rows).toEqual([]);
    expect(layoutDocument('', 'plain', 1).rows).toEqual([]);
    const narrow = layoutDocument('> - **content**', 'markdown', 1);
    expect(narrow.rows.length).toBeGreaterThan(0);
    expect(narrow.rows.every((line) => documentRowWidth(line) <= 1)).toBe(true);
  });

  it('maps source lines to the first corresponding rendered row', () => {
    const source = ['# Heading', '', 'one two three four five six', '', 'tail'].join('\n');
    const layout = layoutDocument(source, 'markdown', 10);
    const paragraphRow = rowForSourceLine(layout, 3);
    const tailRow = rowForSourceLine(layout, 5);
    const rendered = layout.rows.map(documentRowText);
    expect(rendered[paragraphRow]).toBe('one two');
    expect(rendered[tailRow]).toBe('tail');
    expect(tailRow).toBeGreaterThan(paragraphRow);
  });
});
