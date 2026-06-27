import { describe, expect, it } from 'vitest';
import {
  DEFAULT_TAB_LEN,
  formatDocTreeName,
  MIN_TAB_LEN,
  NARROW_INNER_WIDTH,
  parseTreeName,
  tabLenForWidth,
  WIDE_INNER_WIDTH,
  WIDE_TAB_LEN,
} from '../../src/components/panes/docTreeIndent.js';

describe('docTreeIndent', () => {
  const childName = '    Child plan: collect transcript boundaries';

  it('parseTreeName reads depth from 4-space levels', () => {
    expect(parseTreeName(childName)).toEqual({
      depth: 1,
      title: 'Child plan: collect transcript boundaries',
    });
    expect(parseTreeName('Root plan')).toEqual({ depth: 0, title: 'Root plan' });
  });

  it('tabLenForWidth uses discrete 2 / 1 / 1 tiers', () => {
    expect(tabLenForWidth(WIDE_INNER_WIDTH)).toBe(WIDE_TAB_LEN);
    expect(tabLenForWidth(WIDE_INNER_WIDTH + 5)).toBe(WIDE_TAB_LEN);
    expect(tabLenForWidth(WIDE_INNER_WIDTH - 1)).toBe(DEFAULT_TAB_LEN);
    expect(tabLenForWidth(NARROW_INNER_WIDTH + 1)).toBe(DEFAULT_TAB_LEN);
    expect(tabLenForWidth(NARROW_INNER_WIDTH)).toBe(MIN_TAB_LEN);
    expect(tabLenForWidth(8)).toBe(MIN_TAB_LEN);
  });

  it('keeps wide-tier indent at comfortable width', () => {
    const out = formatDocTreeName(childName, WIDE_INNER_WIDTH, { maxLen: 27 });
    expect(out.startsWith('  Child plan')).toBe(true);
  });

  it('compresses indent at narrow width so more title shows', () => {
    const budget = 12;
    const wide = formatDocTreeName(childName, WIDE_INNER_WIDTH, { maxLen: budget });
    const narrow = formatDocTreeName(childName, NARROW_INNER_WIDTH - 1, { maxLen: budget });
    expect(wide.startsWith('  ')).toBe(true);
    expect(narrow.startsWith(' ')).toBe(true);
    expect(narrow).toContain('Child');
    expect(narrow.trimStart().length).toBeGreaterThan(wide.trimStart().length);
  });
});
