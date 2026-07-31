/**
 * `expandTemplates` / inline-prompt helpers — pure `:name:` macro / leading-fill expansion.
 * Covers both forms, the leading-vs-inline precedence rule, builtin shadowing, literal fallthrough,
 * and the workflow-safe {@link expandInlinePromptTemplates} ExpansionResult surface.
 */

import { describe, expect, it } from 'vitest';
import {
  expandInlinePromptTemplates,
  expandTemplates,
  fillPlaceholders,
  parseLeadingColonName,
  parseLeadingTemplateInvocation,
} from '../../src/input/expandTemplates.js';

const registry = new Map<string, string>([
  ['greet', 'hello {who}'],
  ['pair', '{a} and {b}'],
  ['plain', 'just text'],
  ['sig', '— sent from murder'],
  ['Greeting Card', 'dear {who}'],
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

  it('replaces a quoted macro for a human-readable prompt-template name', () => {
    expect(run('hi :"Greeting Card": bye')).toBe('hi dear {who} bye');
  });

  it('leaves an unclosed quoted macro literal', () => {
    expect(run('hi :"Greeting Card: bye')).toBe('hi :"Greeting Card: bye');
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

  it('fills a quoted leading prompt-template name', () => {
    expect(run(':"Greeting Card" Ada')).toBe('dear Ada');
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

describe('expandInlinePromptTemplates', () => {
  it('expands a single known :name:', () => {
    expect(expandInlinePromptTemplates('hi :sig: bye', registry)).toEqual({
      text: 'hi — sent from murder bye',
      missing: [],
      expanded: ['sig'],
    });
  });

  it('leaves unknown :name: verbatim and records it in missing', () => {
    expect(expandInlinePromptTemplates('ratio :nope: here', registry)).toEqual({
      text: 'ratio :nope: here',
      missing: ['nope'],
      expanded: [],
    });
  });

  it('expands multiple known macros and records each expanded name once', () => {
    expect(expandInlinePromptTemplates(':plain: then :sig:', registry)).toEqual({
      text: 'just text then — sent from murder',
      missing: [],
      expanded: ['plain', 'sig'],
    });
  });

  it('dedupes repeated expanded and missing names (first-seen order)', () => {
    expect(expandInlinePromptTemplates(':sig: :nope: :sig: :nope:', registry)).toEqual({
      text: '— sent from murder :nope: — sent from murder :nope:',
      missing: ['nope'],
      expanded: ['sig'],
    });
  });

  it('mixes hits and misses in one pass', () => {
    expect(expandInlinePromptTemplates('a :plain: b :missing: c :sig:', registry)).toEqual({
      text: 'a just text b :missing: c — sent from murder',
      missing: ['missing'],
      expanded: ['plain', 'sig'],
    });
  });

  it('does not re-scan expanded bodies (single pass, no recursion)', () => {
    const templates = new Map<string, string>([['wrap', 'before :sig: after']]);
    expect(expandInlinePromptTemplates('go :wrap: end', templates)).toEqual({
      text: 'go before :sig: after end',
      missing: [],
      expanded: ['wrap'],
    });
  });

  it('does not interpret leading :name args syntax', () => {
    // Leading form is chat/workflow-fire territory — inline helper only touches `:name:`.
    expect(expandInlinePromptTemplates(':greet world', registry)).toEqual({
      text: ':greet world',
      missing: [],
      expanded: [],
    });
  });

  it('returns empty missing/expanded when there are no macros', () => {
    expect(expandInlinePromptTemplates('just a plain message', registry)).toEqual({
      text: 'just a plain message',
      missing: [],
      expanded: [],
    });
  });

  it('treats empty template map as all-missing', () => {
    expect(expandInlinePromptTemplates('x :foo: y :bar:', new Map())).toEqual({
      text: 'x :foo: y :bar:',
      missing: ['foo', 'bar'],
      expanded: [],
    });
  });

  it('requires closing colon — :name alone is not an inline macro', () => {
    expect(expandInlinePromptTemplates('say :greet please', registry)).toEqual({
      text: 'say :greet please',
      missing: [],
      expanded: [],
    });
  });

  it('allows names with digits, underscore, and hyphen', () => {
    const templates = new Map<string, string>([
      ['a1', 'A'],
      ['under_score', 'U'],
      ['kebab-case', 'K'],
    ]);
    expect(expandInlinePromptTemplates(':a1: :under_score: :kebab-case:', templates)).toEqual({
      text: 'A U K',
      missing: [],
      expanded: ['a1', 'under_score', 'kebab-case'],
    });
  });

  it('expands quoted human-readable names and reports quoted misses', () => {
    const templates = new Map<string, string>([['Meeting Notes', 'NOTES']]);
    expect(
      expandInlinePromptTemplates('x :"Meeting Notes": y :"Missing Notes":', templates),
    ).toEqual({
      text: 'x NOTES y :"Missing Notes":',
      missing: ['Missing Notes'],
      expanded: ['Meeting Notes'],
    });
  });

  it('does not treat times, versions, or pure-digit :N: as template refs', () => {
    for (const text of [
      'meet at 12:30: then go',
      'versions 1:2:3 matter',
      'see :100: later',
      'deadline 09:05:00Z',
    ]) {
      expect(expandInlinePromptTemplates(text, new Map())).toEqual({
        text,
        missing: [],
        expanded: [],
      });
    }
  });

  it('still expands identifier-like :my-template: / :review-context:', () => {
    const templates = new Map<string, string>([
      ['my-template', 'BODY'],
      ['review-context', 'CTX'],
    ]);
    expect(expandInlinePromptTemplates('x :my-template: y :review-context:', templates)).toEqual({
      text: 'x BODY y CTX',
      missing: [],
      expanded: ['my-template', 'review-context'],
    });
  });
});

describe('parseLeadingColonName', () => {
  it('parses :name at start with remainder', () => {
    expect(parseLeadingColonName(':greet world')).toEqual({
      name: 'greet',
      remainder: ' world',
    });
  });

  it('parses :name at EOS with empty remainder', () => {
    expect(parseLeadingColonName(':greet')).toEqual({ name: 'greet', remainder: '' });
  });

  it('parses a quoted leading name with its remainder', () => {
    expect(parseLeadingColonName(':"Greeting Card" Ada')).toEqual({
      name: 'Greeting Card',
      remainder: ' Ada',
    });
  });

  it('returns null for inline :name: (trailing colon blocks leading match)', () => {
    expect(parseLeadingColonName(':greet:')).toBeNull();
  });

  it('returns null when :name is not at index 0', () => {
    expect(parseLeadingColonName('say :greet world')).toBeNull();
  });
});

describe('parseLeadingTemplateInvocation', () => {
  it('fills placeholders and returns name/args/text', () => {
    expect(parseLeadingTemplateInvocation(':pair foo bar', registry)).toEqual({
      name: 'pair',
      args: ['foo', 'bar'],
      text: 'foo and bar',
    });
  });

  it('returns null for unknown template name (no builtin check here)', () => {
    expect(parseLeadingTemplateInvocation(':bogus arg', registry)).toBeNull();
  });

  it('returns a hit even when name is also a builtin (caller applies shadowing)', () => {
    const templates = new Map<string, string>([['help', 'docs for {topic}']]);
    expect(parseLeadingTemplateInvocation(':help usage', templates)).toEqual({
      name: 'help',
      args: ['usage'],
      text: 'docs for usage',
    });
  });

  it('returns null for inline :name: form', () => {
    expect(parseLeadingTemplateInvocation(':plain:', registry)).toBeNull();
  });

  it('does not re-inline-scan the filled body', () => {
    const templates = new Map<string, string>([['wrap', 'before :sig: after']]);
    expect(parseLeadingTemplateInvocation(':wrap', templates)).toEqual({
      name: 'wrap',
      args: [],
      text: 'before :sig: after',
    });
  });
});

describe('fillPlaceholders', () => {
  it('fills by first-appearance order and leaves unfilled tokens', () => {
    expect(fillPlaceholders('{a} and {b} and {a}', ['x'])).toBe('x and {b} and x');
  });

  it('ignores extra args', () => {
    expect(fillPlaceholders('hello {who}', ['world', 'extra'])).toBe('hello world');
  });
});
