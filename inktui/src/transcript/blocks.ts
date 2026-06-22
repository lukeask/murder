/**
 * blocks.ts — the shared, presentation-time block classifier (readability engine, TUIchat-2).
 *
 * `classifyBlocks(text)` groups a turn's already-faithful multi-line `text` into typed {@link Block}s
 * so the renderer can STYLE each region differently — prose wraps, code/tables render as no-wrap
 * islands, lists keep their bullet structure. It is a pure function reused by both the Ink TUI and
 * `murder web` (imported via the `@core` alias), so it lives under `src/transcript/` with no React or
 * store dependency.
 *
 * ## What it is NOT
 * This does **not** un-wrap soft-wrapped prose — that is lossy and can only be done at capture time
 * where the source wrap width is known. The Phase-1 Python parser
 * (`murder/llm/harnesses/transcripts/_shared.py`) already produced a faithful multi-line string:
 * real newlines, verbatim code/tables/lists, prose de-wrapped. This module re-derives the SAME block
 * structure purely for styling, mirroring that parser's heuristics so the two classifiers agree:
 *
 *  - **code** — fenced ` ``` ` … ` ``` ` spans, captured verbatim INCLUDING inner blank lines and the
 *    fence lines themselves; the opening fence's trailing word (` ```ts `) is surfaced as `lang`.
 *  - **list** — a block whose body carries `- ` / `* ` / `N. ` / `N) ` bullet leads.
 *  - **pre** — a block that is columnar (≥1 line with an internal 2+-space gap), uniformly indented
 *    (every body line starts with 2 spaces or a tab), or contains box-drawing glyphs. Preformatted:
 *    rendered verbatim, never re-wrapped.
 *  - **prose** — everything else: confident running text, safe to wrap to the pane width at render.
 *
 * The bias matches Phase 1: when in doubt, PRESERVE (label `pre`) rather than wrap — over-preserving
 * leaves original formatting intact, the lesser evil for readability.
 */

/** A block's display kind. `pre` is any preformatted region (tables, trees, aligned columns, indented
 * blocks) that must render verbatim; `code` is specifically a fenced span (and may carry a `lang`). */
export type BlockKind = 'prose' | 'code' | 'pre' | 'list';

/** One classified region of a turn's text. `lines` are the verbatim source lines (no trimming) of the
 * block; the renderer decides how to lay them out per {@link BlockKind}. For `code` the `lines` EXCLUDE
 * the ` ``` ` fence lines (so the island shows only the code), and `lang` carries the fence's language
 * hint when present. */
export interface Block {
  readonly kind: BlockKind;
  readonly lines: readonly string[];
  /** The fenced-code language hint (` ```ts ` → `'ts'`), present only on `code` blocks that declared
   * one. Absent/`undefined` otherwise. */
  readonly lang?: string;
}

/** Box-drawing / block glyphs whose presence marks a block as preformatted (tables, frames, trees).
 * Mirrors `_DEFAULT_BOX_CHARS` in the Phase-1 Python classifier. */
const BOX_CHARS = new Set('┌┐└┘├┤┬┴┼─│┃━═╋╔╗╚╝║╠╣╦╩╬▌▐█▏▕╭╮╯╰');

/** A bullet / numbered list-item lead: `- `, `* `, `1. `, `2) `, … (mirrors `_LIST_LEAD_RE`). */
const LIST_LEAD_RE = /^\s*([-*]\s|\d+[.)]\s)/;

/** A code fence: a line whose stripped form opens/closes with ``` ``` ``` (mirrors `_FENCE_RE`). */
const FENCE_RE = /^\s*```/;

/** An internal multi-space gap: a non-space, then 2+ spaces, then content. The presence of such a gap
 * on any line is the columnar-alignment signal (mirrors `_GAP_RE` + `_is_columnar`). */
const GAP_RE = /\S {2,}\S/;

/** The fence's language hint: everything after the opening ``` ``` ``` backticks on the fence line,
 * trimmed (` ```ts ` → `ts`). Empty string → no hint. */
function fenceLang(fenceLine: string): string {
  return fenceLine.replace(/^\s*```/, '').trim();
}

/** Any line carries an internal 2+-space gap → intentional column alignment. Line-local & monotonic,
 * exactly like the Python `_is_columnar`, so a streaming block never flips prose→pre in a way that
 * would desync content-key dedup. */
function isColumnar(lines: readonly string[]): boolean {
  return lines.some((line) => GAP_RE.test(line));
}

/** Every non-blank line carries a 2+-space (or tab) leading indent (mirrors `_is_indented`). */
function isIndented(lines: readonly string[]): boolean {
  const body = lines.filter((line) => line.trim() !== '');
  return body.length > 0 && body.every((line) => line.startsWith('  ') || line.startsWith('\t'));
}

/** Any line contains a box-drawing glyph (mirrors `_has_box`). */
function hasBox(lines: readonly string[]): boolean {
  return lines.some((line) => [...line].some((ch) => BOX_CHARS.has(ch)));
}

/**
 * Label one blank-line-separated block (NOT a fenced span — those are tagged `code` upstream) as
 * `prose` / `pre` / `list`. Mirrors the Python `classify_block`: list leads win first, then any
 * preserve signal (box glyphs / uniform indent / columnar gaps) → `pre`, else confident `prose`.
 */
function classifyBlock(lines: readonly string[]): BlockKind {
  const body = lines.filter((line) => line.trim() !== '');
  if (body.some((line) => LIST_LEAD_RE.test(line))) {
    return 'list';
  }
  if (hasBox(lines) || isIndented(lines) || isColumnar(body)) {
    return 'pre';
  }
  return 'prose';
}

/**
 * Classify a turn's faithful multi-line `text` into styled {@link Block}s. Pure: same input → same
 * output. Fenced ` ``` ` spans are captured verbatim as `code` (inner blank lines kept, fence lines
 * stripped from the island, `lang` surfaced); the remainder is split on blank lines and each block is
 * classified `prose` / `pre` / `list`. An empty / whitespace-only input yields `[]`.
 */
export function classifyBlocks(text: string): Block[] {
  const lines = text.split('\n');
  const blocks: Block[] = [];
  let current: string[] = [];

  const flush = (): void => {
    if (current.length > 0) {
      blocks.push({ kind: classifyBlock(current), lines: current });
      current = [];
    }
  };

  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i] ?? '';
    if (FENCE_RE.test(line)) {
      // A fenced code span: flush any pending block, then consume verbatim through the closing fence
      // (or EOF if the model never closed it). The fence lines themselves are dropped from the
      // island's `lines`; the opening fence's trailing word becomes `lang`.
      flush();
      const lang = fenceLang(line);
      const codeLines: string[] = [];
      i += 1;
      while (i < n) {
        const inner = lines[i] ?? '';
        if (FENCE_RE.test(inner)) {
          // Consume + drop the closing fence, then stop.
          i += 1;
          break;
        }
        codeLines.push(inner);
        i += 1;
      }
      // An unterminated fence (no closing ```, e.g. mid-stream) still renders its captured lines as a
      // code island — the loop simply ran to EOF.
      blocks.push(
        lang === '' ? { kind: 'code', lines: codeLines } : { kind: 'code', lines: codeLines, lang },
      );
      continue;
    }
    if (line.trim() === '') {
      flush();
      i += 1;
      continue;
    }
    current.push(line);
    i += 1;
  }
  flush();
  return blocks;
}
