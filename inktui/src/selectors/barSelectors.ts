/**
 * View-models for the top and bottom bars (rule 2: bar presentation lives here, not in a component
 * or the store). The bars are pure functions of input state — the toggled-panel set, the effective
 * focus, and the focused panel's declared keymap — so their non-trivial formatting (the subscript
 * labels, the hint list) is a tested pure transform, not inline JSX logic.
 *
 * Phase 3.1: bar *widgets* (1-line segments registered in {@link ./barWidgetRegistry.js}) compose
 * into the bars via {@link selectBottomBarLineItems} and {@link layoutTopBarWidgets}; hints are the
 * first built-in widget.
 */

import { ACTIONS, chordLabel, type ResolvedBindings } from '../input/bindings.js';
import { CHAT_FOCUS, type FocusId } from '../input/focusStore.js';
import { GLOBAL_ACTION_IDS, GLOBAL_SCOPE, inFocusScope } from '../input/globalScope.js';
import type { KeyChord, Keymap } from '../input/keymap.js';
import { PANELS, type PanelId } from '../input/panels.js';
import type { TextRun } from '../render/cellSurface.js';
import type { ConnectionStatus } from '../store/connection/connectionStore.js';
import { decayedCount, type KeyUsageRecord } from '../store/keyUsage/keyUsageStore.js';
import type { UsageState } from '../store/usage/usageSlice.js';
import {
  type BarWidgetId,
  type BarWidgetsConfig,
  enabledBarWidgetIds,
  resolveBarWidgetConfig,
} from './barWidgetRegistry.js';
import { selectUsageBarWidget } from './selectUsageBarWidget.js';
import { selectWorkspaceBarWidget } from './selectWorkspaceBarWidget.js';

/** Horizontal gap between adjacent top-bar widget segments (cells). */
export const TOP_BAR_WIDGET_GAP = 1;
/** `paddingX={1}` each side of the top bar. */
export const TOP_BAR_PADDING = 2;
/** Gap between the left label cluster and the right-side widget/badge cluster. */
export const TOP_BAR_RIGHT_CLUSTER_GAP = 1;
/** Gap between hints (or segment widgets) on a bottom-bar line. */
export const BOTTOM_BAR_ITEM_GAP = 1;

/** Unicode subscript digits 0–9, indexed by the digit — for the top bar's `plans₁ … crows₀` labels
 * (the plan's "Subscript number labels: `plans_1` … `crows_0`"). A real subscript glyph, so the
 * label reads as one token, not `plans_1`. */
const SUBSCRIPT_DIGITS = ['₀', '₁', '₂', '₃', '₄', '₅', '₆', '₇', '₈', '₉'] as const;

/** Map a `0`–`9` digit to its subscript glyph. */
function subscript(digit: number): string {
  return SUBSCRIPT_DIGITS[digit] ?? String(digit);
}

/** One top-bar label: a panel's name with its subscript digit, and whether it is currently toggled
 * on (the bar *highlights toggled panels* — the plan's "highlight currently-toggled panels"). */
export interface TopBarLabel {
  readonly id: PanelId;
  /** Display text, e.g. `plans₁`. */
  readonly text: string;
  /** True when this panel is in the visible set → the bar renders it highlighted. */
  readonly active: boolean;
  /** True on the first right-rail panel → the bar inserts a `·` separator before it. */
  readonly dividerBefore?: boolean;
}

/**
 * The top bar's labels, in screen order, each marked active iff its panel is toggled on. Pure over
 * the visible set; the component just maps these to highlighted/dim `<Text>`. Built from {@link PANELS}
 * so a new panel appears in the bar automatically (no second list to keep in sync).
 */
