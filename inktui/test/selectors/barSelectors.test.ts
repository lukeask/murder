/**
 * Bar view-model tests — the pure transforms the top/bottom bars render from (rule 2). Asserting the
 * selectors directly keeps the bar formatting (subscript labels, hint sourcing) tested without Ink.
 */

import { describe, expect, it } from 'vitest';
import { DEFAULT_BINDINGS, resolveBindings } from '../../src/input/bindings.js';
import { CHAT_FOCUS } from '../../src/input/focusStore.js';
import type { Keymap } from '../../src/input/keymap.js';
import type { PanelId } from '../../src/input/panels.js';
import type { UsageState } from '../../src/store/usage/usageSlice.js';
import {
  bottomBarItemWidth,
  connectionBadgeWidth,
  estimateTopBarLeftWidth,
  layoutTopBarWidgets,
  packBottomBarLineItems,
  selectBottomBar,
  selectBottomBarLineItems,
  selectOneLineHints,
  selectTopBar,
  selectTopBarWidgetSegments,
  type BottomBarHint,
  type BottomBarLineItem,
  type TopBarWidgetSegment,
} from '../../src/selectors/barSelectors.js';
import { KEY_USAGE_HALF_LIFE_MS } from '../../src/store/keyUsage/keyUsageStore.js';

const EMPTY_USAGE: UsageState = { rows: [], status: 'idle', error: null };
const BAR_CONTEXT = { usage: EMPTY_USAGE, keyUsage: {}, now: 0 };
const WIDE_AVAIL = 500;

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
    expect(divided).toEqual(['tree']);
  });

  it('marks only the visible panels active', () => {
    const labels = selectTopBar(new Set<PanelId>(['plans', 'crows']));
    const active = labels.filter((l) => l.active).map((l) => l.id);
    expect(active).toEqual(['plans', 'crows']);
  });
});

describe('selectOneLineHints (cookbook)', () => {
  const help: BottomBarHint = { key: '?', description: 'help', align: 'right' };

  it('keeps every hint when they all fit, in original order', () => {
    const hints: BottomBarHint[] = [
      { key: 'a', description: 'one' },
      { key: 'b', description: 'two' },
      help,
    ];
    expect(selectOneLineHints(hints, {}, 80, 0)).toEqual(hints);
  });

  it('drops heavily-used hints first when overfull, preserving original order among survivors', () => {
    const hints: BottomBarHint[] = [
      { key: 'a', description: 'alpha', actionId: 'global.a' },
      { key: 'b', description: 'beta', actionId: 'global.b' },
      { key: 'c', description: 'gamma', actionId: 'global.c' },
      help,
    ];
    const now = 1_000_000;
    const usage = {
      'global.b': { count: 50, lastAt: now },
      'global.c': { count: 2, lastAt: now },
    };
    // '? help' = 6, gap = 1 → 7 reserved; 'a alpha' + 'c gamma' fit in the rest.
    const selected = selectOneLineHints(hints, usage, 22, now);
    expect(selected.map((h) => h.description)).toEqual(['alpha', 'gamma', 'help']);
  });

  it('always keeps the right-aligned help hint', () => {
    const hints: BottomBarHint[] = [
      { key: 'm', description: 'toggle maximized' },
      help,
    ];
    expect(selectOneLineHints(hints, {}, 6, 0)).toEqual([help]);
  });

  it('with avail too small for any left hint, shows only the help hint', () => {
    const hints: BottomBarHint[] = [
      { key: 'm', description: 'toggle maximized' },
      help,
    ];
    expect(selectOneLineHints(hints, {}, 5, 0)).toEqual([help]);
  });
});

