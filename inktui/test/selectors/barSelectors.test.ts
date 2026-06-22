/**
 * Bar view-model tests — the pure transforms the top/bottom bars render from (rule 2). Asserting the
 * selectors directly keeps the bar formatting (subscript labels, hint sourcing) tested without Ink.
 */

import { describe, expect, it } from 'vitest';
import { DEFAULT_BINDINGS, resolveBindings } from '../../src/input/bindings.js';
import { CHAT_FOCUS } from '../../src/input/focusStore.js';
import type { Keymap } from '../../src/input/keymap.js';
import type { PanelId } from '../../src/input/panels.js';
import { selectBottomBar, selectTopBar } from '../../src/selectors/barSelectors.js';

describe('selectTopBar', () => {
  it('labels every panel with its subscript digit, in screen order', () => {
    const labels = selectTopBar(new Set<PanelId>());
    expect(labels.map((l) => l.text)).toEqual([
      'plans₁',
      'notes₂',
      'reports₃',
      'tickets₄',
      'history₅',
      'tree₈',
      'usage₉',
      'crows₀',
    ]);
  });

  it('marks the first right-rail panel with a divider so the bar groups left/right rails', () => {
    const labels = selectTopBar(new Set<PanelId>());
    // Left-rail panels carry no divider; the first right-rail panel (tree) opens the group.
    const divided = labels.filter((l) => l.dividerBefore).map((l) => l.id);
    expect(divided).toEqual(['transit']);
  });

  it('marks only the visible panels active', () => {
    const labels = selectTopBar(new Set<PanelId>(['plans', 'crows']));
    const active = labels.filter((l) => l.active).map((l) => l.id);
    expect(active).toEqual(['plans', 'crows']);
  });
});