export function selectTopBar(visible: ReadonlySet<PanelId>): readonly TopBarLabel[] {
  let prevRegion: string | undefined;
  return PANELS.map((panel) => {
    const dividerBefore = prevRegion === 'left' && panel.region === 'right';
    prevRegion = panel.region;
    return {
      id: panel.id,
      text: `${panel.label ?? panel.id}${subscript(panel.digit)}`,
      active: visible.has(panel.id),
      ...(dividerBefore ? { dividerBefore: true } : {}),
    };
  });
}

/** One contextual hint: the key and what it does, drawn straight from a declared keymap entry. */
export interface BottomBarHint {
  /** The printable chord char (`j`), or a special-key name (`enter`) for display. */
  readonly key: string;
  readonly description: string;
  /** When set, ties this hint to the dispatcher's usage `action` id for adaptive ranking. */
  readonly actionId?: string;
  /** When `'right'`, the bar pins this hint to the FAR right of the bar (item 12 prep — the help
   * hint a new user can always find). Omitted/`'left'` = normal left-to-right flow. */
  readonly align?: 'left' | 'right';
}

/** The modifier prefix for the digit/nav hints, derived from the resolved bindings so the footer
 * tracks the user's modifier choice. Reads `global.focusChat`'s label (always present) and keeps just
 * its prefix (`A-`, `C-`, or `A-/C-` under both). */
function modifierPrefix(bindings: ResolvedBindings): string {
  // The label is e.g. `A-space`; strip the key part to get the prefix(es). Under `both` it is
  // `A-space/C-space` → `A-/C-`.
  return bindings
    .label('global.focusChat')
    .split('/')
    .map((part) => part.replace(/space$/, ''))
    .join('/');
}

/** The navigation trio shown when a *mode* owns the bar: the chords that stay discoverable even
 * behind a modal (panels, geometric nav, focus-chat). Kept minimal on purpose — under a non-pass-
 * through mode the other globals are captured, so listing them would be a lying affordance. */
function navGlobals(bindings: ResolvedBindings): readonly BottomBarHint[] {
  const prefix = modifierPrefix(bindings);
  return [
    { key: `${prefix}1–0`, description: 'panels' },
    { key: `${prefix}hjkl`, description: 'nav' },
    { key: bindings.label('global.focusChat'), description: 'chat' },
  ];
}

/**
 * The global hints that are *usable from the current focus* — the real fix for the bar/dispatcher
 * drift. The two synthetic groups (panel digits, vim nav) lead, then every named global whose
 * {@link GLOBAL_SCOPE} entry is live from `focused`, in declaration order, labelled from the resolved
 * bindings (so a rebind / modifier choice / the murder `C-m` override all track here). `global.keyHelp`
 * is emitted separately as the right-pinned help hint, so it is skipped in the loop.
 *
 * Nav is itself focus-aware: away from chat all of `hjkl` move focus, but IN chat `A-h`/`A-l` are
 * stolen by the chat-target cycle super-chords (see dispatcher.ts), so only `A-j`/`A-k` still
 * navigate — the hint shows the truthful subset rather than claiming four working arrows.
 */
function globalHints(bindings: ResolvedBindings, focused: FocusId): readonly BottomBarHint[] {
  const prefix = modifierPrefix(bindings);
  const hints: BottomBarHint[] = [{ key: `${prefix}1–0`, description: 'panels' }];
  hints.push(
    focused === CHAT_FOCUS
      ? { key: `${prefix}jk`, description: 'nav' }
      : { key: `${prefix}hjkl`, description: 'nav' },
  );
  for (const id of GLOBAL_ACTION_IDS) {
    if (id === 'global.keyHelp') {
      continue; // rendered as the right-pinned help hint, with the chat-focus `?`-types disambiguation
    }
    if (!inFocusScope(GLOBAL_SCOPE[id], focused)) {
      continue;
    }
    // The two chat-target cycle chords are mirror directions of one gesture; in chat focus they
    // collapse into a single `target` hint (`A-hl`/`C-hl`, matching the nav `jk` style) to save
    // horizontal space rather than spending two slots on `prev target` + `next target`.
    if (id === 'global.cycleTargetNext') {
      continue; // folded into the combined `target` hint emitted at cycleTargetPrev's position
    }
    if (id === 'global.cycleTargetPrev') {
      hints.push({ key: `${prefix}hl`, description: 'target' });
      continue;
    }
    hints.push({ key: bindings.label(id), description: ACTIONS[id].description, actionId: id });
  }
  return hints;
}

