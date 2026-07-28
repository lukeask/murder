# Harness parser geometry audit

The production compatibility geometry remains **220×50**. Parser improvements
below reduce accidental coupling; they do not enable dynamic harness resizing.
The Ink terminal viewport crops and pans locally without changing this geometry.

## Width-independent paths

| Path | Current semantic boundary |
| --- | --- |
| Codex `/status` usage parsing | `codex_status.py` locates a structurally bounded panel, strips ANSI and border gutters, then explicitly folds continuations owned by colon-labelled rows. Limit percentages, reset clocks, context values, and session IDs are tested at 54, 84, 220, and 260 columns. |
| Codex transcript status masking | The transcript grammar masks every physical row belonging to a structurally identified status panel. Wrapped reset/value continuations cannot leak into assistant prose. |
| Pi `Model scope:` parsing | The adapter bounds the labelled row by blank/footer delimiters, reconstructs mid-identifier soft wraps without inserting spaces, then splits the logical comma-delimited value. Tested at 48, 80, 220, and 260 columns. |
| ANSI and trailing padding for the paths above | ANSI is removed before semantic matching; trailing capture padding is non-semantic and covered by the width matrix. |

Fixture matrix: `tests/fixtures/harness_geometry/codex_status_{54,84,220,260}.txt`.
The 220-column fixture preserves the default compatibility shape.

## Paths that still require stable 220×50 geometry

| Path | Remaining assumption |
| --- | --- |
| Transcript `_PaneScrollback` alignment | Coordinates identify physical rows. Rewrapping between frames invalidates exact overlap and can create false epochs or duplicate/mutated segments. A future variable-width producer must declare geometry per frame and begin an explicit reflow epoch on change. |
| Codex legacy `_viewport(raw, height)` fallback | “Last `height` physical lines” is only best-effort when `viewport_text` is absent. Native observation already supplies `viewport_text`; wrapping or omitted blank rows makes the fallback ambiguous. |
| Codex/Cursor/Claude/Antigravity numbered menus and composers | Several adapters infer continuation rows from indentation, box gutters, fixed tail lengths, or ANSI background roles. These remain renderer-specific and should return partial/unknown when their delimiters or ANSI provenance are missing. |
| Shared transcript prose reflow | Two-space gutters, indentation steps, and physical blank lines are compatibility heuristics because `capture-pane` does not expose soft-wrap metadata. Ambiguous blocks remain preserved rather than aggressively reflowed. |
| Pi transcript role attribution | A one-cell leading indent is the only stable role signal currently available; a narrow soft wrap may become flush-left and look like chrome. |
| Model pickers outside Pi scope | Some parsers still assume one choice per physical row or use multi-space column splits. They need bounded, parser-specific row assemblers before variable geometry is safe. |

## Capture/render separation

- Semantic parsers consume normalized capture text or the explicit
  `TerminalFrame.viewport_text`; they do not consume the Ink viewport.
- Raw ANSI remains evidence. Sanitized logical rows are separate parser input.
- Trailing spaces never identify semantics.
- Fixed-count tail and physical-line fallbacks are compatibility-only and must
  not be used as evidence that dynamic harness resizing is safe.