describe('selectBottomBar', () => {
  const keymap: Keymap<'open' | 'star'> = [
    { chord: { input: 'o' }, intent: 'open', description: 'open doc' },
    { chord: { key: { return: true } }, intent: 'star', description: 'star' },
  ];

  it('shows only the global hints when chat is focused', () => {
    const hints = selectBottomBar(CHAT_FOCUS, undefined, DEFAULT_BINDINGS);
    expect(hints.every((h) => h.description !== 'open doc')).toBe(true);
    expect(hints.length).toBeGreaterThan(0);
  });

  it('appends the focused panel keys, naming special keys', () => {
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS);
    const descriptions = hints.map((h) => h.description);
    expect(descriptions).toContain('open doc');
    expect(descriptions).toContain('star');
    // A printable chord renders its char; a key-only chord renders the special-key name.
    expect(hints.find((h) => h.description === 'open doc')?.key).toBe('o');
    expect(hints.find((h) => h.description === 'star')?.key).toBe('return');
  });

  it('omits `hidden` entries (gesture sub-steps like the go-to-line digits) from the hints', () => {
    const gestureKeymap: Keymap<'goto.start' | 'goto.digit.3'> = [
      { chord: { input: 'g' }, intent: 'goto.start', description: 'go to line' },
      {
        chord: { input: '3' },
        intent: 'goto.digit.3',
        description: 'go-to-line digit',
        hidden: true,
      },
    ];
    const descriptions = selectBottomBar('plans', gestureKeymap, DEFAULT_BINDINGS).map(
      (h) => h.description,
    );
    expect(descriptions).toContain('go to line');
    expect(descriptions).not.toContain('go-to-line digit');
  });

  it("shows a command-modified panel key's modifier, varying A-↔C- with the configured modifier", () => {
    // A panel that binds a key through the registry (e.g. star = the command modifier + `f`) must show
    // the modifier in its hint — a bare `f` would read as un-pressable. The prefix tracks the user's
    // configured modifier exactly like the global/nav hints.
    const starKeymap: Keymap<'star'> = [
      { chord: { input: 'f', key: { meta: true } }, intent: 'star', description: 'favorite' },
    ];
    const alt = selectBottomBar('plans', starKeymap, resolveBindings('alt', false, {}));
    expect(alt.find((h) => h.description === 'favorite')?.key).toBe('A-f');

    const ctrlKeymap: Keymap<'star'> = [
      { chord: { input: 'f', key: { ctrl: true } }, intent: 'star', description: 'favorite' },
    ];
    const ctrl = selectBottomBar('plans', ctrlKeymap, resolveBindings('ctrl', true, {}));
    expect(ctrl.find((h) => h.description === 'favorite')?.key).toBe('C-f');
  });

  it('pins a right-aligned help hint (item 12) on the normal bar, labelled from the binding', () => {
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS);
    const help = hints.find((h) => h.description === 'help');
    expect(help).toBeDefined();
    expect(help?.align).toBe('right');
    // The label is the resolved global.keyHelp binding (a plain ?).
    expect(help?.key).toBe(DEFAULT_BINDINGS.label('global.keyHelp'));
  });

  it('shows the `:help` command (not a bare ?) as the chat-focus help hint', () => {
    // First-run UX: in chat focus the dispatcher never steals `?` (it types into the input), so the
    // hint advertises the reachable, self-describing `:help` command instead — no trailing word.
    const help = selectBottomBar(CHAT_FOCUS, undefined, DEFAULT_BINDINGS).find(
      (h) => h.align === 'right',
    );
    expect(help).toBeDefined();
    expect(help?.key).toBe(':help');
    expect(help?.description).toBe('');
  });

  it('surfaces the always-on globals usable from the focused panel (no longer just the nav trio)', () => {
    // The regression this guards against: most globals were live but un-hinted. A list panel sees the
    // always-on globals…
    const descriptions = selectBottomBar('plans', keymap, DEFAULT_BINDINGS).map(
      (h) => h.description,
    );
    for (const d of [
      'panels',
      'nav',
      'chat',
      // TUIchat-3: the old 'tmux' (`y`) global is now the chat-view cycle ('view', on `t`); newTicket
      // lost its global chord/scope, so 'new ticket' no longer appears.
      'view',
      'new plan',
      'settings',
      'note',
    ]) {
      expect(descriptions).toContain(d);
    }
    expect(descriptions).not.toContain('new ticket');
    // …but NOT the chat-only super-chords, the chat-or-stage spawn, nor the Stage-only close-pane.
    expect(descriptions).not.toContain('prev target');
    expect(descriptions).not.toContain('spawn');
    expect(descriptions).not.toContain('close pane');
  });

  it('hides `spawn` on a list panel but shows it on a Stage pane (chat-or-stage scope)', () => {
    const onPlans = selectBottomBar('plans', undefined, DEFAULT_BINDINGS).map((h) => h.description);
    expect(onPlans).not.toContain('spawn');
    const onStage = selectBottomBar('stage:doc:readme', undefined, DEFAULT_BINDINGS).map(
      (h) => h.description,
    );
    expect(onStage).toContain('spawn');
    // `ctrl+q close pane` is Stage-only: present on a Stage pane, absent on a list panel.
    expect(onStage).toContain('close pane');
    expect(onPlans).not.toContain('close pane');
  });

  it('shows the chat-target chords (collapsed into one `target` hint) only while chat is focused', () => {
    const inChat = selectBottomBar(CHAT_FOCUS, undefined, DEFAULT_BINDINGS);
    const descriptions = inChat.map((h) => h.description);
    // The prev/next cycle chords collapse into a single `target` hint (A-hl) to save space…
    const target = inChat.find((h) => h.description === 'target');
    expect(target?.key).toBe('A-hl');
    expect(descriptions).toContain('toggle pane');
    // …so the separate `prev target` / `next target` labels are gone, as is close-pane.
    expect(descriptions).not.toContain('prev target');
    expect(descriptions).not.toContain('next target');
    expect(descriptions).not.toContain('close pane');
  });

  it('hides murder on the crows panel (it falls to the panel keymap there)', () => {
    const onCrows = selectBottomBar('crows', undefined, DEFAULT_BINDINGS).map((h) => h.description);
    expect(onCrows).not.toContain('murder crow');
    const onPlans = selectBottomBar('plans', undefined, DEFAULT_BINDINGS).map((h) => h.description);
    expect(onPlans).toContain('murder crow');
  });

  it('shows only `A-jk` nav (not `A-hjkl`) in chat, where A-h/A-l cycle the target', () => {
    const nav = selectBottomBar(CHAT_FOCUS, undefined, DEFAULT_BINDINGS).find(
      (h) => h.description === 'nav',
    );
    expect(nav?.key).toBe('A-jk');
    const panelNav = selectBottomBar('plans', undefined, DEFAULT_BINDINGS).find(
      (h) => h.description === 'nav',
    );
    expect(panelNav?.key).toBe('A-hjkl');
  });

  it('omits the help hint when a mode owns the bar (a modal has its own keys)', () => {
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS, [
      { key: 'esc', description: 'quit' },
    ]);
    expect(hints.find((h) => h.description === 'help')).toBeUndefined();
  });

  it('mode hints replace the panel keys (globals still lead) when a mode owns the bar', () => {
    const modeHints = [
      { key: 'j/k', description: 'nav' },
      { key: 'esc', description: 'cancel' },
    ];
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS, modeHints);
    const descriptions = hints.map((h) => h.description);
    // The mode's hints are present…
    expect(descriptions).toContain('nav');
    expect(descriptions).toContain('cancel');
    // …and the focused panel's keys are NOT (the mode captures input).
    expect(descriptions).not.toContain('open doc');
    expect(descriptions).not.toContain('star');
    // Globals still lead.
    expect(hints.length).toBeGreaterThan(modeHints.length);
  });
});