/** Normalize a keymap entry's chord(s) to the first chord (the list form binds equivalent chords;
 * the hint shows one). A list always has at least one chord (resolved bindings never empty). */
function firstChord(chord: KeyChord | readonly KeyChord[]): KeyChord {
  if (Array.isArray(chord)) {
    return (chord as readonly KeyChord[])[0] as KeyChord;
  }
  return chord as KeyChord;
}

/**
 * Render a chord's key for the hint bar via the shared {@link chordLabel} — so a command-modified
 * panel key (e.g. star = alt+f) shows its modifier prefix (`A-f` / `C-f`, varying with the configured
 * modifier) instead of a bare, un-pressable `f`, while a plain key (`j`, Enter) reads as itself. One
 * label rule for the panel hints and the globals, so the focused pane's keys never display a modifier
 * the bar's nav/chat hints don't.
 */
function hintKey(entry: Keymap<string>[number]): string {
  return chordLabel(firstChord(entry.chord));
}

/**
 * The bottom bar's hints: the global chords, then the *focused* panel's own declared keys (the plan's
 * "Bottom bar: contextual hints", sourced from the keymap so a declared key is self-documenting —
 * see keymap.ts). When chat is focused there is no panel keymap, so only the globals show.
 *
 * When an active mode supplies its own `modeHints` (the spawn wizard, the help overlay, etc.), THOSE
 * replace the panel keys entirely — the mode captures input, so its keys are the only relevant ones
 * (the panels underneath can't be driven). The globals still lead so the navigation keys stay
 * discoverable. Pure over the effective focus, that panel's keymap, and the active mode's hints,
 * all passed in by the shell.
 */
/** Context passed into bar-widget selectors (usage rows for the usage widget, etc.). */
export interface BarWidgetContext {
  readonly usage: UsageState;
  readonly keyUsage: Readonly<Record<string, KeyUsageRecord>>;
  readonly now: number;
  readonly activeIndex: number;
  readonly count: number;
}

/** One packable bottom-bar item: a contextual hint chip or a widget segment (Phase 3.1). */
export type BottomBarLineItem =
  | { readonly kind: 'hint'; readonly hint: BottomBarHint }
  | {
      readonly kind: 'segment';
      readonly widgetId: BarWidgetId;
      readonly runs: readonly TextRun[];
      readonly width: number;
    };

/** Display width of one bottom-bar packable item. */
export function bottomBarItemWidth(item: BottomBarLineItem): number {
  if (item.kind === 'hint') {
    const hint = item.hint;
    return hint.description.length === 0
      ? hint.key.length
      : hint.key.length + 1 + hint.description.length;
  }
  return item.width;
}

/** Rendered width of a packed line of bottom-bar items (includes inter-item gaps). */
export function bottomBarLineWidth(line: readonly BottomBarLineItem[]): number {
  return (
    line.reduce((sum, item) => sum + bottomBarItemWidth(item), 0) +
    BOTTOM_BAR_ITEM_GAP * Math.max(0, line.length - 1)
  );
}

/**
 * Greedily pack bottom-bar items into single-width lines (left-to-right). Right-aligned hints are
 * pulled out and appended to the last line when they fit.
 */
