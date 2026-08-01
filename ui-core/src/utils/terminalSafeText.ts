/**
 * Sanitize strings before they reach Ink `<Text>` / the terminal.
 *
 * Yoga `overflow="hidden"` cannot stop the terminal from interpreting C0/C1,
 * CSI cursor movement, or OSC sequences after Ink has composed a frame.
 */

/** C0/C1 controls except LF (`\n`) and ESC (`\u001B`). ESC is handled via ANSI stripping. */
// biome-ignore lint/suspicious/noControlCharactersInRegex: intentional C0/C1 matching for sanitizer
const CONTROL_EXCEPT_NEWLINE_AND_ESC = /[\u0000-\u0008\u000B-\u001A\u001C-\u001F\u007F-\u009F]/g;

/**
 * CSI / OSC / DCS / SOS / PM / APC and other ESC-introduced sequences.
 * Leaves printable payload; does not preserve SGR styling (Ink owns color).
 */
const ANSI_ESCAPE =
  // biome-ignore lint/suspicious/noControlCharactersInRegex: intentional ESC matching
  /\u001B(?:\[[\x30-\x3F]*[\x20-\x2F]*[\x40-\x7E]|\][^\u0007\u001B]*(?:\u0007|\u001B\\)|[PX^_][^\u001B]*\u001B\\|.)/g;

/** Remove ANSI/VT escape sequences (including SGR). */
export function stripAnsiEscapes(value: string): string {
  return value.replace(ANSI_ESCAPE, '');
}

export interface TerminalSafeTextOptions {
  /**
   * Strip CSI/OSC/etc. before removing leftover controls.
   * Default true — display text should not carry terminal styling or cursor ops.
   */
  readonly stripAnsi?: boolean;
}

/**
 * Normalize a string for safe terminal display inside a pane.
 * Preserves newlines; expands tabs; drops cursor-affecting controls and (by default) ANSI.
 */
export function terminalSafeText(value: string, options: TerminalSafeTextOptions = {}): string {
  const stripAnsi = options.stripAnsi ?? true;
  let text = stripAnsi ? stripAnsiEscapes(value) : value;
  text = text
    .replace(/\r\n?/g, '\n')
    .replace(/\t/g, '    ')
    .replace(CONTROL_EXCEPT_NEWLINE_AND_ESC, '');
  // Drop orphan ESC bytes when ANSI was stripped (or leftover after partial sequences).
  if (stripAnsi) {
    // biome-ignore lint/suspicious/noControlCharactersInRegex: strip leftover ESC
    text = text.replace(/\u001B/g, '');
  }
  return text;
}
