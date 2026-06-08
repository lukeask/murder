/**
 * `TextInput` — a minimal controlled single-line text-input component for use inside modal dialogs.
 *
 * ## Why hand-rolled (not `ink-text-input`)
 *
 * `ink-text-input` is a real package (ESM, MIT) and works well, but adding an external dep for a
 * modal text field is unnecessary: modal dialogs need only the basics — printable char insertion, Backspace
 * delete-left, the value as state. The tab-completion, history, and mask features of `ink-text-input`
 * are not needed here. A hand-rolled 40-line component avoids a new dep, stays under our own test
 * coverage, and lets C13 copy it cleanly. If more advanced editing is needed later, swapping to
 * `ink-text-input` is a one-file change.
 *
 * ## Usage (the C13 copy recipe)
 *
 * This is a **presentation component** — it renders text and a cursor, but does *not* call `useInput`
 * or own any input capture. Key events reach it because the mode that hosts it declares the text-input
 * chords in its `onIntent` and `onUncaptured` (the mode is the only input consumer; everything goes
 * through the mode's keymap or `onUncaptured` dispatcher extension).
 *
 * ```tsx
 * <TextInput value={value} placeholder="Enter name…" focused={true} />
 * ```
 *
 * All editing is driven by the parent mode's `onIntent` and `onUncaptured`: the mode declares intents
 * for special key events (`backspace`, `deleteAll`) and receives printable chars through `onUncaptured`,
 * then mutates its closure state and calls `refresh()`. The `TextInput` just renders whatever value
 * the parent gives it. The mode is the single-input owner (rule 5).
 *
 * This is the reusable text-input-in-modal sub-pattern C13 (spawn wizard) copies.
 */

import { Box, Text } from 'ink';
import type { JSX } from 'react';

/** Props for the controlled text input display. */
export interface TextInputProps {
  /** The current input value (controlled). */
  readonly value: string;
  /** Shown dimly when `value` is empty. */
  readonly placeholder?: string;
  /** When `true`, renders a blinking cursor `█` at the end of the text. */
  readonly focused?: boolean;
  /** Text color for the input value. */
  readonly color?: string;
}

/**
 * Insert a printable character at the end of the current value. Used by the mode's `onUncaptured`
 * handler (which receives the raw char from the dispatcher). Exported so the mode factory calls it.
 */
export function insertChar(value: string, char: string): string {
  return value + char;
}

/**
 * Delete the last character (Backspace). Returns the value unchanged if empty. Exported so the
 * mode factory calls it from the `backspace` intent handler.
 */
export function deleteLastChar(value: string): string {
  if (value.length === 0) {
    return value;
  }
  return value.slice(0, -1);
}

/**
 * The controlled text input display. Renders the value (or a dim placeholder when empty) with an
 * optional cursor block. Pure over its props — no store/bus knowledge (rule 1). The mode that hosts
 * it owns input via its keymap + `onUncaptured`; this component just draws.
 *
 * C13 (spawn wizard) copies this component alongside the modal pattern.
 */
export function TextInput({
  value,
  placeholder,
  focused = false,
  color = 'white',
}: TextInputProps): JSX.Element {
  const isEmpty = value.length === 0;
  return (
    <Box>
      {isEmpty && placeholder !== undefined ? (
        <Text dimColor>{placeholder}</Text>
      ) : (
        <Text color={color}>{value}</Text>
      )}
      {focused && (
        <Text color="white" bold>
          {'█'}
        </Text>
      )}
    </Box>
  );
}