export function packBottomBarLineItems(
  items: readonly BottomBarLineItem[],
  avail: number,
): BottomBarLineItem[][] {
  const right = items.filter((item) => item.kind === 'hint' && item.hint.align === 'right');
  const left = items.filter((item) => !(item.kind === 'hint' && item.hint.align === 'right'));
  const lines: BottomBarLineItem[][] = [];
  let current: BottomBarLineItem[] = [];
  let used = 0;
  for (const item of left) {
    const w = bottomBarItemWidth(item);
    const add = current.length === 0 ? w : w + BOTTOM_BAR_ITEM_GAP;
    if (current.length > 0 && used + add > avail) {
      lines.push(current);
      current = [item];
      used = w;
    } else {
      current.push(item);
      used += add;
    }
  }
  if (current.length > 0) {
    lines.push(current);
  }
  if (right.length > 0) {
    const last = lines[lines.length - 1];
    if (
      last !== undefined &&
      bottomBarLineWidth(last) + BOTTOM_BAR_ITEM_GAP + bottomBarLineWidth(right) <= avail
    ) {
      last.push(...right);
    } else {
      lines.push([...right]);
    }
  }
  return lines;
}

/** Display width of one hint chip (same math as {@link bottomBarItemWidth} for hint items). */
function bottomBarHintWidth(hint: BottomBarHint): number {
  return bottomBarItemWidth({ kind: 'hint', hint });
}

/**
 * Pick left hints that fit one line, ranking by low key-usage first so unfamiliar bindings surface
 * while mastered ones drop off. Right-aligned hints (help) are always kept; their width is reserved
 * first. Chosen left hints are returned in their original order, then the right-aligned hints.
 */
export function selectOneLineHints(
  hints: readonly BottomBarHint[],
  usage: Readonly<Record<string, KeyUsageRecord>>,
  avail: number,
  now: number,
): BottomBarHint[] {
  const right = hints.filter((hint) => hint.align === 'right');
  const left = hints.filter((hint) => hint.align !== 'right');
  const rightWidth =
    right.length === 0
      ? 0
      : right.reduce((sum, hint) => sum + bottomBarHintWidth(hint), 0) +
        BOTTOM_BAR_ITEM_GAP * Math.max(0, right.length - 1);
  const gapToRight = left.length > 0 && right.length > 0 ? BOTTOM_BAR_ITEM_GAP : 0;
  const remaining = avail - rightWidth - gapToRight;
  if (remaining < 0) {
    return [...right];
  }

  const scored = left.map((hint, index) => ({
    hint,
    index,
    score: (() => {
      if (hint.actionId === undefined) {
        return 0;
      }
      const record = usage[hint.actionId];
      return record !== undefined ? decayedCount(record, now) : 0;
    })(),
  }));
  scored.sort((a, b) => a.score - b.score || a.index - b.index);

  const chosenIndices = new Set<number>();
  let used = 0;
  for (const { hint, index } of scored) {
    const w = bottomBarHintWidth(hint);
    const add = chosenIndices.size === 0 ? w : w + BOTTOM_BAR_ITEM_GAP;
    if (used + add <= remaining) {
      chosenIndices.add(index);
      used += add;
    }
  }

  const selectedLeft = left.filter((_, index) => chosenIndices.has(index));
  if (selectedLeft.length === 0 && left.length > 0) {
    return [...right];
  }
  return [...selectedLeft, ...right];
}

/** A top-bar widget segment: styled runs plus its display width (for layout). */
export interface TopBarWidgetSegment {
  readonly widgetId: BarWidgetId;
  readonly runs: readonly TextRun[];
  readonly width: number;
}

/** Estimate the left cluster width: branding + project + panel labels (display cells). */
export function estimateTopBarLeftWidth(
  project: string | undefined,
  labels: readonly TopBarLabel[],
): number {
  // `murder` + gap before labels; project adds ` · name` when present.
  let width = 'murder'.length + 3;
  if (project !== undefined && project.length > 0) {
    width += ` · ${project}`.length + 3;
  }
  for (const label of labels) {
    if (label.dividerBefore === true) {
      width += 2;
    }
    width += label.text.length + 1;
  }
  return width;
}

