/**
 * `expandTemplates` tests — the pure `:name:` macro / leading-fill expansion pass. Covers both forms,
 * the leading-vs-inline precedence rule, builtin shadowing, and literal fallthrough on misses.
 */

import { describe, expect, it } from 'vitest';
import { expandTemplates } from '../../src/input/expandTemplates.js';

const registry = new Map<string, string>([
  ['greet', 'hello {who}'],
  ['pair', '{a} and {b}'],
  ['plain', 'just text'],
  ['sig', '— sent from murder'],
]);
const builtins = new Set<string>(['help', 'save', 'note']);

const run = (msg: string): string => expandTemplates(msg, registry, builtins);

describe('expandTemplates — inline form', () => {
  it('replaces an inline :name: hit with its body', () => {
    expect(run('hi :sig: bye')).toBe('hi — sent from murder bye');
  });

  it('leaves an inline miss literal (fallthrough)', () => {
    expect(run('ratio :nope: here')).toBe('ratio :nope: here');
  });

  it('replaces multiple inline hits', () => {
    expect(run(':plain: then :sig:')).toBe('just text then — sent from murder');
  });
});

describe('expandTemplates — leading parameterized form', () => {
  it('fills a single placeholder positionally', () => {
    expect(run(':greet world')).toBe('hello world');
  });

  it('fills multiple placeholders by first-appearance order', () => {
    expect(run(':pair foo bar')).toBe('foo and bar');
  });

  it('leaves an unfilled placeholder verbatim', () => {
    expect(run(':greet')).toBe('hello {who}');
    expect(run(':pair foo')).toBe('foo and {b}');
  });

  it('ignores extra args beyond the placeholder count', () => {
    expect(run(':greet world extra ignored')).toBe('hello world');
  });

  it('leaves a leading builtin name untouched (builtin wins)', () => {
    expect(run(':help')).toBe(':help');
    expect(run(':save foo some body')).toBe(':save foo some body');
  });

  it('leaves an unknown leading name untouched (literal fallthrough)', () => {
    expect(run(':bogus arg')).toBe(':bogus arg');
  });
});

describe('expandTemplates — precedence & no-ops', () => {
  it('does NOT re-inline-scan an expanded leading body (single pass)', () => {
    // A body that itself contains a `:sig:` macro is returned verbatim — no recursion.
    const reg = new Map<string, string>([['wrap', 'before :sig: after']]);
    expect(expandTemplates(':wrap', reg, builtins)).toBe('before :sig: after');
  });

  it('returns a message with no colons unchanged', () => {
    expect(run('just a plain message')).toBe('just a plain message');
  });

  it('treats mid-string :name as not-leading (inline scan applies)', () => {
    // `:greet` is not at index 0, so leading-fill does not fire; no `:greet:` inline form either.
    expect(run('say :greet world')).toBe('say :greet world');
  });
});