describe('selectOneLineHints (edge)', () => {
  const help: BottomBarHint = { key: '?', description: 'help', align: 'right' };

  it('ties on usage score keep the original curated order', () => {
    const hints: BottomBarHint[] = [
      { key: 'a', description: 'first', actionId: 'x' },
      { key: 'b', description: 'second', actionId: 'y' },
      help,
    ];
    // '? help' = 6 + gap 1 → 7 reserved; only 'a first' (7) fits in 14.
    const selected = selectOneLineHints(hints, {}, 14, 0);
    expect(selected.map((h) => h.description)).toEqual(['first', 'help']);
  });

  it('decayed usage lowers priority for stale high counts', () => {
    const hints: BottomBarHint[] = [
      { key: 'a', description: 'fresh', actionId: 'global.a' },
      { key: 'b', description: 'stale', actionId: 'global.b' },
      help,
    ];
    const now = KEY_USAGE_HALF_LIFE_MS * 2;
    const usage = {
      'global.a': { count: 1, lastAt: now },
      'global.b': { count: 100, lastAt: 0 },
    };
    // Both left hints fit; stale high count ranks lower but still shown when wide enough.
    const selected = selectOneLineHints(hints, usage, 22, now);
    expect(selected.map((h) => h.description)).toEqual(['fresh', 'stale', 'help']);
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
    expect(hints.find((h) => h.description === 'open doc')?.actionId).toBe('plans:open');
    expect(hints.find((h) => h.description === 'star')?.actionId).toBe('plans:star');
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
    const hints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS);
    const descriptions = hints.map((h) => h.description);
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
    const settings = hints.find((h) => h.description === 'settings');
    expect(settings?.actionId).toBe('global.settings');
    expect(hints.find((h) => h.description === 'panels')?.actionId).toBeUndefined();
    // …but NOT the chat-only super-chords nor spawn (chat-or-stage).
    expect(descriptions).not.toContain('prev target');
    expect(descriptions).not.toContain('spawn');
  });

  it('hides `spawn` on a list panel but shows it on a Stage pane (chat-or-stage scope)', () => {
    const onPlans = selectBottomBar('plans', undefined, DEFAULT_BINDINGS).map((h) => h.description);
    expect(onPlans).not.toContain('spawn');
    const onStage = selectBottomBar('stage:doc:readme', undefined, DEFAULT_BINDINGS).map(
      (h) => h.description,
    );
    expect(onStage).toContain('spawn');
    // `toggle pane` is chat-or-stage: present on a Stage pane and in chat, absent on a list panel.
    expect(onStage).toContain('toggle pane');
    expect(onPlans).not.toContain('toggle pane');
  });

  it('shows the chat-target chords (collapsed into one `target` hint) only while chat is focused', () => {
    const inChat = selectBottomBar(CHAT_FOCUS, undefined, DEFAULT_BINDINGS);
    const descriptions = inChat.map((h) => h.description);
    // The prev/next cycle chords collapse into a single `target` hint (A-hl) to save space…
    const target = inChat.find((h) => h.description === 'target');
    expect(target?.key).toBe('A-hl');
    expect(descriptions).toContain('toggle pane');
    // …so the separate `prev target` / `next target` labels are gone.
    expect(descriptions).not.toContain('prev target');
    expect(descriptions).not.toContain('next target');
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

describe('bar widget framework', () => {
  const keymap: Keymap<'open'> = [
    { chord: { input: 'o' }, intent: 'open', description: 'open doc' },
  ];

  it('selectBottomBarLineItems includes hints by default (registry defaults)', () => {
    const items = selectBottomBarLineItems(
      undefined,
      'plans',
      keymap,
      DEFAULT_BINDINGS,
      BAR_CONTEXT,
      WIDE_AVAIL,
    );
    expect(items.length).toBeGreaterThan(0);
    expect(items.every((item) => item.kind === 'hint')).toBe(true);
  });

  it('selectBottomBarLineItems is empty when the hints widget is disabled', () => {
    const items = selectBottomBarLineItems(
      { hints: { enabled: false, placement: 'bottom' } },
      'plans',
      keymap,
      DEFAULT_BINDINGS,
      BAR_CONTEXT,
      WIDE_AVAIL,
    );
    expect(items).toEqual([]);
  });

  it('selectBottomBarLineItems adaptive=false keeps all hints for multi-line packing', () => {
    const items = selectBottomBarLineItems(
      { hints: { enabled: true, placement: 'bottom', adaptive: false } },
      'plans',
      keymap,
      DEFAULT_BINDINGS,
      BAR_CONTEXT,
      20,
    );
    const allHints = selectBottomBar('plans', keymap, DEFAULT_BINDINGS);
    expect(items).toHaveLength(allHints.length);
    const lines = packBottomBarLineItems(items, 20);
    expect(lines.length).toBeGreaterThan(1);
  });

  it('selectBottomBarLineItems adaptive=true fits one packed line at narrow width', () => {
    const items = selectBottomBarLineItems(
      undefined,
      'plans',
      keymap,
      DEFAULT_BINDINGS,
      BAR_CONTEXT,
      40,
    );
    const lines = packBottomBarLineItems(items, 40);
    expect(lines).toHaveLength(1);
    expect(lines[0]?.some((item) => item.kind === 'hint' && item.hint.align === 'right')).toBe(
      true,
    );
  });

  it('packBottomBarLineItems keeps a lone oversized item on its own line at zero width', () => {
    const items: BottomBarLineItem[] = [{ kind: 'hint', hint: { key: 'j', description: 'down' } }];
    expect(packBottomBarLineItems(items, 0)).toHaveLength(1);
  });

  it('packBottomBarLineItems keeps a lone oversized hint on its own line', () => {
    const big: BottomBarLineItem = {
      kind: 'hint',
      hint: { key: 'm', description: 'toggle maximized' },
    };
    expect(bottomBarItemWidth(big)).toBeGreaterThan(5);
    const lines = packBottomBarLineItems([big], 5);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toEqual([big]);
  });

  it('layoutTopBarWidgets drops trailing segments that do not fit', () => {
    const segments: TopBarWidgetSegment[] = [
      { widgetId: 'hints', runs: [{ text: 'aaaa', style: {} }], width: 4 },
      { widgetId: 'hints', runs: [{ text: 'bbbb', style: {} }], width: 4 },
    ];
    expect(layoutTopBarWidgets(segments, 5)).toHaveLength(1);
    expect(layoutTopBarWidgets(segments, 9)).toHaveLength(2);
  });

  it('layoutTopBarWidgets truncates the last segment with an ellipsis when partially fitting', () => {
    const segments: TopBarWidgetSegment[] = [
      { widgetId: 'hints', runs: [{ text: 'hello-world', style: { dim: true } }], width: 11 },
    ];
    const laid = layoutTopBarWidgets(segments, 6);
    expect(laid).toHaveLength(1);
    expect(laid[0]?.width).toBe(6);
    expect(laid[0]?.runs.map((run) => run.text).join('')).toBe('hello…');
  });

  it('estimateTopBarLeftWidth grows with project name and panel labels', () => {
    const empty = estimateTopBarLeftWidth(undefined, selectTopBar(new Set()));
    const withProject = estimateTopBarLeftWidth('demo', selectTopBar(new Set()));
    expect(withProject).toBeGreaterThan(empty);
  });

  it('connectionBadgeWidth is zero for steady connected/unknown states', () => {
    expect(connectionBadgeWidth('connected')).toBe(0);
    expect(connectionBadgeWidth('unknown')).toBe(0);
    expect(connectionBadgeWidth('reconnecting')).toBeGreaterThan(0);
  });

  it('selectTopBarWidgetSegments is empty when usage widget is disabled by default', () => {
    expect(selectTopBarWidgetSegments(undefined, BAR_CONTEXT)).toEqual([]);
  });
});