/** Display width of the connection badge for layout (0 when silent). */
export function connectionBadgeWidth(status: ConnectionStatus): number {
  switch (status) {
    case 'connecting':
      return 'connecting…'.length;
    case 'reconnecting':
      return '[reconnecting]'.length;
    case 'version-mismatch':
      return '[version mismatch — restart murder]'.length;
    default:
      return 0;
  }
}

/**
 * Fit top-bar widget segments into `avail` cells: left-to-right, drop trailing widgets that do not
 * fit, truncate the last visible segment with `…` when needed. Never wraps — the bar stays one line.
 */
export function layoutTopBarWidgets(
  segments: readonly TopBarWidgetSegment[],
  avail: number,
): readonly TopBarWidgetSegment[] {
  if (avail <= 0 || segments.length === 0) {
    return [];
  }
  const out: TopBarWidgetSegment[] = [];
  let used = 0;
  for (const segment of segments) {
    const gap = out.length === 0 ? 0 : TOP_BAR_WIDGET_GAP;
    if (used + gap + segment.width <= avail) {
      out.push(segment);
      used += gap + segment.width;
      continue;
    }
    const remaining = avail - used - gap;
    if (remaining >= 2) {
      out.push(truncateTopBarSegment(segment, remaining));
    }
    break;
  }
  return out;
}

function truncateTopBarSegment(
  segment: TopBarWidgetSegment,
  maxWidth: number,
): TopBarWidgetSegment {
  if (segment.width <= maxWidth) {
    return segment;
  }
  const ellipsis = '…';
  const target = Math.max(1, maxWidth - ellipsis.length);
  let taken = 0;
  const runs: TextRun[] = [];
  for (const run of segment.runs) {
    if (taken >= target) {
      break;
    }
    const chars = Array.from(run.text);
    const slice = chars.slice(0, target - taken).join('');
    if (slice.length > 0) {
      runs.push({ text: slice, style: run.style });
      taken += slice.length;
    }
  }
  runs.push({ text: ellipsis, style: segment.runs.at(-1)?.style ?? {} });
  return { widgetId: segment.widgetId, runs, width: maxWidth };
}

/**
 * Enabled bottom-bar widgets → packable line items. The hints widget expands to hint chips; future
 * widgets contribute a single `segment` item each.
 */
export function selectBottomBarLineItems(
  barWidgets: BarWidgetsConfig | undefined,
  focused: FocusId,
  focusedKeymap: Keymap<string> | undefined,
  bindings: ResolvedBindings,
  context: BarWidgetContext,
  avail: number,
  modeHints?: readonly BottomBarHint[],
): readonly BottomBarLineItem[] {
  const items: BottomBarLineItem[] = [];
  let reservedWidth = 0;
  for (const widgetId of enabledBarWidgetIds(barWidgets, 'bottom')) {
    if (widgetId === 'hints') {
      const config = resolveBarWidgetConfig('hints', barWidgets);
      const hints = selectBottomBar(focused, focusedKeymap, bindings, modeHints);
      const gapBefore = items.length > 0 ? BOTTOM_BAR_ITEM_GAP : 0;
      const availForHints = avail - reservedWidth - gapBefore;
      const selected =
        config.adaptive !== false
          ? selectOneLineHints(hints, context.keyUsage, availForHints, context.now)
          : hints;
      for (const hint of selected) {
        items.push({ kind: 'hint', hint });
      }
      continue;
    }
    if (widgetId === 'usage') {
      const config = resolveBarWidgetConfig('usage', barWidgets);
      const segment = selectUsageBarWidget(context.usage.rows, config.harnesses);
      if (segment !== null) {
        const gap = items.length > 0 ? BOTTOM_BAR_ITEM_GAP : 0;
        reservedWidth += gap + segment.width;
        items.push({
          kind: 'segment',
          widgetId: 'usage',
          runs: segment.runs,
          width: segment.width,
        });
      }
      continue;
    }
    if (widgetId === 'workspace') {
      const segment = selectWorkspaceBarWidget(context.activeIndex, context.count);
      if (segment !== null) {
        const gap = items.length > 0 ? BOTTOM_BAR_ITEM_GAP : 0;
        reservedWidth += gap + segment.width;
        items.push({
          kind: 'segment',
          widgetId: 'workspace',
          runs: segment.runs,
          width: segment.width,
        });
      }
    }
  }
  return items;
}

/** Enabled top-bar widgets → segments. */
export function selectTopBarWidgetSegments(
  barWidgets: BarWidgetsConfig | undefined,
  context: BarWidgetContext,
): readonly TopBarWidgetSegment[] {
  const segments: TopBarWidgetSegment[] = [];
  for (const widgetId of enabledBarWidgetIds(barWidgets, 'top')) {
    if (widgetId === 'hints') {
      continue;
    }
    if (widgetId === 'usage') {
      const config = resolveBarWidgetConfig('usage', barWidgets);
      const segment = selectUsageBarWidget(context.usage.rows, config.harnesses);
      if (segment !== null) {
        segments.push({
          widgetId: 'usage',
          runs: segment.runs,
          width: segment.width,
        });
      }
      continue;
    }
    if (widgetId === 'workspace') {
      const segment = selectWorkspaceBarWidget(context.activeIndex, context.count);
      if (segment !== null) {
        segments.push({
          widgetId: 'workspace',
          runs: segment.runs,
          width: segment.width,
        });
      }
    }
  }
  return segments;
}

export function selectBottomBar(
  focused: FocusId,
  focusedKeymap: Keymap<string> | undefined,
  bindings: ResolvedBindings,
  modeHints?: readonly BottomBarHint[],
): readonly BottomBarHint[] {
  if (modeHints !== undefined) {
    // A mode owns the bar: the nav trio (still discoverable) then the mode's own hints; no panel keys,
    // and no help hint (a modal's keys are the only relevant ones). The other globals are captured by
    // a non-pass-through mode, so the bar lists only the always-discoverable navigation chords.
    return [...navGlobals(bindings), ...modeHints];
  }
  // The globals usable from THIS focus (the dispatcher's gate, shared via GLOBAL_SCOPE), so a live
  // chord is always hinted and a dead one never is.
  const globals = globalHints(bindings, focused);
  // Item 12: the keybinding-help hint, ALWAYS pinned to the far right so a new user can find it. The
  // label is derived from the resolved `global.keyHelp` binding (so a rebind tracks here too).
  //
  // While CHAT has focus, a bare `?` types into the input (the dispatcher deliberately never steals
  // it — dispatcher.ts gates `global.keyHelp` to non-chat focus), so a plain `?` hint would be a lying
  // affordance. The reachable affordance from the input is the `:help` command (commandDispatch.ts),
  // which is self-describing — so the chat-focus help hint is just `:help`, with no redundant trailing
  // word. Away from chat the bare `?` is live, shown as `? help`.
  const helpHint: BottomBarHint =
    focused === CHAT_FOCUS
      ? { key: ':help', description: '', align: 'right' }
      : { key: bindings.label('global.keyHelp'), description: 'help', align: 'right' };
  if (focused === CHAT_FOCUS || focusedKeymap === undefined) {
    return [...globals, helpHint];
  }
  // `hidden` entries (mechanical sub-steps of a gesture, e.g. go-to-line digits) stay matchable but
  // are not hints — see keymap.ts.
  const panelHints = focusedKeymap
    .filter((entry) => entry.hidden !== true)
    .map((entry) => ({
      key: hintKey(entry),
      description: entry.description,
      actionId: `${focused}:${entry.intent}`,
    }));
  return [...globals, ...panelHints, helpHint];
}
